"""One focused test per structural guarantee in docs/configuration/validation.md.

Each test writes a small configuration that breaks exactly one guarantee,
prepares it the way a host does and asserts the stable code together with the
authored file, line, column, field and hint that the problem reports. Warnings
are checked through ``prepare_application(..., strict=True)``, which fails with
every warning. The last tests keep the documentation table and its examples in
sync with this file.
"""

import re
import textwrap
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from foliqant import open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.compiler import CompilationError, Diagnostic
from foliqant.compiler.errors import hint_for, render_problem

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "configuration" / "validation.md"

SETTINGS = """\
models:
  local:
    provider: openai_compatible
    model: example-model
    base_url: http://127.0.0.1:8000/v1
    allow_insecure_http: true
    output_mode: native
handlers:
  lookup:
    input_schema:
      type: object
      properties:
        identifier:
          type: string
      required:
        - identifier
    output_schema:
      type: object
      properties:
        status:
          enum:
            - found
            - not_found
        fund:
          type: string
      required:
        - status
      additionalProperties: false
    effect: read
  note:
    input_schema:
      type: object
    output_schema:
      type: object
    effect: read
"""

# Every flow without a `definition` gets <id>/flow.yaml with one `note` step;
# the flow `lookup` instead returns the `lookup` handler result.
NOTE_FLOW = "steps:\n  - check\n"
NOTE_STEP = "type: handler\nhandler: note\ninput: {}\n"
LOOKUP_FLOW = "output:\n  pointer: /steps/find/result\nsteps:\n  - find\n"
LOOKUP_STEP = (
    "type: handler\nhandler: lookup\ninput:\n  identifier:\n    pointer: /payload/identifier\n"
)


def _project(
    tmp_path: Path,
    workflow: str,
    *,
    settings: str = SETTINGS,
    files: Mapping[str, str] | None = None,
) -> Path:
    """Write settings.yaml, demo/workflow.yaml and conventional flow definitions."""
    config = tmp_path / "config"
    bundle = config / "demo"
    bundle.mkdir(parents=True)
    (config / "settings.yaml").write_text(settings, encoding="utf-8")
    text = textwrap.dedent(workflow)
    (bundle / "workflow.yaml").write_text(text, encoding="utf-8")
    document = yaml.safe_load(text)
    for name, flow in (document.get("flows") or {}).items():
        if isinstance(flow, dict) and "definition" not in flow:
            folder = bundle / name
            folder.mkdir(exist_ok=True)
            if name == "lookup":
                (folder / "flow.yaml").write_text(LOOKUP_FLOW, encoding="utf-8")
                (folder / "find.step.yaml").write_text(LOOKUP_STEP, encoding="utf-8")
            else:
                (folder / "flow.yaml").write_text(NOTE_FLOW, encoding="utf-8")
                (folder / "check.step.yaml").write_text(NOTE_STEP, encoding="utf-8")
    for relative, content in (files or {}).items():
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content), encoding="utf-8")
    return config / "settings.yaml"


def _at(
    tmp_path: Path, marker: str, relative: str = "demo/workflow.yaml", *, last: bool = False
) -> tuple[int, int]:
    """Line and column of the first non-blank character of a marker.

    The marker must be unique unless ``last`` selects its last occurrence.
    """
    text = (tmp_path / "config" / relative).read_text(encoding="utf-8")
    assert text.count(marker) == 1 or (last and text.count(marker) > 1), marker
    start = text.rindex(marker) if last else text.index(marker)
    offset = start + len(marker) - len(marker.lstrip())
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, column


def _check(
    problem: Diagnostic,
    tmp_path: Path,
    code: str,
    *,
    marker: str,
    field: str,
    level: str = "error",
    relative: str = "demo/workflow.yaml",
    last: bool = False,
) -> None:
    """The problem names its code, file, line, column, field, message and hint."""
    line, column = _at(tmp_path, marker, relative, last=last)
    assert (problem.code, problem.level) == (code, level)
    assert (problem.location.path, problem.location.line, problem.location.column) == (
        relative,
        line,
        column,
    )
    assert problem.field == field
    assert problem.message and problem.hint == hint_for(code)
    assert render_problem(problem) == (
        f"{relative}:{line}:{column}: {code} at {field}: {problem.message} (hint: {problem.hint})"
    )


def _error(settings: Path, **options: object) -> CompilationError:
    with pytest.raises(CompilationError) as caught:
        prepare_application(settings, **options)  # type: ignore[arg-type]
    error = caught.value
    assert str(error) == "\n".join(render_problem(item) for item in error.problems)
    return error


