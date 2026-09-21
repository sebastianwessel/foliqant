"""Offline fallback policy keeps native uncertainty and deterministic routing separate."""

import copy
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_model_executor import _binding, _structured_response
from test_native_adapter import _choice, _choice_result, _sources, _step
from test_runner import Scripted, make_plan, runner

from foliqant.adapters.decisions import build_decision_input, validate_decision_result
from foliqant.adapters.models import ModelExecutor
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.bootstrap import WorkflowApplication
from foliqant.cli import _plan_report
from foliqant.compiler import CompilationError
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import StepResult
from foliqant.contracts.workflow import DecisionStepAuthoring
from foliqant.core.execution import StepOutcome
from foliqant.core.plan import CategoryPlan, FallbackPlan, UnresolvedRoutingPlan

_CHOICE = """type: decision
instructions: Select the supported queue.
sources: {ticket: {pointer: /payload/ticket}}
question:
  type: choice
  criteria: [Use explicit statements.]
  catalog:
    categories:
      - {id: billing, description: Billing questions.}
      - {id: technical, description: Technical questions.}
"""
_FALLBACK = """fallback:
  category:
    id: Misc Queue
    description: |
      Unresolved category.
      Requires triage.
  on: [no_matching_option, missing_information]
"""
_ROUTES = """next: done
on_unresolved:
  default: review
  no_matching_option: other
  missing_information: other
  conflicting_information: review
"""


def _raw(status="not_answerable", issues=None, *, option=None):
    result = _choice_result(question_id="first", status=status, option_id=option)
    result["answerability"]["issues"] = issues if issues is not None else ["no_matching_option"]
    return {"schemaVersion": 1, "results": [result]}


def _compiled(tmp_path, *, fallback=_FALLBACK, routes=_ROUTES, extra=""):
    return make_plan(
        tmp_path,
        {
            "first": _CHOICE + fallback + routes,
            "done": "type: finish\noutcome: completed\n",
            "review": "type: finish\noutcome: needs_review\n",
            "other": "type: finish\noutcome: needs_review\n",
        },
        extra,
    )


def _app(plan, raw):
    def respond(messages, info):
        # The policy category is never sent to the model as an option or prompt.
        assert "misc_queue" not in repr(messages)
        assert "Unresolved category." not in repr(messages)
        return _structured_response(info, raw)

    executor = ModelExecutor({"local": _binding(respond)}, WorkflowSchemas(plan))
    return WorkflowApplication({plan.name: runner(plan, executor)})


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize(
    "status,issues,option,origin,target",
    [
        ("answerable", [], "billing", "model", "done"),
        ("not_answerable", ["no_matching_option"], None, "fallback", "other"),
        (
            "not_answerable",
            ["no_matching_option", "missing_information"],
            None,
            "fallback",
            "other",
        ),
        ("not_answerable", ["conflicting_information"], None, None, "review"),
        ("not_answerable", ["multiple_valid_options"], None, None, "review"),
        (
            "not_answerable",
            ["no_matching_option", "conflicting_information"],
            None,
            None,
            "review",
        ),
        ("undetermined", ["no_matching_option"], None, None, "review"),
    ],
)
async def test_policy_and_routes_share_full_and_isolated_execution(
    tmp_path, isolated, status, issues, option, origin, target
):
    plan = _compiled(tmp_path)
    raw = _raw(status, issues, option=option)
    original = copy.deepcopy(raw)
    app = _app(plan, raw)
    envelope = Envelope(payload={"ticket": "Billing failed for invoice 17."})
    result = (
        await app.run_step("inbox", "first", envelope)
        if isolated
        else await app.run("inbox", envelope)
    )
    record = result.decisions["first"]
    assert record.result == original["results"][0]
    assert raw == original
    assert record.status == ("completed" if origin == "model" else "needs_review")
    assert result.execution.status == record.status
    assert result.execution.usage.model_requests == 1
    if origin is None:
        assert record.selection is None
        assert "selection" not in record.model_dump(mode="json")
    else:
        assert record.selection.origin == origin
        assert record.selection.category.id == ("billing" if origin == "model" else "misc_queue")
        if origin == "fallback":
            assert (
                record.selection.category.description == "Unresolved category.\nRequires triage.\n"
            )
    if isolated:
        assert list(result.decisions) == ["first"]
        assert result.payload == original["results"][0]
    else:
        assert result.decisions[target].status != "skipped"
        for name in {"done", "review", "other"} - {target}:
            assert result.decisions[name].status == "skipped"
    await app.aclose()


