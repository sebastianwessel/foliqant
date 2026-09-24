"""The closed condition language: contract shape and pure evaluation semantics."""

import pytest
from pydantic import TypeAdapter, ValidationError

from foliqant.compiler import CompilationError
from foliqant.compiler.conditions import compile_condition
from foliqant.contracts.conditions import Condition
from foliqant.core.conditions import (
    MAX_MATCH_LENGTH,
    ConditionTrace,
    describe_condition,
    evaluate_condition,
    json_equal,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import freeze_json
from foliqant.core.plan import BindingPlan, LeafConditionPlan, SourceLocation

_ADAPTER: TypeAdapter[object] = TypeAdapter(Condition)
_LOCATION = SourceLocation("workflow.yaml", 1, 1)
_CONTEXT = freeze_json(
    {
        "payload": {
            "text": "FOI-2026-0142",
            "empty_text": "",
            "count": 3,
            "ratio": 0.5,
            "flag": True,
            "none": None,
            "items": [1, 2],
            "no_items": [],
            "object": {"a": 1},
            "no_object": {},
            "number_text": "1",
        }
    }
)


def _evaluate(document: object, trace: ConditionTrace | None = None) -> bool:
    authored = _ADAPTER.validate_python(document, strict=True)
    plan = compile_condition(authored, _LOCATION)  # type: ignore[arg-type]
    return evaluate_condition(plan, _CONTEXT, trace=trace)


def _leaf(pointer: str, **operator: object) -> dict[str, object]:
    return {"binding": {"pointer": f"/payload/{pointer}"}, **operator}


@pytest.mark.parametrize(
    "pointer,expected",
    [
        ("text", True),
        ("empty_text", True),
        ("count", True),
        ("flag", True),
        ("no_items", True),
        ("none", False),
        ("missing", False),
        ("object/a", True),
        ("object/missing", False),
    ],
)
def test_present_treats_null_and_missing_as_absent(pointer, expected):
    assert _evaluate(_leaf(pointer, present=True)) is expected
    assert _evaluate(_leaf(pointer, present=False)) is not expected


@pytest.mark.parametrize(
    "pointer,expected",
    [
        ("empty_text", True),
        ("no_items", True),
        ("no_object", True),
        ("none", True),
        ("missing", True),
        ("text", False),
        ("items", False),
        ("object", False),
        ("count", False),
        ("flag", False),
    ],
)
def test_empty_covers_absent_empty_string_array_and_object(pointer, expected):
    assert _evaluate(_leaf(pointer, empty=True)) is expected
    assert _evaluate(_leaf(pointer, empty=False)) is not expected


@pytest.mark.parametrize(
    "pointer,operand,expected",
    [
        ("text", "FOI-2026-0142", True),
        ("text", "foi-2026-0142", False),
        ("number_text", 1, False),
        ("count", 3, True),
        ("count", 3.0, True),
        ("count", "3", False),
        ("flag", True, True),
        ("flag", 1, False),
        ("items", [1, 2], True),
        ("items", [2, 1], False),
        ("object", {"a": 1}, True),
        ("object", {"a": True}, False),
        ("missing", None, True),
        ("none", None, True),
        ("text", None, False),
    ],
)
def test_equals_is_deep_and_never_coerces_and_absent_equals_null(pointer, operand, expected):
    assert _evaluate(_leaf(pointer, equals=operand)) is expected
    assert _evaluate(_leaf(pointer, not_equals=operand)) is not expected


@pytest.mark.parametrize(
    "pointer,values,expected",
    [
        ("text", ["x", "FOI-2026-0142"], True),
        ("text", ["x"], False),
        ("count", ["3", 4], False),
        ("missing", ["x"], False),
        ("missing", [None, "x"], True),
        ("items", [[1, 2]], True),
    ],
)
def test_in_and_not_in(pointer, values, expected):
    assert _evaluate(_leaf(pointer, **{"in": values})) is expected
    assert _evaluate(_leaf(pointer, not_in=values)) is not expected


@pytest.mark.parametrize(
    "operator,operand,expected",
    [
        ("gt", 2, True),
        ("gt", 3, False),
        ("gte", 3, True),
        ("lt", 3.5, True),
        ("lt", 3, False),
        ("lte", 3, True),
    ],
)
def test_numeric_comparisons(operator, operand, expected):
    assert _evaluate(_leaf("count", **{operator: operand})) is expected


@pytest.mark.parametrize("pointer", ["text", "flag", "items", "object"])
@pytest.mark.parametrize("operator", ["gt", "gte", "lt", "lte"])
def test_numeric_comparison_on_other_types_is_false_and_traced(pointer, operator):
    trace = ConditionTrace()
    assert _evaluate(_leaf(pointer, **{operator: 0}), trace) is False
    assert [(item.leaf.operator, item.reason) for item in trace.mismatches] == [
        (operator, "incompatible_type")
    ]


@pytest.mark.parametrize("pointer", ["missing", "none"])
def test_comparisons_on_absent_values_are_false_without_mismatch(pointer):
    trace = ConditionTrace()
    for operator in ({"gt": 0}, {"matches": ".*"}, {"length": {"gte": 0}}):
        assert _evaluate(_leaf(pointer, **operator), trace) is False
    assert trace.mismatches == []


@pytest.mark.parametrize(
    "pattern,expected",
    [("FOI-[0-9]{4}-[0-9]{4}", True), ("FOI", False), ("^FOI-.*$", True), ("(?i)foi-.*", True)],
)
def test_matches_is_an_implicit_full_match(pattern, expected):
    assert _evaluate(_leaf("text", matches=pattern)) is expected


def test_matches_compares_only_bounded_values():
    context = freeze_json(
        {"payload": {"edge": "a" * MAX_MATCH_LENGTH, "long": "a" * (MAX_MATCH_LENGTH + 1)}}
    )
    authored = _ADAPTER.validate_python(
        {"binding": {"pointer": "/payload/edge"}, "matches": "a*"}, strict=True
    )
    trace = ConditionTrace()
    assert evaluate_condition(compile_condition(authored, _LOCATION), context, trace=trace)  # type: ignore[arg-type]
    assert trace.mismatches == []
    authored = _ADAPTER.validate_python(
        {"binding": {"pointer": "/payload/long"}, "matches": "a*"}, strict=True
    )
    plan = compile_condition(authored, _LOCATION)  # type: ignore[arg-type]
    assert evaluate_condition(plan, context, trace=trace) is False
    assert [(item.leaf.operator, item.reason) for item in trace.mismatches] == [
        ("matches", "value_too_long")
    ]


def test_matches_on_a_number_is_false_and_traced():
    trace = ConditionTrace()
    assert _evaluate(_leaf("count", matches="3"), trace) is False
    assert len(trace.mismatches) == 1


@pytest.mark.parametrize(
    "pointer,comparison,expected",
    [
        ("text", {"equals": 13}, True),
        ("items", {"gt": 1}, True),
        ("no_items", {"lt": 1}, True),
        ("object", {"lte": 1}, True),
        ("empty_text", {"gte": 1}, False),
    ],
)
def test_length_of_strings_arrays_and_objects(pointer, comparison, expected):
    assert _evaluate(_leaf(pointer, length=comparison)) is expected


def test_length_of_a_number_is_false_and_traced():
    trace = ConditionTrace()
    assert _evaluate(_leaf("count", length={"gt": 0}), trace) is False
    assert len(trace.mismatches) == 1


def test_combinators_and_literal_sources():
    present = _leaf("text", present=True)
    missing = _leaf("missing", present=True)
    assert _evaluate({"all": [present, present]}) is True
    assert _evaluate({"all": [present, missing]}) is False
    assert _evaluate({"any": [missing, present]}) is True
    assert _evaluate({"any": [missing]}) is False
    assert _evaluate({"not": missing}) is True
    assert _evaluate({"literal": 5, "gt": 4}) is True
    assert _evaluate({"literal": None, "present": True}) is False


def test_first_of_source_selects_the_first_present_member():
    source = {"first_of": [{"pointer": "/payload/none"}, {"pointer": "/payload/count"}]}
    assert _evaluate({"binding": source, "equals": 3}) is True
    absent = {"first_of": [{"pointer": "/payload/none"}, {"pointer": "/payload/missing"}]}
    assert _evaluate({"binding": absent, "present": True}) is False


def test_all_short_circuits_in_authored_order():
    trace = ConditionTrace()
    document = {"all": [_leaf("missing", present=True), _leaf("text", gt=1)]}
    assert _evaluate(document, trace) is False
    assert trace.mismatches == []


@pytest.mark.parametrize(
    "document",
    [
        {"binding": {"pointer": "/x"}},
        {"binding": {"pointer": "/x"}, "present": True, "empty": True},
        {"present": True},
        {"binding": {"pointer": "/x"}, "literal": 1, "present": True},
        {"binding": {"pointer": "/x", "default": 1}, "present": True},
        {"binding": {"pointer": "x"}, "present": True},
        {"binding": {"pointer": "/x"}, "present": None},
        {"binding": {"pointer": "/x"}, "present": "yes"},
        {"binding": {"pointer": "/x"}, "in": []},
        {"binding": {"pointer": "/x"}, "in": list(range(65))},
        {"binding": {"pointer": "/x"}, "gt": True},
        {"binding": {"pointer": "/x"}, "gt": "3"},
        {"binding": {"pointer": "/x"}, "matches": "("},
        {"binding": {"pointer": "/x"}, "matches": "a" * 257},
        {"binding": {"pointer": "/x"}, "length": {}},
        {"binding": {"pointer": "/x"}, "length": {"gt": 1, "lt": 3}},
        {"binding": {"pointer": "/x"}, "length": {"gt": -1}},
        {"binding": {"pointer": "/x"}, "unknown": 1},
        {"all": []},
        {"any": [{"binding": {"pointer": "/x"}, "present": True}] * 33},
        {"not": {"binding": {"pointer": "/x"}}},
        {"binding": {"first_of": []}, "present": True},
    ],
)
def test_invalid_conditions_are_rejected(document):
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(document, strict=True)


def test_nesting_is_bounded_to_eight_levels():
    condition: dict[str, object] = {"binding": {"pointer": "/x"}, "present": True}
    for _ in range(7):
        condition = {"not": condition}
    _ADAPTER.validate_python(condition, strict=True)
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"all": [condition]}, strict=True)


