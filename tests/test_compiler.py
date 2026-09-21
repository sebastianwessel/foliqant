"""Offline workflow authoring and compilation acceptance tests."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from foliqant.compiler import CompilationError, compile_workflow
from foliqant.contracts.workflow import Binding
from foliqant.core.plan import DecisionStepPlan, LlmStepPlan


def _write(directory: Path, relative: str, text: str) -> None:
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _base(directory: Path) -> None:
    _write(
        directory,
        "workflow.yaml",
        "version: 1\nname: support_triage\nstart: classify\ndefaults:\n  model: local\n",
    )
    _write(
        directory,
        "steps/classify.md",
        """---
type: decision
sources:
  ticket: {pointer: /payload/ticket}
question:
  type: choice
  criteria: [Choose the applicable queue.]
  catalog:
    categories:
      - {id: Billing Issue, description: Billing support}
      - {id: Technical, description: Technical support}
on_answer:
  billing_issue: bill
  technical: tech
---
Which queue applies?
""",
    )
    _write(directory, "steps/bill.yaml", "type: finish\noutcome: completed\n")
    _write(directory, "steps/tech.yaml", "type: finish\noutcome: needs_review\n")


def test_compiles_markdown_shorthand_to_frozen_deterministic_plan(tmp_path: Path) -> None:
    _base(tmp_path)
    first = compile_workflow(
        tmp_path, model_aliases={"local": "provider:model"}, tool_catalogs={}, handler_names=set()
    )
    second = compile_workflow(
        tmp_path, model_aliases={"local": "provider:model"}, tool_catalogs={}, handler_names=set()
    )

    assert first == second
    assert len(first.revision) == 64
    decision = first.step("classify")
    assert isinstance(decision, DecisionStepPlan)
    assert decision.questions[0].prompt == "Which queue applies?"
    assert tuple(option.id for option in decision.questions[0].options) == (
        "billing_issue",
        "technical",
    )
    assert decision.question_mode == "single"
    assert decision.unresolved_before_transition is True
    assert decision.location.path == "steps/classify.md"
    with pytest.raises(AttributeError):
        decision.name = "changed"  # type: ignore[misc]


def test_binding_is_key_discriminated_and_requires_explicit_optional_default() -> None:
    adapter = TypeAdapter(Binding)
    pointer = adapter.validate_python(
        {"pointer": "/payload/maybe", "optional": True, "default": None}
    )
    assert pointer.model_fields_set == {"pointer", "optional", "default"}
    adapter.validate_python({"literal": {"pointer": "is still literal"}})
    for invalid in (
        {"pointer": "/payload/maybe", "optional": True},
        {"pointer": "/payload/value", "default": None},
        {"pointer": "not-a-pointer"},
        {"pointer": "/steps/bad~2name/result"},
        {"literal": 1, "pointer": "/payload/value"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "version: 1\nname: one\nname: two\nstart: done\n",
        "version: 1\nname: !unsafe one\nstart: done\n",
    ],
)
def test_yaml_rejects_duplicate_keys_and_custom_tags_safely(tmp_path: Path, bad_yaml: str) -> None:
    _write(tmp_path, "workflow.yaml", bad_yaml)
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.location.path == "workflow.yaml"
    assert str(error.value) == "The workflow configuration is invalid."
    assert "unsafe" not in str(error.value)


def test_dispatch_is_explicitly_unavailable_in_first_slice(tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: fan_out\n")
    _write(tmp_path, "steps/fan_out.yaml", "type: dispatch\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "unsupported_step_type"


def test_model_alias_must_be_explicitly_declared(tmp_path: Path) -> None:
    _base(tmp_path)
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "unknown_model"


def test_rejects_cycle_unreachable_and_incomplete_choice_routes(tmp_path: Path) -> None:
    _base(tmp_path)
    _write(tmp_path, "steps/never.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "unreachable_step"

    (tmp_path / "steps/never.yaml").unlink()
    classify = (tmp_path / "steps/classify.md").read_text().replace("  technical: tech\n", "")
    _write(tmp_path, "steps/classify.md", classify)
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "incomplete_answer_routes"


def test_rejects_cycle(tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: one\n")
    _write(tmp_path, "steps/one.yaml", "type: handler\nhandler: work\ninput: {}\nnext: two\n")
    _write(tmp_path, "steps/two.yaml", "type: handler\nhandler: work\ninput: {}\nnext: one\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={},
            handler_names={"work"},
        )
    assert error.value.reason == "workflow_cycle"


def test_multiquestion_on_answer_is_forbidden(tmp_path: Path) -> None:
    _write(
        tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: decide\ndefaults: {model: local}\n"
    )
    _write(
        tmp_path,
        "steps/decide.yaml",
        """type: decision
