"""Offline compilation acceptance for explicit workflow/flow/step authoring."""

import json
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from foliqant.compiler import CompilationError, compile_workflow
from foliqant.compiler.models import ModelRegistry
from foliqant.contracts.models import OpenAIModelConfig
from foliqant.contracts.workflow import Binding, FlowDefinition, WorkflowAuthoring
from foliqant.core.json import freeze_json
from foliqant.core.plan import DecisionStepPlan, LlmStepPlan, MatchRoutingPlan
from foliqant.core.prompt import render_prompt


def _write(root: Path, path: str, value: Any) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value if isinstance(value, str) else yaml.safe_dump(value, sort_keys=False))
    return target


def _handler(**extra: Any) -> dict[str, Any]:
    return {"type": "handler", "handler": "echo", "input": {}, **extra}


def _llm(**extra: Any) -> dict[str, Any]:
    return {"type": "llm", "instructions": "Summarize.", "input": {}, "output": "text", **extra}


def test_llm_iteration_limit_compiles_and_changes_revision(tmp_path: Path) -> None:
    _workflow(tmp_path, {"first": _flow(_llm())})
    default_plan = _compile(tmp_path)
    default_step = default_plan.flow("first").steps[0]
    assert isinstance(default_step, LlmStepPlan)
    assert default_step.max_iterations == 4

    _workflow(tmp_path, {"first": _flow(_llm(max_iterations=2))})
    limited_plan = _compile(tmp_path)
    limited_step = limited_plan.flow("first").steps[0]
    assert isinstance(limited_step, LlmStepPlan)
    assert limited_step.max_iterations == 2
    assert limited_plan.revision != default_plan.revision

    for invalid in (0, 1025, True, 1.5):
        _workflow(tmp_path, {"first": _flow(_llm(max_iterations=invalid))})
        _fails(tmp_path)


def _decision(**extra: Any) -> dict[str, Any]:
    return {
        "type": "decision",
        "instructions": "Choose a queue.",
        "sources": {"message": {"pointer": "/payload/message"}},
        "question": {
            "type": "choice",
            "criteria": ["Choose the applicable queue."],
            "catalog": {
                "categories": [
                    {"id": "Billing Issue", "description": "Billing"},
                    {"id": "Technical", "description": "Technical"},
                ]
            },
        },
        **extra,
    }


def _flow(*steps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "input": {},
        "definition": {
            "steps": [
                {"id": f"step{index}", "definition": step}
                for index, step in enumerate(steps or (_handler(),))
            ]
        },
        "transition": {"outcome": "completed"},
        **extra,
    }


