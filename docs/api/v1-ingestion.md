# Ingestion API, v1

How a test runner reports sessions to a Vantage server. This is the contract
`pytest-vantage` is written against, and all a plugin for another test runner
would need. The plugin and the server are released separately; this API, not
their version numbers, is what they agree on.

It covers three routes:

- `GET /api/v1/capabilities`: what the server supports.
- `POST /api/v1/runs`: a report of one session, sent once or several times.
- `POST /api/v1/runs/{run_id}/heartbeat`: a sign that the session is alive.

The server serves an OpenAPI document for every route, the read API included,
at `GET /api/v1/openapi.yaml` (source:
`packages/vantage/src/vantage/service/openapi/v1.yaml`). Every route is under
`/api/v1`; nothing answers an unversioned path.

## Authentication

A server whose database has no user takes every request without a token.
Once a user exists (`vantage user add`), every route but
`GET /api/v1/capabilities` and `GET /api/v1/openapi.yaml` needs one:

```
Authorization: Bearer vantage_...
```

A token belongs to one user and holds one or more scopes: `read`, `record`
and `admin`. The two ingestion routes need `record`, which is all a token
for a test runner needs. The server answers:

- No `Authorization` header, on a server with users: `401 unauthenticated`,
  with `WWW-Authenticate: Bearer realm="vantage"`.
- A header that carries no bearer token, or a token that is unknown, revoked
  or of a disabled user, on any server: `401 unauthenticated`, the challenge
  adding `error="invalid_token"`. A token sent to a server without users is
  refused the same way, not ignored.
- A token that does not grant the route's scope: `403 insufficient_scope`,
  the challenge naming it, as in `scope="record"`.

Neither the body nor the challenge ever holds the token sent. The report
that creates a run records who sent it: the token's user, or nobody on a
server without users. The read API shows it as `recorded_by`. Every later
report and heartbeat of the run must come from the same user, or with no
token if the first came with none: anything else is `409 foreign_run`, and
nothing of it is stored. The client chooses the run id, so this is what
keeps one user's session from finishing, or keeping alive, another's.

`pytest-vantage` reads its token from the `VANTAGE_TOKEN` environment
variable alone, never from a configuration file or a flag. It sends it with
every report and heartbeat, only to the address it reports to, since it
follows no redirect, and never with the capability probe.

## `GET /api/v1/capabilities`

```json
{"session_lifecycle": true}
```

`session_lifecycle` means the server records a run from its start report and
accepts heartbeats for it. A server without this route answers `404`; read
that as every capability absent. `pytest-vantage` sends a start report and
heartbeats only when the answer is a JSON object with `session_lifecycle`
exactly `true`, and otherwise skips both.

## `POST /api/v1/runs`

### The request

| Aspect | Rule | Otherwise |
| --- | --- | --- |
| Media type | `Content-Type: application/json`; parameters such as `charset` are allowed and the type is matched case-insensitively. Checked from the header before any of the body is read. | `415 unsupported_media_type` |
| Size | At most 1,048,576 bytes (1 MiB). Counted while the body streams in; `Content-Length` is not trusted, and reading stops as soon as the limit is passed. | `413 payload_too_large` |
| Encoding | Strict UTF-8, then JSON. | `400 invalid_json` |
| Shape | A JSON object matching the sections below. | `422 invalid_report` |

A `\uXXXX` escape for a lone surrogate (half of a UTF-16 pair with no
partner) is valid JSON but cannot be stored as UTF-8. pytest produces such
text itself, from file names that were not valid UTF-8. The server replaces
every lone surrogate, in keys and values alike, with U+FFFD before
validating, rather than rejecting the session over one character. It does
the same with every U+0000 (`\u0000`), which a PostgreSQL database cannot
store, and with every U+0000 a declared metadata document spells as an
escape, so every database stores the same text. A key, value or node id
that held one reads back with U+FFFD in its place, and looking it up with
the original U+0000 finds nothing.

Nothing is stored unless the whole report is accepted, and an accepted report
is stored in one transaction.

### The body

```
{
  "run":      {"id": "4f1c...", "started_at": "...", "finished_at": null,
               "exit_status": null, "interrupted": false, "interrupt_reason": null},
  "results":  [ ... ],
  "vcs":      {"commit": "...", "branch": "main", "commit_subject": "...",
               "dirty": false, "root": "/home/me/project"},
  "metadata": {"declaration": "vantage-metadata.json", "files": [ ... ]}
}
```

Only `run` is required. `results`, `vcs` and `metadata` may be absent or
`null`; an absent `results` and an empty one both mean no results. Any other
top-level key is ignored, so a newer client can add a section an older server
does not know.

**Timestamps** are ISO 8601 strings. The server converts each to UTC and takes
one without an offset as UTC; one that falls outside the years 1 to 9999 once
converted is rejected. Send them with an offset.

