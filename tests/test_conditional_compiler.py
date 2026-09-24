"""Compiler checks for bindings, conditions, routes, repeat, review defaults and budgets."""

import json
from pathlib import Path
from typing import Any

import pytest
from workflow_documents import (
    IDENTIFIER_INPUT,
    callable_flow,
    codes,
    compile_document,
    flow,
    handler,
    step,
    when,
)

from foliqant.compiler import CompilationError, compile_workflow
from foliqant.core.plan import ConditionalRoutingPlan, MatchRoutingPlan, UnresolvedRoutingPlan


def _fails(tmp_path: Path, reason: str, flows: dict[str, Any], **workflow: Any) -> CompilationError:
    with pytest.raises(CompilationError) as error:
        compile_document(tmp_path, flows, **workflow)
    assert error.value.reason == reason, (error.value.reason, error.value.field)
    return error.value


def _lookup_flow(**extra: Any) -> dict[str, Any]:
    return flow(
        step("lookup", handler("lookup", identifier="/payload/identifier")),
        input={"identifier": {"pointer": "/payload/identifier"}},
        output={"pointer": "/steps/lookup/result"},
        **extra,
    )


def _end() -> dict[str, Any]:
    return flow(step("done", handler()))


# Bindings ---------------------------------------------------------------------


def test_optional_is_not_a_binding_field(tmp_path):
    error = _fails(
        tmp_path,
        "unknown_field",
        {"main": flow(step("a", handler(value={"pointer": "/x", "optional": True, "default": 1})))},
    )
    assert error.field is not None and error.field.endswith("pointer.optional")


def test_default_makes_forward_references_optional(tmp_path):
    steps = [
        step("a", handler(later="/steps/b/result")),
        step("b", handler()),
    ]
    _fails(tmp_path, "unavailable_step_reference", {"main": flow(*steps)})
    steps[0] = step("a", handler(later={"pointer": "/steps/b/result", "default": None}))
    compile_document(tmp_path, {"main": flow(*steps)})


def test_first_of_needs_one_available_member_without_default(tmp_path):
    first_of = {"first_of": [{"pointer": "/steps/b/result"}, {"pointer": "/steps/c/result"}]}
    steps = [step("a", handler(value=first_of)), step("b", handler()), step("c", handler())]
    _fails(tmp_path, "unavailable_step_reference", {"main": flow(*steps)})
    steps[0] = step("a", handler(value={**first_of, "default": None}))
    compile_document(tmp_path, {"main": flow(*steps)})
    available = {"first_of": [{"pointer": "/steps/c/result"}, {"pointer": "/payload/value"}]}
    steps[0] = step("a", handler(value=available))
    compile_document(tmp_path, {"main": flow(*steps)})


def test_first_of_at_the_boundary_needs_a_dominating_member(tmp_path):
    branches = {
        "start": flow(
            step("s", handler()),
            transition={
                "route": [
                    {"when": when("/payload/left", present=True), "flow": "left"},
                    {"flow": "right"},
                ]
            },
        ),
        "left": _lookup_flow(transition={"flow": "join"}),
        "right": _lookup_flow(transition={"flow": "join"}),
    }
    members = [{"pointer": "/flows/left/result"}, {"pointer": "/flows/right/result"}]
    join = flow(step("j", handler()), input={"lookup": {"first_of": members}})
    _fails(tmp_path, "unavailable_flow_reference", {**branches, "join": join})
    join["input"]["lookup"]["default"] = None
    plan = compile_document(tmp_path, {**branches, "join": join})
    assert plan.flow("join").input[0][1].kind == "first_of"


def test_object_output_fields_follow_dominance_and_carry_types(tmp_path):
    fields = {
        "status": {"pointer": "/steps/lookup/result/status"},
        "later": {"pointer": "/steps/second/result"},
    }
    main = flow(
        step("lookup", handler("lookup", identifier="/payload/identifier")),
        step("second", handler()),
        input={"identifier": {"pointer": "/payload/identifier"}},
        output={"fields": fields},
        transition={"flow": "next"},
    )
    target = flow(
        step("n", handler()),
        input={"status": {"pointer": "/flows/main/result/status"}},
        input_schema={"type": "object", "properties": {"status": {"type": "integer"}}},
    )
    _fails(tmp_path, "incompatible_output_binding", {"main": main, "next": target})
    fields["later"]["default"] = None
    _fails(tmp_path, "incompatible_binding_type", {"main": main, "next": target})
    target["definition"]["input_schema"]["properties"]["status"] = {"type": "string"}
    plan = compile_document(tmp_path, {"main": main, "next": target})
    assert plan.flow("main").output is not None and plan.flow("main").output.kind == "fields"


