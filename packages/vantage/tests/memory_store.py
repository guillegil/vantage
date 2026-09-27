"""An in-memory `ExecutionStore`, a test double kept beside the tests: the
`vantage` command always serves a `SqliteExecutionStore`, so nothing shipped
needs it.

The shared contract suite (``vantage_port_contract.py``) runs against both
this and the SQLite adapter, so the port is proven by two independent
mechanisms rather than one implementation agreeing with itself. It is a real
implementation, not a stub: it mirrors the SQLite adapter's catalogue
monotonicity, first-write-wins results keyed by ``(run_id, node_id)``, and
the run upsert guard -- a finish-write (`exit_status` is not `None`) applies
over a start-only row, never the reverse. It compares real `datetime`s where
the SQLite adapter compares fixed-width UTC text; the two orders agree.

A result's identity is read through the catalogue, as the SQLite adapter
reads it through its `test_case` row: one decomposition per node id, shared
by every run's result of it.

``_last_contact`` is a separate dict because ``Execution`` carries no
``last_contact_at`` field; that column is a storage concern. It is set on
the insert branch of ``record_session`` and advanced only by
``touch_last_contact``, monotonically. ``_recorded_by`` is kept apart for
the same reason, and set on the insert branch only.

The routes call a store from FastAPI's threadpool, so every public method
holds one lock, as every SQLite adapter call holds its own: a call sees and
leaves the dicts whole, and a check-then-write such as ``upsert_setting``'s
key bound is one step.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Concatenate, ParamSpec, TypeVar

from vantage.core.domain.access import Grant, Token, User
from vantage.core.domain.execution import Execution, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projects import DEFAULT_PROJECT, Project
from vantage.core.domain.result import CaseIdentity, CatalogueEntry, Result
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    MAX_PAGE_ITEMS,
    ForeignRunError,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    Page,
    ProjectExistsError,
    ProjectMismatchError,
    ProjectSetting,
    ResultListEntry,
    RunDetail,
    RunKey,
    RunListEntry,
    RunMetadata,
    UnknownProjectError,
    UnknownUserError,
    UserExistsError,
)

# Empty VCS context and empty failure evidence become `None` here, on write,
# because this adapter stores the objects themselves; the SQLite adapter
# applies the same `is_empty` rule when it decodes a row.


def _normalized_vcs(vcs: VcsContext | None) -> VcsContext | None:
    return None if vcs is None or vcs.is_empty() else vcs


def _merged_vcs(incoming: VcsContext, previous: VcsContext | None) -> VcsContext:
    """Per-FIELD coalesce, the mirror of the SQLite adapter's per-column
    `COALESCE(excluded.vcs_*, run.vcs_*)`: null -> value only, never value
    -> null. The truncation flag follows whichever subject is kept."""
    if previous is None:
        return incoming
    keeps_subject = incoming.commit_subject is not None
    return VcsContext(
        commit=incoming.commit if incoming.commit is not None else previous.commit,
        branch=incoming.branch if incoming.branch is not None else previous.branch,
        commit_subject=incoming.commit_subject if keeps_subject else previous.commit_subject,
        commit_subject_truncated=incoming.commit_subject_truncated
        if keeps_subject
        else previous.commit_subject_truncated,
        dirty=incoming.dirty if incoming.dirty is not None else previous.dirty,
        root=incoming.root if incoming.root is not None else previous.root,
    )


def _normalized_result(result: Result) -> Result:
    if result.failure is not None and result.failure.is_empty():
        return replace(result, failure=None)
    return result


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _locked(
    method: Callable[Concatenate[InMemoryExecutionStore, _P], _R],
) -> Callable[Concatenate[InMemoryExecutionStore, _P], _R]:
    """Run `method` holding the store's lock."""

    @functools.wraps(method)
    def locked(store: InMemoryExecutionStore, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with store._lock:
            return method(store, *args, **kwargs)

    return locked


def _past(after: RunKey | None, started_at: datetime, run_id: str) -> bool:
    """Whether a run comes after `after` in the newest-first order, as the
    SQLite adapter's `_AFTER_RUN_KEY` decides it; every run does without a
    key."""
    return after is None or (started_at, run_id) < (after.started_at, after.run_id)


class InMemoryExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` with dicts."""

    def __init__(self) -> None:
        # Not re-entrant: no locked method calls another.
        self._lock = threading.Lock()
        self._executions: dict[str, Execution] = {}
        # Keyed by project and node id, as the adapters' unique index is.
        self._catalogue: dict[tuple[str, str], CatalogueEntry] = {}
        self._results: dict[tuple[str, str], Result] = {}
        self._last_contact: dict[str, datetime] = {}
        self._settings: dict[tuple[str, str, str], ProjectSetting] = {}
        # Every store has `default` from its creation, as a database does.
        self._projects: dict[str, Project] = {
            DEFAULT_PROJECT: Project(name=DEFAULT_PROJECT, created_at=datetime.now(timezone.utc))
        }
        self._project_of: dict[str, str] = {}
        self._metadata_files: dict[tuple[str, str], MetadataFile] = {}
        self._metadata_entries: dict[tuple[str, str], MetadataEntry] = {}
        self._recorded_by: dict[str, str | None] = {}
        self._users: dict[str, User] = {}
        # Keyed by id, which counts from 1 like an identity column; revoked
        # tokens stay.
        self._tokens: dict[int, Token] = {}
        self._token_ids: dict[str, int] = {}

    @_locked
    def record_session(
        self,
        execution: Execution,
        *,
        project: str,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
        recorded_by: str | None = None,
    ) -> bool:
        # `received_at` is the server's clock, not the client's: it seeds
        # `_last_contact` for a new run and is not part of `Execution`. The
        # refusals come in the adapters' order: the project, the recorder,
        # the run's own project, then whether it is finished.
        if project not in self._projects:
            raise UnknownProjectError(f"there is no project named {project!r}")
        identity = execution.identity.value
        stored = self._executions.get(identity)
        if stored is not None and self._recorded_by[identity] != recorded_by:
            raise ForeignRunError(f"run {identity} was recorded by another user")
        if stored is not None and self._project_of[identity] != project:
            raise ProjectMismatchError(f"run {identity} was recorded in another project")
        if stored is not None and stored.exit_status is not None:
            # A finished run is final: a report reaching it later is a replay
            # and adds nothing, whatever results it carries.
            return False
        created = stored is None
        if stored is None:
            self._executions[identity] = replace(execution, vcs=_normalized_vcs(execution.vcs))
            self._last_contact[identity] = received_at
            self._recorded_by[identity] = recorded_by
            self._project_of[identity] = project
        elif stored.exit_status is None and execution.exit_status is not None:
            # Mirrors the SQLite adapter's `DO UPDATE ... WHERE`: `exit_status`,
            # never `finished_at`, is the discriminator, and `started_at` is
            # never advanced on this path. `vcs` merges per field, like the
            # SQLite adapter's per-column `COALESCE`, so a partial snapshot
            # never nulls a stored value.
            merged_vcs = _normalized_vcs(
                stored.vcs if execution.vcs is None else _merged_vcs(execution.vcs, stored.vcs)
            )
            self._executions[identity] = Execution(
                identity=stored.identity,
                started_at=stored.started_at,
                finished_at=execution.finished_at,
                exit_status=execution.exit_status,
                interrupted=execution.interrupted,
                interrupt_reason=execution.interrupt_reason,
                vcs=merged_vcs,
            )

        for result in results:
            self._upsert_catalogue_entry(project, execution, result.identity)
            key = (identity, result.identity.node_id)
            if key not in self._results:
                self._results[key] = _normalized_result(result)

        # Mirrors the SQLite adapter's `ON CONFLICT DO NOTHING`: a metadata
        # file/entry is written once and never updated. A run also stops
        # taking new keys once it holds `MAX_METADATA_ENTRIES`, however many
        # reports carry them.
        for metadata_file in metadata.files:
            self._metadata_files.setdefault((identity, metadata_file.source_file), metadata_file)
        held = sum(1 for run_id, _key in self._metadata_entries if run_id == identity)
        for metadata_entry in metadata.entries:
            slot = (identity, metadata_entry.key)
            if slot not in self._metadata_entries and held < MAX_METADATA_ENTRIES:
                self._metadata_entries[slot] = metadata_entry
                held += 1

        return created

    def _upsert_catalogue_entry(
        self, project: str, execution: Execution, identity: CaseIdentity
    ) -> None:
        slot = (project, identity.node_id)
        existing = self._catalogue.get(slot)
        if existing is None:
            self._catalogue[slot] = CatalogueEntry(
                identity=identity,
                first_seen_at=execution.started_at,
                last_seen_at=execution.started_at,
                last_seen_run_id=execution.identity.value,
            )
            return

        # Mirrors the SQLite `DO UPDATE`: `first_seen_at` moves back to an
        # earlier run, and the identity, `last_seen_at` and
        # `last_seen_run_id` advance only when the new run is strictly newer.
        advances = execution.started_at > existing.last_seen_at
        self._catalogue[slot] = CatalogueEntry(
            identity=identity if advances else existing.identity,
            first_seen_at=min(existing.first_seen_at, execution.started_at),
            last_seen_at=execution.started_at if advances else existing.last_seen_at,
            last_seen_run_id=execution.identity.value if advances else existing.last_seen_run_id,
        )

    def _as_catalogued(self, run_id: str, result: Result) -> Result:
        """`result` with the identity its node id's catalogue entry in its
        run's project holds, for a caller already holding the lock."""
        entry = self._catalogue[(self._project_of[run_id], result.identity.node_id)]
        return replace(result, identity=entry.identity)

    def _run_results(self, execution_id: str) -> list[Result]:
        """`execution_id`'s results in insertion order, identities read
        through the catalogue, for a caller already holding the lock."""
        return [
            self._as_catalogued(run_id, result)
            for (run_id, _node_id), result in self._results.items()
            if run_id == execution_id
        ]

    @_locked
    def get_execution(self, execution_id: str) -> Execution | None:
        return self._executions.get(execution_id)

    @_locked
    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        if execution_id not in self._executions:
            return False
        current = self._last_contact.get(execution_id)
        if current is not None and contacted_at <= current:
            return False
        self._last_contact[execution_id] = contacted_at
        return True

    @_locked
    def count_executions(self) -> int:
        return len(self._executions)

    @_locked
    def get_results(self, execution_id: str) -> Sequence[Result]:
        return self._run_results(execution_id)

    @_locked
    def count_results(self) -> int:
        return len(self._results)

    @_locked
    def get_catalogue_entry(self, node_id: str, *, project: str) -> CatalogueEntry | None:
        return self._catalogue.get((project, node_id))

    def _of_project(self, project: str) -> list[Execution]:
        """The project's runs, for a caller already holding the lock."""
        return [
            execution
            for run_id, execution in self._executions.items()
            if self._project_of[run_id] == project
        ]

    def _run_page(
        self,
        candidates: Iterable[Execution],
        *,
        limit: int,
        offset: int,
        after: RunKey | None,
    ) -> Page[RunListEntry]:
        """One page of `candidates`, for a caller already holding the lock.

        Mirrors the SQLite adapter's `ORDER BY started_at DESC, id DESC` /
        `LIMIT min(limit, 200) + 1 OFFSET`: fetching one extra row is what
        sets `has_more` without a second query."""
        page_limit = min(limit, MAX_PAGE_ITEMS)
        ordered = sorted(
            (
                execution
                for execution in candidates
                if _past(after, execution.started_at, execution.identity.value)
            ),
            key=lambda execution: (execution.started_at, execution.identity.value),
            reverse=True,
        )
        window = ordered[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            RunListEntry.from_execution(
                execution,
                last_contact_at=self._last_contact.get(execution.identity.value),
                recorded_by=self._recorded_by[execution.identity.value],
            )
            for execution in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    @_locked
    def list_runs(
        self, *, project: str, limit: int, offset: int, after: RunKey | None = None
    ) -> Page[RunListEntry]:
        return self._run_page(self._of_project(project), limit=limit, offset=offset, after=after)

    def _holds(self, run_id: str, key: str, value: str) -> bool:
        """Mirrors `rm.key = ? AND rm.value = ?`: `entry.value` is `None`
        for a key without a captured value, so it never matches, just as
        SQL NULL never equals a bound string. For a caller already holding
        the lock."""
        entry = self._metadata_entries.get((run_id, key))
        return entry is not None and entry.value == value

    def _predating(self, project: str, key: str) -> int:
        """How many of the project's runs started before `key` first
        appeared in it, for a caller already holding the lock. A row for
        `key` of any status or source counts towards `first_seen`, mirroring
        the SQLite adapter's `run_metadata` join, which does not filter on
        either."""
        runs = self._of_project(project)
        carried_at = [
            self._executions[run_id].started_at
            for (run_id, entry_key) in self._metadata_entries
            if entry_key == key and self._project_of[run_id] == project
        ]
        if not carried_at:
            return len(runs)
        first_seen = min(carried_at)
        return sum(1 for execution in runs if execution.started_at < first_seen)

    @_locked
    def list_runs_with_metadata_horizon(
        self,
        *,
        project: str,
        filters: Sequence[tuple[str, str]],
        limit: int,
        offset: int,
        after: RunKey | None = None,
    ) -> tuple[Page[RunListEntry], tuple[int, ...]]:
        # Every read happens under one hold of the lock, so the page and the
        # counts describe one state.
        page = self._run_page(
            (
                execution
                for execution in self._of_project(project)
                if all(self._holds(execution.identity.value, key, value) for key, value in filters)
            ),
            limit=limit,
            offset=offset,
            after=after,
        )
        keys = dict.fromkeys(key for key, _value in filters)
        return page, tuple(self._predating(project, key) for key in keys)

    @_locked
    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        execution = self._executions.get(execution_id)
        if execution is None:
            return None
        return RunDetail(
            execution=execution,
            last_contact_at=self._last_contact.get(execution_id),
            recorded_by=self._recorded_by[execution_id],
            project=self._project_of[execution_id],
        )

    @_locked
    def get_run_metadata(self, execution_id: str) -> RunMetadata | None:
        # `sorted()` compares code points, the order the SQLite adapter's
        # `ORDER BY` gets from comparing UTF-8 bytes.
        if execution_id not in self._executions:
            return None
        stored = self._stored_metadata(execution_id)
        return RunMetadata(
            files=tuple(sorted(stored.files, key=lambda file: file.source_file)),
            entries=tuple(sorted(stored.entries, key=lambda entry: entry.key)),
        )

    @_locked
    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        # The paginated, lean sibling of `get_results`, with the same
        # clamp/`has_more` mechanism as `list_runs`. Dict insertion order
        # mirrors the SQLite adapter's `ORDER BY r.id`.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        window = self._run_results(execution_id)[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(ResultListEntry.from_result(result) for result in window[:page_limit])
        return Page(items=items, has_more=has_more)

    @_locked
    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        result = self._results.get((execution_id, node_id))
        return None if result is None else self._as_catalogued(execution_id, result)

    @_locked
    def list_history(
        self,
        *,
        project: str,
        node_id: str,
        limit: int,
        offset: int,
        after: RunKey | None = None,
    ) -> Page[HistoryEntry]:
        # Mirrors `list_runs`' total order -- `(started_at, run_id)`
        # descending -- over every execution of the project that has a
        # result for this `node_id`. An unknown `node_id` yields an empty
        # page, never an error.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        matches = [
            (run_id, result)
            for (run_id, result_node_id), result in self._results.items()
            if result_node_id == node_id
            and self._project_of[run_id] == project
            and _past(after, self._executions[run_id].started_at, run_id)
        ]
        ordered = sorted(
            matches,
            key=lambda pair: (self._executions[pair[0]].started_at, pair[0]),
            reverse=True,
        )
        window = ordered[offset : offset + page_limit + 1]
        has_more = len(window) > page_limit
        items = tuple(
            HistoryEntry.from_execution(
                self._executions[run_id],
                last_contact_at=self._last_contact.get(run_id),
                outcome=result.outcome,
                duration=result.duration,
            )
            for run_id, result in window[:page_limit]
        )
        return Page(items=items, has_more=has_more)

    @_locked
    def list_settings(self, namespace: str, *, project: str) -> Sequence[ProjectSetting]:
        # `sorted()` on `key` mirrors the SQLite adapter's `ORDER BY key`.
        matching = [
            setting
            for (setting_project, setting_namespace, _key), setting in self._settings.items()
            if (setting_project, setting_namespace) == (project, namespace)
        ]
        return tuple(sorted(matching, key=lambda setting: setting.key))

    @_locked
    def upsert_setting(
        self,
        namespace: str,
        key: str,
        *,
        project: str,
        value: str,
        updated_at: datetime,
        max_keys: int | None = None,
    ) -> bool:
        if project not in self._projects:
            raise UnknownProjectError(f"there is no project named {project!r}")
        identity = (project, namespace, key)
        created = identity not in self._settings
        if created and max_keys is not None:
            held = sum(
                1
                for setting_project, setting_namespace, _key in self._settings
                if (setting_project, setting_namespace) == (project, namespace)
            )
            if held >= max_keys:
                raise NamespaceFullError(f"{namespace!r} already holds {held} keys")
        self._settings[identity] = ProjectSetting(
            project=project, namespace=namespace, key=key, value=value, updated_at=updated_at
        )
        return created

    @_locked
    def delete_setting(self, namespace: str, key: str, *, project: str) -> bool:
        identity = (project, namespace, key)
        if identity not in self._settings:
            return False
        del self._settings[identity]
        return True

    @_locked
    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        return tuple(
            (result.identity.file_path, result.outcome)
            for result in self._run_results(execution_id)
        )

    @_locked
    def create_project(self, name: str, *, created_at: datetime) -> Project:
        if name in self._projects:
            raise ProjectExistsError(f"there is already a project named {name!r}")
        project = Project(name=name, created_at=created_at)
        self._projects[name] = project
        return project

    @_locked
    def get_project(self, name: str) -> Project | None:
        return self._projects.get(name)

    @_locked
    def list_projects(self) -> Sequence[Project]:
        return tuple(sorted(self._projects.values(), key=lambda project: project.name))

    @_locked
    def access_required(self) -> bool:
        return bool(self._users)

    @_locked
    def create_user(self, name: str, *, admin: bool, created_at: datetime) -> User:
        if name in self._users:
            raise UserExistsError(f"there is already a user named {name!r}")
        user = User(name=name, admin=admin, disabled=False, created_at=created_at)
        self._users[name] = user
        return user

    @_locked
    def get_user(self, name: str) -> User | None:
        return self._users.get(name)

    @_locked
    def list_users(self) -> Sequence[User]:
        # `sorted()` compares code points, as the adapters' `ORDER BY name`.
        return tuple(sorted(self._users.values(), key=lambda user: user.name))

    @_locked
    def update_user(
        self, name: str, *, admin: bool | None = None, disabled: bool | None = None
    ) -> User | None:
        user = self._users.get(name)
        if user is None:
            return None
        user = replace(
            user,
            admin=user.admin if admin is None else admin,
            disabled=user.disabled if disabled is None else disabled,
        )
        self._users[name] = user
        return user

    @_locked
    def create_token(
        self,
        user: str,
        *,
        digest: str,
        label: str,
        scopes: frozenset[str],
        created_at: datetime,
    ) -> Token:
        if user not in self._users:
            raise UnknownUserError(f"there is no user named {user!r}")
        if digest in self._token_ids:
            raise ValueError("a token with that digest is stored already")
        token = Token(
            id=len(self._tokens) + 1,
            user=user,
            label=label,
            scopes=frozenset(scopes),
            created_at=created_at,
            revoked_at=None,
        )
        self._tokens[token.id] = token
        self._token_ids[digest] = token.id
        return token

    @_locked
    def list_tokens(self, *, user: str | None = None) -> Sequence[Token]:
        return tuple(
            token
            for _token_id, token in sorted(self._tokens.items())
            if user is None or token.user == user
        )

    @_locked
    def get_token(self, token_id: int) -> Token | None:
        return self._tokens.get(token_id)

    @_locked
    def revoke_token(self, token_id: int, *, revoked_at: datetime, user: str | None = None) -> bool:
        token = self._tokens.get(token_id)
        if token is None or token.revoked_at is not None:
            return False
        if user is not None and token.user != user:
            return False
        self._tokens[token_id] = replace(token, revoked_at=revoked_at)
        return True

    @_locked
    def authenticate(self, digest: str) -> Grant | None:
        token_id = self._token_ids.get(digest)
        if token_id is None:
            return None
        token = self._tokens[token_id]
        user = self._users[token.user]
        if token.revoked_at is not None or user.disabled:
            return None
        return Grant(user=user.name, admin=user.admin, scopes=token.scopes)

    @_locked
    def metadata(self, run_id: str) -> RunMetadata:
        """The metadata files and entries stored for `run_id`, in the order
        they were stored, for a test to inspect without `get_run_metadata`."""
        return self._stored_metadata(run_id)

    def _stored_metadata(self, run_id: str) -> RunMetadata:
        """`metadata`, for a caller already holding the lock."""
        return RunMetadata(
            files=tuple(
                metadata_file
                for (stored_run_id, _source_file), metadata_file in self._metadata_files.items()
                if stored_run_id == run_id
            ),
            entries=tuple(
                entry
                for (stored_run_id, _key), entry in self._metadata_entries.items()
                if stored_run_id == run_id
            ),
        )

    @_locked
    def close(self) -> None:
        self._executions.clear()
        self._catalogue.clear()
        self._results.clear()
        self._last_contact.clear()
        self._settings.clear()
        self._metadata_files.clear()
        self._metadata_entries.clear()
        self._recorded_by.clear()
        self._project_of.clear()
        self._projects.clear()
        self._users.clear()
        self._tokens.clear()
        self._token_ids.clear()
