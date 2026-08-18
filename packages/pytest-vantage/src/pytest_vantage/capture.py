"""Plugin-local decomposition of the pytest node id (design.md D18, RQ-9).

`vantage.core.domain.result.CaseIdentity` is the shape this information
eventually takes once it reaches the server, but `pytest-vantage` cannot
import `vantage` (RQ-24) -- this module owns a plain stdlib structure of its
own rather than reaching across the package boundary to reuse that
dataclass. Standard library and `pytest` only -- never `xdist`, not even
guarded by `try` (`test_plugin_imports.py` is the guard).
"""

from __future__ import annotations

from typing import NamedTuple


class DecomposedIdentity(NamedTuple):
    """A test's identity, decomposed from its pytest node id.

    ``class_name`` is `None` for a module-level test, never `""` (RQ-9.2).
    ``param_id`` is `None` for an unparametrised test and `""` for a
    parametrised test whose parameter id is itself the empty string -- the
    brackets are the evidence of parametrisation, not their content
    (RQ-9.3, design.md D18). The forbidden idiom here is ``x or None``: it
    would turn a genuine ``""`` into ``None`` and erase that distinction.
    """

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None


def decompose(node_id: str) -> DecomposedIdentity:
    """Split a pytest node id into its identity components (design.md D18).

    1. Split on ``"::"``. The first segment is ``file_path``; the segments
       between the first and the last, joined with ``"::"``, are
       ``class_name`` -- ``None`` when there are none (module-level test).
    2. In the last segment: if it ends with ``"]"`` and contains ``"["``,
       slice on the FIRST ``"["`` and the LAST ``"]"`` -- not
       ``partition``/``rpartition`` symmetry -- so a parameter id that
       itself contains brackets (``test_x[[0]]``) survives intact.
       Otherwise the whole segment is the function name and ``param_id``
       is ``None``.
    """
    segments = node_id.split("::")
    file_path = segments[0]
    class_segments = segments[1:-1]
    class_name = "::".join(class_segments) if class_segments else None

    last_segment = segments[-1]
    if last_segment.endswith("]") and "[" in last_segment:
        bracket_start = last_segment.index("[")
        function_name = last_segment[:bracket_start]
        param_id = last_segment[bracket_start + 1 : -1]
    else:
        function_name = last_segment
        param_id = None

    return DecomposedIdentity(
        node_id=node_id,
        file_path=file_path,
        class_name=class_name,
        function_name=function_name,
        param_id=param_id,
    )


__all__ = ["DecomposedIdentity", "decompose"]