# Step `when` ------------------------------------------------------------------


def test_step_condition_reads_only_earlier_steps(tmp_path):
    later = [step("a", handler(), when=when("/steps/b/status", equals="completed"))]
    later.append(step("b", handler()))
    _fails(tmp_path, "unavailable_step_reference", {"main": flow(*later)})
    itself = [step("a", handler(), when=when("/steps/a/status", present=True))]
    _fails(tmp_path, "unavailable_step_reference", {"main": flow(*itself)})
    dangling = [step("a", handler(), when=when("/steps/missing/status", present=True))]
    _fails(tmp_path, "dangling_pointer", {"main": flow(*dangling)})


def test_results_of_conditional_steps_need_defaults(tmp_path):
    guard = when("/payload/repair", equals=True)
    steps = [
        step("check", handler()),
        step("repair", handler(), when=guard),
        step("after", handler(value="/steps/repair/result")),
    ]
    _fails(tmp_path, "unavailable_step_reference", {"main": flow(*steps)})
    steps[2] = step("after", handler(value="/steps/repair/status"))
    compile_document(tmp_path, {"main": flow(*steps)})
    steps[2] = step("after", handler(value={"pointer": "/steps/repair/result", "default": None}))
    plan = compile_document(tmp_path, {"main": flow(*steps)})
    assert plan.flow("main").step("repair").when is not None
    first_conditional = flow(
        step("repair", handler(), when=guard), output={"pointer": "/steps/repair/result"}
    )
    _fails(tmp_path, "incompatible_output_binding", {"main": first_conditional})


def test_step_condition_types_are_checked_against_result_schemas(tmp_path):
    lookup = step("lookup", handler("lookup", identifier="/payload/identifier"))
    mismatch = step("next", handler(), when=when("/steps/lookup/result/status", gt=1))
    error = _fails(tmp_path, "condition_type_mismatch", {"main": flow(lookup, mismatch)})
    assert error.field == "when"
    assert error.location.line > 1
    impossible = step("next", handler(), when=when("/steps/lookup/result/unknown", present=True))
    _fails(tmp_path, "dangling_pointer", {"main": flow(lookup, impossible)})
    wrong_type = step("next", handler(), when=when("/steps/lookup/result/count", equals="3"))
    _fails(tmp_path, "condition_type_mismatch", {"main": flow(lookup, wrong_type)})
    malformed = step("next", handler(), when={"binding": {"pointer": "/x"}, "near": 1})
    error = _fails(tmp_path, "invalid_condition", {"main": flow(lookup, malformed)})


def test_unsafe_patterns_fail_compilation_at_their_source_location(tmp_path):
    lookup = step("lookup", handler("lookup", identifier="/payload/identifier"))
    unsafe = step("next", handler(), when=when("/steps/lookup/result/fund", matches="(a+)+"))
    error = _fails(tmp_path, "unsafe_pattern", {"main": flow(lookup, unsafe)})
    assert error.field == "matches" and error.location.line > 1
    route = {
        "route": [
            {"when": when("/flows/main/result/fund", matches="(?:x|xy)*"), "outcome": "completed"},
            {"outcome": "needs_review"},
        ]
    }
    _fails(tmp_path, "unsafe_pattern", {"main": _lookup_flow(transition=route)})


def test_business_keys_named_like_conditions_report_contract_errors(tmp_path):
    invalid = {"pointer": 5}
    keyed = step("a", handler(when=invalid, until=invalid))
    _fails(tmp_path, "invalid_contract", {"main": flow(keyed)})
    _fails(
        tmp_path, "invalid_contract", {"main": flow(step("a", handler()), input={"until": invalid})}
    )
    fields = flow(step("a", handler()), output={"fields": {"when": invalid}})
    _fails(tmp_path, "invalid_contract", {"main": fields})
    malformed = {"binding": {"pointer": "/payload/x"}}
    _fails(tmp_path, "invalid_condition", {"main": flow(step("a", handler(), when=malformed))})
    repeat = {"max_attempts": 2, "until": malformed}
    _fails(tmp_path, "invalid_condition", {"main": _lookup_flow(repeat=repeat)})