sources:
  item: {pointer: /payload/item}
instructions: Decide.
questions:
  - id: first
    prompt: Is it first?
    criteria: [Use evidence.]
    allowedSourceIds: [item]
    type: predicate
  - id: second
    prompt: Is it second?
    criteria: [Use evidence.]
    allowedSourceIds: [item]
    type: predicate
on_answer: {'true': done, 'false': done}
""",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "invalid_contract"


def test_mandatory_prior_step_reference_must_dominate_consumer(tmp_path: Path) -> None:
    _write(
        tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: choose\ndefaults: {model: local}\n"
    )
    _write(
        tmp_path,
        "steps/choose.yaml",
        """type: decision
sources: {x: {pointer: /payload/x}}
instructions: Choose.
question: {type: predicate, criteria: [Decide.] }
on_answer: {'true': produce, 'false': consume}
""",
    )
    _write(
        tmp_path,
        "steps/produce.yaml",
        """type: llm
input: {x: {pointer: /payload/x}}
instructions: Produce.
output: text
next: consume
""",
    )
    _write(
        tmp_path,
        "steps/consume.yaml",
        """type: llm
input: {value: {pointer: /steps/produce/result}}
instructions: Consume.
output: text
""",
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "unavailable_step_reference"

    consume = (
        (tmp_path / "steps/consume.yaml")
        .read_text()
        .replace(
            "{pointer: /steps/produce/result}",
            "{pointer: /steps/produce/result, optional: true, default: null}",
        )
    )
    _write(tmp_path, "steps/consume.yaml", consume)
    plan = compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )
    assert isinstance(plan.step("consume"), LlmStepPlan)


def test_step_pointer_name_is_decoded_before_exact_lookup(tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: produce\n")
    _write(
        tmp_path,
        "steps/produce.yaml",
        "type: handler\nhandler: work\ninput: {}\nnext: consume\n",
    )
    _write(
        tmp_path,
        "steps/consume.yaml",
        "type: handler\nhandler: work\ninput: {x: {pointer: /steps/pro~1duce/result}}\n",
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={},
            handler_names={"work"},
        )
    assert error.value.reason == "dangling_pointer"


def test_local_schema_and_declared_tool_catalog_are_checked_offline(tmp_path: Path) -> None:
    _write(
        tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: call\ndefaults: {model: local}\n"
    )
    _write(
        tmp_path,
        "schemas/result.json",
        '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}',
    )
    _write(
        tmp_path,
        "steps/call.yaml",
        """type: llm
input: {}
instructions: Call the tool.
output: {schema: schemas/result.json}
tools:
  server: local_tools
  allow: [lookup]
  choice: {name: lookup}