**Types.** Send the JSON types listed below. The server currently also coerces
some near misses instead of rejecting them (a numeric string for an integer,
`"yes"` or `1` for a boolean, a Unix-time number or a date without a time for
a timestamp); do not rely on that.

#### `run`

Every field is required, `null` where allowed. An unknown field is rejected.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | 32 lowercase hex characters (a `uuid4` without dashes), chosen by the client. The same id in every report of one session. |
| `started_at` | timestamp | When the session started. |
| `finished_at` | timestamp or null | When it came to an orderly end. Null while in progress, and for a session that stopped without one. |
| `exit_status` | integer or null | Null in an in-progress report; the session's exit status in a finishing report. Signed 64-bit. |
| `interrupted` | boolean | Whether the session was cut short by the user or by an explicit exit call. |
| `interrupt_reason` | string or null | Why the session stopped early, when it did. Stored up to 64 KiB; a longer reason is cut with no record of the cut. |

A report whose `exit_status` is null is **in progress**; one with an exit
status is **finishing**.

#### `results`

A list with one entry per test. Two entries with the same `node_id` in one
report reject the report, with `fields: ["results"]`.

These fields are required, `null` where allowed:

| Field | Type | Meaning |
| --- | --- | --- |
| `node_id` | string | The test's identity, e.g. `tests/test_a.py::TestX::test_y[1]`. |
| `file_path`, `function_name` | string | Parts of the node id. |
| `class_name` | string or null | Null for a module-level test. |
| `param_id` | string or null | Null when not parametrised; `""` is a real, empty parameter id. |
| `outcome` | string | `passed`, `failed`, `error`, `skipped`, `xfailed` or `xpassed`. |
| `setup_outcome`, `call_outcome`, `teardown_outcome` | string or null | The same vocabulary; null for a step that never ran. |
| `duration`, `setup_duration`, `call_duration`, `teardown_duration` | number or null | Seconds. Null, not `0`, for a step that never ran. |
| `started_at`, `finished_at` | timestamp or null | |
| `worker_id` | string or null | The xdist worker, or null. |

These fields are optional and default to null, or to `false` for the flags:

| Field | Type |
| --- | --- |
| `failure_type`, `failure_path` | string or null |
| `failure_lineno` | integer or null (signed 64-bit) |
| `failure_message`, `failure_repr`, `traceback`, `skip_reason`, `xfail_reason`, `captured_stdout`, `captured_stderr` | string or null |
| `<field>_truncated` for each field in the row above | boolean |

- The server cuts each field that has a `_truncated` flag to 64 KiB of UTF-8,
  at a character boundary, and sets the flag. The stored flag is set if
  either the client or the server cut the text: only the client knows it
  dropped a field to keep the report under the size cap.
- For `captured_stdout` and `captured_stderr`, `null` means nothing was
  captured and `""` means capture was on and the test printed nothing.
- Any other key on a result is accepted, not stored, and named once in the
  acknowledgement's `ignored` list as `results[].<name>`, however many results
  carried it.

#### `vcs`

All five fields are required and may be null. An unknown field is rejected.

| Field | Type | Meaning |
| --- | --- | --- |
| `commit` | string or null | At most 64 characters (a SHA-256 hash fits). |
| `branch` | string or null | |
| `commit_subject` | string or null | Stored up to 64 KiB; the server records whether it cut it. |
| `dirty` | boolean or null | Whether tracked files had uncommitted changes; null when unknown. |
| `root` | string or null | The repository's local path. Stored, and returned by no response. |

A section whose fields are all null is stored as no VCS data.

#### `metadata`

What the run was made against: values read from files the test repository
declares, and values the test session reported itself. `declaration` is
required; `keys`, `files` and `values` are optional and default to empty.
An unknown field, in the section or in any of its entries, is rejected. No
length or pattern constraint applies: a value the server cannot store is
dropped, never a reason to reject the report.