def _fails(
    tmp_path: Path, workflow: str, code: str, *, settings: str = SETTINGS, **check: object
) -> CompilationError:
    error = _error(_project(tmp_path, workflow, settings=settings))
    assert len(error.problems) == 1
    _check(error.problems[0], tmp_path, code, **check)  # type: ignore[arg-type]
    return error


def _warns(
    tmp_path: Path, workflow: str, code: str, *, settings: str = SETTINGS, **check: object
) -> Diagnostic:
    """A warning compiles, but a strict preparation fails with every warning."""
    path = _project(tmp_path, workflow, settings=settings)
    settings_path = path
    prepared = prepare_application(settings_path)
    assert code in {item.code for item in prepared.diagnostics}
    error = _error(settings_path, strict=True)
    assert error.problems == tuple(item for item in prepared.diagnostics if item.level == "warning")
    problem = next(item for item in error.problems if item.code == code)
    _check(problem, tmp_path, code, level="warning", **check)  # type: ignore[arg-type]
    return problem


def _info(tmp_path: Path, workflow: str, code: str, **check: str) -> Diagnostic:
    settings = _project(tmp_path, workflow)
    prepared = prepare_application(settings, strict=True)
    problem = next(item for item in prepared.diagnostics if item.code == code)
    _check(problem, tmp_path, code, level="info", **check)
    return problem


# Acyclic graph -----------------------------------------------------------------


def test_cycle_between_flows_is_rejected(tmp_path):
    error = _fails(
        tmp_path,
        """\
        start: first
        flows:
          first:
            input: {}
            transition:
              flow: second
          second:
            input: {}
            transition:
              flow: first
        """,
        "workflow_cycle",
        marker="flow: first\n",
        field="flows.second.transition.flow",
    )
    assert "`first` -> `second` -> `first`" in error.message


def test_self_transition_is_a_cycle(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          only:
            input: {}
            transition:
              flow: only
        """,
        "workflow_cycle",
        marker="flow: only",
        field="flows.only.transition.flow",
    )


def test_cycle_through_a_retry_flow_is_rejected(tmp_path):
    error = _fails(
        tmp_path,
        """\
        flows:
          main:
            input: {}
            definition:
              steps:
                - id: each
                  definition:
                    type: flow_collection
                    items:
                      literal: []
                    flows:
                      - item
            transition:
              outcome: completed
          item:
            callable: true
            repeat:
              max_attempts: 2
              until:
                binding:
                  pointer: /flows/item/result/done
                equals: true
              retry:
                flow: fix
          fix:
            callable: true
            definition:
              steps:
                - id: again
                  definition:
                    type: flow_collection
                    items:
                      literal: []
                    flows:
                      - item
        """,
        "workflow_cycle",
        marker="item\n",
        last=True,
        field="flows.fix.definition.steps.0.definition.flows.0",
    )
    assert "`item` -> `fix` -> `item`" in error.message


# Every flow reachable, every target defined ------------------------------------


def test_unreachable_flow_is_rejected(tmp_path):
    _fails(
        tmp_path,
        """\
        start: first
        flows:
          first:
            input: {}
            transition:
              outcome: completed
          orphan:
            input: {}
            transition:
              outcome: completed
        """,
        "unreachable_flow",
        marker="orphan:",
        field="flows.orphan",
    )


def test_flow_behind_a_route_entry_that_can_never_be_selected_is_unreachable(tmp_path):
    error = _fails(
        tmp_path,
        """\
        input_schema:
          type: object
          properties:
            kind:
              enum:
                - fund
                - share
          required:
            - kind
        start:
          route:
            - when:
                binding:
                  pointer: /payload/kind
                not_equals: bond
              flow: first
            - flow: fallback
        flows:
          first:
            input: {}
            transition:
              outcome: completed
          fallback:
            input: {}
            transition:
              outcome: completed
        """,
        "unreachable_flow",
        marker="fallback:",
        field="flows.fallback",
    )
    assert "entry 1 of `start`" in error.message


def test_undefined_route_target_is_rejected(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              flow: missing
        """,
        "missing_flow",
        marker="flow: missing",
        field="flows.first.transition.flow",
    )


def test_undefined_start_is_rejected(tmp_path):
    _fails(
        tmp_path,
        """\
        start: missing
        flows:
          first:
            input: {}
            transition:
              outcome: completed
          second:
            input: {}
            transition:
              outcome: completed
        """,
        "missing_start",
        marker="start: missing",
        field="start",
    )


