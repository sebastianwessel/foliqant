"""Callable collection contracts, graph safety and scoped offline compilation."""

from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.test_compiler import _compile, _fails, _flow, _handler, _workflow, _write

from foliqant.contracts.workflow import FlowCollectionStepAuthoring, WorkflowAuthoring
from foliqant.core.plan import FlowCollectionStepPlan


def _collection(**extra):
    return {"type": "flow_collection", "items": {"literal": []}, "flows": ["child"], **extra}


def _callable(*steps, **definition):
    return {
        "callable": True,
        "definition": {
            "steps": [
                {"id": f"step{i}", "definition": step}
                for i, step in enumerate(steps or (_handler(),))
            ],
            **definition,
        },
    }


def _document(tmp_path, **extra):
    return _workflow(tmp_path, {"first": _flow(_collection()), "child": _callable()}, **extra)


def test_collection_compiles_and_only_routed_start_is_inferred(tmp_path):
    document = _document(tmp_path)
    del document["start"]
    _write(tmp_path, "workflow.yaml", document)
    plan = _compile(tmp_path)
    assert plan.start == "first"
    child = plan.flow("child")
    assert child.callable and child.input == () and child.transition is None
    assert child.on_unresolved is None
    step = plan.flow("first").steps[0]
    assert isinstance(step, FlowCollectionStepPlan)
    assert step.flows == ("child",) and step.max_items == 32
    assert step.input == (("items", step.items),)


@pytest.mark.parametrize(
    "extra",
    [
        {"flows": []},
        {"flows": ["child", "child"]},
        {"flows": ["invalid-id"]},
        {"max_items": 0},
        {"max_items": 1025},
        {"max_items": True},
        {"max_items": "2"},
        {"items": {"literal": [], "pointer": "/payload"}},
    ],
)
def test_closed_collection_authoring_rejects_invalid_contract(extra):
    with pytest.raises(ValidationError):
        FlowCollectionStepAuthoring.model_validate(_collection(**extra), strict=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input", {}),
        ("transition", {"outcome": "completed"}),
        ("on_unresolved", {"outcome": "needs_review"}),
        ("callable", False),
        ("callable", 1),
    ],
)
def test_callable_authoring_forbids_routed_fields(tmp_path, field, value):
    document = _document(tmp_path)
    document["flows"]["child"][field] = value
    with pytest.raises(ValidationError):
        TypeAdapter(WorkflowAuthoring).validate_python(document, strict=True)


@pytest.mark.parametrize("targets", [["unknown"], ["first"], ["child", "first"]])
def test_collection_allowlist_requires_known_callable_targets(tmp_path, targets):
    _workflow(tmp_path, {"first": _flow(_collection(flows=targets)), "child": _callable()})
    _fails(tmp_path, "invalid_callable_flow")


@pytest.mark.parametrize("field", ["transition", "on_unresolved"])
def test_routes_cannot_enter_callable_flow(tmp_path, field):
    _workflow(
        tmp_path,
        {"first": _flow(_collection(), **{field: {"flow": "child"}}), "child": _callable()},
    )
    _fails(tmp_path, "invalid_routed_flow")


def test_callable_cannot_be_start(tmp_path):
    _document(tmp_path, start="child")
    _fails(tmp_path, "missing_start")


@pytest.mark.parametrize("indirect", [False, True])
def test_recursive_calls_are_rejected_even_when_items_are_empty(tmp_path, indirect):
    flows = {
        "first": _flow(_collection()),
        "child": _callable(_collection(flows=["other" if indirect else "child"])),
    }
    if indirect:
        flows["other"] = _callable(_collection())
    _workflow(tmp_path, flows)
    _fails(tmp_path, "workflow_cycle")


def test_unreferenced_callable_is_rejected(tmp_path):
    _workflow(tmp_path, {"first": _flow(), "child": _callable()})
    _fails(tmp_path, "unreachable_flow")


