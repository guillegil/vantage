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
        │                                         │              or PostgreSQL
        │ local modes     any HTTP client  <──────┘  read API (JSON)
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
  serves the recorded history, a local file's included.

Recording to a server needs one the test machine can reach. The local modes
need none, and the backup modes keep what a server could not take and send
it once the server is back.

## Install

Python 3.10 to 3.13.

| Install | Brings | Enough for |
| --- | --- | --- |
| `pytest-vantage` | the plugin, and nothing but pytest | recording to a server |
| `vantage` | the plugin, local storage (with Pydantic and PyYAML) and `vantage push` | every mode, the local ones included |
| `vantage[server]` | also FastAPI and Uvicorn: the `vantage` server | serving recorded runs, from a local file or a shared database |
| `vantage[server,postgres]` | also psycopg and its connection pool | a server storing in PostgreSQL |

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
pytest                                          # records nothing
```

At the start of a recorded session pytest prints
`vantage: recording run <id> to <address>` (or to the local database, or to
both; see [Where runs go](#where-runs-go)). Recording works under
pytest-xdist; a session is one run however many workers it uses.

### Options

| Command line | ini option | Environment | Default | Purpose |
| --- | --- | --- | --- | --- |
| `--vantage` | none | none | off | Turns recording on. |
| `--vantage-server URL` | `vantage_server` | `VANTAGE_SERVER` | `http://127.0.0.1:8765` | Where to report. |
| `--vantage-timeout SECONDS` | `vantage_timeout` | none | `10` | Upper bound on each report request, start to finish. |
| `--vantage-mode MODE` | `vantage_mode` | none | `server` | Where the run goes: `server`, `local`, `server+backup` or `server+local`. |
| `--vantage-local-database PATH` | `vantage_local_database` | none | the database `vantage` serves by default | The SQLite file the other three modes store in. |
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

`--vantage-server`, `--vantage-timeout`, `--vantage-mode` and
`--vantage-local-database` are honoured wherever they come from: they say
where and how, never whether.

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
`vantage: recording run <id> to <database>`. A run it cannot store (the path
cannot be written, the disk is full, the database is from another schema
version) produces one warning ending `the run is lost`.

**`server+local`** reports to the server exactly as `server` does and also
stores every run locally; the header reads
`vantage: recording run <id> to <address> and <database>`. A local database
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
- **The final report is refused outright** (any other 4xx, a redirect, an
  answer that does not acknowledge the run): the warning is the usual
  `vantage: error while reporting: ...`, and the run is not queued, since
  the server would refuse it again. It is still stored locally.

**Sending the queue.** After a session whose own run reached a server, the
plugin sends the runs queued for that server, oldest first, within the
report timeout, and prints one line with the test summary:
`vantage: sent 3 queued runs to http://ci-vantage:8765 (0 waiting)`. It stops
when the server is unreachable again, answers a 408 or 429, or the time is
spent, and says why. A run the server answers with a 5xx stays queued and
the next is sent; one it refuses with any other 4xx can never succeed, and
is dropped with a warning naming its run id. Each run is only ever sent to
the address it was queued for, compared exactly as written, so
`http://ci-vantage:8765` and `http://ci-vantage:8765/` are two different
servers to the queue. `vantage push` sends the queue on demand.

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

Sessions can keep storing into the file while it serves.

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
teardown. Under `-s` nothing is captured and the output fields are null.

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
key, whether it was read from a file or reported by the session:

```json
{"items": [
  {"key": "bench", "name": null, "value": "lab-3", "status": "captured",
   "source": "session", "source_file": null, "declared": false},
  {"key": "fmc.hardware", "name": "FMC hardware version", "value": "5.2.0",
   "status": "captured", "source": "session", "source_file": null, "declared": true}
]}
```

- `source` is `file` or `session`; `source_file` is the declared path of a
  `file` key and null otherwise.
- `value` is null unless `status` is `captured`. The other statuses are
  `absent`, `value_too_large`, and, for a file's key only, `not_scalar` and
  `source_unavailable`.
- A run that recorded no metadata has no items; a run id never recorded
  answers `404`. The list is not paged.

The run list filters by up to 16 keys at once. Repeat `metadata_key` and
`metadata_value` once per key; each value pairs with the key in the same
position, and a run must hold every pair to be listed:

```
GET /api/v1/runs?metadata_key=fpga.firmware&metadata_value=1.1.0&metadata_key=fmc.hardware&metadata_value=5.2.0
```