def test_routes_cannot_enter_callable_flows(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              flow: helper
          helper:
            callable: true
        """,
        "invalid_routed_flow",
        marker="flow: helper",
        field="flows.first.transition.flow",
    )


# Every transition terminates ---------------------------------------------------


def test_route_needs_a_final_otherwise_entry(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              route:
                - when:
                    binding:
                      pointer: /payload/urgent
                    equals: true
                  outcome: completed
        """,
        "route_without_otherwise",
        marker="when:",
        field="flows.first.transition.route.0",
    )


def test_only_the_last_route_entry_may_omit_when(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              route:
                - outcome: completed
                - when:
                    binding:
                      pointer: /payload/urgent
                    equals: true
                  outcome: needs_review
        """,
        "misplaced_otherwise",
        marker="outcome: completed",
        field="flows.first.transition.route.0",
    )


def test_cases_need_a_default_target(tmp_path):
    error = _fails(
        tmp_path,
        """\
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            transition:
              binding:
                pointer: /flows/lookup/result/status
              cases:
                found:
                  outcome: completed
        """,
        "invalid_contract",
        marker="transition:",
        field="flows.lookup.transition.default",
    )
    assert error.message == "A required field is missing."


# Review routes -----------------------------------------------------------------


def test_review_never_completes_a_run(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              outcome: completed
            on_unresolved:
              default:
                outcome: completed
        """,
        "review_completes_run",
        marker="outcome: completed\n",
        last=True,
        field="flows.first.on_unresolved.default.outcome",
    )


