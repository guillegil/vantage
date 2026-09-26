"""HTTP surface: the ingestion endpoint, the read API and the `vantage`
command.

- **Ingestion.** ``pytest-vantage`` reports sessions here, and
  ``vantage.ingestion`` turns each into rows in the store this server
  holds. The path is versioned (``/api/v1/...``) because the plugin and the
  server are released independently and the API, not their version
  numbers, is the contract between them.
- **Read API.** Serves recorded history to any HTTP client.
- **The command.** ``vantage`` serves both (``cli.py``); ``vantage push``
  (``push.py``) sends the runs the plugin queued for a server it could not
  reach.

This is the only package that imports the web framework, which the
``server`` extra installs: it is installed deliberately by someone who
wants a server, never pulled into a stranger's test environment. Only
serving imports it, so ``vantage push`` runs without the extra.
"""