""",
    )
    catalog = {
        "local_tools": {
            "tools": {
                "lookup": {
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "effect": "read",
                }
            }
        }
    }
    plan = compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs=catalog, handler_names=set()
    )
    assert isinstance(plan.step("call"), LlmStepPlan)

    catalog["local_tools"]["tools"]["lookup"]["input_schema"] = {"type": "unknown"}
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs=catalog, handler_names=set()
        )
    assert error.value.reason == "invalid_tool_schema"


@pytest.mark.parametrize("schema", ["https://example.test/schema.json", "../outside.json"])
def test_schema_paths_are_local_and_confined(tmp_path: Path, schema: str) -> None:
    _write(
        tmp_path, "workflow.yaml", f"version: 1\nname: wf\nstart: done\ninput_schema: {schema}\n"
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_schema_path"


def test_schema_rejects_remote_refs_and_escaping_symlinks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schemas/input.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    _write(tmp_path, "schemas/input.json", '{"$ref":"https://example.test/input.json"}')
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_schema_path"

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{"type":"object"}')
    (tmp_path / "schemas/input.json").unlink()
    (tmp_path / "schemas/input.json").symlink_to(outside)
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_schema_path"


def test_revision_hashes_exact_schema_bytes_read_outside_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: input.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    schema_path = tmp_path / "input.json"
    schema_path.write_text('{"const":"first"}')
    original_read_bytes = Path.read_bytes
    changed = False

    def mutate_after_read(path: Path) -> bytes:
        nonlocal changed
        source = original_read_bytes(path)
        if path == schema_path and not changed:
            changed = True
            schema_path.write_text('{"const":"second"}')
        return source

    monkeypatch.setattr(Path, "read_bytes", mutate_after_read)
    first = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    second = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert first.revision != second.revision
    assert first.input_schema == {"const": "first"}
    assert second.input_schema == {"const": "second"}


def test_transitive_schema_resources_are_frozen_and_revision_bound(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schemas/root.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    _write(tmp_path, "schemas/root.json", '{"$ref":"defs.json#/$defs/value"}')
    _write(tmp_path, "schemas/defs.json", '{"$defs":{"value":{"const":"first"}}}')
    first = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert first.input_schema_path == "schemas/root.json"
    assert tuple(resource.path for resource in first.schema_resources) == (
        "schemas/defs.json",
        "schemas/root.json",
    )
    _write(tmp_path, "schemas/defs.json", '{"$defs":{"value":{"const":"second"}}}')
    second = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert first.revision != second.revision


def test_schema_reference_walk_ignores_annotations_and_checks_fragments(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schema.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    _write(tmp_path, "schema.json", '{"const":{"$ref":"https://business.example/value"}}')
    compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    _write(tmp_path, "schema.json", '{"$ref":"#/missing"}')
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_schema_reference"


def test_schema_ids_are_rejected_only_at_schema_positions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schema.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    _write(tmp_path, "schema.json", '{"const":{"$id":"business-value"}}')
    compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    _write(tmp_path, "schema.json", '{"$id":"local-id","type":"object"}')
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_schema_path"


@pytest.mark.parametrize(
    "schema, reason",
    [
        (
            {"$ref": "#/default", "default": {"$ref": "https://invalid.example/x"}},
            "invalid_schema_path",
        ),
        ({"$ref": "#/const", "const": 17}, "invalid_schema_reference"),
    ],
)
def test_referenced_annotation_values_become_validated_schemas(
    tmp_path: Path, schema: dict[str, object], reason: str
) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schema.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    _write(tmp_path, "schema.json", json.dumps(schema))
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == reason


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/default", "default": {"$ref": "https://invalid.example/x"}},
        {"$ref": "#/const", "const": 17},
    ],
)
def test_declared_tool_referenced_annotations_cannot_bypass_schema_policy(
    tmp_path: Path, schema: dict[str, object]
) -> None:
    _write(tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: done\n")
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    catalog = {"tools": {"lookup": {"input_schema": schema, "effect": "read"}}}
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={"tools": catalog},
            handler_names=set(),
        )
    assert error.value.reason == "invalid_tool_schema"


def test_deep_json_schema_is_rejected_with_safe_compilation_error(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\ninput_schema: schema.json\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    schema: dict[str, object] = {"type": "string"}
    for _ in range(70):
        schema = {"properties": {"child": schema}}
    _write(tmp_path, "schema.json", json.dumps(schema))
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert str(error.value) == "The workflow configuration is invalid."


def test_duplicate_explicit_question_ids_are_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: decide\ndefaults: {model: local}\n",
    )
    question = """    id: duplicate
    prompt: Decide.
    criteria: [Use evidence.]
    allowedSourceIds: [item]
    type: predicate