```json
{
  "declaration": "vantage-metadata.json",
  "keys": {"fpga.firmware": {"name": "FPGA firmware version"}},
  "files": [
    {"path": "settings.json", "format": "json", "status": "captured",
     "keys": ["service_version"], "content": "{\"service_version\": \"2.10\"}"}
  ],
  "values": [
    {"key": "fpga.firmware", "value": "1.1.0", "status": "captured"},
    {"key": "fmc.hardware", "value": null, "status": "absent"}
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `declaration` | string or null | The declaration's file name, or null when the session had none. |
| `keys` | object | The keys the declaration names, each mapped to `{"name": ...}`: the display name to show it under, a string or null. `name` may be left out. |
| `files` | list | One entry per file the declaration lists. |
| `values` | list | The values the session reported. |

| Field of a file | Type | Meaning |
| --- | --- | --- |
| `path` | string | The declared path, relative to the directory holding the declaration (pytest's rootdir, for `pytest-vantage`), which need not be the repository root. |
| `format` | string | `json` or `yaml`. |
| `status` | string | `captured`, or why not: `not_found`, `path_rejected`, `unreadable`, `too_large`, `not_text`, `over_budget`, `malformed`. |
| `keys` | list of strings | The top-level keys to record from this file. |
| `content` | string or null | The file's text when `status` is `captured`, otherwise null. |

Every field of a value is required:

| Field of a value | Type | Meaning |
| --- | --- | --- |
| `key` | string | A flat key, such as `fpga.firmware`. |
| `value` | string or null | The value, as text, when `status` is `captured`; otherwise null. |
| `status` | string | `captured`; `absent` for a key reported without a value, or declared in `keys` and never reported; or `value_too_large` for a value over 1,024 bytes of UTF-8, sent without it. |

A key is **declared** when the same report names it, in `keys` or in any
file's `keys`. Each stored key records whether it was declared and the
display name `keys` gives it; a file's key is always declared. The server
derives no key from the declaration: a declared key that was never reported
is stored only if the client sends it, as `absent`.

The server re-applies the client's bounds by dropping, never by rejecting:

- A file is dropped, with its keys, when its path is absolute, contains `..`,
  a backslash or a drive, is longer than 1,024 characters, or repeats an
  earlier file's path, or when its `format` or `status` is not one listed
  above. `pytest-vantage` refuses a declaration holding such a path, with a
  warning, so it never sends one.
- A file's key is dropped when it is longer than 1,024 characters or was
  declared by an earlier file.
- A value is dropped when its key is longer than 1,024 characters, its
  `status` is not one of the three above, or its key was already given by a
  file or by an earlier value of the report: a file's value is kept over the
  session's.
- A value whose `value` and `status` disagree (a value with a status other
  than `captured`, or `captured` with a null value) is stored as `absent`,
  and a `captured` value over 1,024 bytes of UTF-8 as `value_too_large`,
  without it.
- A display name longer than 256 characters is stored as no name; its key
  is kept.
- A run holds at most 200 keys, counted over all its reports. Within a
  report the files' keys come first, then the values, in order; every key
  past the 200th is dropped.
- A `captured` file is recorded `malformed` when its content is null, cannot
  be parsed, or is not a mapping at the top level; `too_large` when its
  content exceeds 8 KiB; and `over_budget` once the files' contents pass
  32 KiB in total, counted in order. Other statuses are stored as sent.

Each key of a file is stored with a status: `captured` (its value, as text,
when it is a scalar of at most 1,024 bytes), `absent`, `not_scalar`,
`value_too_large`, or `source_unavailable` when its file was not captured or
parsed. JSON numbers keep their literal text (`2.10` stays `"2.10"`), and
YAML is parsed without constructing any object. The file's text itself is
not stored.

`keys` and `values` were added to `/api/v1` before either distribution was
released. A server that predates them rejects a report carrying either with
`422 invalid_report`, so `pytest-vantage` leaves both out when they are
empty: a session that declares no key and reports no value is accepted by
such a server as before.

What was stored reads back from `GET /api/v1/runs/{run_id}/metadata`, each
declared file's status included, and filters the run list; see the OpenAPI
document.

### The answer

`201` for the first report of a run id, `200` for every later one:

```json
{"run_id": "4f1c...", "status": "created", "ignored": []}
```

`status` is `created` with `201` and `duplicate` with `200`. `ignored` lists
the unknown result keys, as described above. `pytest-vantage` treats any
answer that is not this shape, with its own run id and one of these two
statuses, as a failed report.

### Several reports for one run

A client may send as many reports for one run id as it needs, and may retry
any of them.

- **The run row** is created by the first report. Its finish fields
  (`finished_at`, `exit_status`, `interrupted`, `interrupt_reason`) and VCS
  fields change once more at most: when the stored run has no exit status and
  a finishing report arrives. After that, no report changes them. An
  in-progress report never overwrites a finish, whatever order they arrive
  in, and `started_at` never changes after the first report. A VCS field that
  is null in the finishing report keeps its earlier value.
- **Results accumulate** until the run is finished. Each report adds the
  results whose node ids the run does not have yet. A result already stored
  is never replaced.
- **Metadata** is stored once per file path and per key: the first report
  to carry a key decides its value, and later copies are ignored. A run
  holds at most 200 keys over all its reports.
- **One user per run.** Every report must come from the user whose token
  sent the first, or with no token if the first came with none
  (see [Authentication](#authentication)); anything else is
  `409 foreign_run` and changes nothing, finished run or not.
- **A finished run is final.** A report arriving once the run has an exit
  status stores nothing: no results, no metadata, no change to the run. A
  client sends the finishing report last, so anything after it is a retry
  or a replay. It still answers `200`.
- **`200` does not mean nothing changed.** It means the run already existed.
  A finishing report after a start report answers `200` and stores the
  finish and its results.

A session with more results than fit in 1 MiB sends them over several
in-progress reports and puts the last of them in the finishing report.
`pytest-vantage` does exactly this, so a report lost on the way leaves the
run unfinished rather than finished with results missing. Its full sequence
is: a start report (in progress, no results), heartbeats, any in-progress
reports with results, and the finishing report. Without the
`session_lifecycle` capability it sends only the last two. In its backup
modes, a report that got no answer, a `5xx`, a `408`, a `429`, or a `401`
or `403` refusing its token, is sent again later, unchanged, with the
reports after it: by a later session or by `vantage push`, from another
process, possibly days later, and with that sender's token. A session that
could not reach the server at its start sends no start report at all, and
its reports arrive only that way.

Every report of a session carries the declaration's `keys` and `files`, so
the files' keys are stored from the first report that arrives. Only the
finishing report carries `values`; when the last results leave no room for
them, those results go in one more in-progress report and the finishing
report carries none. The server takes values from any report that reaches
the run before it is finished.

## `POST /api/v1/runs/{run_id}/heartbeat`

`run_id` is the run's 32-hex id. The request body and its `Content-Type` are
ignored; `pytest-vantage` sends `{}` as `application/json`.

```json
{"run_id": "4f1c...", "status": "acknowledged"}
```

- A known run answers `200` and its last-contact time moves forward to now.
  It never moves backwards, and a heartbeat never changes the finish fields.
  A heartbeat for a finished run is harmless.
- A run the server has never seen answers `404 unknown_run`. A heartbeat never
  creates a run: send a report first.
- A run another user recorded answers `409 foreign_run`, and its last
  contact does not move.
- A `run_id` that is not 32 lowercase hex characters answers
  `422 invalid_report` with `fields: ["path.run_id"]`.

The last-contact time is set when a run is created and advanced only by
heartbeats. The read API presents a run with no finishing report as
`running`, and as `abandoned` once its last contact is older than the
server's grace period (900 seconds by default). A run with a finish time is
`finished`; one whose finishing report carried no finish time is
`interrupted`. `pytest-vantage` beats every 30 seconds from a background
thread, from the start report until the session finishes, whatever its tests
are doing: a heartbeat says the process is alive, so a test that hangs keeps
its run `running`.

## Status codes

| Status | `error` | When |
| --- | --- | --- |
| `200` | | A later report of a known run; a heartbeat acknowledged; the capabilities answer. |
| `201` | | The first report of a run. |
| `400` | `invalid_json` | The body is not strict UTF-8, not JSON, or nested too deeply to parse. |
| `400` | `incomplete_body` | The client disconnected before sending the whole body. It never sees this answer. |
| `401` | `unauthenticated` | No token on a server that has users, or a token that is not valid on any server. |
| `403` | `insufficient_scope` | The token does not grant the `record` scope. |
| `404` | `unknown_run` | A heartbeat for a run never recorded. |
| `404` | `not_found` | No route matches the path, unversioned paths included. |
| `405` | `method_not_allowed` | The path exists but does not take this method. The `Allow` header lists the ones it takes. |
| `409` | `foreign_run` | A report or heartbeat of a run another user recorded. |
| `413` | `payload_too_large` | The body passed 1,048,576 bytes. |
| `415` | `unsupported_media_type` | `Content-Type` is absent or not `application/json`. |
| `422` | `invalid_report` | The body does not match the shape above, or a heartbeat's `run_id` is malformed. |
| `500` | | The server failed while handling the request (the database was unavailable, for example). The body is plain text, not a rejection. |

## The rejection body

Every `4xx` above has the same body:

```json
{"error": "invalid_report",
 "detail": "The submitted report does not match the expected shape.",
 "fields": ["run.started_at", "results.3.outcome"]}
```

- `error` is a stable code from the table above.
- `detail` is a fixed English sentence. The only client text it ever holds
  is the media type a `415` names back, filtered as below.
- `fields` lists the failing fields as dotted paths, with list indexes as
  numbers. It is empty when the problem is the body as a whole. A path
  parameter appears as `path.<name>`.
- A path segment is echoed only if it looks like a name this schema could
  declare (a letter or underscore, then letters, digits or underscores, up to
  64 characters) or is an index. Anything else, such as an unknown key
  containing a space or a newline, comes back as `<unnamed>`: an extra field
  the server will not repeat, so client text can never forge a line in a log
  that records the rejection. The same rule applies to the names in
  `ignored`, and to each half of the media type a `415` names back (an
  absent header reads `<absent>`).