A pair matches a `captured` value spelt exactly the same, from a file or
the session. Giving the two parameters a different number of times, or more
than 16 pairs, answers `422`. A run recorded before any run held a key
cannot match it, so the answer's `metadata_horizon` says, for each filtered
key in the order given, how many runs predate it:

```json
"metadata_horizon": [{"key": "fpga.firmware", "predating": 12},
                     {"key": "fmc.hardware", "predating": 30}]
```

Without a metadata filter, `metadata_horizon` is null.

## When something goes wrong

**Invalid configuration** (an address that is not `http` or `https`, no
host, a bad port, a timeout that is not a positive number, an unknown mode,
a local database that is a PostgreSQL URL or a directory, an ini value of
the wrong type) stops pytest with a usage error naming the option, exit
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
- **The final report fails** (HTTP error, rejected report): if the start
  report got through, the run stays unfinished on the server and later reads
  as `abandoned`; otherwise nothing is recorded on the server. The backup
  modes keep the run locally, and queue it unless the server refused it.
- **The final report times out:** the plugin stops waiting at the deadline,
  but the server may still finish storing the report, so the run may be
  recorded as finished after all. The warning cannot tell which.
- **Something cannot be read** (git, the metadata declaration, a test report
  of an unexpected shape): that part of the report is left out.
- **The local database or the outbox cannot take the run:** one warning
  saying where the run is, if anywhere; the server's copy is unaffected.

Warnings are `VantageWarning`, a `UserWarning` subclass. Where one appears
depends on when it is raised:

- **At the start of the session** (server unreachable, switches ignored,
  recording could not start, session tracking unavailable, the start report
  failed, git or the metadata declaration unreadable): printed to stderr as
  a plain Python warning, above pytest's session header. pytest neither
  lists nor counts it in its warnings summary, so in a long CI log the line
  saying a session is not being recorded is at the top.
- **At the end of the session** (the final report failed, where a run the
  server could not take was kept, a queued run dropped, results left out,
  metadata keys undeclared, left out or reported twice): in pytest's
  warnings summary.
- **While tests run** (a heartbeat failed, a test report of an unexpected
  shape): in the warnings summary, listed under whichever test was running
  at the time; under pytest-xdist, printed to stderr when it happens.
- **When a metadata key or value is skipped:** in the warnings summary,
  under the test whose fixture set it and pointing at the line that set it,
  and under pytest-xdist once for each worker that set it.

If your warning filters turn warnings into errors, the message is written to
the terminal instead.

A run whose final report never arrives (the process was killed, the machine
lost power) reads as `abandoned` once the server has heard nothing from it
for the grace period, provided the server received its start report. One
whose final report was queued reads the same until the queue is sent, and
then as finished.

## Running the server

```
vantage [--database PATH-OR-URL] [--host HOST] [--port PORT] [--grace-period SECONDS]
```

Serving needs the `server` extra. Without it, `vantage` refuses in one line,
`vantage: serving needs the server extra: pip install 'vantage[server]'`,
with exit status 1 and nothing created; `vantage push` and `vantage --help`
work either way.

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
- **There is no authentication.** Anyone who can reach the port can record
  runs, change the section definitions and read everything recorded, failure
  text included. Any `--host` other than `127.0.0.1` logs a warning saying so
  at startup.
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
| `GET /api/v1/runs` | Runs, newest first, each `running`, `finished`, `interrupted` or `abandoned` |
| `GET /api/v1/runs/{run_id}` | One run |
| `GET /api/v1/runs/{run_id}/metadata` | One run's metadata, from declared files and from the session |
| `GET /api/v1/runs/{run_id}/results` | One run's results, with a short failure summary |
| `GET /api/v1/runs/{run_id}/result?node_id=...` | One result in full, failure text included |
| `GET /api/v1/tests/history?node_id=...` | One test across runs, newest first |
| `GET`, `POST`, `DELETE /api/v1/config/sections` | Named file-path prefixes that group results |
| `GET /api/v1/runs/{run_id}/sections` | One run's pass rate per section |

The run list, a run's results and a test's history are paged: they take
`limit` (at most 200 per page) and `offset`, and say in `has_more` whether
more items exist. The section lists and a run's metadata are returned
whole. The run list also filters by metadata values; see
[Reading it back](#reading-it-back).

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
  read and write them afterwards. The database must be encoded in UTF-8,
  PostgreSQL's usual default; any other is refused with one line.
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
to 3.13, with and without pytest-xdist, and once more against PostgreSQL 17.

How the code is organised, and why: [`docs/architecture.md`](docs/architecture.md).

## Licence

MIT.
