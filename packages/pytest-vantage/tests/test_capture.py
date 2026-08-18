"""Table-driven tests for `pytest_vantage.capture.decompose` (design.md D18,
RQ-9). Pure function over a string -- no subprocess, no server, no
`pytester` needed, so these run as ordinary unit tests directly against the
production function (design.md Testing Strategy, Phase 6 row).
"""

from __future__ import annotations

import pytest
from pytest_vantage.capture import decompose


@pytest.mark.req("RQ-9")
@pytest.mark.parametrize(
    (
        "node_id",
        "expected_file_path",
        "expected_class_name",
        "expected_function_name",
        "expected_param_id",
    ),
    [
        pytest.param(
            "packages/vantage/tests/test_execution.py::test_it_passes",
            "packages/vantage/tests/test_execution.py",
            None,
            "test_it_passes",
            None,
            id="module-level-unparametrised",
        ),
        pytest.param(
            "packages/vantage/tests/test_memory_store.py"
            "::TestInMemoryExecutionStore::test_first_write_creates_a_row",
            "packages/vantage/tests/test_memory_store.py",
            "TestInMemoryExecutionStore",
            "test_first_write_creates_a_row",
            None,
            id="single-class",
        ),
        pytest.param(
            "packages/vantage/tests/test_x.py::Outer::Inner::test_nested",
            "packages/vantage/tests/test_x.py",
            "Outer::Inner",
            "test_nested",
            None,
            id="nested-class-segments-joined-with-double-colon",
        ),
        pytest.param(
            "test_nid.py::test_p[a::b]",
            "test_nid.py",
            None,
            "test_p",
            "a::b",
            id="parametrised-value-itself-contains-double-colon",
        ),
        pytest.param(
            "test_nid.py::TestOuter::TestInner::test_q[m::n]",
            "test_nid.py",
            "TestOuter::TestInner",
            "test_q",
            "m::n",
            id="nested-class-and-a-parametrised-value-containing-double-colon",
        ),
    ],
)
def test_decompose_identity_class_name_and_unparametrised_param_id(
    node_id: str,
    expected_file_path: str,
    expected_class_name: str | None,
    expected_function_name: str,
    expected_param_id: str | None,
) -> None:
    """RQ-9.2: a module-level test's `class_name` is `None`, never `""`.
    A nested class's segments join with `"::"`. RQ-9.3: an unparametrised
    test's `param_id` is `None`.
    """
    identity = decompose(node_id)

    assert identity.file_path == expected_file_path
    assert identity.class_name == expected_class_name
    assert identity.function_name == expected_function_name
    assert identity.param_id == expected_param_id
    if expected_param_id is None:
        assert identity.param_id is None  # None must stay None, never a computed ""


@pytest.mark.req("RQ-9")
def test_decompose_identity_empty_brackets_is_empty_string_not_none() -> None:
    """The brackets are the evidence of parametrisation; their content is
    not (design.md D18). This is not a hypothetical case -- this exact node
    id exists in this repository today:
    `test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]`.
    It IS parametrised and its parameter id is the empty string, which must
    survive as `""`, not be coerced to `None` (the forbidden idiom `x or
    None`).
    """
    node_id = (
        "packages/vantage/tests/test_execution.py"
        "::test_identity_rejects_anything_but_32_lowercase_hex_characters[]"
    )

    identity = decompose(node_id)

    assert identity.function_name == (
        "test_identity_rejects_anything_but_32_lowercase_hex_characters"
    )
    assert identity.param_id == ""
    assert identity.param_id is not None


@pytest.mark.req("RQ-9")
def test_decompose_identity_slices_on_first_and_last_bracket_not_partition_symmetry() -> None:
    """A parameter id that itself contains brackets must survive intact.
    Slicing on the first `"["` and the last `"]"` gets this right; a
    symmetric `partition`/`rpartition` split would not (design.md D18).
    """
    node_id = "packages/vantage/tests/test_x.py::test_x[[0]]"

    identity = decompose(node_id)

    assert identity.function_name == "test_x"
    assert identity.param_id == "[0]"


@pytest.mark.req("RQ-9")
def test_decompose_identity_directory_containing_brackets_is_not_mistaken_for_a_parameter() -> None:
    """A directory component may itself contain brackets (design.md D18
    addendum). The parameter section must be located within the remainder
    AFTER the file path has already been split off -- searching the whole
    `node_id` for a bracket would misidentify this directory as the start
    of a parameter section and corrupt every field that follows it.
    """
    node_id = "tests/data[1]/test_a.py::test_b[x]"

    identity = decompose(node_id)

    assert identity.file_path == "tests/data[1]/test_a.py"
    assert identity.class_name is None
    assert identity.function_name == "test_b"
    assert identity.param_id == "x"


@pytest.mark.req("RQ-9")
def test_decompose_identity_rejects_a_string_with_no_double_colon_at_all() -> None:
    """Every real pytest node id has at least one `"::"` separating the
    file path from the test path (design.md D18 addendum). A string with
    none is not a node id at all, and silently treating the whole string
    as a function name (or as a bare file path with no test) would hide
    that malformed input rather than surface it -- so this raises instead.
    """
    with pytest.raises(ValueError, match="::"):
        decompose("not_a_node_id_at_all.py")
