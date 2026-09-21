"""Bindings must preserve JSON semantics and distinguish absence from null."""

import pytest

from foliqant.core.bindings import resolve_binding, resolve_bindings
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import freeze_json, thaw_json
from foliqant.core.plan import BindingPlan


@pytest.mark.parametrize(
    "pointer, expected",
    [
        ("/payload/a~1b/~0key/0", "Grüße"),
        ("/payload/a~1b/~0key/1", None),
        ("/payload/", False),
        ("/payload/~01", "one pass"),
        ("/payload/01", "object key"),
        ("/steps/classify/result", "request_info"),
    ],
)
def test_exact_pointer_semantics(pointer: str, expected: object) -> None:
    context = freeze_json(
        {
            "payload": {
                "a/b": {"~key": ["Grüße", None]},
                "": False,
                "~1": "one pass",
                "01": "object key",
            },
            "steps": {"classify": {"status": "completed", "result": "request_info"}},
        }
    )
    assert (
        thaw_json(resolve_binding(BindingPlan(kind="pointer", pointer=pointer), context))
        == expected
    )


def test_empty_pointer_selects_whole_context() -> None:
    context = freeze_json({"payload": [1], "metadata": {}})
    assert resolve_binding(BindingPlan(kind="pointer", pointer=""), context) is context


@pytest.mark.parametrize(
    "pointer", ["/items/01", "/items/-", "/items/-1", "/items/2", "/items/١", "/missing", "/null/x"]
)
def test_missing_never_becomes_null(pointer: str) -> None:
    context = freeze_json({"items": [0, 1], "null": None})
    with pytest.raises(ServiceError) as error:
        resolve_binding(BindingPlan(kind="pointer", pointer=pointer), context)
    assert error.value.code == ErrorCode.MISSING_BINDING
    assert pointer not in str(error.value)


def test_default_applies_only_to_missing_not_explicit_null() -> None:
    binding = BindingPlan(
        kind="pointer", pointer="/value", optional=True, has_default=True, default=7
    )
    assert resolve_binding(binding, freeze_json({})) == 7
    assert resolve_binding(binding, freeze_json({"value": None})) is None


@pytest.mark.parametrize("pointer", ["not/pointer", "#/fragment", "/bad~", "/bad~2"])
def test_invalid_pointer_cannot_be_hidden_by_default(pointer: str) -> None:
    with pytest.raises(ServiceError) as error:
        resolve_binding(
            BindingPlan(kind="pointer", pointer=pointer, optional=True, has_default=True),
            freeze_json({}),
        )
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


def test_literal_object_is_not_interpreted_as_pointer() -> None:
    literal = freeze_json({"pointer": "/payload/private"})
    assert thaw_json(resolve_binding(BindingPlan(kind="literal", literal=literal), None)) == {
        "pointer": "/payload/private"
    }


def test_resolved_arguments_are_immutable_and_duplicate_names_fail() -> None:
    binding = BindingPlan(kind="literal", literal="value")
    result = resolve_bindings((("argument", binding),), None)
    with pytest.raises(TypeError):
        result["argument"] = "changed"  # type: ignore[index]
    with pytest.raises(ServiceError) as error:
        resolve_bindings((("argument", binding), ("argument", binding)), None)
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION
