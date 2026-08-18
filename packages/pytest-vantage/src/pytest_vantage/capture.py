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

    The parameter section (a parametrised test's ``[...]`` suffix) is
    arbitrary user data supplied to ``@pytest.mark.parametrize`` -- it MAY
    itself contain ``"::"`` (``test_p[a::b]``) or brackets
    (``test_p[[0]]``). It is therefore removed from the remainder BEFORE
    that remainder is split on ``"::"`` to find the class and function
    segments -- do NOT re-simplify this back into one ``node_id.split("::")``
    call, which would misparse the parameter section's own ``"::"`` as a
    class separator.

    Likewise a directory component MAY itself contain brackets
    (``tests/data[1]/test_a.py::test_b[x]``), so the parameter section is
    located only within the remainder AFTER the file path has already been
    split off -- never by searching the whole ``node_id`` for a bracket.

    Steps:

    1. Partition on the FIRST ``"::"`` only. The part before it is
       ``file_path``; pytest node ids do not put ``"::"`` inside a file
       path, so this is unambiguous. A ``node_id`` with no ``"::"`` at all
       is not a real pytest node id (every one has a file path and at
       least one test-path segment) and raises ``ValueError`` rather than
       silently inventing a function name.
    2. In that remainder: if it ends with ``"]"`` and contains ``"["``,
       slice on the FIRST ``"["`` and the LAST ``"]"`` -- not
       ``partition``/``rpartition`` symmetry -- to pull out ``param_id``,
       then drop that suffix from the remainder. Otherwise ``param_id`` is
       ``None`` and the remainder is left untouched.
    3. Split what is left on ``"::"``. The last segment is
       ``function_name``; every segment before it, joined with ``"::"``,
       is ``class_name`` -- ``None`` when there are none (module-level
       test).
    """
    file_path, separator, remainder = node_id.partition("::")
    if not separator:
        raise ValueError(
            f"not a pytest node id (missing '::' between file path and test path): {node_id!r}"
        )

    if remainder.endswith("]") and "[" in remainder:
        bracket_start = remainder.index("[")
        param_id = remainder[bracket_start + 1 : -1]
        remainder = remainder[:bracket_start]
    else:
        param_id = None

    segments = remainder.split("::")
    class_segments = segments[:-1]
    class_name = "::".join(class_segments) if class_segments else None
    function_name = segments[-1]

    return DecomposedIdentity(
        node_id=node_id,
        file_path=file_path,
        class_name=class_name,
        function_name=function_name,
        param_id=param_id,
    )


__all__ = ["DecomposedIdentity", "decompose"]
