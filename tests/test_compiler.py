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
next: done
""",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
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
        "type: handler\nhandler: work\n"
        "input: {x: {pointer: /steps/pro~1duce/result}}\nnext: done\n",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
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
next: done
output: {schema: schemas/result.json}
tools:
  server: local_tools
  allow: [lookup]
  choice: {name: lookup}
""",
    )
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
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
next: done
output: text
tools: {server: tools, allow: [lookup], choice: {name: auto}}
""",
    )
    tool = {"input_schema": {"type": "object"}, "effect": "read"}
    _write(tmp_path, "steps/done.yaml", "type: finish\noutcome: completed\n")
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


def _inline(
    directory: Path, *, schema: object | None = None, pointer: str = "/payload/value"
) -> None:
    workflow: dict[str, object] = {
        "version": 1,
        "name": "wf",
        "start": "produce",
        "defaults": {"model": "local"},
        "steps": {
            "produce": {
                "type": "llm",
                "instructions": "Return value.",
                "input": {"value": {"pointer": pointer}},
                "output": {
                    "schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    }
                },
                "next": "done",
            },
            "done": {"type": "finish", "outcome": "completed"},
        },
    }
    if schema is not None:
        workflow["input_schema"] = schema
    _write(directory, "workflow.yaml", json.dumps(workflow))


def test_inline_steps_and_schemas_share_runtime_validation(tmp_path: Path) -> None:
    from foliqant.adapters.validation.schema import WorkflowSchemas
    from foliqant.core.errors import ServiceError
    from foliqant.core.json import freeze_json

    _inline(
        tmp_path,
        schema={
            "$defs": {"value": {"type": "string"}},
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/value"}},
        },
    )
    plan = compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )
    validator = WorkflowSchemas(plan)
    validator.validate_input(freeze_json({"value": "accepted"}))
    validator.validate_output("produce", freeze_json({"value": "accepted"}))
    with pytest.raises(ServiceError):
        validator.validate_input(freeze_json({"value": 17}))
    with pytest.raises(ServiceError):
        validator.validate_output("produce", freeze_json({"value": 17}))
    assert validator.provider_output_schema("produce")["type"] == "object"
    assert plan.step("produce").location.path == "workflow.yaml"


def test_inline_schema_file_refs_are_frozen_and_revision_bound(tmp_path: Path) -> None:
    _inline(tmp_path, schema={"$ref": "input.json"})
    _write(tmp_path, "input.json", '{"type":"object","properties":{"value":{"type":"string"}}}')
    first = compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )
    _write(tmp_path, "input.json", '{"type":"object","properties":{"value":{"type":"number"}}}')
    second = compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )
    assert first.revision != second.revision
    assert "input.json" in {resource.path for resource in first.schema_resources}


def test_inline_and_file_steps_cannot_be_mixed(tmp_path: Path) -> None:
    _inline(tmp_path)
    _write(tmp_path, "steps/other.yaml", "type: finish\noutcome: completed\n")
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "mixed_step_sources"
    assert error.value.field == "steps"
    assert "inline" in error.value.hint


def test_markdown_body_and_instructions_are_ambiguous(tmp_path: Path) -> None:
    _base(tmp_path)
    path = tmp_path / "steps/classify.md"
    path.write_text(
        path.read_text().replace("type: decision", "type: decision\ninstructions: Secret sentinel")
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "ambiguous_instructions"
    assert error.value.field == "instructions"
    assert "Secret" not in str(vars(error.value))


@pytest.mark.parametrize(
    "step",
    [
        {"type": "handler", "handler": "work", "input": {}},
        {"type": "llm", "model": "local", "input": {}, "instructions": "Reply.", "output": "text"},
        {
            "type": "decision",
            "model": "local",
            "sources": {"value": {"literal": "evidence"}},
            "instructions": "Decide.",
            "question": {"type": "predicate", "criteria": ["Use evidence."]},
        },
    ],
)
def test_nonterminal_success_requires_an_explicit_transition(
    tmp_path: Path, step: dict[str, object]
) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        json.dumps({"version": 1, "name": "wf", "start": "work", "steps": {"work": step}}),
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names={"work"}
        )
    assert error.value.reason == "missing_transition"
    assert error.value.field == "next"


@pytest.mark.parametrize(
    "schema,pointer",
    [
        (
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
            "/payload/typo",
        ),
        ({"type": "object", "properties": {"value": {"type": "string"}}}, "/payload/value/child"),
        ({"type": "array", "items": {"type": "string"}}, "/payload/word"),
    ],
)
def test_impossible_schema_paths_are_rejected(
    tmp_path: Path, schema: dict[str, object], pointer: str
) -> None:
    _inline(tmp_path, schema=schema, pointer=pointer)
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "dangling_pointer"


def test_uncertain_applicator_paths_remain_runtime_checked(tmp_path: Path) -> None:
    _inline(
        tmp_path,
        schema={
            "anyOf": [
                {"type": "object", "properties": {"value": {"type": "string"}}},
                {"type": "string"},
            ]
        },
    )
    compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )


@pytest.mark.parametrize(
    "binding,reason",
    [
        ({"literal": 42}, "incompatible_binding_type"),
        ({"pointer": "/payload/value"}, "incompatible_binding_type"),
        ({"pointer": "/steps/produce/result/typo"}, "dangling_pointer"),
    ],
)
def test_mcp_inputs_and_prior_schema_outputs_are_checked(
    tmp_path: Path, binding: dict[str, object], reason: str
) -> None:
    _inline(tmp_path, schema={"type": "object", "properties": {"value": {"type": "number"}}})
    path = tmp_path / "workflow.yaml"
    raw = json.loads(path.read_text())
    raw["steps"]["produce"]["next"] = "lookup"
    raw["steps"]["lookup"] = {
        "type": "mcp",
        "server": "tools",
        "tool": "lookup",
        "arguments": {"value": binding},
        "next": "done",
    }
    path.write_text(json.dumps(raw))
    catalog = {
        "tools": {
            "lookup": {
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "effect": "read",
            }
        }
    }
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            tool_catalogs={"tools": catalog},
            handler_names=set(),
        )
    assert error.value.reason == reason


def test_declared_model_capabilities_fail_before_adapter_creation(tmp_path: Path) -> None:
    from foliqant.contracts.models import OpenAIModelConfig

    _inline(tmp_path)
    profile = OpenAIModelConfig(
        provider="openai",
        model="test",
        api="chat",
        output_mode="native",
        supports_json_schema=False,
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={"local": "model"},
            model_profiles={"local": profile},
            tool_catalogs={},
            handler_names=set(),
        )
    assert error.value.reason == "unsupported_model_capability"
    assert error.value.field == "model"


def test_contract_diagnostics_hide_unknown_field_names_and_values(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\nsecret_sentinel: secret_value\n",
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.field == "*"
    assert "secret" not in str(vars(error.value))


def test_handler_schemas_check_input_and_output_paths_and_bind_revision(tmp_path: Path) -> None:
    from dataclasses import dataclass
    from typing import cast

    from foliqant.core.json import FrozenObject, freeze_json

    @dataclass(frozen=True)
    class Schemas:
        input_schema: FrozenObject
        output_schema: FrozenObject

    inputs = cast(
        FrozenObject,
        freeze_json(
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        ),
    )
    outputs = cast(
        FrozenObject,
        freeze_json(
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
    )
    workflow = {
        "version": 1,
        "name": "wf",
        "start": "work",
        "steps": {
            "work": {
                "type": "handler",
                "handler": "trusted",
                "input": {"value": {"literal": 1}},
                "next": "done",
            },
            "done": {"type": "finish", "outcome": "completed"},
        },
        "output": {"pointer": "/steps/work/result/value"},
    }
    _write(tmp_path, "workflow.yaml", json.dumps(workflow))
    first = compile_workflow(
        tmp_path,
        model_aliases={},
        tool_catalogs={},
        handler_names={"trusted"},
        handler_schemas={"trusted": Schemas(inputs, outputs)},
    )
    second = compile_workflow(
        tmp_path,
        model_aliases={},
        tool_catalogs={},
        handler_names={"trusted"},
        handler_schemas={"trusted": Schemas(inputs, cast(FrozenObject, freeze_json({})))},
    )
    assert first.revision != second.revision
    workflow["output"] = {"pointer": "/steps/work/result/typo"}
    _write(tmp_path, "workflow.yaml", json.dumps(workflow))
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path,
            model_aliases={},
            tool_catalogs={},
            handler_names={"trusted"},
            handler_schemas={"trusted": Schemas(inputs, outputs)},
        )
    assert error.value.reason == "dangling_pointer"


@pytest.mark.parametrize(
    "arguments", [{}, {"value": {"literal": "yes"}, "extra": {"literal": "no"}}]
)
def test_required_and_closed_mcp_argument_shapes_are_checked(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        json.dumps(
            {
                "version": 1,
                "name": "wf",
                "start": "lookup",
                "steps": {
                    "lookup": {
                        "type": "mcp",
                        "server": "tools",
                        "tool": "lookup",
                        "arguments": arguments,
                        "next": "done",
                    },
                    "done": {"type": "finish", "outcome": "completed"},
                },
            }
        ),
    )
    catalog = {
        "tools": {
            "lookup": {
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "effect": "read",
            }
        }
    }
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={}, tool_catalogs={"tools": catalog}, handler_names=set()
        )
    assert error.value.reason == "invalid_input_bindings"


def test_inline_mapping_is_the_only_step_name_source(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workflow.yaml",
        "version: 1\nname: wf\nstart: done\nsteps:\n"
        "  done: {name: done, type: finish, outcome: completed}\n",
    )
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names=set())
    assert error.value.reason == "ambiguous_step_name"


def test_object_only_keywords_do_not_prove_array_paths_impossible(tmp_path: Path) -> None:
    _inline(
        tmp_path, schema={"properties": {}, "additionalProperties": False}, pointer="/payload/0"
    )
    compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )


def test_inline_schema_diagnostics_reference_the_authored_file(tmp_path: Path) -> None:
    _inline(tmp_path, schema={"$ref": "#/not_present"})
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "invalid_schema_reference"
    assert error.value.location.path == "workflow.yaml"
    assert error.value.field == "input_schema"


def test_output_projection_accounts_for_handler_unresolved_terminal(tmp_path: Path) -> None:
    workflow = {
        "version": 1,
        "name": "wf",
        "start": "first",
        "output": {"pointer": "/steps/second/result"},
        "steps": {
            "first": {"type": "handler", "handler": "work", "input": {}, "next": "second"},
            "second": {"type": "handler", "handler": "work", "input": {}, "next": "done"},
            "done": {"type": "finish", "outcome": "completed"},
        },
    }
    _write(tmp_path, "workflow.yaml", json.dumps(workflow))
    with pytest.raises(CompilationError) as error:
        compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names={"work"})
    assert error.value.reason == "incompatible_output_binding"
    workflow["output"] = {"pointer": "/steps/second/result", "optional": True, "default": None}
    _write(tmp_path, "workflow.yaml", json.dumps(workflow))
    compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names={"work"})


def test_remote_inline_schema_reference_never_opens_a_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline compilation attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    _inline(tmp_path, schema={"$ref": "https://private.example/schema?token=SECRET_SENTINEL"})
    with pytest.raises(CompilationError) as error:
        compile_workflow(
            tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
        )
    assert error.value.reason == "invalid_schema_path"
    assert "SECRET_SENTINEL" not in str(vars(error.value))
    assert "private.example" not in str(vars(error.value))
    assert error.value.__suppress_context__


def test_optional_proven_absent_schema_path_uses_its_explicit_default(tmp_path: Path) -> None:
    _inline(tmp_path, schema={"type": "object", "properties": {}, "additionalProperties": False})
    path = tmp_path / "workflow.yaml"
    raw = json.loads(path.read_text())
    raw["steps"]["produce"]["input"]["value"] = {
        "pointer": "/payload/missing",
        "optional": True,
        "default": "fallback",
    }
    path.write_text(json.dumps(raw))
    compile_workflow(
        tmp_path, model_aliases={"local": "model"}, tool_catalogs={}, handler_names=set()
    )


def test_long_acyclic_graph_does_not_depend_on_python_recursion_limit(tmp_path: Path) -> None:
    steps: dict[str, object] = {
        f"step_{index}": {
            "type": "handler",
            "handler": "work",
            "input": {},
            "next": f"step_{index + 1}" if index < 1049 else "done",
        }
        for index in range(1050)
    }
    steps["done"] = {"type": "finish", "outcome": "completed"}
    _write(
        tmp_path,
        "workflow.yaml",
        json.dumps({"version": 1, "name": "wf", "start": "step_0", "steps": steps}),
    )
    plan = compile_workflow(tmp_path, model_aliases={}, tool_catalogs={}, handler_names={"work"})
    assert len(plan.steps) == 1051