def test_review_route_cannot_target_its_own_flow(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              outcome: completed
            on_unresolved:
              flow: first
        """,
        "review_route_to_self",
        marker="flow: first",
        field="flows.first.on_unresolved.flow",
    )


def test_inherited_review_default_cannot_close_a_cycle_through_a_retried_flow(tmp_path):
    error = _fails(
        tmp_path,
        """\
        defaults:
          on_unresolved:
            flow: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 2
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
              retry:
                flow: correct
            transition:
              outcome: completed
          correct:
            callable: true
        """,
        "invalid_default_review_route",
        marker="on_unresolved:",
        field="defaults.on_unresolved",
    )
    assert "`lookup` is the flow itself" in error.message


def test_review_ending_the_run_without_the_projected_result_is_a_warning(tmp_path):
    problem = _warns(
        tmp_path,
        """\
        output:
          pointer: /flows/answer/result
          default: null
        start: classify
        flows:
          classify:
            input: {}
            transition:
              flow: answer
          answer:
            input: {}
            transition:
              outcome: completed
            on_unresolved:
              outcome: needs_review
        """,
        "review_ends_run",
        marker="classify:\n",
        field="flows.classify.on_unresolved",
    )
    assert "the workflow output default null" in problem.message


def test_review_returning_the_output_flow_result_is_info(tmp_path):
    problem = _info(
        tmp_path,
        """\
        output:
          pointer: /flows/answer/result
        flows:
          answer:
            input: {}
            transition:
              outcome: completed
        """,
        "review_ends_run",
        marker="answer:\n",
        field="flows.answer.on_unresolved",
    )
    assert "the projected result of `answer`" in problem.message


# Bounded repeat ----------------------------------------------------------------


def test_repeat_attempts_are_bounded(tmp_path):
    error = _fails(
        tmp_path,
        """\
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 100
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
            transition:
              outcome: completed
        """,
        "invalid_contract",
        marker="max_attempts: 100",
        field="flows.lookup.repeat.max_attempts",
    )
    assert error.message == "The value is above the allowed maximum."


def test_retry_flow_must_be_a_callable_flow(tmp_path):
    error = _fails(
        tmp_path,
        """\
        start: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 2
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
              retry:
                flow: other
            transition:
              flow: other
          other:
            input: {}
            transition:
              outcome: completed
        """,
        "invalid_repeat",
        marker="flow: other\n    transition",
        field="flows.lookup.repeat.retry.flow",
    )
    assert "must be `callable: true`" in error.message


def test_repeat_worst_case_must_fit_the_step_budget(tmp_path):
    settings = SETTINGS + "execution:\n  max_steps: 8\n"
    workflow = """\
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 5
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
              retry:
                flow: correct
            transition:
              outcome: completed
          correct:
            callable: true
        """
    error = _error(_project(tmp_path, workflow, settings=settings))
    _check(
        error.problems[0],
        tmp_path,
        "repeat_budget",
        marker="max_attempts: 5",
        field="flows.lookup.repeat.max_attempts",
    )
    assert "may visit 9 steps (5 attempts x 1 steps + 4 retries x 1 steps)" in error.message


# Bounded collections -----------------------------------------------------------


_COLLECTION = """\
flows:
  main:
    input: {{}}
    definition:
      steps:
        - id: each
          definition:
            type: flow_collection
            items:
              pointer: /payload/items
            flows:
              - item
            max_items: {max_items}
    transition:
      outcome: completed
  item:
    callable: true
"""


def test_collection_size_is_bounded(tmp_path):
    error = _fails(
        tmp_path,
        _COLLECTION.format(max_items=5000),
        "invalid_contract",
        marker="max_items: 5000",
        field="flows.main.definition.steps.0.definition.max_items",
    )
    assert error.message == "The value is above the allowed maximum."


def test_collection_nesting_depth_is_bounded(tmp_path):
    levels = 17
    lines = ["flows:", "  main:", "    input: {}", "    definition:", "      steps:"]
    lines += _collection_lines("level_1", indent=8)
    lines += ["    transition:", "      outcome: completed"]
    for level in range(1, levels + 1):
        lines += [f"  level_{level}:", "    callable: true"]
        if level < levels:
            lines += ["    definition:", "      steps:"]
            lines += _collection_lines(f"level_{level + 1}", indent=8)
    error = _fails(
        tmp_path,
        "\n".join(lines) + "\n",
        "collection_depth_exceeded",
        marker="main:",
        field="flows.main",
    )
    assert "17 collection levels" in error.message


def _collection_lines(target: str, *, indent: int) -> list[str]:
    pad = " " * indent
    return [
        f"{pad}- id: each",
        f"{pad}  definition:",
        f"{pad}    type: flow_collection",
        f"{pad}    items:",
        f"{pad}      literal: []",
        f"{pad}    flows:",
        f"{pad}      - {target}",
    ]


def test_collection_worst_case_counts_nested_collections(tmp_path):
    workflow = """\
        flows:
          main:
            input: {}
            definition:
              steps:
                - id: each
                  definition:
                    type: flow_collection
                    items:
                      pointer: /payload/items
                    flows:
                      - item
                    max_items: 4
            transition:
              outcome: completed
          item:
            callable: true
            definition:
              steps:
                - id: nested
                  definition:
                    type: flow_collection
                    items:
                      pointer: /payload/items
                    flows:
                      - leaf
                    max_items: 4
          leaf:
            callable: true
        """
    problem = _warns(
        tmp_path,
        workflow,
        "collection_budget",
        settings=SETTINGS + "execution:\n  max_steps: 16\n",
        marker="max_items: 4\n    transition",
        field="flows.main.definition.steps.0.definition.max_items",
    )
    # 4 items x (1 nested collection step + 4 leaf items x 1 step) = 20 > 16.
    assert "may visit 20 steps" in problem.message


def test_repeat_around_a_collection_counts_every_attempt(tmp_path):
    workflow = """\
        start: main
        flows:
          main:
            input: {}
            definition:
              output:
                literal:
                  done: false
              steps:
                - id: each
                  definition:
                    type: flow_collection
                    items:
                      pointer: /payload/items
                    flows:
                      - item
                    max_items: 50
            repeat:
              max_attempts: 3
              until:
                binding:
                  pointer: /flows/main/result/done
                equals: true
            transition:
              outcome: completed
          item:
            callable: true
        """
    problem = _warns(
        tmp_path,
        workflow,
        "run_budget",
        marker="main:\n    input",
        field="flows.main",
    )
    # 3 attempts x (1 collection step + 50 items x 1 step) = 153 > 128.
    assert "may visit 153 steps" in problem.message


# Bindings resolvable on every path ---------------------------------------------


def test_flow_input_needs_a_flow_that_runs_on_every_path(tmp_path):
    error = _fails(
        tmp_path,
        """\
        start: classify
        flows:
          classify:
            input: {}
            transition:
              route:
                - when:
                    binding:
                      pointer: /payload/urgent
                    equals: true
                  flow: escalate
                - flow: answer
          escalate:
            input: {}
            transition:
              flow: answer
          answer:
            input:
              escalation:
                pointer: /flows/escalate/result
            transition:
              outcome: completed
        """,
        "unavailable_flow_reference",
        marker="escalation:",
        field="flows.answer.input.escalation",
    )
    assert "`escalate`" in error.message and "Add a `default`" in error.message


def test_first_of_needs_a_member_available_on_every_path(tmp_path):
    _fails(
        tmp_path,
        """\
        start: classify
        flows:
          classify:
            input: {}
            transition:
              route:
                - when:
                    binding:
                      pointer: /payload/urgent
                    equals: true
                  flow: left
                - flow: right
          left:
            input: {}
            transition:
              flow: join
          right:
            input: {}
            transition:
              flow: join
          join:
            input:
              branch:
                first_of:
                  - pointer: /flows/left/result
                  - pointer: /flows/right/result
            transition:
              outcome: completed
        """,
        "unavailable_flow_reference",
        marker="branch:",
        field="flows.join.input.branch",
    )


def test_cases_binding_is_a_required_pointer(tmp_path):
    _fails(
        tmp_path,
        """\
        start: classify
        flows:
          classify:
            input: {}
            transition:
              route:
                - when:
                    binding:
                      pointer: /payload/identifier
                    present: true
                  flow: lookup
                - flow: answer
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            transition:
              flow: answer
          answer:
            input: {}
            transition:
              binding:
                pointer: /flows/lookup/result/status
              cases:
                found:
                  outcome: completed
              default:
                outcome: needs_review
              default_covers:
                - not_found
        """,
        "unavailable_flow_reference",
        marker="binding:\n        pointer: /flows/lookup/result/status",
        field="flows.answer.transition.binding",
    )


_CONDITIONAL_LOOKUP = """\
start: classify
flows:
  classify:
    input: {{}}
    transition:
      route:
        - when:
            binding:
              pointer: {classify_pointer}
            present: true
          flow: lookup
        - flow: answer
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      flow: answer
  answer:
    input: {{}}
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup/result/status
            equals: found
          outcome: completed
        - outcome: needs_review
    on_unresolved:
      outcome: needs_review
"""


def test_route_conditions_tolerate_absence_but_not_flows_that_never_ran(tmp_path):
    # `lookup` does not run on every path to `answer`: its condition is false then.
    absent = _CONDITIONAL_LOOKUP.format(classify_pointer="/payload/identifier")
    prepare_application(_project(tmp_path / "absent", absent))
    # `answer` can never have run when `classify` selects its route.
    never = _CONDITIONAL_LOOKUP.format(classify_pointer="/flows/answer/result")
    error = _fails(
        tmp_path / "never",
        never,
        "unavailable_flow_reference",
        marker="when:\n            binding:\n              pointer: /flows/answer",
        field="flows.classify.transition.route.0.when",
    )
    assert "`answer`, which can never have run" in error.message


def test_retry_flow_result_needs_a_default(tmp_path):
    error = _fails(
        tmp_path,
        """\
        start: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 2
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
              retry:
                flow: correct
            transition:
              flow: answer
          correct:
            callable: true
          answer:
            input:
              correction:
                pointer: /flows/correct/result
            transition:
              outcome: completed
            on_unresolved:
              outcome: needs_review
        """,
        "unavailable_flow_reference",
        marker="correction:",
        field="flows.answer.input.correction",
    )
    assert "retry flow runs only after a failed attempt" in error.message


def test_later_repeat_attempts_need_a_default(tmp_path):
    _fails(
        tmp_path,
        """\
        start: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            repeat:
              max_attempts: 2
              until:
                binding:
                  pointer: /flows/lookup/result/status
                equals: found
              retry:
                flow: correct
            transition:
              flow: answer
          correct:
            callable: true
          answer:
            input:
              second:
                pointer: /flows/lookup/attempts/1/result
            transition:
              outcome: completed
            on_unresolved:
              outcome: needs_review
        """,
        "unavailable_value",
        marker="second:",
        field="flows.answer.input.second",
    )


def test_a_flow_result_default_must_hold_the_selected_key(tmp_path):
    workflow = """\
        start: check
        flows:
          check:
            input: {}
            definition:
              output:
                pointer: /steps/second/result
                default: {}
              steps:
                - id: first
                  definition:
                    type: handler
                    handler: note
                    input: {}
                - id: second
                  definition:
                    type: handler
                    handler: note
                    input: {}
            transition:
              flow: answer
            on_unresolved:
              flow: answer
          answer:
            input:
              status:
                pointer: /flows/check/result/status
            transition:
              outcome: completed
            on_unresolved:
              outcome: needs_review
        """
    error = _fails(
        tmp_path,
        workflow,
        "unavailable_value",
        marker="status:\n        pointer",
        field="flows.answer.input.status",
    )
    assert "`default` lacks" in error.message


def test_skipped_step_results_need_a_default(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            definition:
              steps:
                - id: check
                  definition:
                    type: handler
                    handler: note
                    input: {}
                - id: repair
                  when:
                    binding:
                      pointer: /steps/check/result/broken
                    equals: true
                  definition:
                    type: handler
                    handler: note
                    input: {}
                - id: report
                  definition:
                    type: handler
                    handler: note
                    input:
                      repaired:
                        pointer: /steps/repair/result
            transition:
              outcome: completed
        """,
        "unavailable_step_reference",
        marker="repaired:",
        field="flows.first.definition.steps.2.definition.input.repaired",
    )