def test_constant_conditions_are_reported(tmp_path):
    lookup = step("lookup", handler("lookup", identifier="/payload/identifier"))
    never = step("never", handler(), when=when("/steps/lookup/result/status", equals="lost"))
    always = step(
        "always",
        handler(),
        when=when("/steps/lookup/result/status", **{"in": ["found", "not_found", "ambiguous"]}),
    )
    plan = compile_document(tmp_path, {"main": flow(lookup, never, always)})
    assert codes(plan).count("condition_always_false") == 1
    assert codes(plan).count("condition_always_true") == 1
    assert all(item.level == "warning" for item in plan.diagnostics if "always" in item.code)


def test_constant_condition_checks_compare_numbers_by_value(tmp_path):
    schema = {
        "type": "object",
        "properties": {"level": {"type": "integer", "enum": [1, 2]}},
        "required": ["level"],
    }
    main = flow(
        step("first", handler(), when=when("/payload/level", equals=1.0)),
        step("both", handler(), when=when("/payload/level", **{"in": [1.0, 2.0]})),
        input={"level": {"pointer": "/payload/level"}},
        input_schema=schema,
    )
    plan = compile_document(tmp_path, {"main": main})
    # `1.0` equals `1` at run time, so neither condition is statically false.
    assert "condition_always_false" not in codes(plan)
    assert codes(plan).count("condition_always_true") == 1
    never = flow(
        step("never", handler(), when=when("/payload/level", equals=True)),
        input={"level": {"pointer": "/payload/level"}},
        input_schema=schema,
    )
    _fails(tmp_path, "condition_type_mismatch", {"main": never})


# Routes and start -------------------------------------------------------------


def _routed(route: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "main": _lookup_flow(transition={"route": route}),
        "repair": _end(),
        **extra,
    }


def test_route_requires_a_final_otherwise_entry(tmp_path):
    entry = {"when": when("/flows/main/result/status", equals="found"), "outcome": "completed"}
    _fails(tmp_path, "route_without_otherwise", _routed([entry, {**entry, "flow": None}]))
    _fails(tmp_path, "route_without_otherwise", _routed([entry]))
    _fails(
        tmp_path, "misplaced_otherwise", _routed([{"flow": "repair"}, entry, {"flow": "repair"}])
    )


def test_route_compiles_ordered_entries(tmp_path):
    plan = compile_document(
        tmp_path,
        _routed(
            [
                {"when": when("/flows/main/result/status", equals="found"), "outcome": "completed"},
                {"flow": "repair"},
            ]
        ),
    )
    route = plan.flow("main").transition
    assert isinstance(route, ConditionalRoutingPlan)
    assert [entry.when is None for entry in route.entries] == [False, True]
    assert "route_unreachable_entry" not in codes(plan)


def test_statically_false_entries_are_unreachable(tmp_path):
    plan = compile_document(
        tmp_path,
        _routed(
            [
                {"when": when("/flows/main/result/status", equals="lost"), "flow": "repair"},
                {"when": when("/payload/value", present=False), "outcome": "completed"},
                {"flow": "repair"},
            ]
        ),
    )
    assert {"route_unreachable_entry", "condition_always_false"} <= set(codes(plan))
    location = next(item for item in plan.diagnostics if item.code == "route_unreachable_entry")
    assert location.location.path == "workflow.yaml" and location.location.line > 1


def test_route_conditions_cannot_read_flows_that_never_ran(tmp_path):
    flows = _routed(
        [
            {"when": when("/flows/repair/result/x", present=True), "flow": "repair"},
            {"outcome": "completed"},
        ]
    )
    _fails(tmp_path, "unavailable_flow_reference", flows)
    flows = _routed(
        [{"when": when("/flows/child/result", present=True), "flow": "repair"}, {"flow": "repair"}],
        child=callable_flow(step("c", handler())),
    )
    flows["main"]["definition"]["steps"].append(
        step(
            "collect",
            {"type": "flow_collection", "items": {"literal": []}, "flows": ["child"]},
        )
    )
    _fails(tmp_path, "invalid_flow_reference", flows)