def _workflow(root: Path, flows: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    document = {
        "name": "demo",
        "start": "first",
        "defaults": {"model": "local"},
        "flows": flows or {"first": _flow()},
        **extra,
    }
    _write(root, "workflow.yaml", document)
    return document


def _compile(root: Path, **extra: Any) -> Any:
    return compile_workflow(
        root, model_aliases={"local": "model"}, tool_catalogs={}, handler_names={"echo"}, **extra
    )


def _fails(root: Path, reason: str | None = None, **extra: Any) -> CompilationError:
    with pytest.raises(CompilationError) as caught:
        _compile(root, **extra)
    if reason is not None:
        assert caught.value.reason == reason
    assert str(caught.value) == "The workflow configuration is invalid."
    return caught.value


def test_inline_sequence_is_frozen_and_order_does_not_follow_ids(tmp_path: Path) -> None:
    flow = _flow()
    flow["definition"]["steps"] = [
        {"id": "z_first", "definition": _handler()},
        {
            "id": "a_second",
            "definition": _handler(input={"prior": {"pointer": "/steps/z_first/result"}}),
        },
    ]
    _workflow(tmp_path, {"first": flow})
    plan = _compile(tmp_path)
    assert plan == _compile(tmp_path)
    assert len(plan.revision) == 64
    assert [step.name for step in plan.flow("first").steps] == ["z_first", "a_second"]
    with pytest.raises(AttributeError):
        plan.flow("first").name = "changed"
    with pytest.raises(KeyError):
        plan.flow("missing")
    with pytest.raises(KeyError):
        plan.flow("first").step("missing")


def test_explicit_markdown_definition_retains_source_and_question_identity(tmp_path: Path) -> None:
    _write(
        tmp_path, "triage/flow.yaml", {"steps": [{"id": "assess", "definition": "assets/step.md"}]}
    )
    step = _decision()
    instructions = step.pop("instructions")
    _write(
        tmp_path, "triage/assets/step.md", "---\n" + yaml.safe_dump(step) + "---\n" + instructions
    )
    _workflow(tmp_path, {"first": _flow(definition="triage/flow.yaml")})
    decision = _compile(tmp_path).flow("first").step("assess")
    assert isinstance(decision, DecisionStepPlan)
    assert decision.questions[0].id == "assess"
    assert [option.id for option in decision.questions[0].options] == ["billing_issue", "technical"]
    assert decision.location.path == "triage/assets/step.md"
    assert decision.instructions == instructions


def test_inline_and_file_definitions_share_one_contract(tmp_path: Path) -> None:
    _write(tmp_path, "item.yaml", _handler())
    _workflow(tmp_path, {"first": _flow()})
    inline = _compile(tmp_path).flow("first").steps[0]
    _workflow(
        tmp_path,
        {"first": _flow(definition={"steps": [{"id": "step0", "definition": "item.yaml"}]})},
    )
    external = _compile(tmp_path).flow("first").steps[0]
    assert inline == replace(external, location=inline.location)
    _write(tmp_path, "ignored.yaml", "!unsafe")
    _write(tmp_path, "steps/ignored.yaml", "!unsafe")
    assert _compile(tmp_path).flow("first").steps[0] == external


def test_binding_discriminator_requires_explicit_default_and_preserves_null() -> None:
    adapter = TypeAdapter(Binding)
    value = adapter.validate_python({"pointer": "/payload/missing", "default": None})
    assert value.model_fields_set == {"pointer", "default"}
    adapter.validate_python({"literal": {"pointer": "literal data"}})
    for invalid in (
        {"pointer": "/x", "optional": True},
        {"pointer": "/x", "optional": True, "default": None},
        {"pointer": "bad"},
        {"pointer": "/bad~2name"},
        {"pointer": "/x", "literal": 1},
        {"literal": "data", "format": "json"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


@pytest.mark.parametrize("field", ["version", "unknown_setting"])
def test_workflow_rejects_unknown_fields(tmp_path: Path, field: str) -> None:
    _workflow(tmp_path, **{field: 1})
    _fails(tmp_path, "unknown_field")


@pytest.mark.parametrize("field", ["next", "on_answer", "on_unresolved", "name"])
def test_step_routes_and_implicit_names_are_rejected(tmp_path: Path, field: str) -> None:
    _workflow(tmp_path, {"first": _flow(_handler(**{field: "other"}))})
    _fails(tmp_path, "unknown_field")


@pytest.mark.parametrize("kind", ["finish", "dispatch", "workflow"])
def test_nonoperation_steps_are_rejected(tmp_path: Path, kind: str) -> None:
    _workflow(tmp_path, {"first": _flow({"type": kind})})
    _fails(tmp_path, "invalid_contract")


def test_step_files_without_explicit_flows_are_not_loaded(tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", {"name": "demo", "start": "item"})
    _write(tmp_path, "steps/item.yaml", _handler())
    _fails(tmp_path, "invalid_contract")


def test_duplicate_ids_empty_sequences_and_unordered_steps_are_rejected() -> None:
    for steps in ([], {"a": _handler()}, [{"id": "a", "definition": _handler()}] * 2):
        with pytest.raises(ValidationError):
            FlowDefinition.model_validate({"steps": steps})


@pytest.mark.parametrize(
    "source", ["name: first\nname: second", "name: !unsafe first", "x: &x [*x]"]
)
def test_yaml_rejects_duplicates_tags_and_aliases_safely(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "workflow.yaml", source)
    error = _fails(tmp_path, "invalid_yaml")
    assert error.location.path == "workflow.yaml"


def test_contract_diagnostics_do_not_echo_unknown_fields_or_values(tmp_path: Path) -> None:
    _workflow(tmp_path, secret_sentinel="secret_value")
    error = _fails(tmp_path, "unknown_field")
    assert error.field == "*"
    assert "secret" not in str(vars(error))


@pytest.mark.parametrize(
    "target,reason", [("first", "workflow_cycle"), ("missing", "missing_flow")]
)
def test_flow_cycles_and_missing_targets_fail_offline(
    tmp_path: Path, target: str, reason: str
) -> None:
    _workflow(tmp_path, {"first": _flow(transition={"flow": target})})
    _fails(tmp_path, reason)


def test_unreachable_declared_flow_is_rejected(tmp_path: Path) -> None:
    _workflow(tmp_path, {"first": _flow(), "unused": _flow()})
    _fails(tmp_path, "unreachable_flow")


def test_unresolved_routes_participate_in_graph_and_cannot_directly_complete(
    tmp_path: Path,
) -> None:
    _workflow(
        tmp_path,
        {
            "first": _flow(
                on_unresolved={
                    "default": {"flow": "review"},
                    "conflicting_information": {"outcome": "needs_review"},
                }
            ),
            "review": _flow(),
        },
    )
    assert len(_compile(tmp_path).flows) == 2
    for route in (
        {"outcome": "completed"},
        {"default": {"outcome": "completed"}},
        {
            "default": {"outcome": "needs_review"},
            "multiple_valid_options": {"outcome": "completed"},
        },
    ):
        document = _workflow(tmp_path, {"first": _flow(on_unresolved=route)})
        with pytest.raises(ValidationError):
            WorkflowAuthoring.model_validate(document)


def test_exact_routes_require_a_default_and_do_not_reserve_terminal_ids(tmp_path: Path) -> None:
    route = {
        "binding": {"literal": "billing"},
        "cases": {"billing": {"flow": "completed"}},
        "default": {"outcome": "needs_review"},
    }
    _workflow(tmp_path, {"first": _flow(transition=route), "completed": _flow()})
    assert isinstance(_compile(tmp_path).flow("first").transition, MatchRoutingPlan)
    del route["default"]
    _workflow(tmp_path, {"first": _flow(transition=route), "completed": _flow()})
    _fails(tmp_path, "invalid_contract")


@pytest.mark.parametrize("value", [True, 3, [], {}])
def test_known_nonstring_routes_are_rejected(tmp_path: Path, value: Any) -> None:
    _workflow(
        tmp_path,
        {
            "first": _flow(
                transition={
                    "binding": {"literal": value},
                    "cases": {"yes": {"outcome": "completed"}},
                    "default": {"outcome": "needs_review"},
                }
            )
        },
    )
    _fails(tmp_path, "incompatible_route_type")


@pytest.mark.parametrize("pointer", ["/flows/first/result", "/steps/step1/result"])
def test_step_scope_rejects_cross_flow_or_unavailable_local_results(
    tmp_path: Path, pointer: str
) -> None:
    _workflow(
        tmp_path, {"first": _flow(_handler(input={"value": {"pointer": pointer}}), _handler())}
    )
    _fails(
        tmp_path,
        "dangling_pointer" if pointer.startswith("/flows") else "unavailable_step_reference",
    )


def test_optional_future_local_record_and_early_review_output_are_explicit(tmp_path: Path) -> None:
    flow = _flow(
        _handler(input={"later": {"pointer": "/steps/step1/result", "default": None}}),
        _handler(),
    )
    flow["definition"]["output"] = {"pointer": "/steps/step1/result"}
    _workflow(tmp_path, {"first": flow})
    _fails(tmp_path, "incompatible_output_binding")
    flow["definition"]["output"].update(default=None)
    _workflow(tmp_path, {"first": flow})
    assert _compile(tmp_path).flow("first").output.has_default


@pytest.mark.parametrize(
    "pointer",
    [
        "/steps/step0/result",
        "/flows/first/steps/step0/result",
        "/flows/first",
        "/flows/missing/result",
    ],
)
def test_boundary_scope_exposes_only_declared_flow_results(tmp_path: Path, pointer: str) -> None:
    _workflow(tmp_path, {"first": _flow(input={"data": {"pointer": pointer}})})
    _fails(tmp_path)


def test_branch_specific_input_and_workflow_output_require_explicit_defaults(
    tmp_path: Path,
) -> None:
    route = {
        "binding": {"literal": "left"},
        "cases": {"left": {"flow": "left"}},
        "default": {"flow": "right"},
    }
    flows = {
        "first": _flow(transition=route),
        "left": _flow(transition={"flow": "join"}),
        "right": _flow(transition={"flow": "join"}),
        "join": _flow(input={"prior": {"pointer": "/flows/left/result"}}),
    }
    _workflow(tmp_path, flows)
    _fails(tmp_path, "unavailable_flow_reference")
    flows["join"]["input"]["prior"].update(default=None)
    _workflow(tmp_path, flows, output={"pointer": "/flows/join/result"})
    _fails(tmp_path, "unavailable_flow_reference")
    _workflow(tmp_path, flows, output={"pointer": "/flows/join/result", "default": None})
    _compile(tmp_path)


@pytest.mark.parametrize(
    "kind,path",
    [
        ("flow", "../outside.yaml"),
        ("flow", "/tmp/file.yaml"),
        ("flow", "https://example.test/flow.yaml"),
        ("step", "../outside.yaml"),
        ("step", "missing.yaml"),
        ("step", "step.txt"),
    ],
)
def test_definition_paths_are_explicit_confined_and_supported(
    tmp_path: Path, kind: str, path: str
) -> None:
    _write(tmp_path, "nested/flow.yaml", {"steps": [{"id": "run", "definition": path}]})
    definition = path if kind == "flow" else "nested/flow.yaml"
    _workflow(tmp_path, {"first": _flow(definition=definition)})
    _fails(tmp_path, "invalid_flow_path" if kind == "flow" else "invalid_step_path")


def test_shared_flow_definitions_use_config_scope_but_steps_use_flow_scope(tmp_path: Path) -> None:
    _write(tmp_path, "shared/flow.yaml", {"steps": [{"id": "run", "definition": "step.yaml"}]})
    _write(tmp_path, "shared/step.yaml", _handler())
    bundle = tmp_path / "workflow"
    _workflow(
        bundle,
        {
            "first": _flow(definition="../shared/flow.yaml", transition={"flow": "second"}),
            "second": _flow(definition="../shared/flow.yaml"),
        },
    )
    plan = _compile(bundle, configuration_root=tmp_path)
    assert [flow.name for flow in plan.flows] == ["first", "second"]
    assert plan.flow("first").step("run").name == plan.flow("second").step("run").name
    _fails(bundle, "invalid_flow_path")
    # An explicit step definition may resolve anywhere inside the configuration root.
    (tmp_path / "shared/step.yaml").unlink()
    _write(tmp_path, "elsewhere.yaml", _handler())
    (tmp_path / "shared/step.yaml").symlink_to(tmp_path / "elsewhere.yaml")
    _compile(bundle, configuration_root=tmp_path)
    # ...but never outside it.
    outside = tmp_path.parent / f"{tmp_path.name}-outside.yaml"
    outside.write_text(json.dumps(_handler()))
    try:
        (tmp_path / "shared/step.yaml").unlink()
        (tmp_path / "shared/step.yaml").symlink_to(outside)
        _fails(bundle, "invalid_step_path", configuration_root=tmp_path)
    finally:
        outside.unlink()


def test_explicit_step_definitions_are_shared_across_flow_folders(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "shared/correct.step.md",
        "---\ntype: llm\ninput: {}\noutput:\n  schema: correct.schema.json\n---\nCorrect it.\n",
    )
    _write(
        tmp_path,
        "shared/correct.schema.json",
        '{"type":"object","properties":{"value":{"$ref":"common.json#/$defs/value"}}}',
    )
    _write(tmp_path, "shared/common.json", '{"$defs":{"value":{"type":"string"}}}')
    for folder in ("lookup", "answer"):
        _write(
            tmp_path,
            f"workflow/{folder}/flow.yaml",
            {"steps": [{"id": "correct", "definition": "../../shared/correct.step.md"}]},
        )
    bundle = tmp_path / "workflow"
    _workflow(
        bundle,
        {
            "lookup": _flow(definition="lookup/flow.yaml", transition={"flow": "answer"}),
            "answer": _flow(definition="answer/flow.yaml"),
        },
        start="lookup",
    )
    plan = _compile(bundle, configuration_root=tmp_path)
    paths = {plan.flow(name).step("correct").output_schema_path for name in ("lookup", "answer")}
    assert paths == {"shared/correct.schema.json"}
    assert {item.path for item in plan.schema_resources} == {
        "shared/correct.schema.json",
        "shared/common.json",
    }
    assert plan.flow("lookup").step("correct").location.path == "shared/correct.step.md"
    # The shared step's own resources stay confined to the step's directory.
    _write(
        tmp_path,
        "shared/correct.schema.json",
        '{"type":"object","properties":{"value":{"$ref":"../workflow/x.json"}}}',
    )
    _write(tmp_path, "workflow/x.json", '{"type":"string"}')
    with pytest.raises(CompilationError):
        _compile(bundle, configuration_root=tmp_path)
    # Conventional discovery still looks only beside flow.yaml.
    _write(tmp_path, "workflow/lookup/flow.yaml", {"steps": ["correct"]})
    _fails(bundle, "missing_step_file", configuration_root=tmp_path)


def test_colocated_schema_bases_transitive_resources_and_exact_bytes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "flow/flow.yaml",
        {"steps": [{"id": "extract", "definition": "extract/step.yaml"}]},
    )
    _write(tmp_path, "flow/extract/step.yaml", _llm(output={"schema": "output.json"}))
    _write(tmp_path, "flow/extract/output.json", '{"$ref":"../shared.json#/$defs/value"}')
    _write(tmp_path, "flow/shared.json", '{"$defs":{"value":{"type":"string"}}}')
    _workflow(tmp_path, {"first": _flow(definition="flow/flow.yaml")})
    first = _compile(tmp_path)
    step = first.flow("first").step("extract")
    assert step.output_schema_path == "flow/extract/output.json"
    assert {item.path for item in first.schema_resources} == {
        "flow/extract/output.json",
        "flow/shared.json",
    }
    _write(tmp_path, "flow/shared.json", '{ "$defs": {"value": {"type": "string"}}}')
    assert _compile(tmp_path).revision != first.revision
    # Move the colocated step directory; its relative schema still resolves.
    (tmp_path / "flow/extract").rename(tmp_path / "flow/renamed")
    _write(
        tmp_path,
        "flow/flow.yaml",
        {"steps": [{"id": "extract", "definition": "renamed/step.yaml"}]},
    )
    assert _compile(tmp_path).flow("first").step("extract").name == "extract"


def test_inline_schema_refs_use_declaring_step_directory(tmp_path: Path) -> None:
    _write(tmp_path, "flow.yaml", {"steps": [{"id": "extract", "definition": "assets/step.yaml"}]})
    _write(tmp_path, "assets/step.yaml", _llm(output={"schema": {"$ref": "shape.json"}}))
    _write(tmp_path, "assets/shape.json", '{"type":"string"}')
    _workflow(tmp_path, {"first": _flow(definition="flow.yaml")})
    plan = _compile(tmp_path)
    assert {item.path for item in plan.schema_resources} == {
        "assets/shape.json",
        "assets/.foliqant-inline-step-first-extract.json",
    }


@pytest.mark.parametrize(
    "reference", ["https://example.test/schema.json", "../../../outside.json", "/tmp/outside.json"]
)
def test_schema_references_never_escape_or_open_remote_files(
    tmp_path: Path, reference: str
) -> None:
    _workflow(tmp_path, {"first": _flow(_llm(output={"schema": {"$ref": reference}}))})
    _fails(tmp_path, "invalid_schema_path")


def test_schema_annotations_are_data_until_referenced_as_schema(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "examples": [{"$id": "arbitrary", "$ref": "https://invalid.test"}],
        "properties": {"x": {"type": "string"}},
    }
    _workflow(tmp_path, {"first": _flow(_llm(output={"schema": schema}))})
    _compile(tmp_path)
    schema["$ref"] = "#/examples/0"
    _workflow(tmp_path, {"first": _flow(_llm(output={"schema": schema}))})
    _fails(tmp_path, "invalid_schema_path")


def test_reused_definition_and_schema_bytes_are_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "flow.yaml", {"steps": [{"id": "same", "definition": "step.yaml"}]})
    _write(tmp_path, "step.yaml", _llm(output={"schema": "schema.json"}))
    _write(tmp_path, "schema.json", '{"type":"string"}')
    _workflow(
        tmp_path,
        {
            "first": _flow(definition="flow.yaml", transition={"flow": "second"}),
            "second": _flow(definition="flow.yaml"),
        },
    )
    original = Path.read_bytes
    reads: Counter[Path] = Counter()

    def read(path: Path) -> bytes:
        reads[path] += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    _compile(tmp_path)
    assert set(reads.values()) == {1}
    assert {path.name for path in reads} == {
        "workflow.yaml",
        "flow.yaml",
        "step.yaml",
        "schema.json",
    }


def test_model_overrides_are_scoped_by_flow_and_keep_shared_admission(tmp_path: Path) -> None:
    profile = OpenAIModelConfig(provider="openai", model="base", api="chat", output_mode="native")
    registry = ModelRegistry({"local": profile})
    _workflow(
        tmp_path,
        {
            "first": _flow(
                _llm(model={"profile": "local", "model": "first_model"}),
                transition={"flow": "second"},
            ),
            "second": _flow(_llm(model={"profile": "local", "model": "second_model"})),
        },
    )
    plan = _compile(tmp_path, model_profiles={"local": profile}, _model_registry=registry)
    before = plan.flow("first").step("step0").model
    after = plan.flow("second").step("step0").model
    assert before != after
    assert registry.profiles[before].model == "first_model"
    assert registry.profiles[after].model == "second_model"
    assert registry.admission_groups[before] == registry.admission_groups[after] == "local"


def test_model_capabilities_are_checked_before_client_creation(tmp_path: Path) -> None:
    profile = OpenAIModelConfig(
        provider="openai",
        model="base",
        api="chat",
        output_mode="native",
        supports_json_schema=False,
    )
    _workflow(tmp_path, {"first": _flow(_llm(output={"schema": {"type": "string"}}))})
    _fails(tmp_path, "unsupported_model_capability", model_profiles={"local": profile})


def test_prompt_is_compiled_once_and_unknown_variables_fail_offline(tmp_path: Path) -> None:
    step = _llm(input={"message": {"literal": "Hi"}}, prompt="Message: {{ message }}")
    _workflow(tmp_path, {"first": _flow(step)})
    compiled = _compile(tmp_path).flow("first").steps[0]
    assert isinstance(compiled, LlmStepPlan)
    assert render_prompt(compiled.prompt, freeze_json({"message": "Hi"})) == 'Message: "Hi"'
    step["prompt"] = "{{ missing }}"
    _workflow(tmp_path, {"first": _flow(step)})
    _fails(tmp_path, "invalid_prompt")


def test_json_source_format_is_explicit_and_does_not_change_native_questions(
    tmp_path: Path,
) -> None:
    step = _decision(sources={"message": {"literal": {"prior": True}, "format": "json"}})
    _workflow(tmp_path, {"first": _flow(step)})
    compiled = _compile(tmp_path).flow("first").steps[0]
    assert compiled.source_formats == (("message", "json"),)
    assert compiled.questions[0].allowed_source_ids == ("message",)
    del step["sources"]["message"]["format"]
    _workflow(tmp_path, {"first": _flow(step)})
    _fails(tmp_path, "incompatible_binding_type")


def test_known_flow_input_mismatches_and_missing_keys_fail_offline(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    for inputs, reason in [
        ({}, "invalid_input_bindings"),
        ({"message": {"literal": 3}}, "incompatible_binding_type"),
        ({"message": {"literal": "ok"}, "extra": {"literal": True}}, "invalid_input_bindings"),
    ]:
        flow = _flow(input=inputs)
        flow["definition"]["input_schema"] = schema
        _workflow(tmp_path, {"first": flow})
        _fails(tmp_path, reason)


def test_markdown_cannot_have_two_instruction_sources(tmp_path: Path) -> None:
    _write(tmp_path, "step.md", "---\n" + yaml.safe_dump(_llm()) + "---\nOther instructions")
    _workflow(
        tmp_path, {"first": _flow(definition={"steps": [{"id": "call", "definition": "step.md"}]})}
    )
    _fails(tmp_path, "ambiguous_instructions")


def test_long_flow_graph_does_not_use_recursive_traversal(tmp_path: Path) -> None:
    flows = {
        f"flow{index}": _flow(
            transition={"flow": f"flow{index + 1}"} if index < 1049 else {"outcome": "completed"}
        )
        for index in range(1050)
    }
    _workflow(tmp_path, flows, start="flow0")
    assert len(_compile(tmp_path).flows) == 1050


def test_exact_read_bytes_define_revision_even_when_disk_changes_mid_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workflow(tmp_path, input_schema="schema.json")
    schema = _write(tmp_path, "schema.json", '{"const":"before"}')
    original = Path.read_bytes
    changed = False

    def mutate(path: Path) -> bytes:
        nonlocal changed
        data = original(path)
        if path == schema and not changed:
            changed = True
            schema.write_text('{"const":"after"}')
        return data

    monkeypatch.setattr(Path, "read_bytes", mutate)
    before = _compile(tmp_path)
    after = _compile(tmp_path)
    assert before.input_schema == {"const": "before"}
    assert after.input_schema == {"const": "after"}
    assert before.revision != after.revision


@pytest.mark.parametrize(
    "schema,reason",
    [
        ({"$ref": "#/missing"}, "invalid_schema_reference"),
        ({"$id": "local", "type": "object"}, "invalid_schema_path"),
        ({"$ref": "#/const", "const": 17}, "invalid_schema_reference"),
        ({"$ref": "#/default", "default": {"$ref": "https://invalid.test"}}, "invalid_schema_path"),
    ],
)
def test_schema_fragments_and_referenced_annotations_are_validated(
    tmp_path: Path, schema: dict[str, Any], reason: str
) -> None:
    _workflow(tmp_path, input_schema=schema)
    _fails(tmp_path, reason)


def test_schema_symlinks_cannot_leave_flow_bundle(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "flow/flow.yaml",
        {"steps": [{"id": "extract", "definition": _llm(output={"schema": "output.json"})}]},
    )
    outside = _write(tmp_path, "outside.json", '{"type":"string"}')
    (tmp_path / "flow/output.json").symlink_to(outside)
    _workflow(tmp_path, {"first": _flow(definition="flow/flow.yaml")})
    _fails(tmp_path, "invalid_schema_path")


@pytest.mark.parametrize("depth", [70, 600])
def test_deep_schema_and_literal_values_fail_with_safe_errors(tmp_path: Path, depth: int) -> None:
    nested = "[" * depth + "null" + "]" * depth
    _workflow(tmp_path)
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(workflow.read_text() + f"output: {{literal: {nested}}}\n")
    _fails(tmp_path)
    schema: dict[str, Any] = {"type": "string"}
    for _ in range(70):
        schema = {"properties": {"child": schema}}
    _write(tmp_path, "schema.json", json.dumps(schema))
    _workflow(tmp_path, input_schema="schema.json")
    _fails(tmp_path, "invalid_json_schema")


def test_remote_schema_reference_is_rejected_before_any_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline compiler must not open a socket")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    _workflow(tmp_path, input_schema={"$ref": "https://invalid.example/private"})
    _fails(tmp_path, "invalid_schema_path")


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://invalid.test/tool.json"},
        {"$ref": "#/default", "default": {"$ref": "https://invalid.test"}},
        {"$ref": "#/const", "const": 17},
        {"type": "unsupported"},
    ],
)
def test_declared_tool_schema_validation_cannot_be_bypassed(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    _workflow(tmp_path)
    catalog = {"tools": {"lookup": {"input_schema": schema, "effect": "read"}}}
    with pytest.raises(CompilationError) as caught:
        compile_workflow(
            tmp_path, model_aliases={}, tool_catalogs={"tools": catalog}, handler_names={"echo"}
        )
    assert caught.value.reason == "invalid_tool_schema"


def test_named_tool_choice_and_declared_capabilities_are_exact(tmp_path: Path) -> None:
    tool = {"input_schema": {"type": "object"}, "effect": "read"}
    catalog = {"tools": {"lookup": tool, "auto": tool}}
    step = _llm(tools={"server": "tools", "allow": ["lookup"], "choice": {"name": "auto"}})
    _workflow(tmp_path, {"first": _flow(step)})
    with pytest.raises(CompilationError) as caught:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={"tools": catalog},
            handler_names=set(),
        )
    assert caught.value.reason == "unknown_tool"
    step["tools"]["allow"].append("auto")
    _workflow(tmp_path, {"first": _flow(step)})
    compiled = (
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={"tools": catalog},
            handler_names=set(),
        )
        .flow("first")
        .steps[0]
    )
    assert compiled.tools.choice_mode == "named"
    assert compiled.tools.choice_name == "auto"