def test_flow_output_is_projected_on_review_so_later_steps_need_a_default(tmp_path):
    _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            definition:
              output:
                pointer: /steps/second/result
              steps:
                - id: first
                  definition:
                    type: handler
                    handler: note
                    input: {}
                - id: second
                  definition:
                    type: handler
                    handler: note
                    input: {}
            transition:
              outcome: completed
        """,
        "incompatible_output_binding",
        marker="output:",
        field="flows.first.definition.output",
    )


def test_a_selection_is_missing_when_the_first_step_stops_for_review(tmp_path):
    _fails(
        tmp_path,
        """\
        defaults:
          model: local
        flows:
          classify:
            input:
              message:
                pointer: /payload/message
            definition:
              output:
                pointer: /steps/classify/selection/category/id
              steps:
                - id: classify
                  definition:
                    type: decision
                    instructions: Classify the message.
                    sources:
                      message:
                        pointer: /payload/message
                    question:
                      type: choice
                      criteria:
                        - Select the requested queue.
                      catalog:
                        categories:
                          - id: billing
                            description: Invoices, charges and refunds.
                          - id: cancellation
                            description: Ending a subscription.
            transition:
              outcome: completed
        """,
        "incompatible_output_binding",
        marker="output:",
        field="flows.classify.definition.output",
    )


# No dangling pointers into known schemas ---------------------------------------


def test_pointer_outside_a_closed_schema_is_dangling(tmp_path):
    error = _fails(
        tmp_path,
        """\
        start: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            transition:
              flow: answer
          answer:
            input:
              owner:
                pointer: /flows/lookup/result/owner
            transition:
              outcome: completed
            on_unresolved:
              outcome: needs_review
        """,
        "dangling_pointer",
        marker="owner:",
        field="flows.answer.input.owner",
    )
    assert "`/flows/lookup/result/owner` can never resolve" in error.message


def test_attempts_of_a_flow_that_does_not_repeat_are_invalid(tmp_path):
    _fails(
        tmp_path,
        """\
        start: first
        flows:
          first:
            input: {}
            transition:
              flow: second
          second:
            input:
              tries:
                pointer: /flows/first/attempts
            transition:
              outcome: completed
        """,
        "invalid_flow_reference",
        marker="tries:",
        field="flows.second.input.tries",
    )


# Route case coverage -----------------------------------------------------------


_CASES = """\
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      binding:
        pointer: /flows/lookup/result/status
      cases:
{cases}
      default:
        outcome: needs_review
{extra}"""


def test_case_that_can_never_match_is_rejected(tmp_path):
    error = _fails(
        tmp_path,
        _CASES.format(cases="        fonud:\n          outcome: completed", extra=""),
        "unmatched_case",
        marker="fonud:",
        field="flows.lookup.transition.cases",
    )
    assert "`fonud`" in error.message and "`found`, `not_found`" in error.message


def test_allowed_value_without_a_case_is_a_warning(tmp_path):
    problem = _warns(
        tmp_path,
        _CASES.format(cases="        found:\n          outcome: completed", extra=""),
        "uncovered_value",
        marker="cases:",
        field="flows.lookup.transition.cases",
    )
    assert "not_found" in problem.message


def test_default_covers_must_equal_the_uncovered_values(tmp_path):
    error = _fails(
        tmp_path,
        _CASES.format(
            cases="        found:\n          outcome: completed",
            extra="      default_covers:\n        - found\n",
        ),
        "default_covers_mismatch",
        marker="default_covers:",
        field="flows.lookup.transition.default_covers",
    )
    assert "`not_found`" in error.message


def test_cases_bind_only_strings_or_null(tmp_path):
    _fails(
        tmp_path,
        """\
        input_schema:
          type: object
          properties:
            priority:
              enum:
                - high
                - 1
        flows:
          first:
            input: {}
            transition:
              binding:
                pointer: /payload/priority
              cases:
                high:
                  outcome: completed
              default:
                outcome: needs_review
        """,
        "incompatible_route_type",
        marker="binding:",
        field="flows.first.transition.binding",
    )


# Conditions --------------------------------------------------------------------


_CONDITION = """\
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup/result/status
            {operator}
          outcome: completed
        - outcome: needs_review
