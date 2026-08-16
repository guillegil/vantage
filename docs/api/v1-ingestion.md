# Session ingestion API (v1)

The plugin-to-server contract `pytest-vantage` is written against. Published
per ADR-4 (two independently released distributions), served per ADR-11
(FastAPI on uvicorn).

## `POST /api/v1/runs`

- **Media type:** `application/json` — anything else, or an absent
  `Content-Type`, is rejected before a body byte is read.
- **Size cap:** `1 MiB` (`MAX_REPORT_BYTES`), enforced while streaming, not
  after buffering.

### Request shape

```json
{"run": {"id": "32 lowercase hex chars", "started_at": "2026-08-15T09:14:02.481930+00:00", "finished_at": "2026-08-15T09:14:47.002118+00:00", "exit_status": 0, "interrupted": false, "interrupt_reason": null}}
```

**`extra=` is asymmetric between `run` and the envelope, deliberately.** `run`
is `extra="forbid"`: an unrecognised field there is a client/server
disagreement about what a run *is* — a bug, rejected loudly. The envelope is
`extra="ignore"`: an unrecognised *section* beside `run` (Milestone 2 adds
`results`, Milestone 3 adds `environment`/`vcs`) means a newer
`pytest-vantage` talking to an older `vantage` — expected, not an error.
This asymmetry IS the version-skew contract between the two independently
released distributions (ADR-4); either direction of "fixing" it into
symmetry breaks one of the two skew cases it exists to handle.

### Responses

| Status | Meaning | Body |
| --- | --- | --- |
| `201` | Stored, new run | `{"run_id", "status": "created", "ignored": []}` |
| `200` | Retried, already stored | `{"run_id", "status": "duplicate", "ignored": []}` |
| `400 invalid_json` | Body is not valid JSON | rejection body |
| `400 incomplete_body` | Client disconnected before the whole body arrived | rejection body |
| `413 payload_too_large` | Body exceeds the 1 MiB cap | rejection body |
| `415 unsupported_media_type` | `Content-Type` missing or not `application/json` | rejection body |
| `422 invalid_report` | Valid JSON, `run` fails validation | rejection body |

Every rejection shares one body shape, `{"error", "detail", "fields"}`, e.g.
`{"error": "invalid_report", "detail": "...", "fields": ["run.started_at"]}`.
`fields` names the offending dotted path(s).

An entry may read `"<unnamed>"` instead of the field the client actually
sent: for an unrecognised key inside `run` (`extra_forbidden`), that path
segment is a key the *client* chose, not a name this schema declared, and
echoing it verbatim would reflect arbitrary client-controlled bytes back —
including characters that forge log lines wherever the rejection is logged.
Only identifier- or index-shaped segments are echoed; anything else comes
back as `<unnamed>`, which means "an extra field the server will not name
back," never that the server failed to identify it.

Nothing is written to storage before a request reaches `201` or `200` — a
rejection at any step leaves the run table unchanged.
