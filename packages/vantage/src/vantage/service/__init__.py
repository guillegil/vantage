"""HTTP surface: the ingestion endpoint and the read API.

- **Ingestion.** ``pytest-vantage`` reports sessions here, and this package
  performs every write. The path is versioned (``/api/v1/...``) because the
  plugin and the server are released independently and the API, not their
  version numbers, is the contract between them.
- **Read API.** Serves recorded history to any HTTP client. A web interface
  talks to it over HTTP only and imports nothing from here.

This is the only package in the workspace allowed third-party dependencies
beyond the plugin's pytest: it is installed deliberately by someone who
wants a server, never pulled into a stranger's test environment.
"""