"""


def test_condition_operand_must_fit_the_field_type(tmp_path):
    _fails(
        tmp_path,
        _CONDITION.format(operator="gt: 3"),
        "condition_type_mismatch",
        marker="when:",
        field="flows.lookup.transition.route.0.when",
    )


def test_condition_that_can_never_hold_is_a_warning(tmp_path):
    problem = _warns(
        tmp_path,
        _CONDITION.format(operator="equals: fonud"),
        "condition_always_false",
        marker="when:",
        field="flows.lookup.transition.route.0.when",
    )
    assert "`/flows/lookup/result/status equals`" in problem.message


def test_route_entry_that_can_never_be_selected_is_a_warning(tmp_path):
    _warns(
        tmp_path,
        _CONDITION.format(operator="equals: fonud"),
        "route_unreachable_entry",
        marker="when:",
        field="flows.lookup.transition.route.0",
    )


def test_matches_patterns_must_have_bounded_backtracking(tmp_path):
    error = _fails(
        tmp_path,
        """\
        flows:
          first:
            input: {}
            transition:
              route:
                - when:
                    binding:
                      pointer: /payload/reference
                    matches: (a+)+b
                  outcome: completed
                - outcome: needs_review
        """,
        "unsafe_pattern",
        marker="when:",
        field="flows.first.transition.route.0.when",
    )
    assert "(a+)+b" not in str(error)


# Declared handler contracts ----------------------------------------------------


_HANDLER_FLOW = """\
flows:
  first:
    input: {{}}
    definition:
      steps:
        - id: act
          definition:
            type: handler
            handler: {handler}
            input: {{}}
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
"""


def test_handler_steps_need_a_declared_contract(tmp_path):
    error = _fails(
        tmp_path,
        _HANDLER_FLOW.format(handler="notify"),
        "unknown_handler",
        marker="handler: notify",
        field="flows.first.definition.steps.0.definition.handler",
    )
    assert "`notify`" in error.message


async def _note(_inputs, _context):  # pragma: no cover - never executed here
    raise AssertionError


def test_registration_must_match_its_declaration(tmp_path):
    settings = _project(tmp_path, _HANDLER_FLOW.format(handler="note"))
    registration = HandlerRegistration(_note, {"type": "object"}, {"type": "string"})
    error = _error(settings, handlers={"note": registration})
    _check(
        error.problems[0],
        tmp_path,
        "handler_contract_mismatch",
        marker="output_schema:\n      type: object\n    effect",
        field="handlers.note.output_schema/type",
        relative="settings.yaml",
    )


async def test_declared_handlers_need_a_registration_to_run(tmp_path):
    settings = _project(tmp_path, _HANDLER_FLOW.format(handler="note"))
    prepared = prepare_application(settings)
    with pytest.raises(CompilationError) as caught:
        async with open_application(prepared, environment={}):
            pass
    codes = [item.code for item in caught.value.problems]
    assert codes == ["missing_handler_registration", "missing_handler_registration"]
    _check(
        caught.value.problems[0],
        tmp_path,
        "missing_handler_registration",
        marker="lookup:\n    input_schema",
        field="handlers.lookup",
        relative="settings.yaml",
    )


# Prompt inputs -----------------------------------------------------------------


_LLM_FLOW = """\
defaults:
  model: local