def test_routed_start_reads_the_envelope_only(tmp_path):
    flows = {"extract": _end(), "classify": _end()}
    start = {
        "route": [
            {"when": when("/payload/report_type", present=True), "flow": "extract"},
            {"flow": "classify"},
        ]
    }
    plan = compile_document(tmp_path, flows, start=start)
    assert isinstance(plan.start, ConditionalRoutingPlan)
    assert plan.flow("extract").available_flows == ()
    flow_pointer = {
        "route": [
            {"when": when("/flows/extract/result", present=True), "flow": "extract"},
            {"flow": "classify"},
        ]
    }
    _fails(tmp_path, "unavailable_flow_reference", flows, start=flow_pointer)
    outcome = {"route": [{"outcome": "completed"}]}
    _fails(tmp_path, "invalid_contract", flows, start=outcome)
    child = {"route": [{"flow": "child"}]}
    _fails(
        tmp_path,
        "missing_start",
        {**flows, "child": callable_flow(step("c", handler()))},
        start=child,
    )


def test_both_start_candidates_dominate_nothing_but_themselves(tmp_path):
    flows = {
        "extract": _end() | {"transition": {"flow": "enrich"}},
        "classify": _end() | {"transition": {"flow": "enrich"}},
        "enrich": flow(step("e", handler()), input={"x": {"pointer": "/flows/extract/result"}}),
    }
    start = {
        "route": [
            {"when": when("/payload/x", present=True), "flow": "extract"},
            {"flow": "classify"},
        ]
    }
    _fails(tmp_path, "unavailable_flow_reference", flows, start=start)


# Case coverage ----------------------------------------------------------------


def _cases(cases: dict[str, Any], **extra: Any) -> dict[str, Any]:
    transition = {
        "binding": {"pointer": "/flows/main/result/status"},
        "cases": cases,
        "default": {"outcome": "needs_review"},
        **extra,
    }
    return {"main": _lookup_flow(transition=transition), "repair": _end()}


def test_case_keys_must_be_allowed_values(tmp_path):
    error = _fails(tmp_path, "unmatched_case", _cases({"lost": {"flow": "repair"}}))
    assert error.field == "transition.cases"


def test_uncovered_values_warn_unless_listed_in_default_covers(tmp_path):
    plan = compile_document(tmp_path, _cases({"found": {"flow": "repair"}}))
    warning = next(item for item in plan.diagnostics if item.code == "uncovered_value")
    assert warning.level == "warning"
    assert "not_found" in warning.message and "ambiguous" in warning.message
    covered = _cases({"found": {"flow": "repair"}}, default_covers=["ambiguous", "not_found"])
    assert "uncovered_value" not in codes(compile_document(tmp_path, covered))
    wrong = _cases({"found": {"flow": "repair"}}, default_covers=["not_found"])
    _fails(tmp_path, "default_covers_mismatch", wrong)


def test_unknown_types_are_reported_as_info(tmp_path):
    flows = {
        "main": flow(
            step("echo", handler()),
            output={"pointer": "/steps/echo/result"},
            transition={
                "binding": {"pointer": "/flows/main/result"},
                "cases": {"anything": {"outcome": "completed"}},
                "default": {"outcome": "needs_review"},
            },
        )
    }
    plan = compile_document(tmp_path, flows)
    info = next(item for item in plan.diagnostics if item.code == "case_on_unknown_type")
    assert info.level == "info"
    flows["main"]["transition"]["default_covers"] = ["x"]
    _fails(tmp_path, "default_covers_mismatch", flows)


def test_decision_selection_values_include_the_fallback_category(tmp_path):
    decision = {
        "type": "decision",
        "sources": {"message": {"pointer": "/payload/message"}},
        "instructions": "Classify the message.",
        "question": {
            "type": "choice",
            "criteria": ["Use explicit statements."],
            "catalog": {
                "categories": [
                    {"id": "billing", "description": "Billing."},
                    {"id": "cancellation", "description": "Cancellation."},
                ]
            },
        },
        "fallback": {"category": {"id": "general"}, "on": ["no_supported_answer"]},
    }
    flows = {
        "main": flow(
            step("classify", decision),
            input={"message": {"pointer": "/payload/message"}},
            output={"pointer": "/steps/classify/selection/category/id", "default": None},
            transition={
                "binding": {"pointer": "/flows/main/result"},
                "cases": {"billing": {"outcome": "completed"}, "general": {"flow": "repair"}},
                "default": {"outcome": "needs_review"},
            },
        ),
        "repair": _end(),
    }
    plan = compile_document(tmp_path, flows)
    warning = next(item for item in plan.diagnostics if item.code == "uncovered_value")
    assert "cancellation" in warning.message and "general" not in warning.message
    flows["main"]["transition"]["cases"]["other"] = {"outcome": "completed"}
    _fails(tmp_path, "unmatched_case", flows)


