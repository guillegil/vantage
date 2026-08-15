"""HTTP surface: the ingestion endpoint and the read API.

Two responsibilities, deliberately distinct:

- **Ingestion.** ``pytest-vantage`` reports finished sessions here, and this
  package performs every write (ADR-9). Version the path -- ``/api/v1/...``
  -- because plugin and server release independently and the API is the
  contract between them, not their version numbers.
- **Read API.** Serves recorded history to the web interface and to anything
  else that speaks it (RQ-14). The interface is a separate TypeScript client
  built in CI (ADR-8); it reaches this package only over HTTP and holds no
  import of any module here.

The only package in the workspace permitted a third-party dependency,
because it is installed deliberately by someone who wants a server rather
than landing in a stranger's test environment.
"""