@pytest.mark.parametrize("optional", [False, True])
def test_callable_cannot_be_read_as_root_flow_record(tmp_path, optional):
    pointer = {"pointer": "/flows/child/result"}
    if optional:
        pointer.update(default=None)
    _document(tmp_path, output=pointer)
    _fails(tmp_path, "invalid_flow_reference")


def test_callable_calls_do_not_affect_routed_dominance(tmp_path):
    _workflow(
        tmp_path,
        {
            "first": _flow(_collection(), transition={"flow": "last"}),
            "child": _callable(),
            "last": _flow(input={"earlier": {"pointer": "/flows/first/result"}}),
        },
        output={"pointer": "/flows/last/result", "default": None},
    )
    _compile(tmp_path)


def test_callable_conventional_flow_and_step_discovery_and_revision(tmp_path):
    _workflow(tmp_path, {"first": _flow(_collection()), "child": {"callable": True}})
    _write(tmp_path, "child/flow.yaml", {"steps": ["work"]})
    step = _write(tmp_path, "child/work.step.yaml", _handler())
    before = _compile(tmp_path)
    assert before.flow("child").step("work").location.path == "child/work.step.yaml"
    step.write_text(step.read_text() + "\n# semantic dependency provenance\n")
    assert _compile(tmp_path).revision != before.revision


def test_collection_binding_scope_and_known_array_type(tmp_path):
    document = _document(tmp_path)
    definition = document["flows"]["first"]["definition"]
    definition["input_schema"] = {"type": "object", "properties": {"items": {"type": "string"}}}
    definition["steps"][0]["definition"]["items"] = {"pointer": "/payload/items"}
    _write(tmp_path, "workflow.yaml", document)
    _fails(tmp_path, "incompatible_binding_type")
    definition["steps"][0]["definition"]["items"] = {"pointer": "/flows/child/result"}
    _write(tmp_path, "workflow.yaml", document)
    _fails(tmp_path, "dangling_pointer")


def test_collection_known_result_ledger_schema(tmp_path):
    document = _document(tmp_path)
    definition = document["flows"]["first"]["definition"]
    definition["steps"].append(
        {
            "id": "after",
            "definition": _handler(
                input={"selected": {"pointer": "/steps/step0/result/items/0/flow"}}
            ),
        }
    )
    _write(tmp_path, "workflow.yaml", document)
    _compile(tmp_path)
    bad = deepcopy(document)
    bad["flows"]["first"]["definition"]["steps"][1]["definition"]["input"]["selected"] = {
        "pointer": "/steps/step0/result/not_a_field"
    }
    _write(tmp_path, "workflow.yaml", bad)
    _fails(tmp_path, "dangling_pointer")


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "a", "flow": "child", "input": {}}, {"id": "a", "flow": "child", "input": {}}],
        [{"id": "invalid-id", "flow": "child", "input": {}}],
        [{"id": "a", "flow": "first", "input": {}}],
        [{"id": "a", "flow": "child", "input": []}],
        [{"id": "a", "flow": "child"}],
        [{"id": "a", "flow": "child", "input": {}, "extra": True}],
    ],
)
def test_collection_known_literal_batch_rejected_before_runtime(tmp_path, items):
    _workflow(
        tmp_path, {"first": _flow(_collection(items={"literal": items})), "child": _callable()}
    )
    _fails(tmp_path, "invalid_collection_items")


def test_literal_max_items_and_optional_default_are_checked(tmp_path):
    items = [{"id": name, "flow": "child", "input": {}} for name in ["a", "b"]]
    _workflow(
        tmp_path,
        {"first": _flow(_collection(items={"literal": items}, max_items=1)), "child": _callable()},
    )
    _fails(tmp_path, "invalid_collection_items")
    _workflow(
        tmp_path,
        {
            "first": _flow(
                _collection(
                    items={"pointer": "/payload/optional", "default": items},
                    max_items=1,
                )
            ),
            "child": _callable(),
        },
    )
    _fails(tmp_path, "invalid_collection_items")


