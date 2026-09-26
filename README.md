# Vantage

[![CI](https://github.com/guillegil/vantage/actions/workflows/ci.yml/badge.svg)](https://github.com/guillegil/vantage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10-3.13](https://img.shields.io/badge/python-3.10--3.13-blue.svg)](https://www.python.org/downloads/)

A pytest run leaves nothing behind but terminal scrollback. Vantage records
what a test suite did, run after run, and on which commit, so that *when did
this start failing, and what changed* is a question you look up instead of
bisecting for.

## Status

Early and unreleased (0.1.0). Neither distribution is published yet, so
install from a checkout. There is no web interface yet: recorded history is
read through the server's JSON API. The database has no migrations, so a
server upgrade that changes the schema refuses the old database and needs a
new one.

## How the pieces fit

```
pytest + pytest-vantage  ──HTTP /api/v1──>  vantage server  ──>  SQLite file
                                                  │
                          any HTTP client  <──────┘  read API (JSON)
```

- **`pytest-vantage`** is the pytest plugin. It collects the session in
  memory and reports it to the server over HTTP. It depends on pytest and the
  standard library only, and never opens a database.
- **`vantage`** is the server. It validates each report and performs every
  write, to one SQLite database, and serves the recorded history.

Recording therefore needs a running server that the test machine can reach,
in CI as much as on a laptop.

## Install

Python 3.10 to 3.13. The two distributions are installed separately.

```bash
git clone https://github.com/guillegil/vantage.git

# In the environment that runs your tests (needs nothing but pytest):
pip install ./vantage/packages/pytest-vantage

# Wherever the server runs (brings FastAPI, Uvicorn and PyYAML):
pip install ./vantage/packages/vantage
```

The plugin needs pytest 8.0 or later. It registers itself through pytest's
plugin entry point; `-p no:vantage` switches it off completely.

## Recording a session

```bash
vantage                                         # start the server (see below)
pytest --vantage                                # record this session
pytest --vantage --vantage-server http://ci-vantage:8765
pytest --vantage --vantage-failure-text         # also record failure text
pytest --vantage --vantage-metadata             # also record declared configuration
pytest                                          # records nothing
```

At the start of a recorded session pytest prints
`vantage: recording run <id> to <address>`. Recording works under
pytest-xdist; a session is one run however many workers it uses.

### Options

| Command line | ini option | Environment | Default | Purpose |
| --- | --- | --- | --- | --- |
| `--vantage` | none | none | off | Turns recording on. |
| `--vantage-server URL` | `vantage_server` | `VANTAGE_SERVER` | `http://127.0.0.1:8765` | Where to report. |
| `--vantage-timeout SECONDS` | `vantage_timeout` | none | `10` | Upper bound on each report request, start to finish. |
| `--vantage-failure-text` | none | none | off | Adds failure text and captured output. |
| `--vantage-metadata` | none | none | off | Adds values from declared files. |

- The server address is resolved as `--vantage-server`, then
  `VANTAGE_SERVER`, then the `vantage_server` ini value, then the default. The
  default matches the server's own default bind, so `--vantage` alone reaches
  a server started with no options.
- The address is the server's base URL: `http` or `https`, a host, and
  optionally a port (1 to 65535) and a path prefix. The plugin appends
  `/api/v1/...` itself.
- The timeout is resolved as `--vantage-timeout`, then `vantage_timeout`,
  then 10 seconds. It must be above zero. The reachability check, the
  capability check, the start report and heartbeats wait at most
  `min(2, timeout)` seconds.
- The plugin connects directly. It ignores `http_proxy`/`https_proxy` and does
  not follow redirects: a 3xx answer counts as a failed report.
- `--vantage-failure-text` and `--vantage-metadata` do nothing without
  `--vantage`.

### What is ignored

The three switches (`--vantage`, `--vantage-failure-text`,
`--vantage-metadata`) count only when typed on the command line. Given
through `addopts`, `PYTEST_ADDOPTS` or an `@file` of arguments, they are
ignored, with one warning naming them. A committed configuration file
therefore cannot start recording, or start capturing failure text or reading
files, for everyone who checks the project out. Arguments after a bare `--`
never count.

`--vantage-server` and `--vantage-timeout` are honoured wherever they come
from: they say where and how, never whether.

### What is not recorded

A session that runs no test is not recorded, even with `--vantage`:
`--collect-only` (`--co`), `--setup-only`, `--setup-plan`, `--fixtures`,
`--fixtures-per-test`, `--cache-show`, `--markers` and `--help`.

## What each switch uploads

### `--vantage`

- **The run:** a random run id, start and finish times (UTC), the exit
  status, whether the session was interrupted, and the reason pytest gave for
  stopping early (for example the `pytest.exit()` message, up to 1,024
  characters).
- **Each test:** its node id and the parts of it (file, class, function,
  parameter id), the overall outcome (`passed`, `failed`, `error`,
  `skipped`, `xfailed` or `xpassed`), the outcome and duration of setup, call
  and teardown, start and finish times, and the xdist worker id.
- **The repository:** the commit hash, branch, commit subject, whether
  tracked files have uncommitted changes, and the absolute path of the
  repository root. The server stores the root path but no API response
  returns it. Outside a git repository, or when git cannot be read, these
  are null.

### `--vantage-failure-text`

Adds, for each result: the exception type, message and `repr`, the full
traceback, the failing file (relative to pytest's rootdir when inside it) and
line, the skip reason and the xfail reason, **and the captured stdout and
stderr of every result, passing ones included**, from setup, call and
teardown. Under `-s` nothing is captured and the output fields are null.

**This text is stored unredacted.** Anything a test printed, logged to a
captured stream, asserted or raised with, credentials and tokens included,
is uploaded and stored as it is.

Each field is cut to 64 KiB. The messages, tracebacks and captured output of
a whole session are capped at 512 KiB, spent on failed and errored results
first; a field that does not fit is dropped and marked as truncated.

### `--vantage-metadata`

Reads `vantage-metadata.json` in pytest's rootdir and the files it names, and
records the values of the keys it declares, such as the service version or
deployment region a run was made against.

```json
{
  "version": 1,
  "files": [
    {"path": "settings.json", "format": "json", "keys": ["service_version", "database_engine"]},
    {"path": "deploy/values.yaml", "format": "yaml", "keys": ["region"]}
  ]
}
```

**The declaration:**

- `version` is the integer `1`.
- `files` is a list of at most 16 entries, each with a `path`, a `format`
  (`"json"` or `"yaml"`; it is never guessed from the extension) and a list
  of `keys`.
- A `path` uses forward slashes. It may not contain a backslash, a drive
  letter or a NUL character, may not exceed 1,024 characters, and may
  appear only once.
- A key may appear only once in the whole declaration and may not exceed
  1,024 characters. At most 200 keys in total.
- The declaration itself must be a JSON object in UTF-8, at most 1 MiB, and
  its paths and keys together must fit the 32 KiB metadata budget.

A declaration that breaks any of these rules, or is missing, produces one
warning; the session is still recorded, without metadata.

**The declared files:**

- Each path must be relative, have no `..` component, and resolve, symlinks
  followed, to a regular file strictly inside the rootdir. A file that fails
  this is never opened.
- **An absolute path or one containing `..` is dropped without a warning.**
  The session is recorded with the rest of its metadata, but the server
  discards that file and its keys, so the run carries no trace of them.
  Every other path that fails the check (a symlink leading out of the
  rootdir, a directory) is recorded as `path_rejected`, or as `not_found`
  when nothing exists there.
- A file larger than 8 KiB is recorded as `too_large`, one that is not UTF-8
  as `not_text`, and one that cannot be read as `unreadable`; none is sent.
  The whole metadata section of a report, file contents included, is capped
  at 32 KiB and filled in declaration order; a file whose contents no longer
  fit is `over_budget`.
- Only top-level keys are read. A key whose value is a scalar is stored as
  text, as the file spells it (`2.10` stays `2.10`), up to 1,024 bytes. A key
  that is missing, holds a list or mapping, or whose value is too long is
  recorded with that status and no value.
- YAML is parsed without constructing any object, so tags cannot run code.

**What leaves the machine is the whole text of every declared file that
passes these checks**, not only the declared keys; the server keeps only the
declared values and each file's status. Declare only files you are content to
upload. Every declared path is recorded with the run, with its status, except
the absolute and `..` paths the server drops.

Runs can be filtered by a recorded value:
`GET /api/v1/runs?metadata_key=region&metadata_value=eu-west-1`.

## When something goes wrong

**Invalid configuration** (an address that is not `http` or `https`, no
host, a bad port, a timeout that is not a positive number, an ini value of
the wrong type) stops pytest with a usage error naming the option, exit
status 4, before any test runs: recording was asked for and cannot work.

Everything else produces at most one warning per kind of problem. The tests
still run and the suite's exit status is never changed.

- **Server unreachable at the start** (nothing listening, host does not
  resolve): the session runs unrecorded.
- **Server does not offer session tracking** (an older server, or the check
  got no usable answer): no start report or heartbeats are sent, so the run
  appears only when the session ends.
- **The start report or a heartbeat fails:** heartbeats stop; the final
  report is still sent.
- **The final report fails** (timeout, HTTP error, rejected report): if the
  start report got through, the run stays unfinished on the server and later
  reads as `abandoned`; otherwise nothing is recorded.
- **Something cannot be read** (git, the metadata declaration, a test report
  of an unexpected shape): that part of the report is left out.

Warnings are `VantageWarning`, a `UserWarning` subclass, and appear in
pytest's warnings summary. If your warning filters turn warnings into errors,
the message is written to the terminal instead.

A run whose final report never arrives (the process was killed, the machine
lost power) reads as `abandoned` once the server has heard nothing from it
for the grace period, provided the server received its start report.

## Running the server

```
vantage [--database PATH] [--host HOST] [--port PORT] [--grace-period SECONDS]
```

| Setting | Flag | Environment | Default |
| --- | --- | --- | --- |
| Database file | `--database` | `VANTAGE_DATABASE` | `$XDG_DATA_HOME/vantage/vantage.db`, or `~/.local/share/vantage/vantage.db` |
| Bind address | `--host` | none | `127.0.0.1` |
| Port | `--port` | none | `8765` |
| Grace period before an unfinished run reads as abandoned | `--grace-period` | none | `900` seconds |

- A flag beats the environment variable, which beats the default. An empty
  value counts as unset, and a relative `XDG_DATA_HOME` is ignored.
- A missing database directory is created with mode 0700 and a new database
  file with mode 0600. An existing database file open to its group or to
  others is used as it is, with a warning.
- **There is no authentication.** Anyone who can reach the port can record
  runs, change the section definitions and read everything recorded, failure
  text included. Any `--host` other than `127.0.0.1` logs a warning saying so
  at startup.
- The server refuses to start, with one `vantage: ...` line on stderr and
  exit status 1, when a setting is unusable, the database cannot be created
  or opened, or the database was made by a build with a different schema.
  There are no migrations: move the old file aside and start again.

The server's API is under `/api/v1`. It serves its own OpenAPI document at
`GET /api/v1/openapi.yaml`, and the ingestion contract the plugin uses is
described in [`docs/api/v1-ingestion.md`](docs/api/v1-ingestion.md). The read
side:

| Route | Returns |
| --- | --- |
| `GET /api/v1/runs` | Runs, newest first, each `running`, `finished`, `interrupted` or `abandoned` |
| `GET /api/v1/runs/{run_id}` | One run |
| `GET /api/v1/runs/{run_id}/results` | One run's results, with a short failure summary |
| `GET /api/v1/runs/{run_id}/result?node_id=...` | One result in full, failure text included |
| `GET /api/v1/tests/history?node_id=...` | One test across runs, newest first |
| `GET`, `POST`, `DELETE /api/v1/config/sections` | Named file-path prefixes that group results |
| `GET /api/v1/runs/{run_id}/sections` | One run's pass rate per section |

Lists take `limit` (at most 200 per page) and `offset`, and say whether more
items exist in `has_more`.

## Development

A uv workspace with both distributions. The tools are in the `dev` extra.

```bash
uv sync --extra dev
uv run --extra dev pytest                          # everything
uv run --extra dev pytest -m 'not slow'            # skip the timing measurements
uv run --extra dev pytest packages/pytest-vantage  # one distribution
uv run --extra dev ruff format . && uv run --extra dev ruff check --fix .
uv run --extra dev mypy .                          # strict
uv run --extra dev deptry .                        # undeclared or unused dependencies
uv run --extra dev pip-audit                       # known vulnerabilities
uv build --wheel --all-packages -o dist            # both wheels
```

`pre-commit install` (with pre-commit installed separately, for example
`uv tool install pre-commit`) runs ruff on each commit, and mypy and the
tests not marked `slow` on each push. CI runs the whole suite on Python 3.10
to 3.13, with and without pytest-xdist.

How the code is organised, and why: [`docs/architecture.md`](docs/architecture.md).

## Licence

MIT.