def test_json_equality_distinguishes_booleans_numbers_and_strings():
    assert json_equal(1, 1.0)
    assert not json_equal(True, 1)
    assert not json_equal("1", 1)
    assert not json_equal(None, False)
    assert json_equal(freeze_json({"a": [1, None]}), freeze_json({"a": [1.0, None]}))
    assert not json_equal(freeze_json({"a": 1}), freeze_json({"a": 1, "b": 2}))


def test_malformed_plan_is_a_configuration_error():
    plan = LeafConditionPlan(BindingPlan(kind="pointer", pointer="/x"), "gt", "not-a-number")
    with pytest.raises(ServiceError) as error:
        evaluate_condition(plan, freeze_json({"x": 1}))
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


def test_description_contains_pointers_and_operators_but_no_operands():
    authored = _ADAPTER.validate_python(
        {
            "all": [
                {"binding": {"pointer": "/payload/status"}, "equals": "PRIVATE-VALUE"},
                {"not": {"binding": {"pointer": "/payload/items"}, "length": {"gt": 3}}},
                {"literal": "PRIVATE-LITERAL", "present": True},
            ]
        },
        strict=True,
    )
    text = describe_condition(compile_condition(authored, _LOCATION))  # type: ignore[arg-type]
    assert text == (
        "all(/payload/status equals; not(/payload/items length gt); literal present=true)"
    )
    assert "PRIVATE" not in text


