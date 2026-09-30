"""Guards on the server image: the root ``Dockerfile`` and ``.dockerignore``.

CI's ``image`` job builds the image and runs it. These read the files, with
no Docker, for what running it cannot show, or shows only in that job: that
the image installs exactly the lock, runs as a fixed unprivileged user, and
starts ``vantage`` as PID 1 on the database its volume holds, at the port it
exposes and its health check asks.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from vantage.core.config.database import SqliteTarget
from vantage.core.config.resolution import ServerConfig, resolve_server_config
from vantage.service import cli
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = WORKSPACE_ROOT / "Dockerfile"
DOCKERIGNORE = WORKSPACE_ROOT / ".dockerignore"
CI = WORKSPACE_ROOT / ".github" / "workflows" / "ci.yml"

Instruction = tuple[str, str]


def _instructions() -> list[Instruction]:
    """Every instruction as ``(KEYWORD, arguments)``, comment lines dropped
    and continued lines joined, as the builder reads them."""
    instructions: list[Instruction] = []
    pending = ""
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("#") or not (text or pending):
            continue
        if text.endswith("\\"):
            pending += text[:-1] + " "
            continue
        keyword, _, arguments = (pending + text).partition(" ")
        instructions.append((keyword.upper(), arguments.strip()))
        pending = ""
    return instructions


def _stages() -> list[list[Instruction]]:
    """The instructions of each stage, its `FROM` first."""
    stages: list[list[Instruction]] = []
    for keyword, arguments in _instructions():
        if keyword == "FROM":
            stages.append([])
        if stages:
            stages[-1].append((keyword, arguments))
    return stages


def _image(stage: list[Instruction]) -> str:
    """The image a stage starts from, with the build arguments declared
    before the first `FROM`, the only ones a `FROM` can use, put in."""
    instructions = _instructions()
    first = [keyword for keyword, _ in instructions].index("FROM")
    defaults = {
        name: value
        for keyword, arguments in instructions[:first]
        if keyword == "ARG"
        for name, value in [arguments.split("=", 1)]
    }
    image = stage[0][1].split()[0]
    return re.sub(r"\$\{(\w+)\}", lambda name: defaults[name[1]], image)


def _last(keyword: str) -> str:
    """The arguments of the final stage's last `keyword`: what the image
    keeps."""
    (found, *_) = [arguments for name, arguments in reversed(_stages()[-1]) if name == keyword]
    return found


def _commands(stage: list[Instruction]) -> list[list[str]]:
    """The shell commands of a stage's `RUN` instructions, split into words,
    without the options `RUN` itself takes (`--mount=...`)."""
    return [
        shlex.split(command)
        for keyword, arguments in stage
        if keyword == "RUN"
        for command in re.sub(r"^(--\S+\s+)*", "", arguments).split("&&")
    ]


def _environment() -> dict[str, str]:
    """The environment the image sets, for the server and `docker exec`."""
    return {
        name: value
        for keyword, arguments in _stages()[-1]
        if keyword == "ENV"
        for pair in shlex.split(arguments)
        for name, value in [pair.split("=", 1)]
    }


def _served(allowed_hosts: str | None = None) -> ServerConfig:
    """What the image's command serves, parsed by `vantage`'s own parser and
    resolved as `vantage` resolves it, with no home directory: a uid given
    with `--user` has none; with `allowed_hosts`, as `docker run -e
    VANTAGE_ALLOWED_HOSTS=...` would set it."""
    args = cli._parse_args(json.loads(_last("CMD")))
    environment = _environment()
    if allowed_hosts is not None:
        environment["VANTAGE_ALLOWED_HOSTS"] = allowed_hosts
    return resolve_server_config(
        cli_database=args.database,
        env_database=environment.get("VANTAGE_DATABASE"),
        cli_host=args.host,
        cli_port=args.port,
        cli_grace_period=args.grace_period,
        cli_allowed_hosts=args.allowed_host,
        env_allowed_hosts=environment.get("VANTAGE_ALLOWED_HOSTS"),
        home=None,
        xdg_data_home=None,
    )


def test_every_image_is_a_release_named_by_its_tag() -> None:
    """A tag, never none or `latest`, so a rebuild moves to another release
    only when this file says so; and every image is a stage's, where this
    sees it, never one a `COPY --from` names."""
    names = {
        words[2]
        for keyword, arguments in _instructions()
        if keyword == "FROM" and len(words := arguments.split()) == 3 and words[1].upper() == "AS"
    }
    images = [_image(stage) for stage in _stages() if _image(stage) not in names]
    assert images
    for image in images:
        repository, _, tag = image.rpartition(":")
        assert repository and "/" not in tag and tag not in ("", "latest"), image
    copied = re.findall(r"--from=(\S+)", " ".join(a for k, a in _instructions() if k == "COPY"))
    assert set(copied) <= names


def test_the_server_runs_on_the_interpreter_it_was_built_for_one_ci_tests() -> None:
    """The environment the build stage makes links to its interpreter, so the
    image the server runs in must be the same one; a Python the suite never
    ran on would be one nothing was tested on; and the Debian release is
    named, so a rebuild never moves to the next one unannounced."""
    (build,) = [stage for stage in _stages() if ["uv"] in (c[:1] for c in _commands(stage))]
    assert _image(build) == _image(_stages()[-1])
    version = re.fullmatch(r"python:(\d+\.\d+)-slim-\w+", _image(build))
    assert version is not None, _image(build)
    matrix = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["test"]["strategy"]["matrix"]
    assert version[1] in matrix["python-version"]


def test_the_environment_is_exactly_the_lock() -> None:
    """`--locked` installs what uv.lock pins and fails the build when the lock
    no longer matches the pyproject files, where `--frozen` would build
    without whatever the lock is missing, and pip would resolve afresh;
    `--no-editable` leaves the environment nothing to need from the source
    tree the image does not keep."""
    commands = [command for stage in _stages() for command in _commands(stage)]
    (sync,) = [command[command.index("sync") + 1 :] for command in commands if "sync" in command]
    assert "--locked" in sync and "--frozen" not in sync
    assert "--no-editable" in sync
    assert sync[sync.index("--package") + 1] == "vantage"
    assert {sync[i + 1] for i, word in enumerate(sync) if word == "--extra"} == {
        "server",
        "postgres",
    }
    assert not [c for c in commands for i in range(len(c)) if c[i : i + 2] == ["pip", "install"]]


def test_the_server_runs_as_a_fixed_unprivileged_user_owning_its_data() -> None:
    """A fixed, numeric uid and gid, so a bind mount can be given to them,
    never root; the directory the database goes in is theirs alone, and a
    new named volume copies that owner and mode."""
    uid, _, gid = _last("USER").partition(":")
    assert uid.isdigit() and gid.isdigit() and "0" not in (uid, gid)
    commands = _commands(_stages()[-1])
    assert ["groupadd", "--gid", gid, "vantage"] in commands
    (useradd,) = [command for command in commands if command[:1] == ["useradd"]]
    assert useradd[useradd.index("--uid") + 1] == uid
    assert useradd[useradd.index("--gid") + 1] == gid
    database = _served().database
    assert isinstance(database, SqliteTarget)
    data = str(database.path.parent)
    assert ["install", "-d", "-m", "0700", "-o", uid, "-g", gid, data] in commands


def test_vantage_is_pid_1_in_exec_form() -> None:
    """A shell as PID 1 would not pass docker stop's SIGTERM on, and the store
    would be killed open ten seconds later; an init would pass it on, but a
    clean stop would then exit 143 rather than 0."""
    assert json.loads(_last("ENTRYPOINT")) == ["vantage"]
    assert isinstance(json.loads(_last("CMD")), list)


def test_the_command_serves_the_volume_on_every_interface_at_the_exposed_port() -> None:
    """The database is the environment's, with no `--database` to override an
    operator's `-e VANTAGE_DATABASE=postgresql://...`, and in the volume,
    which is also the working directory, where SQLite and Python put their
    temporary files under a read-only root; every interface, since the port
    is published from outside the container; and the port the image
    exposes."""
    served = _served()
    assert "--database" not in json.loads(_last("CMD"))
    assert isinstance(served.database, SqliteTarget)
    data = str(served.database.path.parent)
    assert data in json.loads(_last("VOLUME"))
    assert _last("WORKDIR") == data
    assert served.host == "0.0.0.0"  # noqa: S104
    assert _last("EXPOSE") == str(served.port)


def test_the_health_check_asks_the_served_port_a_route_that_needs_no_token(
    tmp_path: Path,
) -> None:
    """A database of the server's own always has a user, and then every route
    but a few needs a token, which a health check has none of: asking one of
    those, it would never see the server healthy."""
    _, _, probe = _last("HEALTHCHECK").partition(" CMD ")
    command = json.loads(probe)
    assert command[:2] == ["python", "-c"]
    (url,) = re.findall(r"http://[^'\"]+", command[2])
    address = urllib.parse.urlsplit(url)
    assert (address.hostname, address.port) == ("127.0.0.1", _served().port)

    store = SqliteExecutionStore(tmp_path / "vantage.db")
    try:
        store.create_user("admin", admin=True, created_at=datetime.now(timezone.utc))
        client = TestClient(create_app(store))
        assert client.get("/api/v1/projects").status_code == 401
        assert client.get(address.path).status_code == 200
    finally:
        store.close()


def test_the_image_answers_every_name_until_its_operator_names_some() -> None:
    """Bound to every interface inside the container, the server cannot
    know the names it is reached by, so it checks none; given some in
    VANTAGE_ALLOWED_HOSTS, it answers those alone, and the health check
    still, by the IP literal it asks."""
    _, _, probe = _last("HEALTHCHECK").partition(" CMD ")
    (url,) = re.findall(r"http://[^'\"]+", json.loads(probe)[2])
    health = urllib.parse.urlsplit(url).netloc

    assert _served().hosts is None
    rule = _served("vantage.example.com")
    assert rule.hosts is not None
    assert rule.hosts.answers("vantage.example.com")
    assert not rule.hosts.answers("rebound.example.net")
    assert rule.hosts.answers(health)


def test_the_build_context_is_what_the_build_copies() -> None:
    """The checkout holds a .venv, caches and databases, and a credentials
    archive once sat in its root (see .gitignore): everything is left out of
    the context but what the build stage copies."""
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert patterns[0] == "*"
    admitted = [pattern[1:].rstrip("/") for pattern in patterns if pattern.startswith("!")]
    copied = [
        source
        for keyword, arguments in _instructions()
        if keyword == "COPY" and not arguments.startswith("--from=")
        for source in arguments.split()[:-1]
    ]
    assert all(any(fnmatch.fnmatch(path, pattern) for path in copied) for pattern in admitted)
    assert all(any(fnmatch.fnmatch(path, pattern) for pattern in admitted) for path in copied)


# --- The web client ----------------------------------------------------------

WEB = WORKSPACE_ROOT / "web"
CLIENT = "packages/vantage/src/vantage/service/client"


def _stage_named(name: str) -> list[Instruction]:
    (stage,) = [stage for stage in _stages() if stage[0][1].split()[-2:] == ["AS", name]]
    return stage


def test_the_client_is_built_from_its_lock_by_a_node_ci_builds_with() -> None:
    """The client's stage runs the Node the CI jobs set up, and the pnpm
    `web/package.json` names, and installs exactly the lock before the
    sources arrive, so a source change reuses the installed dependencies
    and a lock that no longer matches fails the build."""
    stage = _stage_named("web")
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    (node,) = {
        str(step["with"]["node-version"])
        for name in ("web", "e2e")
        for step in jobs[name]["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    }
    assert _image(stage) == f"node:{node}-trixie-slim"
    manager = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["packageManager"]
    assert manager.startswith("pnpm@")
    commands = _commands(stage)
    assert ["npm", "install", "--global", manager] in commands

    steps = [(keyword, arguments) for keyword, arguments in stage if keyword in ("COPY", "RUN")]
    lock = steps.index(("COPY", "web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./"))
    install = steps.index(("RUN", "pnpm install --frozen-lockfile"))
    sources = steps.index(("COPY", "web ./"))
    build = steps.index(("RUN", "pnpm run build"))
    assert lock < install < sources < build


def test_the_package_is_built_with_the_client_and_the_image_holds_no_node() -> None:
    """The build stage puts the client where Vite writes it in a checkout,
    inside the package, before `uv sync` builds the wheel hatch's
    `artifacts` carry it in; the image itself copies only the environment."""
    stage = _stage_named("web")
    (workdir,) = [arguments for keyword, arguments in stage if keyword == "WORKDIR"]
    config = (WEB / "vite.config.ts").read_text(encoding="utf-8")
    (out_dir,) = re.findall(r"outDir: '([^']+)'", config)
    built = str(Path(workdir, out_dir).resolve())

    (build,) = [stage for stage in _stages() if ["uv"] in (c[:1] for c in _commands(stage))]
    copies = [
        arguments.split()
        for keyword, arguments in build
        if keyword == "COPY" and arguments.startswith("--from=web ")
    ]
    assert copies == [["--from=web", built, CLIENT]]
    order = [
        "client" if keyword == "COPY" and arguments.startswith("--from=web ") else "sync"
        for keyword, arguments in build
        if (keyword == "COPY" and arguments.startswith("--from=web "))
        or (keyword == "RUN" and "sync" in arguments)
    ]
    assert order == ["client", "sync"]

    final = _stages()[-1]
    sources = re.findall(r"--from=(\S+)", " ".join(a for k, a in final if k == "COPY"))
    assert sources == ["build"]


def test_a_local_build_never_reaches_the_image() -> None:
    """A checkout's own node_modules, test output and client build are left
    out of the context, so the image's client is always the one it builds."""
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for excluded in ("web/node_modules", "web/test-results", "web/playwright-report", CLIENT):
        assert excluded in patterns
        assert patterns.index(excluded) > patterns.index("!web")
