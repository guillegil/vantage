"""Anchor package for `importlib.resources.files(...)`.

Holds `v1.yaml`, the hand-written OpenAPI 3.1 document served as raw bytes
by `GET /api/v1/openapi.yaml` (`routes/read.py`). `v1.yaml` is authored
independently of `app.routes` and must stay that way: deriving it from the
route table would make the drift check compare the code with itself, so it
could never fail.
"""

from __future__ import annotations
