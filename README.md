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
install from a checkout; the server's container image is built from one
too. The server's web client shows a project's runs and each run; the rest
of the recorded history, a test's across runs included, is read through
the server's JSON API. The database has no migrations, so a
server upgrade that changes the schema refuses the old database and needs a
new one.

## How the pieces fit

```
pytest + pytest-vantage  ──HTTP /api/v1──>  vantage server  ──>  SQLite file
        │                                         │              or PostgreSQL
        │ local modes     any HTTP client  <──────┤  read API (JSON)
        │                 a browser        <──────┘  web client
        └──> local SQLite file  <──  vantage, started on the test machine
```

- **`pytest-vantage`** is the pytest plugin. It collects the session in
  memory and reports it to a server over HTTP. It depends on pytest and the
  standard library only, and its own code never opens a database.
- **`vantage`** is the server, and the local storage. Its base package
  brings the plugin and the code that validates a report and stores it, so a
  test machine can keep its runs in a SQLite file of its own, with no server
  (see [Where runs go](#where-runs-go)). The `server` extra adds the HTTP
  server, which stores reports in one SQLite or PostgreSQL database and
  serves the recorded history, a local file's included, through its JSON
  API and a web client for browsers.

Recording to a server needs one the test machine can reach. The local modes
need none, and the backup modes keep what a server could not take and send
it once the server is back.

## Install

Python 3.10 to 3.13.

| Install | Brings | Enough for |
| --- | --- | --- |
| `pytest-vantage` | the plugin, and nothing but pytest | recording to a server |
| `vantage` | the plugin, local storage (with Pydantic and PyYAML), `vantage push`, and `vantage user`, `vantage token` and `vantage project` | every mode, the local ones included |
| `vantage[server]` | also FastAPI and Uvicorn: the `vantage` server | serving recorded runs, from a local file or a shared database |
| `vantage[server,postgres]` | also psycopg and its connection pool | a server storing in PostgreSQL |
| The `Dockerfile`'s image | `vantage[server,postgres]` as `uv.lock` pins it, on Python 3.13 | the server in a container (see [In a container](#in-a-container)) |

Neither distribution is published yet, so install from a checkout. Name the
plugin's directory alongside `vantage`'s: `vantage` depends on
`pytest-vantage`, which pip would otherwise look for on an index.

```bash
git clone https://github.com/guillegil/vantage.git

# Where your tests run, recording to a server:
pip install ./vantage/packages/pytest-vantage
# ...or recording locally as well:
pip install ./vantage/packages/pytest-vantage ./vantage/packages/vantage

# Where the server runs:
pip install ./vantage/packages/pytest-vantage './vantage/packages/vantage[server]'
# ...or, to store in PostgreSQL:
pip install ./vantage/packages/pytest-vantage './vantage/packages/vantage[server,postgres]'
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
pytest --vantage --vantage-mode local           # keep it in a local database, no server
pytest --vantage --vantage-mode server+backup   # and locally what the server cannot take
pytest --vantage --vantage-project firmware     # a run of the firmware project
pytest                                          # records nothing
```

At the start of a recorded session pytest prints
`vantage: recording run <id> in project <name> to <address>` (or to the
local database, or to both; see [Where runs go](#where-runs-go)). Recording works under
pytest-xdist; a session is one run however many workers it uses.

A database the server made has a user from its first start, so recording
to it needs a token from the first run (see
[Users and tokens](#users-and-tokens)). On one machine, once `vantage` is
running:

```bash
export VANTAGE_TOKEN=$(vantage token create admin --scope record --label laptop)
pytest --vantage
```

Or, while the default database does not exist yet, record with
`--vantage-mode local` first: the database that session creates is the
local store's, and `vantage` then serves it without a token (see
[Browsing the local database](#browsing-the-local-database)).

### Options

| Command line | ini option | Environment | Default | Purpose |
| --- | --- | --- | --- | --- |
| `--vantage` | none | none | off | Turns recording on. |
| `--vantage-server URL` | `vantage_server` | `VANTAGE_SERVER` | `http://127.0.0.1:8765` | Where to report. |
| `--vantage-timeout SECONDS` | `vantage_timeout` | none | `10` | Upper bound on each report request, start to finish. |
| `--vantage-mode MODE` | `vantage_mode` | none | `server` | Where the run goes: `server`, `local`, `server+backup` or `server+local`. |
| `--vantage-local-database PATH` | `vantage_local_database` | none | the database `vantage` serves by default | The SQLite file the other three modes store in. |
| `--vantage-project NAME` | `vantage_project` | `VANTAGE_PROJECT` | `default` | The project the run belongs to (see [Projects](#projects)). |
| `--vantage-failure-text` | none | none | off | Adds failure text and captured output. |
| `--vantage-metadata` | none | none | off | Adds values from declared files. |
| none | none | `VANTAGE_TOKEN` | none | The token a server requires once its database has a user (see [Users and tokens](#users-and-tokens)). |

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
- The project is resolved as `--vantage-project`, then `VANTAGE_PROJECT`,
  then the `vantage_project` ini value, then `default`. A committed ini
  value names a repository's project once for everyone. A name no project
  can have (see [Projects](#projects)) stops pytest with a usage error; the
  server must have the project, or it refuses the run.
- The token is read from `VANTAGE_TOKEN` alone, and only when `--vantage` is
  given: a value in a committed configuration file would be read by
  everyone who checks the project out, and one on the command line by every
  user of the machine. It goes with every report and heartbeat, never
  anywhere but the server address, and is never printed. An empty one
  counts as unset; one that could never be a token (spaces, characters
  outside printable ASCII, over 512 characters) stops pytest with a usage
  error that does not repeat it.

### What is ignored

The three switches (`--vantage`, `--vantage-failure-text`,
`--vantage-metadata`) count only when typed on the command line. Given
through `addopts`, `PYTEST_ADDOPTS` or an `@file` of arguments, they are
ignored, with one warning naming them. A committed configuration file
therefore cannot start recording, or start capturing failure text or reading
files, for everyone who checks the project out. Arguments after a bare `--`
never count.

`--vantage-server`, `--vantage-timeout`, `--vantage-mode`,
`--vantage-local-database` and `--vantage-project` are honoured wherever
they come from: they say where and how, never whether.

### What is not recorded

These invocations never run a test and are not recorded, even with
`--vantage`: `--collect-only` (`--co`), `--setup-only`, `--setup-plan`,
`--fixtures`, `--fixtures-per-test`, `--cache-show`, `--markers` and
`--help`. A session whose selection turns out empty (`-k` or `-m` matching
nothing) is still recorded, as a finished run with exit status 5 and no
results.

## Where runs go

`--vantage-mode` (or `vantage_mode` in the ini file) says where a recorded
run goes:

| Mode | The server | The local database |
| --- | --- | --- |
| `server` (the default) | every run | never |
| `local` | none: nothing touches the network | every run |
| `server+backup` | every run | the runs the server could not take, which are also queued and sent later |
| `server+local` | every run | every run; those the server could not take are also queued and sent later |

The three modes with a local database need `vantage` installed where the
tests run (see [Install](#install)); without it they stop pytest with a
usage error:
`vantage: --vantage-mode local stores runs locally and needs the vantage package: pip install vantage`.

**The local database** is a SQLite file, by default the one `vantage`
serves when started with no options: `$XDG_DATA_HOME/vantage/vantage.db`, or
`~/.local/share/vantage/vantage.db` (an empty or relative `XDG_DATA_HOME`
is ignored, and `VANTAGE_DATABASE` is not read). So on that machine
`vantage` shows what the tests stored; see
[Browsing the local database](#browsing-the-local-database).

- `--vantage-local-database PATH`, or `vantage_local_database` in the ini
  file, names another. A relative path is taken from the directory pytest
  was started in when typed (or given with `-o`), and from the ini file's
  directory when written there; `~` is expanded. There is no environment
  variable for it.
- It must be a file path: a `postgresql://` URL, or any other URL, is a
  usage error, since local storage is SQLite only, and so is a directory.
- A new directory is created 0700 and a new database 0600, as `vantage`
  creates them. A database made by a build with another schema version is
  left untouched, and the run is not stored there.
- The run is stored once, when the session ends, from the same reports a
  server would get, so it reads back exactly as it would from a server. A
  session that is killed stores nothing locally.

**`local` mode** makes no reachability check, capability probe, start report
or heartbeat, and never reads the server address. The header reads
`vantage: recording run <id> in project <name> to <database>`. The local
database has no admin but its owner, so a project the run names is made
there if it is missing. A run it cannot store (the path
cannot be written, the disk is full, the database is from another schema
version) produces one warning ending `the run is lost`.

**`server+local`** reports to the server exactly as `server` does and also
stores every run locally; the header reads
`vantage: recording run <id> in project <name> to <address> and <database>`. A local database
that cannot take the run costs one warning and never the server's copy.

### When the server cannot take the run

In `server+backup` and `server+local`, a run the server could not take is
stored locally and queued, and one warning says so:

```
vantage: http://ci-vantage:8765 is unreachable; this run was stored in /home/u/.local/share/vantage/vantage.db and queued (3 runs waiting to be sent)
```

- **The server is unreachable at the start:** the session is recorded all
  the same, without a start report or heartbeats, and the run is stored and
  queued when it ends.
- **The final report gets no answer** (the connection is refused or broken,
  or it times out)**, a 5xx, or a 408 or 429** (a busy server or proxy
  asking for it again later): the run is stored locally and the reports
  the server has not acknowledged are queued. A report that timed out may
  have been stored after all; sending it again stores nothing twice.
- **The server refuses the token, or its absence** (a 401 or 403): the run
  is stored and queued in the same way, since `vantage push` with a token
  the server accepts can deliver it. The queue never holds a token; whoever
  sends it sends their own.
- **The server has no project of the run's name** (`404 unknown_project`):
  the run is stored and queued too, and the warning names the command an
  admin adds it with. Once the project exists, the queue delivers the run
  into it.
- **The token's user may not record in the run's project**
  (`403 not_a_member` or `403 insufficient_role`; see
  [Members and roles](#members-and-roles)): the run is stored and queued
  too, and the warning names the command that makes the user an editor of
  the project. Once they are one, the queue delivers the run.
- **The final report is refused outright** (any other 4xx, a redirect, an
  answer that does not acknowledge the run): the warning is the usual
  `vantage: error while reporting: ...`, and the run is not queued, since
  the server would refuse it again. It is still stored locally.

**Sending the queue.** After a session whose own run reached a server, the
plugin sends the runs queued for that server, oldest first, within the
report timeout, and prints one line with the test summary:
`vantage: sent 3 queued runs to http://ci-vantage:8765 (0 waiting)`. It stops
when the server is unreachable again, answers a 408 or 429, refuses the
token with a 401 or 403 (but for the two below), or the time is spent, and says why. A run the server answers with a 5xx stays queued and
the next is sent; one it refuses with any other 4xx can never succeed, and
is dropped with a warning naming its run id. Each run is only ever sent to
the address it was queued for, compared exactly as written, so
`http://ci-vantage:8765` and `http://ci-vantage:8765/` are two different
servers to the queue. A run of a project the server does not have stays
queued, and the rest of that project's runs are not tried again until the
next send; the line says so with `; kept runs of projects it does not
have: ...`. So does a run of a project the token's user may not record in
(`403 not_a_member` or `insufficient_role`), with `; kept runs of projects
the token's user may not record in: ...`; the send goes on, since the same
token may record in other projects. Both come before any `; stopped: ...`.
`vantage push` sends the queue on demand.

Under pytest-xdist only the controller stores or queues a run.

### The outbox

The queue lives in the outbox, a SQLite file of the plugin's own next to the
local database, named after it: `vantage.db-outbox` beside `vantage.db`. It
is not a vantage database. It holds each queued run's reports exactly as
they would have been sent, failure text and metadata included, so it is
created 0600, in a directory created 0700 if missing.

- It holds at most 1,000 runs and 256 MiB of reports. Queueing past either
  drops the oldest runs, with one warning naming them.
- Several sessions, and `vantage push`, may send from it at once. Each run
  is claimed before it is sent, so two senders do not both send it; a run
  sent twice would still be stored once.
- A run whose reports no longer read back from the file, because the file
  was damaged, is dropped with a warning naming it rather than left to hold
  up the runs behind it.
- Deleting the file drops every queued run; the local database keeps its
  copies.

### `vantage push`

```
vantage push [--database PATH] [--to URL] [--timeout SECONDS]
```

Sends every queued run now, each to the server it was queued for, or only
those queued for `--to`, written exactly as it was configured. It needs
`vantage` but not its `server` extra.

- Each run goes with the token in `VANTAGE_TOKEN`, if one is set, as the
  plugin sends it; one that could never be a token is refused in one line
  that does not repeat it. A run recorded by another user's token is
  refused by the server and dropped, once the token's user may record in
  the run's project; until then it is kept, as below.
- A run of a project the server does not have stays queued, and the line
  names the project: `, kept runs of projects it does not have (firmware)`.
  So does a run of a project the token's user may not record in:
  `, kept runs of projects the token's user may not record in (firmware)`.

- `--database` is the local database the outbox sits beside, as given to
  `--vantage-local-database`; by default the same default database
  (`VANTAGE_DATABASE` is not read). A PostgreSQL URL is refused.
- `--timeout` bounds each report, 10 seconds by default. There is no bound
  on the whole: every run is tried.
- It prints one line per server, for example
  `vantage: sent 2 queued runs to http://ci-vantage:8765, dropped 1 it refused (<run id>) (0 waiting)`,
  with `then stopped: <why>` when the server could not be reached. It exits
  0 when nothing is left waiting for the servers it sent to, and 1
  otherwise. Ctrl-C stops it in one line, with exit status 130; the run it
  was sending stays queued.
- With nothing queued it says `vantage: nothing queued`, and creates
  nothing. Beside a `pytest-vantage` too old to have an outbox, it refuses
  in one line naming the upgrade.

### Browsing the local database

With the `server` extra installed on the test machine, `vantage` started
with no options serves the default local database, on
`http://127.0.0.1:8765`, through the same read API as any server:

```bash
vantage                                  # the default local database
vantage --database ./runs/vantage.db     # one named with --vantage-local-database
```

Sessions can keep storing into the file while it serves, and
`http://127.0.0.1:8765/` shows its runs in a browser (see
[Browsing runs](#browsing-runs)).

A database a local-mode session made has no user, so `vantage` serves it
to anyone who reaches the port, with no token and no login; there is no
password to log in with, and `POST /api/v1/login` answers
`409 open_server`. It stays open until `vantage user add` makes its first
user. A default database that `vantage` made itself, before any session
stored there, is a server's and has the user `admin`; see
[Running the server](#running-the-server).

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
- **Each module, class or directory pytest could not collect** (an import
  error, a syntax error, a `conftest.py` that raises): one `error` result
  under its node id, such as `tests/test_broken.py`, with no setup, call or
  teardown. pytest counts it as an error, and so does the run, whether
  `--continue-on-collection-errors` let the other tests run or pytest
  stopped before running any.
- **The repository:** the commit hash, branch, commit subject, whether
  tracked files have uncommitted changes, and the absolute path of the
  repository root. The server stores the root path but no API response
  returns it. Outside a git repository, or when git cannot be read, these
  are null.
- **Metadata your tests report:** every value set through the
  `vantage_metadata` fixture, and the keys and display names declared in the
  `keys` section of `vantage-metadata.json` (see
  [Reporting metadata from your tests](#reporting-metadata-from-your-tests)).
  The files that declaration lists are opened only with `--vantage-metadata`.

### `--vantage-failure-text`

Adds, for each result: the exception type, message and `repr`, the full
traceback, the failing file (relative to pytest's rootdir when inside it) and
line, the skip reason and the xfail reason, **and the captured stdout and
stderr of every result, passing ones included**, from setup, call and
teardown. Under `-s` nothing is captured and the output fields are null. A
module that could not be collected gets the text pytest prints for it as its
message.

**This text is stored unredacted.** Anything a test printed, logged to a
captured stream, asserted or raised with, credentials and tokens included,
is uploaded and stored as it is.

Each field is cut to 64 KiB. The messages, tracebacks and captured output of
a whole session are capped at 512 KiB, spent on failed and errored results
first; a field that does not fit is dropped and marked as truncated.

### `--vantage-metadata`

Opens the files that `vantage-metadata.json`, in pytest's rootdir, lists
under `files`, and records the values of the keys it declares for each, such
as the service version or deployment region a run was made against.

```json
{
  "version": 1,
  "files": [
    {"path": "settings.json", "format": "json", "keys": ["service_version", "database_engine"]},
    {"path": "deploy/values.yaml", "format": "yaml", "keys": ["region"]}
  ]
}
```

**The declaration** is read in every recorded session, for the keys it
declares, but only `--vantage-metadata` opens the files it lists. Without
`--vantage` nothing is read at all.

- `version` is the integer `1`.
- `files` is optional: a list of at most 16 entries, each with a `path`, a
  `format` (`"json"` or `"yaml"`; it is never guessed from the extension) and
  a list of `keys`.
- `keys` is optional too: the keys your tests report themselves, each with a
  display name (see [Declaring keys](#declaring-keys)).
- A `path` is relative to the rootdir and uses forward slashes. It may not
  be absolute or contain a `..` component, a backslash, a drive letter or a
  NUL character, may not exceed 1,024 characters, and may appear only once.
- A key may appear only once across `files`, and only once in `keys`; a key
  in both is a file's key that `keys` gives a display name. No key may
  exceed 1,024 characters, and at most 200 keys are declared in total.
- The declaration itself must be a JSON object in UTF-8, at most 1 MiB, and
  its paths, keys and display names together must fit the 32 KiB metadata
  budget.

A declaration that breaks any of these rules produces one warning ending
`the declaration is ignored`: none of its keys counts as declared and none of
its files is read, and the session is still recorded. A missing declaration
is warned about only under `--vantage-metadata`.

**The declared files:**

- Each path must resolve, symlinks followed, to a regular file strictly
  inside the rootdir. A file that fails this is never opened: it is recorded
  as `path_rejected` (a symlink leading out of the rootdir, a directory), or
  as `not_found` when nothing exists there.
- A file larger than 8 KiB is recorded as `too_large`, one that is not UTF-8
  as `not_text`, and one that cannot be read as `unreadable`; none is sent.
  What a report carries of the declaration, file contents included, is
  capped at 32 KiB and filled in declaration order; a file whose contents no
  longer fit is `over_budget`.
- Only top-level keys are read. A key whose value is a scalar is stored as
  text, as the file spells it (`2.10` stays `2.10`), up to 1,024 bytes. A key
  that is missing, holds a list or mapping, or whose value is too long is
  recorded with that status and no value.
- YAML is parsed without constructing any object, so tags cannot run code.

**What leaves the machine is the whole text of every declared file that
passes these checks**, not only the declared keys; the server keeps only the
declared values and each file's status. Declare only files you are content to
upload. Every declared path is recorded with the run, with its status.

A key read from a file reads back like one the session reported, and
filters the run list the same way; see
[Reading it back](#reading-it-back).

## Reporting metadata from your tests

Some of what a run was made against is known only once the session has
looked: the firmware a board is running, the revision of a card plugged
into it. The `vantage_metadata` fixture takes such values from your own
fixtures and records them with the run. It needs `--vantage` and nothing
else. The fixture comes with the plugin, so a project that asks for it
needs `pytest-vantage` installed wherever its tests run, and its tests
error under `-p no:vantage`.

```python
import pytest


@pytest.fixture(scope="session", autouse=True)
def dut(vantage_metadata):
    fpga = connect_fpga()
    vantage_metadata.update({
        "fpga": {"firmware": fpga.read_fw_version(), "hardware": "v0.5.3"},
        "fmc": {"hardware": "5.2.0", "pic": {"firmware": "2.0.0"}},
    })
    vantage_metadata["bench"] = "lab-3"
    yield fpga
```

`vantage_metadata` is a session-scoped mapping of flat, dotted keys to text.
Setting a dict, with `[]=` or `update`, flattens it under its key, so the
example records `fpga.firmware`, `fpga.hardware`, `fmc.hardware`,
`fmc.pic.firmware` and `bench`. Setting a key again replaces its value, and
`vantage_metadata["fpga.firmware"]` reads it back.

**Values become text as they are set:**

| You set | Recorded as |
| --- | --- |
| a `str` | itself |
| `True`, `False` | `"true"`, `"false"` |
| an `int` or `float` | what `str()` gives: `1.10` becomes `"1.1"`, so pass a string when the spelling matters |
| a `list` or `tuple` | compact JSON: `["a", 2, None]` becomes `["a",2,null]` |
| `None` | no value, with status `absent` |
| anything else | what `str()` gives, so a version object records as it prints |

**Setting never raises.** A key that is not a string, is empty, is longer
than 1,024 characters or contains a control character, or a value that
cannot be turned into text, is skipped with one warning naming it. Without
`--vantage` the fixture takes everything silently and sends nothing, and so
does it in a session that is not recorded (the server was unreachable,
say), so the same fixtures run either way.

**When it is sent.** The values go to the server in the session's last
report only, once every test has run. A session that is killed loses them,
and so can a value set in a fixture's teardown when the session stops early
(`-x`, `--maxfail`, Ctrl-C): set values in the fixture's setup, before its
`yield`.
A value longer than 1,024 bytes of UTF-8 is recorded as `value_too_large`,
without the value.

**Under pytest-xdist**, session fixtures run in every worker. Each worker
hands its values to the controller as it finishes, and the controller sends
them all in the run's one last report. A key two workers report alike is
recorded once. A key they report with different values keeps the first
value the controller received, with one warning. Ctrl-C stops the
controller before its workers have handed anything over, so a session
interrupted that way under pytest-xdist is recorded without any of the
values its fixtures reported.

### Declaring keys

`vantage-metadata.json`, the declaration whose files `--vantage-metadata`
reads, can also list the keys your tests report, with a name to show each
under:

```json
{
  "version": 1,
  "keys": {
    "fpga.firmware":    {"name": "FPGA firmware version"},
    "fpga.hardware":    {"name": "FPGA hardware version"},
    "fmc.hardware":     {"name": "FMC hardware version"},
    "fmc.pic.firmware": {"name": "FMC PIC firmware version"}
  }
}
```

- Each entry is an object with an optional `name` of at most 256
  characters. Any other field, a key that is empty or longer than 1,024
  characters, or a key listed twice refuses the whole declaration, with one
  warning.
- The declared keys are the ones in `keys` and every key a declared file
  lists. The declaration is read under `--vantage` alone; its files are
  still opened only with `--vantage-metadata`.
- Declaring is optional. With no declared keys, every key the session
  reports is recorded as it comes, marked undeclared, without a warning.

Once keys are declared:

- **A key the declaration does not list is still recorded**, marked
  undeclared, and named at the end of the session in one warning, with the
  closest declared key when there is one:
  `vantage: undeclared metadata keys: 'fpga.firmwre' (did you mean 'fpga.firmware'?), 'bench'`.
- **A key in `keys` that nothing gave a value** is recorded with status
  `absent`, so "not reported" reads differently from "never declared".
- **A key read from a declared file** under `--vantage-metadata` keeps the
  file's value when the session reports it too, with one warning.

**Bounds.** A run records at most 200 keys: the keys of the files read
first, then the keys the session reported, in the order first set, then the
declared keys recorded `absent`. All the keys and values together may
take up to 512 KiB of the last report, counted as sent, where each
character outside ASCII takes six bytes. Keys past either bound are left
out, with one warning for each bound.

### Reading it back

`GET /api/v1/runs/{run_id}/metadata` returns every key of one run, sorted by
key, whether it was read from a file or reported by the session, and every
file the declaration named, sorted by path:

```json
{"items": [
  {"key": "bench", "name": null, "value": "lab-3", "status": "captured",
   "source": "session", "source_file": null, "declared": false},
  {"key": "fmc.hardware", "name": "FMC hardware version", "value": "5.2.0",
   "status": "captured", "source": "session", "source_file": null, "declared": true},
  {"key": "toolchain", "name": null, "value": null, "status": "source_unavailable",
   "source": "file", "source_file": "build/manifest.json", "declared": true}
],
 "files": [
  {"source_file": "build/manifest.json", "content_type": "json", "status": "not_found"}
]}
```

- `source` is `file` or `session`; `source_file` is the declared path of a
  `file` key and null otherwise.
- `value` is null unless `status` is `captured`. The other statuses are
  `absent`, `value_too_large`, and, for a file's key only, `not_scalar` and
  `source_unavailable`.
- A file's `status` is `captured` when it was read. Otherwise it says why
  its keys are `source_unavailable`: `not_found`, `path_rejected` (outside
  the project), `too_large`, `not_text`, `unreadable`, `over_budget` or
  `malformed`.
- A run that recorded no metadata has no items and no files; a run id never
  recorded answers `404`. The lists are not paged.

A project's run list filters by up to 16 keys at once. Repeat `metadata_key` and
`metadata_value` once per key; each value pairs with the key in the same
position, and a run must hold every pair to be listed:

```
GET /api/v1/projects/default/runs?metadata_key=fpga.firmware&metadata_value=1.1.0&metadata_key=fmc.hardware&metadata_value=5.2.0
```

A pair matches a `captured` value spelt exactly the same, from a file or
the session. Giving the two parameters a different number of times, or more
than 16 pairs, answers `422`. A run recorded before any run held a key
cannot match it, so the answer's `metadata_horizon` says, for each filtered
key in the order given, how many of the project's runs predate it:

```json
"metadata_horizon": [{"key": "fpga.firmware", "predating": 12},
                     {"key": "fmc.hardware", "predating": 30}]
```

Without a metadata filter, `metadata_horizon` is null.

## When something goes wrong

**Invalid configuration** (an address that is not `http` or `https`, no
host, a bad port, a timeout that is not a positive number, an unknown mode,
a local database that is a PostgreSQL URL or a directory, an ini value of
the wrong type, a `VANTAGE_TOKEN` that could never be a token) stops pytest with a usage error naming the option, exit
status 4, before any test runs: recording was asked for and cannot work. So
does a mode with a local database where `vantage` is not installed.

Everything else produces at most one warning per kind of problem. The tests
still run and the suite's exit status is never changed.

- **Server unreachable at the start** (nothing listening, host does not
  resolve): in `server` mode the session runs unrecorded; `server+backup`
  and `server+local` record it and keep it locally (see
  [When the server cannot take the run](#when-the-server-cannot-take-the-run)).
- **Server does not offer session tracking** (an older server, or the check
  got no usable answer): no start report or heartbeats are sent, so the run
  appears only when the session ends.
- **The start report or a heartbeat fails:** heartbeats stop; the final
  report is still sent.
- **The server refuses the token:** the warning says what to fix instead of
  the bare status: `the server requires a token: set VANTAGE_TOKEN to one
  with the record scope` (none sent to a server with users), `the server
  does not accept the token in VANTAGE_TOKEN` (unknown, revoked or expired,
  its user disabled, or sent to a server without users), `the token in
  VANTAGE_TOKEN does not grant the record scope` (a login token never
  does), or `the run was recorded by another user`. The start report and
  the final report each warn once.
- **The server does not let the token's user record in the run's project**
  (`403 not_a_member` or `insufficient_role`): the warning names the
  project and the command that makes the user an editor of it (see
  [Members and roles](#members-and-roles)). The start report and the final
  report each warn once. A heartbeat refused that way, the user having lost
  the role while the session ran, stops heartbeats with `the user of the
  token in VANTAGE_TOKEN may no longer record in this run's project`.
- **The final report fails** (HTTP error, rejected report): if the start
  report got through, the run stays unfinished on the server and later reads
  as `abandoned`; otherwise nothing is recorded on the server. The backup
  modes keep the run locally, and queue it unless the server would refuse
  it again.
- **The final report times out:** the plugin stops waiting at the deadline,
  but the server may still finish storing the report, so the run may be
  recorded as finished after all. The warning cannot tell which.
- **Something cannot be read** (git, the metadata declaration, a test report
  of an unexpected shape): that part of the report is left out.
- **The local database or the outbox cannot take the run:** one warning
  saying where the run is, if anywhere; the server's copy is unaffected.

Warnings are `VantageWarning`, a `UserWarning` subclass. Whenever one is
raised, it appears in pytest's warnings summary, counted with the others, and
your warning filters apply to it as to any other: one that ignores
`VantageWarning` hides it.

- **At the start of the session** (server unreachable, switches ignored,
  recording could not start, session tracking unavailable, the start report
  failed, git or the metadata declaration unreadable) and **at its end**
  (the final report failed, where a run the server could not take was kept,
  a queued run dropped, results left out, metadata keys undeclared, left out
  or reported twice): listed on its own, under no test.
- **While tests run** (a heartbeat failed, a test report of an unexpected
  shape): listed under whichever test was running at the time; under
  pytest-xdist, whose controller runs no test itself, under no test.
- **When a metadata key or value is skipped:** under the test whose fixture
  set it and pointing at the line that set it, and under pytest-xdist once
  for each worker that set it.

If your warning filters turn warnings into errors, the message is written to
the terminal instead. With pytest's warnings plugin disabled
(`-p no:warnings`) there is no summary, and it is printed to stderr.

While a session runs, the plugin sends the server a heartbeat every 30
seconds from a background thread, whatever the tests are doing, so a test,
fixture or collection that takes longer than the grace period keeps its run
`running`. So does a test that hangs: a heartbeat says the process is alive,
not that its tests are moving, and ending a hung test is a job for a
timeout.

A run whose final report never arrives (the process was killed, the machine
lost power) reads as `abandoned` once the server has heard nothing from it
for the grace period, provided the server received its start report. One
whose final report was queued reads the same until the queue is sent, and
then as finished.

## Browsing runs

`vantage` serves a web client at every address outside `/api`: start it and
open `http://127.0.0.1:8765/` in a browser. It opens on the runs of the
project you had open last, or of `default`.

- **A project's runs**, newest first, fifty at a time: each run's status and
  exit status, its commit, who recorded it, when, how long it took, its
  counts in pytest's words (`2 failed · 208 passed · 1 error`) and a line of
  marks, one per result in the order pytest reported them (or one for a few,
  the most severe, in a long run), where a failure stands up out of the
  line. The latest finished run's closing line, as
  pytest printed it, heads the list. A project with no runs yet shows the
  exact `pytest` command that records one into it on this server.
- **A run**: its status (and why, when it was interrupted or abandoned), its
  commit and subject, who recorded it, when it started and finished, pytest's
  closing line, the line of every result, what did not pass (failed, error,
  xpassed) with the first line of each failure, every result in order, and
  its metadata. Failure text shows only for runs recorded with
  `--vantage-failure-text`. Under pytest-xdist the workers' results
  interleave in the line, as they were reported. Each result opens its own
  page.
- **A result**: the test's node id and outcome, with the run's status,
  commit and who recorded it; everything recorded about why it did not pass,
  each verbatim in a block of its own -- the traceback (marked with the phase
  and the line it failed at), the exception's message and `repr`, the skip or
  xfail reason, and the captured stdout and stderr -- saying where the plugin
  cut a field to 64 KiB, or dropped it when the session's failure text passed
  512 KiB, and that this text may hold anything a test printed or asserted,
  credentials included; how long setup, call and teardown took and which of
  them failed; the run's metadata; and the test's latest 24 runs in the
  run's project, oldest first, as a line of marks with its pass rate or
  failing streak and a line of durations, each run opening its result there.
  Where a result holds none of this, the page says why: a failure without
  failure text was recorded without `--vantage-failure-text`; any other
  result may also have run with output capture off (`-s`).
  A character that reorders or hides text, such as a right-to-left override,
  a zero-width space, or the U+FFFD the server stores for U+0000, shows as
  its code point (`U+202E`) in node ids and failure text, so neither reads as
  something else.
- **A test's history** in a project, newest first, fifty at a time: each
  run's outcome, commit, start and duration, each opening that run's
  result, under the same line of marks and durations for the runs listed.
- **A server with no users**, as one serving a local store's database, needs
  no sign-in, and says that anyone who can reach it can read and record.
- **A server with users** asks you to sign in with a user's name and
  password; the first start printed `admin`'s (see
  [Running the server](#running-the-server)). A session lasts 12 hours.
  Signing out, and any change of that user's password, ends it; a page open
  when it ends stays as it is and offers to sign in again. A session reads,
  and never records runs.

### Signing in needs HTTPS or this machine

Browsers keep the session only over HTTPS, or on this machine's own
address, `http://127.0.0.1` or `http://localhost`. Opened over plain HTTP
from another machine, the sign-in page says so and sends no password. To
browse a server from elsewhere, put it behind a TLS reverse proxy that sets
HSTS, or reach it through an SSH tunnel and open `http://127.0.0.1:8765`:

```bash
ssh -L 8765:127.0.0.1:8765 vantage-host
```

Cookies do not separate ports: every service on the same host name
receives vantage's session cookie, so give vantage a host name of its own
(see [Signing in from a browser](#signing-in-from-a-browser)).

## Running the server

```
vantage [--database PATH-OR-URL] [--host HOST] [--port PORT] [--grace-period SECONDS]
```

Serving needs the `server` extra. Without it, `vantage` refuses in one line,
`vantage: serving needs the server extra: pip install 'vantage[server]'`,
with exit status 1 and nothing created; `vantage push`, `vantage project`,
`vantage user`, `vantage token` and `vantage --help` work either way.

| Setting | Flag | Environment | Default |
| --- | --- | --- | --- |
| Database: a SQLite file, or a PostgreSQL URL | `--database` | `VANTAGE_DATABASE` | `$XDG_DATA_HOME/vantage/vantage.db`, or `~/.local/share/vantage/vantage.db` |
| Bind address | `--host` | none | `127.0.0.1` |
| Port | `--port` | none | `8765` |
| Grace period before an unfinished run reads as abandoned | `--grace-period` | none | `900` seconds |

- A flag beats the environment variable, which beats the default. An empty
  `--database`, `VANTAGE_DATABASE` or `XDG_DATA_HOME` counts as unset, and a
  relative `XDG_DATA_HOME` is ignored. An empty `--host` is refused rather
  than taken to mean every interface.
- The default database is also where the plugin's local modes store by
  default, so on a test machine `vantage` with no options serves what the
  tests stored.
- A missing SQLite database directory is created with mode 0700 and a new
  database file with mode 0600. An existing database file open to its group
  or to others is used as it is, with a warning.
- **A database of the server's own starts with an admin.** Before it serves
  anything, a start that finds a database with no user gives it the
  enabled admin user `admin`, with a random password, and prints that
  password once, on one line of stderr:

  ```
  vantage: created the user admin; change its password at once (vantage user password admin, or POST /api/v1/password). Shown this once: 7kPq...
  ```

  **Change it at once.** Whatever keeps the server's stderr (journald,
  `docker logs`) keeps that line; a new password makes it useless. Since
  the database has a user, the server requires a token from the start,
  and recording needs one from the first run (see
  [Users and tokens](#users-and-tokens)). No later start prints anything,
  and a database that already has a user gets no `admin`. This holds for
  every database but one pytest-vantage's local store made: one `vantage`
  created, one `vantage user add` or `vantage project add` created, and
  every PostgreSQL database.
- **A database pytest-vantage's local store made, until it has a user, has
  no authentication.** Anyone who can reach the port can record runs, add
  projects, change the section definitions and read everything recorded,
  failure text included. Only the users, tokens, members, login, sign-in
  and password routes refuse, with `409 open_server`, so its first user is
  always made with `vantage user add`. Any `--host` other than `127.0.0.1` logs a warning
  saying so at startup, as long as the database has no user; a database of
  the server's own has one by then, and never warns.
- **Whichever creates the default database first decides what it is.** On
  a test machine `vantage` and the local modes share the default database
  (see [Where runs go](#where-runs-go)). Started first, `vantage` creates
  a database of its own, with `admin`: sessions still store into it, but
  browsing it needs a token. Stored into first, the database is the local
  store's, and `vantage` serves it open. To start over, stop the server
  and move the file aside.
- The server refuses to start, with one `vantage: ...` line on stderr and
  exit status 1, when a setting is unusable (an empty host, a port outside 1
  to 65535, a grace period that is not positive or exceeds 365 days), the
  database cannot be created, opened or connected to, or the database was
  made by a build with a different schema. There are no migrations: move the
  old file aside and start again.
- A `--port` or `--grace-period` that is not a number never gets that far:
  the argument parser refuses it with its usage message and exit status 2.

The server's API is under `/api/v1`. It serves its own OpenAPI document at
`GET /api/v1/openapi.yaml`, and the ingestion contract the plugin uses is
described in [`docs/api/v1-ingestion.md`](docs/api/v1-ingestion.md). The read
side:

| Route | Returns |
| --- | --- |
| `GET /api/v1/projects` | The projects you have a role in, `default` included, each with that `role` |
| `POST /api/v1/projects` | Adds one: `{"name": "firmware"}` |
| `GET /api/v1/projects/{project}/runs` | The project's runs, newest first, each `running`, `finished`, `interrupted` or `abandoned` |
| `GET /api/v1/runs/{run_id}` | One run, and the project it belongs to |
| `GET /api/v1/runs/{run_id}/metadata` | One run's metadata, from declared files and from the session, and each declared file's status |
| `GET /api/v1/runs/{run_id}/results` | One run's results in the order reported, with a short failure summary; `?outcome=failed&outcome=error` keeps only those |
| `GET /api/v1/runs/{run_id}/changes` | The tests that changed against the run it was compared with, new failures first, then still failing, fixed, new and missing tests; `?change=new_failure` keeps only those |
| `GET /api/v1/runs/{run_id}/outcomes` | Every outcome of one run in the order reported, one of pytest's characters each (`.` `F` `E` `s` `x` `X`), unpaged, and beside it each result's change (`-` unchanged, `n` new failure, `s` still failing, `f` fixed, `t` new test) |
| `GET /api/v1/runs/{run_id}/result?node_id=...` | One result in full, failure text included, with its place in the run and its change |
| `GET /api/v1/projects/{project}/tests/history?node_id=...` | One test across the project's runs, newest first, each result with its change in its own run |
| `GET`, `POST`, `DELETE /api/v1/projects/{project}/config/sections` | The project's named file-path prefixes that group results |
| `GET /api/v1/runs/{run_id}/sections` | One run's pass rate per section of its project |

The run list, a run's results and changes, and a test's history are
paged: they take `limit` (at most 200 per page) and `offset`, and say in
`has_more` whether more items exist. The section lists and a run's
metadata are returned whole.

An offset counts from the newest run, so a run recorded while you page
through the run list or a test's history pushes one you have already seen
onto the next page. To walk either list without that, pass each page's
`next_cursor` back as `cursor`: the next page starts just past the last run
the previous one listed, whatever was recorded since. `next_cursor` is null
on the last page, and a cursor is not combined with a non-zero `offset`.

```
GET /api/v1/projects/default/runs?limit=50
GET /api/v1/projects/default/runs?limit=50&cursor=MjAyNi0wOS0yN1QwODowMDowMC4...
```

The run list also filters by metadata values; see
[Reading it back](#reading-it-back). Each run, in the list and on its own,
says in `recorded_by` which user's token recorded it, or `null` when none
did, and in `counts` how many of its results passed, failed, errored,
were skipped, xfailed and xpassed, every one present, zeros included.
The plugin reports results as the session finishes, so a running run
counts none, or only some, until then; a finished run's counts are final.

Every path outside `/api` belongs to the web client. When the client was
built into the package, `http://127.0.0.1:8765/`, and any other address
outside `/api`, answers its page; otherwise each answers `404` with a page
saying the client is missing and how to build it. The API is served either
way. Every answer, the API's included, tells browsers not to cache it
(`Cache-Control: no-store`), not to frame it and not to let another site
read it, unless it sets its own caching, as the client's files do.

### Projects

Every run belongs to a project: the one its session names with
`--vantage-project`, `VANTAGE_PROJECT` or `vantage_project`, or `default`,
which every database has. Each project has its own run list, its own
catalogue of tests, so a test's history never mixes two projects however
alike their node ids, and its own section definitions. A run never moves
to another project.

```bash
vantage project add firmware                   # an admin adds it, before runs name it
vantage project list
```

- A project name is 1 to 64 characters of `a-z`, `0-9`, `.`, `_` and `-`,
  starting with a letter or a digit. Nothing about a project changes once it
  is made: it is never renamed or deleted.
- A server never makes a project from a report: one naming a project the
  server does not have is refused with `404 unknown_project`, and a backup
  mode keeps the run until the project is added (see
  [When the server cannot take the run](#when-the-server-cannot-take-the-run)).
  The local store is the exception: it makes the project a run names, since
  a database on a test machine has nobody else to.
- `vantage project add` finds the database as the server does, like
  `vantage user`, and creates a SQLite database that is not there yet.
  `POST /api/v1/projects` does the same over HTTP with an admin's token;
  only on a database pytest-vantage's local store made, while it has no
  user, may anyone. A new project has no members, its maker included.
- The run list, a test's history, the sections and the members live under
  `/api/v1/projects/{project}/`; a project nobody has answers `404`. Every
  route that names a run id finds the run whatever its project, and then
  needs the same role in that project as the project's own routes.

#### Members and roles

Once a database has users, each user acts in a project with a role:

| Role | May |
| --- | --- |
| `viewer` | read the project: its runs, results, history, section definitions and members |
| `editor` | also record runs into it and change its section definitions |
| `owner` | also manage its members |

- **`default` is everyone's.** Every user is an editor of `default`
  without being made one, so a server with one team needs no members at
  all. `default` has no members, and none can be set or removed there.
- **Every other project starts with no members**; an admin adds them.
- **An admin may do everything in every project**, as its owner, whether
  or not they are a member of it.
- A database with no user checks no role (see
  [Running the server](#running-the-server)).

```bash
vantage project member set firmware alice owner   # adds alice, or changes her role
vantage project member set firmware bob viewer
vantage project member list firmware
vantage project member remove firmware bob
```

- Each command finds the database as `vantage user` does, and none creates
  one. None asks for a token: whoever can open the database acts with full
  authority, which is how a project whose owners are all gone gets one
  back.
- `member set` says `added bob to firmware as viewer`, or
  `bob is now viewer in firmware`, and points out a disabled user, whose
  role grants nothing until `vantage user update bob --enable`, and an
  admin, whom a role changes nothing for. `member list` prints a `USER`
  and `ROLE` table.
- A project or user that does not exist, removing a user who is not a
  member, and setting or removing a member of `default` are each refused
  in one line; `member list default` says every user is an editor of it.

Over HTTP the same goes through `/api/v1/projects/{project}/members` (see
[Over HTTP](#over-http)):

- `GET .../members` lists the members, by user, to anyone who may read the
  project, since every run's `recorded_by` already shows user names.
  `everyone` is the role every user holds there without being a member:
  `editor` in `default`, whose list is empty, and null elsewhere.

  ```json
  {"items": [{"user": "alice", "role": "owner"}, {"user": "bob", "role": "viewer"}],
   "everyone": null}
  ```

- `PUT .../members/{user}` with `{"role": "editor"}` adds the user
  (`201`) or sets their role (`200`), and `DELETE .../members/{user}`
  removes them (`204`). Both need an owner of the project, or an admin,
  with a token holding `manage`. A user nobody has is `404 unknown_user`,
  one who is not a member `404 unknown_member`, and `default`
  `409 default_project`.
- An owner may make other members owners, and may demote or remove any
  member, themselves included: an admin can always put a project right. A
  disabled user or an admin may be made a member.
- On a database with no user they answer `409 open_server`: nobody is a
  member of anything there.

**Scopes and roles narrow each other.** A request needs the scope its
route declares (see [Users and tokens](#users-and-tokens)) and, within a
project, a role there: an owner's token holding only `read` only reads,
and a token holding `record` records only where its user is at least an
editor. A user with no role in the project is refused with
`403 not_a_member`, one whose role is too low with `403 insufficient_role`;
`GET /api/v1/projects` lists only the projects the user has a role in, each
with that `role`. A change of role applies from the next request, on every
server sharing the database.

**Recording from CI.** Give CI a user of its own, an editor of the
projects it records, rather than an admin's token:

```bash
vantage user add ci-firmware
vantage project member set firmware ci-firmware editor
vantage token create ci-firmware --label ci    # read and record: all pytest-vantage needs
```

That token records in `firmware` and in `default`, and nowhere else, and
changes no section and no member.

**When the plugin's user may not record.** A session whose token's user
is not a member of the run's project, or only a viewer of it, is refused,
and warns with one of these:

```
http://ci-vantage:8765 does not let the user of the token in VANTAGE_TOKEN record in project firmware, which they are not a member of; an owner of it or an admin adds them with: vantage project member set firmware USER editor
http://ci-vantage:8765 lets the user of the token in VANTAGE_TOKEN only read project firmware; recording needs the editor role, which an owner of it or an admin gives with: vantage project member set firmware USER editor
```

The backup modes keep the run until the user is an editor there (see
[When the server cannot take the run](#when-the-server-cannot-take-the-run)).

Two consequences follow:

- Any user may change `default`'s section definitions, with a token
  holding `manage`, such as their login token, since every user is an
  editor there.
- A database pytest-vantage's local store made, once `vantage user add`
  gives it users, has no members in the projects its sessions made: make
  its first user an admin, who adds the others.

### Users and tokens

A database the server made has a user from its first start: `admin`,
unless one was made before (see [Running the server](#running-the-server)).
A database pytest-vantage's local store made has no user until
`vantage user add` makes one, and until then needs no token: that is how
`vantage` serves the local database on a test machine. Once a database has
a user, every route but
`GET /api/v1/capabilities`, `GET /api/v1/openapi.yaml`,
`POST /api/v1/login` and `POST /api/v1/password` needs
`Authorization: Bearer <token>`, a token of an enabled user that has not
been revoked or expired. Users are disabled, never deleted, so a database
that has had one stays closed. A server already serving the database needs
no restart: its next request needs a token.

```bash
vantage user password admin                    # first of all, on a server's own database
vantage user add alice --admin                 # the first user closes a local store's database
vantage token create alice                     # prints a token, once
vantage user password alice                    # asks for a password, to log in with
vantage user add ci
vantage token create ci --scope record --label nightly
vantage user list
vantage token list [USER]
vantage token revoke 3                         # by the id the list shows
vantage user update bob --disable              # or --enable, --admin, --no-admin
```

- Each command takes `--database` and finds the database as the server
  does: `--database`, then `VANTAGE_DATABASE`, then the default. It needs
  neither the `server` extra nor a running server. Only `user add` and
  `project add` create a database that is not there yet.
- A token holds one or more scopes. `read` reads projects, runs, results,
  history, sections and members; `record` sends reports and heartbeats, all
  `pytest-vantage` needs; `manage` changes a project's section definitions
  where its user is an editor, and its members where they are an owner;
  `admin` adds projects and manages users and tokens, and only an admin
  user's token may hold it. It grants nothing once
  its user stops being one. Within a project, what a scope allows is
  bounded by the user's role there (see
  [Members and roles](#members-and-roles)). A token holds `read` and
  `record` unless `--scope` names others.
- `token create` prints the token alone on stdout, so
  `VANTAGE_TOKEN=$(vantage token create ci --scope record)` captures it and
  nothing else. It is shown once: the database keeps only its SHA-256.
- A user name is 1 to 64 characters of `a-z`, `0-9`, `.`, `_` and `-`.
- A disabled user's tokens, a revoked token and an expired one are refused
  like a token that never existed. So is any token sent to a server without
  users.
- The report that creates a run records its token's user, and only that
  user's token may send the run's later reports and heartbeats. Another
  user's gets `409 foreign_run`, whether the run is finished or not.
- A token, like a password, travels in the clear over plain `http`. Put the
  server behind HTTPS wherever the network between it and its clients is
  not yours.

#### Passwords and logging in

A user may have a password, to log in with over HTTP and get a token for a
while, as a person at a keyboard does. `vantage user add` makes a user
without one, which is what an account for CI wants: it needs a token, never
a password.

- `vantage user password NAME` sets one, asking for it twice on the
  terminal. `--password-stdin` reads it from stdin instead, less one
  trailing newline, for a script:
  `vantage user password alice --password-stdin < password.txt`. No option
  or environment variable ever takes a password, since the process list
  and shell history would show it, and the command never prints it.
  Without a terminal and without `--password-stdin` it refuses to ask.
- A password is 15 to 256 characters, counted once Unicode-normalised
  (NFKC), with no control characters. Spaces and every other character
  count, and nothing is trimmed. The rule applies when a password is set,
  never when one is checked. The database keeps only its scrypt hash.
  `vantage user list` says in `PASSWORD` who has one.
- `POST /api/v1/login` trades a name and a password for a **login token**.
  It holds `read` and `manage`, and `admin` for an admin, but never
  `record`: recording needs a token made for it, so a login token that
  leaks cannot record runs. It expires 12 hours after the login, with no
  refresh: log in again. It is for people, not for CI, whose token comes from
  `vantage token create` or `POST /api/v1/tokens` and never expires.
- Setting a user's password, whichever way, revokes every login token of
  theirs; tokens made otherwise are left alone. `vantage token list` shows
  login tokens, labelled `login`, with their expiry under `EXPIRES`, and
  any of them can be revoked by id. An expired login token is deleted at
  its user's next login, so the lists stay short.
- **A lost admin password** is set again with `vantage user password admin`
  on the server's database (`--database` as the server was started),
  whether or not the server is running: whoever can open the database is
  trusted with it.
- **To keep a password out of the log**, make your own admin before the
  server's first start: `vantage user add alice --admin` and
  `vantage user password alice` on the database the server will use. A
  database with a user gets no `admin`, and nothing is printed.
- **Guessing is bounded by cost.** Each check of a password costs the
  server about 0.2 seconds of work and 32 MiB, and it checks at most two
  at a time, so one server process takes about ten guesses a second (and
  several processes sharing a PostgreSQL database that many times more).
  At most 32 more requests wait for a check; any beyond them are answered
  `503 password_checks_busy` at once, with `Retry-After: 1`, so a flood of
  logins slows the real ones rather than queueing minutes of work ahead of
  them. There is no lockout, which would let anyone lock `admin` out. Put a
  server reachable from networks you do not trust behind a proxy that
  limits the rate of `POST /api/v1/login` and `POST /api/v1/password`, and
  behind HTTPS.

#### Over HTTP

Admins can manage users and tokens through the API as the commands do, and
owners a project's members, from anywhere that reaches the server. The
users and tokens routes need an admin's token holding the `admin` scope:
an admin's login token holds it, as does one made with
`vantage token create NAME --scope admin`; the default made token does
not. The members routes need a token holding `read` to list and `manage`
to change, as every login token does, and a role in the project (see
[Members and roles](#members-and-roles)).

| Route | Does |
| --- | --- |
| `POST /api/v1/login` | Answers a login token for a name and its password: `{"name": "alice", "password": "..."}` |
| `POST /api/v1/password` | Changes one's own password, given the current one: `{"name": "alice", "password": "...", "new_password": "..."}` |
| `POST`, `GET`, `DELETE /api/v1/session` | Signs a browser in, says who is asking, and signs it out (see [Signing in from a browser](#signing-in-from-a-browser)) |
| `GET /api/v1/users` | Every user, disabled ones included, by name |
| `POST /api/v1/users` | Adds an enabled user, without a password: `{"name": "bob", "admin": false}` |
| `PATCH /api/v1/users/{name}` | Sets `admin`, `disabled` or both: `{"disabled": true}` |
| `PUT /api/v1/users/{name}/password` | Sets a user's password: `{"password": "..."}` |
| `GET /api/v1/tokens[?user=NAME]` | Every token, or one user's, revoked ones included, oldest first |
| `POST /api/v1/tokens` | Makes a token: `{"user": "ci", "scopes": ["record"], "label": "nightly"}` |
| `POST /api/v1/tokens/{id}/revoke` | Revokes a token |
| `GET /api/v1/projects/{project}/members` | The project's members, by user |
| `PUT /api/v1/projects/{project}/members/{user}` | Adds a member or sets their role: `{"role": "editor"}` |
| `DELETE /api/v1/projects/{project}/members/{user}` | Removes a member |

Logging in as an admin, then making a user for CI, an editor of
`firmware`, and a token for it, with the login token:

```bash
# Asks for alice's password, which never reaches a command line.
ADMIN_TOKEN=$(python3 -c 'import getpass, json; print(json.dumps({"name": "alice", "password": getpass.getpass()}))' \
  | curl -sf -X POST http://vantage.example:8765/api/v1/login \
      -H 'Content-Type: application/json' --data-binary @- \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')

curl -s -X POST http://vantage.example:8765/api/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "ci", "admin": false}'
curl -s -X PUT http://vantage.example:8765/api/v1/projects/firmware/members/ci \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"role": "editor"}'
curl -s -X POST http://vantage.example:8765/api/v1/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"user": "ci", "scopes": ["record"], "label": "nightly"}'
```

- `POST /login` and `POST /password` take no token: the name and the
  password are the credentials, and an `Authorization` header is ignored.
  Every failure of them -- an unknown name, a user without a password or a
  disabled one, a wrong password -- answers the same
  `401 invalid_credentials`, after the same work. A new password that
  breaks the rule is `422 invalid_password`, before the current one is
  checked.
  `POST /password` answers `204` and revokes every login token of the
  user, who logs in again. It asks for the current password rather than a
  token, so a login token that leaks cannot take the account over, and the
  password the first start printed is changed in one request.
- `PUT /users/{name}/password` asks for no current password, even for the
  admin's own account: an admin's token already makes admin tokens. It
  answers `204`, or `404 unknown_user`, and revokes the user's login
  tokens. It enables and promotes nobody: a disabled user given a password
  still cannot log in.
- A database pytest-vantage's local store made, while it has no user,
  answers every one of these routes `409 open_server`, even though it
  serves everything else to anyone: nobody there has a password or is a
  member of anything, and a first user made over HTTP would belong to
  whoever asked first. A token
  sent to the ones that take one is refused with `401`, as everywhere.
- A new token is in the `201` answer of `POST /tokens` or `POST /login`
  alone (or in the cookie `POST /session` sets), marked
  `Cache-Control: no-store`, and never again: lists show its
  id, user, scopes, label and times (`expires_at` null but for a login
  token), never the token.
- An admin cannot demote or disable their own user over HTTP
  (`409 own_account`); another admin can, or `vantage user update` on the
  database. If no admin is left, `vantage user update NAME --admin --enable`
  and `vantage token create NAME --scope admin` bring one back.
- Revoking is idempotent: a token revoked already answers `200` with the
  time it was first revoked. (`vantage token revoke` says it was revoked
  already.) An admin may revoke the token they are using; their next
  request is refused. A login token deleted since it expired is unknown:
  `404 unknown_token`.
- An admin can make other admins, and tokens for any user. A token made for
  another user records runs as that user.
- The lists come whole, not paged. The commands' checks apply with the same
  wording, and a rejection never repeats a name, a label, a password or a
  token.

#### Signing in from a browser

A browser signs in with a user's name and password through
`POST /api/v1/session`, which checks them as `POST /api/v1/login` does and
keeps the same 12-hour login token in a cookie, `__Host-vantage_session`,
that the page's script can never read. The answer names the user and when
the session ends, never the token. `GET /api/v1/session` says who is
asking, whether by the cookie or a token in `Authorization`, and
`DELETE /api/v1/session` signs out, revoking the cookie's token. Signing
out, the 12 hours passing, any password set for the user and disabling the
user each end the session; a session never records runs, since a login
token never holds `record`.

- **A token in `Authorization` decides alone.** A request that sends one
  is judged by it, whatever cookie comes along, so the plugin, CI and
  scripts work as before. A server with no user ignores the cookie.
- **The cookie counts only on requests from vantage's own pages.** The
  browser marks where a request comes from (`Sec-Fetch-Site`), and a
  request carrying only the cookie is refused, `403 cross_site_request`,
  unless it is marked as coming from vantage itself; a `GET` typed into
  the address bar counts too. The same goes for signing in and out.
- **Signing in needs HTTPS or this machine.** Browsers keep the cookie only
  over HTTPS, and on `http://127.0.0.1` and `http://localhost`. Opened over
  plain HTTP from another machine, a sign-in holds nowhere. To browse a
  server from elsewhere, either put it behind a TLS reverse proxy that sets
  HSTS, or reach it through an SSH tunnel and open
  `http://127.0.0.1:8765`:

  ```bash
  ssh -L 8765:127.0.0.1:8765 vantage-host
  ```

- **Cookies do not separate ports.** Every service on the same host name,
  whatever its port, receives vantage's session cookie, and a page any of
  them serves is same-site to vantage. Vantage refuses what such a page
  asks with the cookie, but the cookie still reaches those services: give
  vantage a host name of its own.

### Storing in PostgreSQL

A `--database` or `VANTAGE_DATABASE` whose scheme is `postgresql://` or
`postgres://` names a PostgreSQL database instead of a file:

```bash
vantage --database postgresql://vantage@db.example:5432/vantage
VANTAGE_DATABASE=postgresql://vantage@db.example/vantage vantage
```

- Any other URL (`mysql://`, `sqlite:///`, `postgresql+psycopg://`) is
  refused with one line, never taken as a file path.
- The driver comes with the `postgres` extra (see [Install](#install)).
  Without it, a PostgreSQL URL is refused with one line naming the extra; a
  server storing in SQLite never needs it.
- The password goes wherever libpq looks for one: in the URL
  (`postgresql://user:password@host/db`, with special characters
  percent-encoded), or in `PGPASSWORD`, `~/.pgpass` or libpq's other `PG*`
  environment variables, which apply to the rest of the connection too. A
  password in `--database` is shown to every user of the machine in the
  process list; `VANTAGE_DATABASE`, `PGPASSWORD` or `~/.pgpass` keep it out.
  The server prints the URL only with its passwords replaced by `***`.
- Everything the server stores lives in a schema named `vantage`, so its
  tables never collide with anything else in the database. The first server
  to start on a database creates the schema and all its tables in one
  transaction; the user it connects as needs the right to do so then, and to
  read and write them afterwards. It then creates the user `admin`, as on
  any database of its own; of several servers starting on one new database
  at once, one does, and prints the password. The database must be encoded
  in UTF-8, PostgreSQL's usual default; any other is refused with one line.
- Several servers can share one database, each with its own pool of at most
  10 connections. Each write is one transaction that locks what a
  concurrent write to the same run or setting would change, so every run,
  result and metadata value is stored exactly once, and a bound such as the
  number of sections holds across all the servers.
- A server rides out a database restart or failover: while the database is
  gone a request waits up to 30 seconds for it and then fails with a 500,
  and the server reconnects by itself, serving again as soon as the
  database is back.
- There are no migrations here either. A `vantage` schema made by a build
  with a different schema version, or one holding tables but no version, is
  refused with one line and left as it is.

### In a container

The repository's `Dockerfile` builds an image of the server:
`vantage[server,postgres]` exactly as `uv.lock` pins it, on Python 3.13.
Nothing is published, so build it from a checkout, with Docker 25 or
later:

```bash
docker build -t vantage .
docker run -d --name vantage -p 8765:8765 -v vantage-data:/data vantage
docker logs vantage 2>&1 | grep 'Shown this once'    # the first start's admin password
docker exec -it vantage vantage user password admin  # change it at once
```

- The image runs `vantage --host 0.0.0.0` on port 8765, as the
  unprivileged user and group 10001, with
  `VANTAGE_DATABASE=/data/vantage.db`. `/data` is a volume: a new named
  volume takes its owner and mode 0700, and the database is created in it
  at 0600. Serving every interface of the container warns about nothing,
  since the database has its admin by then.
- The container's log keeps the admin's password for as long as the
  container exists. To have none logged, make your own admin before the
  first start; the server then makes no `admin`:

  ```bash
  docker run --rm -v vantage-data:/data vantage user add alice --admin
  docker run --rm -it -v vantage-data:/data vantage user password alice
  ```

- `vantage user`, `vantage token` and `vantage project` run with
  `docker exec`, as the server's user and on the database it serves, which
  they find in the same `VANTAGE_DATABASE`:

  ```bash
  docker exec vantage vantage user add ci
  docker exec vantage vantage project add firmware
  docker exec vantage vantage project member set firmware ci editor
  VANTAGE_TOKEN="$(docker exec vantage vantage token create ci --label firmware-ci)"
  ```

  Leave out `-t` when capturing a token: a terminal ends it with a carriage
  return and mixes in what the command says on stderr. Never add `-u root`:
  a database file root creates is one the server cannot open. With the
  server stopped, the same commands run in a container of their own, as
  `docker run --rm -v vantage-data:/data vantage user list`.
- Arguments after the image name replace `--host 0.0.0.0`, so repeat it:
  `docker run ... vantage --host 0.0.0.0 --grace-period 1800`. Without it
  the server listens only inside the container, where no published port
  reaches it, and still reports itself healthy. Publish another port with
  `-p 9000:8765` rather than `--port`, which the health check does not
  follow.
- A bind mount must be writable by uid 10001: `sudo install -d -o 10001 -g
  10001 -m 0700 /srv/vantage`, then `-v /srv/vantage:/data`. Or run the
  container as the directory's owner, `--user "$(id -u):$(id -g)"`, and
  `docker exec` runs as that user too. A directory the server cannot write
  is refused in one line: `vantage: /data exists but is not writable by
  this process; cannot create or open /data/vantage.db.`
- To store in PostgreSQL, set `VANTAGE_DATABASE` to its URL and the
  password in `PGPASSWORD`, from a file rather than the command line:
  `docker run -d --name vantage -p 8765:8765 --env-file vantage.env
  vantage`, with `VANTAGE_DATABASE=postgresql://vantage@db.example:5432/vantage`
  and `PGPASSWORD=...` in `vantage.env`, mode 0600. `/data` goes unused;
  `docker rm -v` removes the volume Docker made for it. With Compose, and
  `VANTAGE_DB_PASSWORD=...` in a `.env` file beside it, mode 0600:

  ```yaml
  services:
    db:
      image: postgres:17
      environment:
        POSTGRES_USER: vantage
        POSTGRES_DB: vantage
        POSTGRES_PASSWORD: ${VANTAGE_DB_PASSWORD:?set it in .env}
      volumes:
        - db-data:/var/lib/postgresql/data
      healthcheck:
        test: ["CMD", "pg_isready", "-U", "vantage", "-d", "vantage"]
        interval: 2s
        retries: 30
      restart: unless-stopped
    vantage:
      image: vantage  # built with docker build -t vantage .
      environment:
        VANTAGE_DATABASE: postgresql://vantage@db:5432/vantage
        PGPASSWORD: ${VANTAGE_DB_PASSWORD:?set it in .env}
      ports:
        - "8765:8765"
      depends_on:
        db:
          condition: service_healthy
      restart: unless-stopped
  volumes:
    db-data:
  ```

  `docker compose logs vantage` shows the admin's password, and `docker
  compose exec -T vantage vantage token create admin` makes a token.
- `docker stop` sends SIGTERM: the server finishes the requests in
  flight, closes the store and exits 0, well within Docker's 10-second
  grace period. A stop sent in the first second or so of a start, before
  the server is serving, goes unseen, and Docker kills it once the grace
  period is over; nothing is lost. With `docker run --init` a clean stop
  exits 143 instead.
- The health check asks `GET /api/v1/capabilities`, which needs no token,
  every second while the server starts and every minute after, one line of
  the access log each time. It shows the server answers, not that its
  database does.
- The image carries the web client (see [Browsing runs](#browsing-runs)).
  Browsing it from another machine needs a TLS reverse proxy in front of the
  container or an SSH tunnel to its host, since a browser signs in only over
  HTTPS or on its own machine's address.
- It runs on a read-only root filesystem as well, `--read-only --cap-drop
  ALL --security-opt no-new-privileges`, putting its temporary files in
  `/data`.
- To upgrade, build the new image, then `docker stop vantage`, `docker rm
  vantage` and the same `docker run`. There are no migrations: an image built for another schema
  version refuses the database in one line and exits 1, again at every
  restart a restart policy makes, and leaves it as it was. Run the previous
  image again, or move the file aside and start over. To back up a SQLite
  database, `docker stop vantage`, then `docker cp
  vantage:/data/vantage.db ./vantage-backup.db` copies all of it, at mode
  0600.

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
docker build -t vantage .                          # the server image (builds the client too)

# The PostgreSQL tests too, against a server you can create databases on:
VANTAGE_TEST_POSTGRES_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres \
  uv run --extra dev pytest
```

Without `VANTAGE_TEST_POSTGRES_URL` the tests that need PostgreSQL are
skipped. With it, each creates a database of its own on that server and
drops it afterwards, so the URL must name a user allowed to do both.

`pre-commit install` (with pre-commit installed separately, for example
`uv tool install pre-commit`) runs ruff on each commit, and mypy and the
tests not marked `slow` on each push. CI runs the whole suite on Python 3.10
to 3.13, with and without pytest-xdist, and once more against PostgreSQL 17,
and builds the server image and runs it as [In a container](#in-a-container)
does.

The web client lives in `web/`, a pnpm project, and needs Node 22.22 or
later and pnpm 12:

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web run build        # into vantage/service/client; restart vantage to serve it
pnpm --dir web run dev          # on http://localhost:5173, asking a vantage on 127.0.0.1:8765
pnpm --dir web run typecheck && pnpm --dir web run lint && pnpm --dir web run test
pnpm --dir web exec playwright install chromium && pnpm --dir web run e2e
pnpm --dir web run api:types    # after changing the API document
```

Building a wheel needs no Node: it carries whatever client was built into
the package, and CI builds the wheels it checks after building the client.
A checkout where the client was never built serves the API as usual, and
every other address answers a page saying the client is missing and how to
build it. CI checks, builds and tests the client in Chromium against real
servers, and checks that the wheel and the image serve it.

How the code is organised, and why: [`docs/architecture.md`](docs/architecture.md).

## Licence

MIT. The web client ships the Chivo and Chivo Mono fonts, which are under
the SIL Open Font License 1.1; their licence is served beside them, at
`/fonts/OFL.txt`.
