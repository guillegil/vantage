"""Anchor package for `importlib.resources.files(...)` (design.md Q5, task
6.7).

Holds `v1.yaml`, the hand-written OpenAPI 3.1 document served as raw bytes
by `GET /api/v1/openapi.yaml` (`routes/read.py`). This package exists only
to be an `importlib.resources` anchor -- it declares nothing else, so
nothing here could ever become a place a generated document ends up
instead. `v1.yaml` is authored independently of `app.routes` and stays that
way: deriving it from the route table would make the drift check compare
the code with itself and it could never fail (design.md Q5).
"""

from __future__ import annotations