# Review routes ----------------------------------------------------------------


def test_workflow_review_default_is_inherited_by_flows_without_their_own(tmp_path):
    flows = {
        "main": _lookup_flow(transition={"flow": "enrich"}),
        "enrich": flow(step("e", handler()), on_unresolved={"outcome": "needs_review"}),
    }
    plan = compile_document(tmp_path, flows, defaults={"on_unresolved": {"flow": "enrich"}})
    main = plan.flow("main")
    assert main.on_unresolved_inherited and main.on_unresolved is not None
    assert main.on_unresolved.flow == "enrich"  # type: ignore[union-attr]
    assert not plan.flow("enrich").on_unresolved_inherited
    assert "review_ends_run" not in codes(plan)


def test_inherited_review_default_must_not_create_a_cycle(tmp_path):
    flows = {
        "main": _lookup_flow(transition={"flow": "enrich"}),
        "enrich": flow(step("e", handler())),
    }
    error = _fails(
        tmp_path,
        "invalid_default_review_route",
        flows,
        defaults={"on_unresolved": {"flow": "enrich"}},
    )
    assert error.field == "defaults.on_unresolved"
    # A cycle closed by an inherited edge names the default, not a generic cycle.
    flows["enrich"]["on_unresolved"] = {"flow": "main"}
    _fails(
        tmp_path,
        "invalid_default_review_route",
        flows,
        defaults={"on_unresolved": {"flow": "enrich"}},
    )
    flows["enrich"]["on_unresolved"] = {"outcome": "needs_review"}
    compile_document(tmp_path, flows, defaults={"on_unresolved": {"flow": "enrich"}})


def test_review_routes_cannot_target_their_own_flow(tmp_path):
    flows = {"main": _lookup_flow(on_unresolved={"flow": "main"})}
    _fails(tmp_path, "review_route_to_self", flows)


def test_review_default_accepts_issue_maps_and_routes(tmp_path):
    review = {
        "route": [
            {"when": when("/flows/main/result/status", equals="ambiguous"), "flow": "enrich"},
            {"outcome": "needs_review"},
        ]
    }
    flows = {
        "main": _lookup_flow(transition={"flow": "enrich"}),
        "enrich": flow(
            step("e", handler()), on_unresolved={"default": {"outcome": "needs_review"}}
        ),
    }
    plan = compile_document(tmp_path, flows, defaults={"on_unresolved": review})
    assert isinstance(plan.flow("main").on_unresolved, ConditionalRoutingPlan)
    assert isinstance(plan.flow("enrich").on_unresolved, UnresolvedRoutingPlan)
    completes = {"route": [{"outcome": "completed"}]}
    _fails(tmp_path, "invalid_contract", flows, defaults={"on_unresolved": completes})


def test_implicit_review_termination_is_reported_with_the_output_default(tmp_path):
    plan = compile_document(
        tmp_path,
        {"main": _lookup_flow()},
        output={"pointer": "/flows/main/result", "default": {"status": "review"}},
    )
    info = next(item for item in plan.diagnostics if item.code == "review_ends_run")
    assert info.level == "info" and '"status": "review"' in info.message
    # The reviewing flow's own result is present, so the default is only a fallback.
    assert "resolved from the flows that ran, or its default" in info.message
    flows = {
        "classify": _lookup_flow(transition={"flow": "answer"}),
        "answer": flow(
            step("answer", handler()),
            output={"pointer": "/steps/answer/result"},
            on_unresolved={"outcome": "needs_review"},
        ),
    }
    plan = compile_document(
        tmp_path, flows, output={"pointer": "/flows/answer/result", "default": None}
    )
    info = next(item for item in plan.diagnostics if item.code == "review_ends_run")
    # `answer` never runs when `classify` stops for review: the host gets the default.
    assert info.message.endswith("the host receives the workflow output default null.")


# Repeat -----------------------------------------------------------------------