"""
    _write(
        tmp_path,
        "steps/decide.yaml",
        "type: decision\nsources: {item: {pointer: /payload/item}}\n"
        "instructions: Decide.\nquestions:\n  - "
        + question.lstrip()
        + "  - "
        + question.lstrip()
        + "next: done\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={},
            handler_names=set(),
        )
    assert error.value.reason == "invalid_contract"


def test_declared_tool_schemas_reject_nonlocal_refs(tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", "version: 1\nname: wf\nstart: done\n")
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    catalog = {
        "tools": {
            "lookup": {
                "input_schema": {"$ref": "https://example.test/tool.json"},
                "effect": "read",
            }
        }
    }
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={"local_tools": catalog},
            handler_names=set(),
        )
    assert error.value.reason == "invalid_tool_schema"


def test_named_tool_choice_does_not_collide_with_control_values(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: call\ndefaults: {model: local}\n",
    )
    _write(
        tmp_path,
        "steps/call.yaml",
        """type: llm
input: {}
instructions: Call.
output: text
tools: {server: tools, allow: [lookup], choice: {name: auto}}
""",
    )
    tool = {"input_schema": {"type": "object"}, "effect": "read"}
    catalog = {"tools": {"lookup": tool}}
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={"tools": catalog},
            handler_names=set(),
        )
    assert error.value.reason == "unknown_tool"

    catalog["tools"]["auto"] = tool
    _write(
        tmp_path,
        "steps/call.yaml",
        (tmp_path / "steps/call.yaml").read_text().replace("[lookup]", "[lookup, auto]"),
    )
    plan = compile_workflow(
        tmp_path,
        model_aliases={"local": "model"},
        tool_catalogs={"tools": catalog},
        handler_names=set(),
    )
    step = plan.step("call")
    assert isinstance(step, LlmStepPlan)
    assert step.tools is not None
    assert step.tools.choice_mode == "named"
    assert step.tools.choice_name == "auto"


@pytest.mark.parametrize("version", ["true", "1.0"])
def test_workflow_version_requires_exact_integer(version: str, tmp_path: Path) -> None:
    _write(tmp_path, "workflow.yaml", f"version: {version}\nname: wf\nstart: done\n")
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_contract"


def test_deep_yaml_is_rejected_with_safe_compilation_error(tmp_path: Path) -> None:
    nested = "[" * 600 + "null" + "]" * 600
    _write(
        tmp_path,
        "workflow.yaml",
        f"version: 1\nname: wf\nstart: done\noutput: {{literal: {nested}}}\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert str(error.value) == "The workflow configuration is invalid."


def test_deep_literal_maps_to_safe_compilation_error_after_parsing(tmp_path: Path) -> None:
    nested = "[" * 70 + "null" + "]" * 70
    _write(
        tmp_path,
        "workflow.yaml",
        f"version: 1\nname: wf\nstart: done\noutput: {{literal: {nested}}}\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_contract"


def test_output_binding_accounts_for_implicit_unresolved_review_terminal(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        """version: 1
name: wf
start: decide
defaults: {model: local}
output: {pointer: /steps/later/result}
""",
    )
    _write(
        tmp_path,
        "steps/decide.yaml",
        """type: decision
sources: {item: {pointer: /payload/item}}
instructions: Decide.
question: {type: predicate, criteria: [Use evidence.]}
next: later
""",
    )
    _write(tmp_path, "steps/later.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={},
            handler_names=set(),
        )
    assert error.value.reason == "incompatible_output_binding"

    workflow = (
        (tmp_path / "workflow.yaml")
        .read_text()
        .replace(
            "{pointer: /steps/later/result}",
            "{pointer: /steps/later/result, optional: true, default: null}",
        )
    )
    _write(tmp_path, "workflow.yaml", workflow)
    compile_workflow(
        tmp_path,
        model_aliases={"local": "model"},
        tool_catalogs={},
        handler_names=set(),
    )


@pytest.mark.parametrize(
    "literal",
    [
        "&cycle [*cycle]",
        "[&shared [1], *shared, *shared]",
    ],
)
def test_yaml_aliases_are_rejected_before_recursive_expansion(tmp_path: Path, literal: str) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        f"version: 1\nname: wf\nstart: done\noutput: {{literal: {literal}}}\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "invalid_yaml"