def test_callable_literal_input_schema_uses_confined_relative_refs(tmp_path):
    items = [{"id": "a", "flow": "child", "input": {"count": 4}}]
    _workflow(
        tmp_path,
        {"first": _flow(_collection(items={"literal": items})), "child": {"callable": True}},
    )
    _write(
        tmp_path,
        "child/flow.yaml",
        {"input_schema": "input.json", "steps": [{"id": "work", "definition": _handler()}]},
    )
    _write(
        tmp_path,
        "child/input.json",
        '{"type":"object","required":["count"],'
        '"properties":{"count":{"$ref":"count.json"}},"additionalProperties":false}',
    )
    _write(tmp_path, "child/count.json", '{"type":"integer","minimum":2}')
    _compile(tmp_path)
    items[0]["input"]["count"] = 1
    _workflow(
        tmp_path,
        {"first": _flow(_collection(items={"literal": items})), "child": {"callable": True}},
    )
    _fails(tmp_path, "invalid_collection_input")


def test_nested_acyclic_callable_collections_and_shared_targets(tmp_path):
    _workflow(
        tmp_path,
        {
            "first": _flow(_collection(flows=["child", "leaf"])),
            "child": _callable(_collection(flows=["leaf"])),
            "leaf": _callable(),
        },
    )
    plan = _compile(tmp_path)
    assert [flow.name for flow in plan.flows] == ["first", "child", "leaf"]


def test_callable_local_scope_cannot_capture_parent_steps(tmp_path):
    _workflow(
        tmp_path,
        {
            "first": _flow(_collection()),
            "child": _callable(_handler(input={"parent": {"pointer": "/steps/parent/result"}})),
        },
    )
    _fails(tmp_path, "dangling_pointer")


def test_callable_input_schema_must_accept_object_inputs(tmp_path):
    _workflow(
        tmp_path,
        {
            "first": _flow(_collection()),
            "child": _callable(input_schema={"type": "string"}),
        },
    )
    _fails(tmp_path, "incompatible_binding_type")


def test_collection_child_step_ledger_is_an_object(tmp_path):
    document = _document(tmp_path)
    definition = document["flows"]["first"]["definition"]
    definition["steps"].append(
        {
            "id": "after",
            "definition": _handler(
                input={"child": {"pointer": "/steps/step0/result/items/0/steps/step0/result"}}
            ),
        }
    )
    _write(tmp_path, "workflow.yaml", document)
    _compile(tmp_path)


def test_collection_kind_marker_has_a_known_string_schema(tmp_path):
    document = _document(tmp_path)
    definition = document["flows"]["first"]["definition"]
    definition["steps"].append(
        {
            "id": "after",
            "definition": _handler(
                input={
                    "kind": {"pointer": "/steps/step0/kind"},
                    "partial": {
                        "pointer": "/steps/step0/partial_result/items",
                        "default": [],
                    },
                }
            ),
        }
    )
    _write(tmp_path, "workflow.yaml", document)
    _compile(tmp_path)


@pytest.mark.parametrize("levels,valid", [(16, True), (17, False)])
def test_callable_nesting_has_a_fixed_supported_bound(tmp_path, levels, valid):
    flows = {"first": _flow(_collection(flows=["child0"]))}
    for index in range(levels):
        flows[f"child{index}"] = _callable(
            *([_collection(flows=[f"child{index + 1}"])] if index + 1 < levels else [])
        )
    _workflow(tmp_path, flows)
    if valid:
        assert len(_compile(tmp_path).flows) == 17
    else:
        _fails(tmp_path, "collection_depth_exceeded")


def test_routed_transitions_do_not_count_as_collection_nesting(tmp_path):
    flows = {f"route{i}": _flow(transition={"flow": f"route{i + 1}"}) for i in range(20)}
    flows["route20"] = _flow(_collection())
    flows["child"] = _callable()
    _workflow(tmp_path, flows, start="route0")
    _compile(tmp_path)