def _repeat_flows(repeat: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "lookup_fund": _lookup_flow(repeat=repeat, transition={"flow": "enrich"}),
        "correct": callable_flow(
            step("correct", handler("correct", lookup="/payload/lookup")),
            output={"pointer": "/steps/correct/result"},
        ),
        "enrich": flow(step("e", handler())),
        **extra,
    }


_UNTIL = when("/flows/lookup_fund/result/status", equals="found")


def _retry(**extra: Any) -> dict[str, Any]:
    return {
        "max_attempts": 2,
        "until": _UNTIL,
        "retry": {"flow": "correct", "input": {"lookup": {"pointer": "/flows/lookup_fund/result"}}},
        "retry_input": {"identifier": {"pointer": "/flows/correct/result/identifier"}},
        **extra,
    }


def test_repeat_compiles_retry_flow_and_overrides(tmp_path):
    plan = compile_document(tmp_path, _repeat_flows(_retry()), max_steps=32)
    repeat = plan.flow("lookup_fund").repeat
    assert repeat is not None and repeat.retry_flow == "correct" and repeat.max_attempts == 2
    assert [name for name, _ in repeat.retry_input] == ["identifier"]
    assert "repeat_without_retry" not in codes(plan)


@pytest.mark.parametrize(
    "change",
    [
        {"retry_input": {"unknown": {"pointer": "/flows/correct/result/identifier"}}},
        {"retry": {"flow": "enrich"}},
        {"retry": {"flow": "missing"}},
    ],
)
def test_invalid_repeat_shapes(tmp_path, change):
    error = _fails(tmp_path, "invalid_repeat", _repeat_flows(_retry(**change)))
    assert error.field is not None and error.field.startswith("repeat")


def test_retry_input_without_retry_cannot_read_the_retry_flow(tmp_path):
    repeat = {"max_attempts": 2, "until": _UNTIL}
    repeat["retry_input"] = {"identifier": {"pointer": "/flows/correct/result/identifier"}}
    flows = _repeat_flows(repeat)
    flows["enrich"]["definition"]["steps"].append(
        step("c", {"type": "flow_collection", "items": {"literal": []}, "flows": ["correct"]})
    )
    _fails(tmp_path, "invalid_repeat", flows)


def test_one_retry_flow_serves_one_repeat(tmp_path):
    flows = _repeat_flows(_retry())
    flows["lookup_fund"]["transition"] = {"flow": "second"}
    flows["second"] = _lookup_flow(repeat=_retry(), transition={"flow": "enrich"})
    flows["second"]["repeat"]["until"] = when("/flows/second/result/status", equals="found")
    flows["second"]["repeat"]["retry"]["input"] = {"lookup": {"pointer": "/flows/second/result"}}
    _fails(tmp_path, "invalid_repeat", flows)


@pytest.mark.parametrize("attempts", [1, 65, True])
def test_max_attempts_bounds(tmp_path, attempts):
    _fails(tmp_path, "invalid_contract", _repeat_flows(_retry(max_attempts=attempts)))


def test_repeat_budget_is_checked_against_max_steps(tmp_path):
    compile_document(tmp_path, _repeat_flows(_retry(max_attempts=4)), max_steps=8)
    error = _fails(tmp_path, "repeat_budget", _repeat_flows(_retry(max_attempts=5)), max_steps=8)
    assert error.field == "repeat"


def test_repeat_of_a_model_flow_without_retry_warns(tmp_path):
    llm = {"type": "llm", "input": {}, "instructions": "Summarize.", "output": "text"}
    flows = {
        "main": flow(
            step("model", llm),
            repeat={"max_attempts": 2, "until": when("/flows/main/result", present=True)},
        )
    }
    plan = compile_document(tmp_path, flows)
    assert "repeat_without_retry" in codes(plan)