@pytest.mark.parametrize(
    "inputs,reason",
    [
        ({}, "invalid_input_bindings"),
        ({"value": {"literal": 3}}, "incompatible_binding_type"),
        ({"value": {"literal": "ok"}, "other": {"literal": 1}}, "invalid_input_bindings"),
    ],
)
def test_required_closed_mcp_arguments_are_checked_offline(
    tmp_path: Path, inputs: Any, reason: str
) -> None:
    tool = {
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "effect": "read",
    }
    _workflow(
        tmp_path,
        {"first": _flow({"type": "mcp", "server": "tools", "tool": "lookup", "arguments": inputs})},
    )
    with pytest.raises(CompilationError) as caught:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={"tools": {"tools": {"lookup": tool}}},
            handler_names=set(),
        )
    assert caught.value.reason == reason


def test_handler_schemas_enforce_inputs_outputs_and_revision_identity(tmp_path: Path) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Schemas:
        input_schema: Any
        output_schema: Any

    registered = Schemas(
        freeze_json(
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        ),
        freeze_json(
            {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
    )
    flow = _flow(_handler(input={"value": {"literal": "bad"}}))
    _workflow(tmp_path, {"first": flow})
    _fails(tmp_path, "incompatible_binding_type", handler_schemas={"echo": registered})
    flow["definition"]["steps"][0]["definition"]["input"]["value"] = {"literal": 1}
    flow["definition"]["output"] = {"pointer": "/steps/step0/result/absent"}
    _workflow(tmp_path, {"first": flow})
    _fails(tmp_path, "dangling_pointer", handler_schemas={"echo": registered})
    flow["definition"]["output"] = {"pointer": "/steps/step0/result/message"}
    _workflow(tmp_path, {"first": flow})
    before = _compile(tmp_path, handler_schemas={"echo": registered})
    after = _compile(
        tmp_path,
        handler_schemas={
            "echo": replace(registered, output_schema=freeze_json({"type": "object"}))
        },
    )
    assert before.revision != after.revision


@pytest.mark.parametrize(
    "schema,path",
    [
        ({"type": "string"}, "/child"),
        ({"type": "array", "items": {"type": "string"}}, "/0/child"),
        ({"type": "object", "additionalProperties": False}, "/unknown"),
    ],
)
def test_proven_impossible_local_input_paths_fail_offline(
    tmp_path: Path, schema: Any, path: str
) -> None:
    flow = _flow(_handler(input={"value": {"pointer": "/payload" + path}}))
    flow["definition"]["input_schema"] = schema
    _workflow(tmp_path, {"first": flow})
    _fails(tmp_path, "dangling_pointer")


def test_uncertain_applicator_and_numeric_object_paths_remain_runtime_checked(
    tmp_path: Path,
) -> None:
    for schema in (
        {"allOf": [{"properties": {"value": {"type": "string"}}}], "additionalProperties": False},
        {"additionalProperties": False},
    ):
        flow = _flow(_handler(input={"value": {"pointer": "/payload/0"}}))
        flow["definition"]["input_schema"] = schema
        _workflow(tmp_path, {"first": flow})
        _compile(tmp_path)


def test_optional_proven_absent_path_checks_its_default(tmp_path: Path) -> None:
    flow = _flow(
        _decision(sources={"message": {"pointer": "/payload/missing", "default": "fallback"}})
    )
    flow["definition"]["input_schema"] = {"type": "object", "additionalProperties": False}
    _workflow(tmp_path, {"first": flow})
    _compile(tmp_path)
    flow["definition"]["steps"][0]["definition"]["sources"]["message"]["default"] = 3
    _workflow(tmp_path, {"first": flow})
    _fails(tmp_path, "incompatible_binding_type")


def test_explicit_decision_questions_enforce_unique_ids_and_allowed_sources(tmp_path: Path) -> None:
    question = {
        "id": "same",
        "type": "predicate",
        "prompt": "Decide.",
        "criteria": ["Use evidence."],
        "allowedSourceIds": ["message"],
    }
    step = _decision()
    del step["question"]
    step["questions"] = [question, deepcopy(question)]
    _workflow(tmp_path, {"first": _flow(step)})
    _fails(tmp_path, "invalid_contract")
    step["questions"][1]["id"] = "other"
    step["questions"][1]["allowedSourceIds"] = ["absent"]
    _workflow(tmp_path, {"first": _flow(step)})
    _fails(tmp_path, "unknown_question_source")


def test_inline_schema_error_location_is_authored_file(tmp_path: Path) -> None:
    _workflow(tmp_path, {"first": _flow(_llm(output={"schema": {"type": "invalid"}}))})
    error = _fails(tmp_path, "invalid_json_schema")
    assert error.location.path == "workflow.yaml"
    assert error.field == "output.schema"