@pytest.mark.parametrize(
    "operator,expected",
    [
        ({"present": True}, "/payload/text present=true"),
        ({"present": False}, "/payload/text present=false"),
        ({"empty": True}, "/payload/text empty=true"),
        ({"empty": False}, "/payload/text empty=false"),
    ],
)
def test_description_keeps_the_polarity_of_present_and_empty(operator, expected):
    authored = _ADAPTER.validate_python(_leaf("text", **operator), strict=True)
    assert describe_condition(compile_condition(authored, _LOCATION)) == expected  # type: ignore[arg-type]


_SAFE_PATTERNS = [
    "FOI-[0-9]{4}-[0-9]{4}",
    ".*account A-[0-9]+.*",
    r"\d+(?:\.\d+)?",
    r"\d+(?:\.\d++)?",
    "[a-z]+(?:-[a-z]+){0,3}",
    "(a{1,2}){1,4}",
    r"(\s*x)?",
    "(?:foo|bar)+",
    "[ab]*",
    "(a|b)*",
    r"(\d{1,3}\.){3}\d{1,3}",
    "(?:(?>a|ab))*",
    "(?:(?=.*x)a)*",
    "(a+)++b",
    "(foo|bar)",
    r"\w+@\w+\.\w+",
]
_UNSAFE_PATTERNS = [
    "(a+)+",
    "(a*)*b",
    r"((?:\.\d+)?)*",
    "(a|ab)*",
    "(?:ab|ab)+",
    "(a{1,2})*",
    "([a-z]{2,3})+",
    "(?:a{1,2}){1,40}b",
    "(a{1,2}){1,40}",
    "a*a*a*a*b",
    "(?>(a+)+c)",
]
_ADVERSARIAL = [
    "a" * MAX_MATCH_LENGTH,
    "a" * (MAX_MATCH_LENGTH - 1) + "!",
    "1" * (MAX_MATCH_LENGTH - 1) + "!",
    "1." * (MAX_MATCH_LENGTH // 2),
    "a-" * (MAX_MATCH_LENGTH // 2),
    "a-" * (MAX_MATCH_LENGTH // 2 - 1) + "a!",
    " " * (MAX_MATCH_LENGTH - 1) + "!",
]


def _slowest(pattern: str) -> float:
    import time

    authored = _ADAPTER.validate_python(_leaf("value", matches=pattern), strict=True)
    plan = compile_condition(authored, _LOCATION)  # type: ignore[arg-type]
    slowest = 0.0
    for value in _ADVERSARIAL:
        context = freeze_json({"payload": {"value": value}})
        best = float("inf")
        for _ in range(3):  # Best of three: measure the matcher, not scheduler noise.
            started = time.perf_counter()
            evaluate_condition(plan, context)
            best = min(best, time.perf_counter() - started)
        slowest = max(slowest, best)
    return slowest


@pytest.mark.parametrize("pattern", [r"\d+(?:\.\d+)?", "[a-z]+(?:-[a-z]+){0,3}", "(a{1,2}){1,4}"])
def test_bounded_outer_quantifiers_match_adversarial_values_within_50_ms(pattern):
    assert _slowest(pattern) <= 0.05


@pytest.mark.parametrize("pattern", _SAFE_PATTERNS)
def test_bounded_patterns_are_accepted_and_match_adversarial_values_quickly(pattern):
    assert _slowest(pattern) < 1.0


@pytest.mark.parametrize("pattern", _UNSAFE_PATTERNS)
def test_catastrophic_backtracking_patterns_are_rejected(pattern):
    authored = _ADAPTER.validate_python(_leaf("value", matches=pattern), strict=True)
    with pytest.raises(CompilationError) as error:
        compile_condition(authored, _LOCATION)  # type: ignore[arg-type]
    assert error.value.reason == "unsafe_pattern"
    assert error.value.field == "matches"
    assert pattern not in str(error.value) + (error.value.hint or "")