def test_repeat_scopes(tmp_path):
    later = _retry(until=when("/flows/enrich/result", present=True))
    _fails(tmp_path, "unavailable_flow_reference", _repeat_flows(later))
    flows = _repeat_flows(_retry())
    flows["enrich"]["input"] = {"retry": {"pointer": "/flows/correct/result"}}
    _fails(tmp_path, "unavailable_flow_reference", flows)
    flows["enrich"]["input"] = {"retry": {"pointer": "/flows/correct/result", "default": None}}
    flows["enrich"]["input"]["attempts"] = {"pointer": "/flows/lookup_fund/attempts"}
    compile_document(tmp_path, flows)
    flows["enrich"]["input"]["attempts"] = {"pointer": "/flows/enrich/attempts"}
    _fails(tmp_path, "invalid_flow_reference", flows)
    continued = _retry()
    continued["retry"]["continue_when"] = when("/flows/correct/result/status", equals="accepted")
    compile_document(tmp_path, _repeat_flows(continued))
    continued["retry"]["continue_when"] = when("/flows/correct/result/status", gt=1)
    _fails(tmp_path, "condition_type_mismatch", _repeat_flows(continued))


def test_attempt_entries_expose_status_result_and_error_only(tmp_path):
    flows = _repeat_flows(_retry())
    for field in ("attempt", "status", "result", "error"):
        flows["enrich"]["input"] = {"entry": {"pointer": f"/flows/lookup_fund/attempts/0/{field}"}}
        compile_document(tmp_path, flows)
    # Step records, usage and timing are never part of the bound attempt entry.
    for field in ("steps", "usage", "elapsed_seconds"):
        flows["enrich"]["input"] = {"entry": {"pointer": f"/flows/lookup_fund/attempts/0/{field}"}}
        _fails(tmp_path, "dangling_pointer", flows)


def test_retry_flow_input_is_checked_against_its_schema(tmp_path):
    flows = _repeat_flows(_retry())
    flows["correct"]["definition"]["input_schema"] = {
        "type": "object",
        "properties": {"lookup": {"type": "object"}, "reason": {"type": "string"}},
        "required": ["lookup", "reason"],
    }
    _fails(tmp_path, "invalid_input_bindings", flows)
    flows["lookup_fund"]["repeat"]["retry"]["input"]["reason"] = {"literal": "not found"}
    compile_document(tmp_path, flows)
    flows["lookup_fund"]["repeat"]["retry"]["input"]["reason"] = {"literal": 1}
    _fails(tmp_path, "incompatible_binding_type", flows)


def test_unreachable_retry_callable_is_rejected(tmp_path):
    flows = _repeat_flows({"max_attempts": 2, "until": _UNTIL})
    _fails(tmp_path, "unreachable_flow", flows)


# Prompts, sources, budgets and defaults ---------------------------------------


def test_unused_prompt_inputs_and_empty_text_sources(tmp_path):
    llm = {
        "type": "llm",
        "input": {"message": {"pointer": "/payload/message"}, "unused": {"literal": 1}},
        "instructions": "Summarize.",
        "prompt": "Summarize {{ message }}.",
        "output": "text",
    }
    decision = {
        "type": "decision",
        "sources": {"body": {"pointer": "/steps/text/result/body"}},
        "instructions": "Does the body ask for help?",
        "question": {"type": "predicate", "criteria": ["Use explicit statements."]},
    }
    plan = compile_document(
        tmp_path,
        {"main": flow(step("model", llm), step("text", handler("text")), step("ask", decision))},
    )
    unused = next(item for item in plan.diagnostics if item.code == "unused_llm_input")
    assert "unused" in unused.message and unused.level == "warning"
    empty = next(item for item in plan.diagnostics if item.code == "empty_text_source")
    assert empty.level == "info"


def test_collection_budget_warning(tmp_path):
    flows = {
        "main": flow(
            step(
                "collect",
                {
                    "type": "flow_collection",
                    "items": {"pointer": "/payload/items"},
                    "flows": ["child"],
                    "max_items": 20,
                },
            )
        ),
        "child": callable_flow(step("a", handler()), step("b", handler())),
    }
    assert "collection_budget" in codes(compile_document(tmp_path, flows, max_steps=32))
    assert "collection_budget" not in codes(compile_document(tmp_path, flows, max_steps=64))
    assert "collection_budget" not in codes(compile_document(tmp_path, flows))


def test_flow_default_model_overrides_the_workflow_default(tmp_path):
    llm = {"type": "llm", "input": {}, "instructions": "Summarize.", "output": "text"}
    document = {
        "name": "demo",
        "defaults": {"model": "local"},
        "flows": {
            "main": {
                "input": {},
                "definition": {"defaults": {"model": "large"}, "steps": [step("model", llm)]},
                "transition": {"outcome": "completed"},
            }
        },
    }
    (tmp_path / "workflow.yaml").write_text(json.dumps(document))
    plan = compile_workflow(
        tmp_path,
        model_aliases={"local": "small-model", "large": "large-model"},
        tool_catalogs={},
        handler_names=set(),
    )
    assert plan.flow("main").step("model").model == "large"  # type: ignore[union-attr]