@pytest.mark.parametrize("kind", ["empty_issues", "invalid_option", "invalid_citation"])
async def test_invalid_native_results_never_receive_fallback(tmp_path, kind):
    raw = _raw()
    if kind == "empty_issues":
        raw["results"][0]["answerability"]["issues"] = []
    elif kind == "invalid_option":
        raw = _raw("answerable", [], option="misc_queue")
    else:
        raw["results"][0]["explanation"]["evidence"] = [
            {"sourceId": "ticket", "quote": "not in source"}
        ]
    app = _app(_compiled(tmp_path), raw)
    result = await app.run("inbox", Envelope(payload={"ticket": "Billing failed"}))
    assert result.execution.status == "failed"
    assert result.decisions["first"].selection is None
    assert result.decisions["other"].status == "skipped"
    await app.aclose()


async def test_selection_projection_and_cli_policy_description(tmp_path):
    plan = _compiled(
        tmp_path,
        extra=(
            "output: {pointer: /steps/first/selection/category/id, optional: true, default: null}\n"
        ),
    )
    app = _app(plan, _raw())
    result = await app.run("inbox", Envelope(payload={"ticket": "Unknown request"}))
    assert result.payload == "misc_queue"
    assert result.model_dump(mode="json")["decisions"]["first"]["selection"] == {
        "category": {"id": "misc_queue", "description": "Unresolved category.\nRequires triage.\n"},
        "origin": "fallback",
    }
    report = _plan_report(plan)
    first = next(
        step for step in json.loads(json.dumps(report))["steps"] if step["name"] == "first"
    )
    assert first["fallback"]["category"]["id"] == "misc_queue"
    assert first["on_unresolved"]["missing_information"] == "other"
    await app.aclose()


def test_normal_selection_preserves_exact_native_option_id():
    step = _step(_choice())
    raw = {"schemaVersion": 1, "results": [_choice_result()]}
    validated = validate_decision_result(step, build_decision_input(step, _sources()), raw)
    assert validated.selection.category.id == "billing.queue-v2"
    assert validated.selection.origin == "model"


def test_conflict_requires_explicit_policy_and_optional_description_is_omitted():
    step = replace(
        _step(_choice()),
        fallback=FallbackPlan(CategoryPlan("misc"), ("conflicting_information",)),
    )
    raw = _choice_result(status="not_answerable", option_id=None)
    raw["answerability"]["issues"] = ["conflicting_information"]
    validated = validate_decision_result(
        step, build_decision_input(step, _sources()), {"schemaVersion": 1, "results": [raw]}
    )
    assert dict(validated.selection.as_json()["category"]) == {"id": "misc"}
    assert validated.selection.origin == "fallback"


@pytest.mark.parametrize(
    "fallback",
    [
        "fallback: {category: misc, on: [no_matching_option]}\n",
        "fallback: {category: {id: BILLING}, on: [no_matching_option]}\n",
        "fallback: {category: {id: misc}, on: []}\n",
        "fallback: {category: {id: misc}, on: [no_matching_option, no_matching_option]}\n",
        "fallback: {category: {id: misc}, on: [technical_error]}\n",
        "fallback: {category: {id: misc, description: '  '}, on: [no_matching_option]}\n",
    ],
)
def test_invalid_fallback_authoring_rejected(tmp_path, fallback):
    with pytest.raises(CompilationError):
        _compiled(tmp_path, fallback=fallback)


def test_fallback_restricted_to_single_choice():
    with pytest.raises(ValidationError):
        DecisionStepAuthoring.model_validate(
            {
                "type": "decision",
                "instructions": "Decide.",
                "sources": {},
                "question": {"type": "predicate", "criteria": ["Explicit facts"]},
                "fallback": {"category": {"id": "misc"}, "on": ["no_matching_option"]},
            }
        )


@pytest.mark.parametrize(
    "routes",
    [
        "next: done\non_unresolved: {no_matching_option: other}\n",
        _ROUTES.replace("no_matching_option: other", "no_matching_option: absent"),
        _ROUTES.replace("no_matching_option: other", "no_matching_option: first"),
        _ROUTES.replace("conflicting_information", "unknown_issue"),
    ],
)
def test_issue_routes_use_existing_graph_validation(tmp_path, routes):
    with pytest.raises(CompilationError):
        _compiled(tmp_path, routes=routes)


def test_issue_routing_defaults_without_facts_and_when_targets_disagree():
    routing = UnresolvedRoutingPlan(
        "review", (("no_matching_option", "other"), ("missing_information", "other"))
    )
    assert routing.target(()) == "review"
    assert routing.target(("no_matching_option", "missing_information")) == "other"
    assert routing.target(("missing_information", "conflicting_information")) == "review"
    assert routing.target(("multiple_valid_options",)) == "review"


