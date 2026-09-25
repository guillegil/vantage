# Session ingestion API (v1)

**Contract:** `packages/vantage/src/vantage/service/openapi/v1.yaml` states
the request shape and response status codes; this file explains the choices
that document cannot carry.

This is the contract `pytest-vantage` is written against. The plugin and the
server are released independently, so this API, not their version numbers,
is what they agree on.

## `POST /api/v1/runs`

- **Media type:** `application/json` — anything else, or an absent
  `Content-Type`, is rejected before a body byte is read.
- **Size cap:** `1 MiB` (`MAX_REPORT_BYTES`), enforced while streaming, not
  after buffering.

**`extra=` is asymmetric between `run` and the envelope, deliberately.** `run`
is `extra="forbid"`, and so are `vcs` and `metadata`: an unrecognised field
there is a client/server disagreement about what the section *is* — a bug,
rejected loudly. The envelope is `extra="ignore"`: an unrecognised *section*
beside `run` means a newer `pytest-vantage` talking to an older `vantage` —
expected, not an error. This asymmetry is the version-skew contract between
the two distributions; "fixing" it into symmetry in either direction breaks
one of the two skew cases it exists to handle.

Each `results[]` entry is `extra="allow"`: an unknown key on a result is
accepted, and its name is reported back once in the acknowledgement's
`ignored` list as `results[].<name>`, however many results carried it.

Every rejection shares one body shape, `{"error", "detail", "fields"}`, e.g.
`{"error": "invalid_report", "detail": "...", "fields": ["run.started_at"]}`.
`fields` names the offending dotted path(s).

An entry may read `"<unnamed>"` instead of the field the client actually
sent: for an unrecognised key inside a `forbid` section (`extra_forbidden`),
that path segment is a key the *client* chose, not a name this schema
declared, and echoing it verbatim would reflect arbitrary client-controlled
bytes back — including characters that forge log lines wherever the
rejection is logged. Only identifier- or index-shaped segments are echoed;
anything else comes back as `<unnamed>`, which means "an extra field the
server will not name back," never that the server failed to identify it.

Nothing is written to storage before a request reaches `201` or `200` — a
rejection at any step leaves the run table unchanged.