flows:
  answer:
    input:
      message:
        pointer: /payload/message
      language:
        pointer: /payload/language
    definition:
      steps:
        - id: reply
          definition:
            type: llm
            instructions: Answer the message.
            input:
              message:
                pointer: /payload/message
              language:
                pointer: /payload/language
            prompt: "{prompt}"
            output: text
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
"""


def test_llm_input_not_used_by_the_prompt_is_a_warning(tmp_path):
    problem = _warns(
        tmp_path,
        _LLM_FLOW.format(prompt="Message: {{ message }}"),
        "unused_llm_input",
        marker="language:\n                pointer: /payload/language\n            prompt",
        field="flows.answer.definition.steps.0.definition.input",
    )
    assert "language" in problem.message


def test_prompt_placeholders_must_name_declared_inputs(tmp_path):
    _fails(
        tmp_path,
        _LLM_FLOW.format(prompt="Message: {{ body }}"),
        "invalid_prompt",
        marker="prompt:",
        field="flows.answer.definition.steps.0.definition.prompt",
    )


def test_repeating_a_model_step_without_a_retry_is_a_warning(tmp_path):
    workflow = _LLM_FLOW.format(prompt="{{ message }} {{ language }}").replace(
        "    on_unresolved:\n",
        "    repeat:\n      max_attempts: 2\n      until:\n        binding:\n"
        "          pointer: /flows/answer/result\n        present: true\n"
        "    on_unresolved:\n",
    )
    _warns(
        tmp_path,
        workflow,
        "repeat_without_retry",
        marker="repeat:",
        field="flows.answer.repeat",
    )


# Hard failure and host plumbing ------------------------------------------------


def test_strict_preparation_lists_every_warning(tmp_path):
    settings = _project(
        tmp_path,
        """\
        start: lookup
        flows:
          lookup:
            input:
              identifier:
                pointer: /payload/identifier
            transition:
              binding:
                pointer: /flows/lookup/result/status
              cases:
                found:
                  outcome: completed
              default:
                flow: other
          other:
            input: {}
            transition:
              outcome: needs_review
            on_unresolved:
              outcome: needs_review
        """,
    )
    prepared = prepare_application(settings)
    warnings = [item for item in prepared.diagnostics if item.level == "warning"]
    assert [item.code for item in warnings] == ["uncovered_value", "review_ends_run"]
    error = _error(settings, strict=True)
    assert error.problems == tuple(warnings)
    assert error.diagnostics == prepared.diagnostics
    assert len(str(error).splitlines()) == len(warnings)
    assert error.reason == "uncovered_value" and error.field == "flows.lookup.transition.cases"


async def test_open_application_refuses_warnings_of_a_strict_preparation(tmp_path):
    settings = _project(tmp_path, _HANDLER_FLOW.format(handler="note"))
    registrations = {"note": HandlerRegistration(_note), "lookup": HandlerRegistration(_note)}
    prepared = prepare_application(settings, handlers=registrations, strict=True)
    assert prepared.strict and not prepared.diagnostics
    warning = Diagnostic(
        "uncovered_value", "warning", prepared.plans["demo"].location, "A later warning."
    )
    # A host that adds or replaces diagnostics cannot bypass the strict request.
    tampered = replace(prepared, diagnostics=(warning,))
    with pytest.raises(CompilationError) as caught:
        async with open_application(tampered, environment={}):
            pass
    assert caught.value.reason == "uncovered_value"
    # Without strict, the same diagnostics do not block activation.
    async with open_application(replace(tampered, strict=False), environment={}) as app:
        assert app.ready


# Documentation stays in sync ---------------------------------------------------


def _documented_rows() -> list[tuple[str, str]]:
    """``(code, test name)`` of every row of the guarantees table."""
    rows = re.findall(
        r"^\| [^|]+ \| `([a-z_]+)`[^|]*\| [a-z]+ \| `(test_[a-z_]+)` \|",
        GUIDE.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert rows, "the guarantees table was not found"
    return rows


def test_every_documented_guarantee_has_a_test_in_this_module():
    names = {name for name in globals() if name.startswith("test_")}
    missing = [test for _, test in _documented_rows() if test not in names]
    assert missing == []


_EXAMPLE = re.compile(
    r"```yaml\n# demo/workflow\.yaml: (?P<verdict>fails|warns) with (?P<code>[a-z_]+)\n"
    r"(?P<body>.*?)```",
    re.DOTALL,
)


def _examples() -> list[tuple[str, str, str]]:
    return [
        (match["verdict"], match["code"], match["body"])
        for match in _EXAMPLE.finditer(GUIDE.read_text(encoding="utf-8"))
    ]


def test_the_guide_shows_an_example_for_every_documented_code():
    documented = {code for code, _ in _documented_rows()}
    shown = {code for _, code, _ in _examples()}
    # Registrations live in host code; seventeen nested flows are too long to show.
    not_shown = {
        "handler_contract_mismatch",
        "missing_handler_registration",
        "collection_depth_exceeded",
    }
    assert documented - shown <= not_shown


@pytest.mark.parametrize(
    "verdict,code,body",
    _examples(),
    ids=[f"{item[1]}-{index}" for index, item in enumerate(_examples())],
)
def test_documented_examples_fail_with_their_code(tmp_path, verdict, code, body):
    settings = _project(tmp_path, body)
    if verdict == "fails":
        assert _error(settings).reason == code
    else:
        assert code in {item.code for item in _error(settings, strict=True).problems}