async def test_nondecision_review_uses_default_issue_route(tmp_path):
    plan = make_plan(
        tmp_path,
        {
            "first": "type: handler\nhandler: echo\ninput: {}\n" + _ROUTES,
            "done": "type: finish\noutcome: completed\n",
            "review": "type: finish\noutcome: needs_review\n",
            "other": "type: finish\noutcome: needs_review\n",
        },
    )

    async def execute(step, inputs, context):
        return StepOutcome(None, needs_review=True, unresolved_issues=("no_matching_option",))

    app = WorkflowApplication({"inbox": runner(plan, Scripted(execute))})
    result = await app.run("inbox", Envelope(payload={}))
    assert result.decisions["review"].status == "needs_review"
    assert result.decisions["other"].status == "skipped"
    await app.aclose()


@pytest.mark.parametrize("status", ["failed", "skipped", "cancelled", "completed"])
def test_public_result_rejects_inconsistent_fallback_selection(status):
    value = {
        "status": status,
        "result": _raw()["results"][0],
        "selection": {"category": {"id": "misc"}, "origin": "fallback"},
    }
    with pytest.raises(ValidationError):
        StepResult.model_validate(value)


async def test_absent_policy_retains_native_review_and_string_routing(tmp_path):
    plan = make_plan(
        tmp_path,
        {
            "first": _CHOICE + "next: done\non_unresolved: review\n",
            "done": "type: finish\noutcome: completed\n",
            "review": "type: finish\noutcome: needs_review\n",
        },
    )
    app = _app(plan, _raw())
    result = await app.run("inbox", Envelope(payload={"ticket": "Unrecognized request"}))
    assert result.execution.status == "needs_review"
    assert result.decisions["first"].selection is None
    assert result.decisions["review"].status == "needs_review"
    await app.aclose()


async def test_fallback_never_uses_ordinary_answer_routes(tmp_path):
    routes = _ROUTES.replace("next: done", "on_answer: {billing: done, technical: done}")
    app = _app(_compiled(tmp_path, routes=routes), _raw())
    result = await app.run("inbox", Envelope(payload={"ticket": "Unrecognized request"}))
    assert result.decisions["first"].selection.origin == "fallback"
    assert result.decisions["done"].status == "skipped"
    assert result.decisions["other"].status == "needs_review"
    await app.aclose()


def test_yaml_policy_key_preserves_duplicates_and_boolean_values():
    from foliqant.compiler._loader import load_yaml

    assert load_yaml("on: [no_matching_option]\nenabled: true", relative_path="workflow.yaml") == {
        "on": ["no_matching_option"],
        "enabled": True,
    }
    with pytest.raises(CompilationError):
        load_yaml("on: []\n'on': []", relative_path="workflow.yaml")


@pytest.mark.parametrize("format", ["inline", "markdown"])
def test_fallback_uses_the_shared_inline_and_markdown_compiler(tmp_path, format):
    import yaml

    from foliqant.compiler import compile_workflow
    from foliqant.compiler._loader import load_yaml

    body = _CHOICE + _FALLBACK + "next: done\n"
    workflow = {"version": 1, "name": "inbox", "start": "first", "defaults": {"model": "local"}}
    if format == "inline":
        workflow["steps"] = {
            "first": load_yaml(body, relative_path="workflow.yaml"),
            "done": {"type": "finish", "outcome": "completed"},
        }
    else:
        (tmp_path / "steps").mkdir()
        (tmp_path / "steps" / "first.md").write_text("---\n" + body + "---\n")
        (tmp_path / "steps" / "done.yaml").write_text("type: finish\noutcome: completed\n")
    (tmp_path / "workflow.yaml").write_text(yaml.safe_dump(workflow))
    plan = compile_workflow(
        tmp_path, model_aliases={"local": "test-model"}, tool_catalogs={}, handler_names=set()
    )
    assert plan.step("first").fallback.category.id == "misc_queue"


async def test_concurrent_results_keep_model_and_fallback_selections_isolated(tmp_path):
    import asyncio

    plan = _compiled(tmp_path)

    def respond(messages, info):
        content = repr(messages)
        raw = _raw("answerable", [], option="billing") if "Billing failed" in content else _raw()
        return _structured_response(info, raw)

    from foliqant.core.admission import CapacityLimiter

    executor = ModelExecutor(
        {"local": _binding(respond, admission=CapacityLimiter(concurrency=2, queue_limit=0))},
        WorkflowSchemas(plan),
    )
    app = WorkflowApplication({"inbox": runner(plan, executor)})
    first, second = await asyncio.gather(
        app.run("inbox", Envelope(payload={"ticket": "Billing failed"})),
        app.run("inbox", Envelope(payload={"ticket": "Unknown request"})),
    )
    assert first.decisions["first"].selection.origin == "model"
    assert second.decisions["first"].selection.origin == "fallback"
    first.decisions["first"].result["answer"]["optionId"] = "caller mutation"
    assert second.decisions["first"].result["answer"] is None
    await app.aclose()