def test_cases_routes_keep_their_default_covers(tmp_path):
    flows = _cases({"found": {"flow": "repair"}}, default_covers=["not_found", "ambiguous"])
    route = compile_document(tmp_path, flows).flow("main").transition
    assert isinstance(route, MatchRoutingPlan)
    assert route.default_covers == ("not_found", "ambiguous")


# Repeat on callable flows ----------------------------------------------------


def _item_repeat_flows(repeat: dict[str, Any], *, max_items: int = 4) -> dict[str, Any]:
    return {
        "main": flow(
            step(
                "collect",
                {
                    "type": "flow_collection",
                    "items": {"literal": []},
                    "flows": ["lookup_item"],
                    "max_items": max_items,
                },
            ),
        ),
        "lookup_item": {
            **callable_flow(
                step("lookup", handler("lookup", identifier="/payload/identifier")),
                output={"pointer": "/steps/lookup/result"},
                input_schema=IDENTIFIER_INPUT,
            ),
            "repeat": repeat,
        },
        "correct": callable_flow(step("correct", handler("correct"))),
    }


def _item_repeat(**extra: Any) -> dict[str, Any]:
    return {
        "max_attempts": 2,
        "until": when("/flows/lookup_item/result/status", equals="found"),
        "retry": {"flow": "correct", "input": {"lookup": {"pointer": "/flows/lookup_item/result"}}},
        "retry_input": {"identifier": {"pointer": "/flows/correct/result/identifier"}},
        **extra,
    }


def test_callable_repeat_compiles_in_the_item_scope(tmp_path):
    plan = compile_document(tmp_path, _item_repeat_flows(_item_repeat()), max_steps=32)
    repeat = plan.flow("lookup_item").repeat
    assert repeat is not None and repeat.retry_flow == "correct"
    # The item retry flow is no workflow-level record, so routes cannot read it.
    flows = _item_repeat_flows(_item_repeat())
    flows["main"]["transition"] = {
        "route": [
            {"when": when("/flows/correct/result", present=True), "outcome": "needs_review"},
            {"outcome": "completed"},
        ]
    }
    _fails(tmp_path, "invalid_flow_reference", flows)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"until": when("/flows/main/result", present=True)}, "invalid_flow_reference"),
        ({"until": when("/steps/lookup/result", present=True)}, "dangling_pointer"),
        (
            {"retry": {"flow": "correct", "input": {"x": {"pointer": "/flows/correct/result"}}}},
            "unavailable_flow_reference",
        ),
        ({"retry": {"flow": "lookup_item"}}, "invalid_repeat"),
        ({"retry": {"flow": "main"}}, "invalid_repeat"),
        ({"retry_input": {"unknown": {"literal": "x"}}}, "invalid_repeat"),
        ({"retry_input": {"identifier": {"literal": 1}}}, "incompatible_binding_type"),
        ({"until": when("/flows/lookup_item/result/status", gt=1)}, "condition_type_mismatch"),
    ],
)
def test_callable_repeat_rules(tmp_path, change, reason):
    _fails(tmp_path, reason, _item_repeat_flows(_item_repeat(**change)))


def test_a_retry_flow_cannot_repeat_itself(tmp_path):
    flows = _item_repeat_flows(_item_repeat())
    flows["correct"]["repeat"] = {"max_attempts": 2, "until": when("/payload/x", present=True)}
    _fails(tmp_path, "invalid_repeat", flows)


def test_collection_budget_includes_the_item_repeat_worst_case(tmp_path):
    # 4 items x (2 attempts x 1 step + 1 retry x 1 step) = 12 steps.
    plan = compile_document(tmp_path, _item_repeat_flows(_item_repeat()), max_steps=12)
    assert "collection_budget" not in codes(plan)
    plan = compile_document(tmp_path, _item_repeat_flows(_item_repeat()), max_steps=11)
    assert "collection_budget" in codes(plan)
    message = next(item.message for item in plan.diagnostics if item.code == "collection_budget")
    assert "12 steps" in message
