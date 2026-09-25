"""Collection invocation attribution, honest summaries and offline replay."""

import json
from copy import deepcopy

import pytest
import yaml
from handler_contracts import declare
from tests.test_evaluation import result, suite, variant

from foliqant import prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.json import freeze_json
from foliqant.evaluation import Expectation, MetricSpec, evaluate
from foliqant.evaluation.analysis import compare_reports
from foliqant.evaluation.artifact import write_report
from foliqant.evaluation.command import evaluate_configuration
from foliqant.evaluation.dataset import EvaluationDataset, validate_targets

BASE = "/flows/main/steps/collect"


def _usage(count=1):
    return {
        "model_requests": count,
        "tool_calls": 0,
        "output_retries": 0,
        "input_tokens": count * 2,
        "output_tokens": count,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def _child(identity, *, status="completed", elapsed=1.0, flow="child"):
    record = {
        "id": identity,
        "flow": flow,
        "status": status,
        "steps": {"work": {"status": status}},
        "attempt_count": 0 if status == "skipped" else 1,
    }
    if status == "completed":
        record.update(result={"label": "a"}, usage=_usage(), elapsed_seconds=elapsed)
        record["steps"]["work"].update(
            result={"label": "a"}, usage=_usage(), elapsed_seconds=elapsed
        )
    elif status == "failed":
        error = {
            "code": "dependency_failure",
            "message": str(ServiceError(ErrorCode.DEPENDENCY_FAILURE)),
            "retryable": False,
        }
        record.update(error=error, usage=_usage(), elapsed_seconds=elapsed)
        record["steps"]["work"].update(error=error, usage=_usage(), elapsed_seconds=elapsed)
    return record


def _result(children, *, failed=False, marked=True):
    raw = result().model_dump(mode="json")
    ledger = {"items": children}
    step = {
        "status": "failed" if failed else "completed",
        "usage": _usage(len(children)),
        "elapsed_seconds": 5.0,
        "partial_result" if failed else "result": ledger,
    }
    if marked:
        step["kind"] = "flow_collection"
    flow = {
        "status": step["status"],
        "steps": {"collect": step},
        "usage": _usage(len(children)),
        "elapsed_seconds": 5.0,
        "attempt_count": 1,
    }
    if failed:
        error = {
            "code": "dependency_failure",
            "message": str(ServiceError(ErrorCode.DEPENDENCY_FAILURE)),
            "retryable": False,
        }
        step["error"] = flow["error"] = raw["execution"]["error"] = error
        raw["execution"]["status"] = "failed"
    else:
        flow["result"] = raw["payload"] = ledger  # Projections must not duplicate observations.
    raw["execution"]["usage"] = _usage(len(children))
    raw["flows"] = {"main": flow}
    return ExecutionResult.model_validate(raw, strict=True)


async def test_repeated_children_have_unique_paths_and_all_measurements_are_counted():
    output = _result([_child("a", elapsed=1.0), _child("b", elapsed=3.0)])

    async def run(envelope):
        return output

    path = BASE + "/result/items/1/steps/work/result/label"
    report = await evaluate(
        suite(Expectation("second", path, "a")), variant(run), include_details=True
    )
    check = report.cases[0].checks[0]
    assert (check.flow, check.step, check.outcome) == ("child", "work", "passed")
    assert check.invocation_path == BASE + "/result/items/1/steps/work"
    children = [record for record in report.cases[0].flows if record.name == "child"]
    assert len(children) == 2 and len({record.invocation_path for record in children}) == 2
    child = next(flow for flow in report.flows if flow.name == "child")
    step = next(step for step in report.steps if step.flow == "child")
    for summary in (child, step):
        assert summary.observed_invocations == 2
        assert summary.latency.count == 2 and summary.latency.median == 2
        assert summary.usage.model_requests.total == 2
    assert report.usage.model_requests.total == 2  # Never sum nested and inclusive parent totals.
    assert len(report.cases[0].flows) == 3
    assert len(report.cases[0].steps) == 3


async def test_unmarked_business_ledger_shapes_are_not_invocation_records():
    output = _result([_child("a")], marked=False)

    async def run(envelope):
        return output

    path = BASE + "/result/items/0/steps/work/result/label"
    report = await evaluate(suite(Expectation("business", path, "a")), variant(run))
    assert len(report.cases[0].flows) == len(report.cases[0].steps) == 1
    assert (report.cases[0].checks[0].flow, report.cases[0].checks[0].step) == ("main", "collect")


async def test_failed_partial_ledger_retains_completed_failed_and_skipped_children():
    output = _result(
        [_child("a"), _child("b", status="failed"), _child("c", status="skipped")], failed=True
    )

    async def run(envelope):
        return output

    paths = [BASE + f"/partial_result/items/{index}/steps/work/result/label" for index in range(3)]
    report = await evaluate(
        suite(*(Expectation(str(i), path, "a") for i, path in enumerate(paths))),
        variant(run),
        metrics=(MetricSpec("skipped", paths[2], "classification", ("a",)),),
    )
    assert [check.outcome for check in report.cases[0].checks] == ["passed", "error", "skipped"]
    assert {(check.flow, check.step) for check in report.cases[0].checks} == {("child", "work")}
    child = next(flow for flow in report.flows if flow.name == "child")
    assert (child.observed_invocations, child.failed_invocations, child.skipped_invocations) == (
        3,
        1,
        1,
    )
    assert child.latency.count == 2 and child.latency.unavailable == 1
    assert child.usage.model_requests.known_total == 2 and child.usage.model_requests.total is None
    assert report.metrics[0].skipped == 1


async def test_nested_collection_path_uses_innermost_flow_and_step():
    child = _child("outer")
    child["steps"] = {
        "nested": {
            "kind": "flow_collection",
            "status": "completed",
            "result": {"items": [_child("inner", flow="leaf")]},
        }
    }
    output = _result([child])

    async def run(envelope):
        return output

    path = BASE + "/result/items/0/steps/nested/result/items/0/steps/work/result/label"
    report = await evaluate(suite(Expectation("leaf", path, "a")), variant(run))
    assert (report.cases[0].checks[0].flow, report.cases[0].checks[0].step) == ("leaf", "work")
    assert [flow.name for flow in report.cases[0].flows] == ["main", "child", "leaf"]


async def test_compare_allows_result_derived_child_attribution_to_change(tmp_path):
    gold = suite(Expectation("label", BASE + "/result/items/0/result/label", "a"))
    for name, flow in [("before", "child"), ("after", "other")]:

        async def run(envelope, selected=flow):
            return _result([_child("a", flow=selected)])

        report = await evaluate(gold, variant(run, name), include_details=True)
        write_report(
            tmp_path / f"{name}.json",
            [report],
            mode="offline_wiring",
            dataset_name="synthetic",
            dataset_revision="gold",
        )
    compared = compare_reports(tmp_path / "after.json", tmp_path / "before.json").to_dict()
    assert compared["suites"][0]["cases"][0]["outcome"] == "unchanged"


def _project(tmp_path):
    calls = []

    async def handler(inputs, context):
        calls.append(context.flow_id)
        return StepOutcome(inputs["value"])

    registration = HandlerRegistration(handler, freeze_json({"type": "object"}), freeze_json({}))
    directory = tmp_path / "demo"
    directory.mkdir()
    items = [{"id": name, "flow": "child", "input": {"value": name}} for name in ("a", "b")]
    collection = {
        "type": "flow_collection",
        "items": {"pointer": "/payload/items"},
        "flows": ["child"],
        "max_items": 2,
    }
    workflow = {
        "flows": {
            "main": {
                "input": {"items": {"pointer": "/payload/items"}},
                "transition": {"outcome": "completed"},
                "definition": {
                    "steps": [{"id": "collect", "definition": collection}],
                    "output": {"pointer": "/steps/collect/result"},
                },
            },
            "child": {
                "callable": True,
                "definition": {
                    "steps": [
                        {
                            "id": "work",
                            "definition": {
                                "type": "handler",
                                "handler": "echo",
                                "input": {"value": {"pointer": "/payload/value"}},
                            },
                        }
                    ],
                    "output": {"pointer": "/steps/work/result"},
                },
            },
        }
    }
    (directory / "workflow.yaml").write_text(yaml.safe_dump(workflow))
    config = tmp_path / "settings.yaml"
    config.write_text("workflows:\n  demo: demo\nevaluation:\n  dataset: dataset.json\n")
    paths = [
        BASE + "/result/items/1/steps/work/result",
        "/flows/child/steps/work/result",
        "/flows/child/steps/work/result",
        BASE + "/result/items/1/steps/work/result",
    ]
    specs = []
    for i, (flow, step, payload, expected) in enumerate(
        [
            (None, None, {"items": items}, "b"),
            ("child", None, {"value": "c"}, "c"),
            ("child", "work", {"value": "d"}, "d"),
            ("main", "collect", {"items": items}, "b"),
        ]
    ):
        specs.append(
            {
                "name": f"suite{i}",
                "workflow": "demo",
                "flow": flow,
                "step": step,
                "cases": [
                    {
                        "id": "case",
                        "input": {"payload": payload},
                        "expectations": [{"name": "value", "path": paths[i], "expected": expected}],
                    }
                ],
            }
        )
    dataset = {"name": "collections", "revision": "gold", "suites": specs}
    (tmp_path / "dataset.json").write_text(json.dumps(dataset))
    return (
        prepare_application(
            declare(config, {"echo": registration}), handlers={"echo": registration}
        ),
        dataset,
        calls,
    )


async def test_workflow_callable_flow_step_collection_and_replay(tmp_path, monkeypatch):
    prepared, dataset, calls = _project(tmp_path)
    before, code = await evaluate_configuration(prepared, output=tmp_path / "execution.json")
    assert code == 0 and before["passed_checks"] == 4 and len(calls) == 6
    original = json.loads((tmp_path / "execution.json").read_text())

    def no_clients(*args, **kwargs):
        pytest.fail("replay must not open adapters")

    monkeypatch.setattr("foliqant.bootstrap.open_application", no_clients)
    after, code = await evaluate_configuration(
        prepared, replay=tmp_path / "execution.json", output=tmp_path / "replay.json"
    )
    assert code == 0 and after["passed_checks"] == 4 and len(calls) == 6
    replay = json.loads((tmp_path / "replay.json").read_text())
    for old, new in zip(original["reports"], replay["reports"], strict=True):
        assert old["cases"] == new["cases"]
        assert old["steps"] == new["steps"] and old["flows"] == new["flows"]


@pytest.mark.parametrize(
    "path",
    [
        "/flows/child/result",
        BASE + "/result/items/2/result",
        BASE + "/result/items/01/result",
        BASE + "/result/items/0/steps/missing/result",
    ],
)
def test_static_dataset_targets_reject_unavailable_nested_records(tmp_path, path):
    prepared, dataset, _ = _project(tmp_path)
    changed = deepcopy(dataset)
    changed["suites"][0]["cases"][0]["expectations"][0]["path"] = path
    with pytest.raises(ValueError, match="unavailable"):
        validate_targets(EvaluationDataset.model_validate(changed), prepared)


def _deep_value(depth):
    value = "leaf"
    for _ in range(depth):
        value = {"n": value}
    return value


@pytest.mark.parametrize("levels", [1, 3])
async def test_generated_nested_ledger_overhead_does_not_reduce_business_depth(levels, tmp_path):
    business = _deep_value(64)
    child = _child("leaf")
    child["result"] = child["steps"]["work"]["result"] = business
    for _ in range(levels - 1):
        ledger = {"items": [child]}
        child = {
            "id": "nested",
            "flow": "child",
            "status": "completed",
            "attempt_count": 1,
            "result": ledger,
            "steps": {
                "nested": {"kind": "flow_collection", "status": "completed", "result": ledger}
            },
        }
    output = _result([child])

    async def run(envelope):
        return output

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")),
        variant(run),
        include_details=True,
    )
    assert report.cases[0].status == "completed" and report.checks.passed == 1
    assert len(report.cases[0].flows) == levels + 1
    path = tmp_path / "deep.json"
    write_report(
        path, [report], mode="offline_wiring", dataset_name="deep", dataset_revision="gold"
    )
    assert compare_reports(path, path).to_dict()["suites"][0]["cases"][0]["outcome"] == "unchanged"


@pytest.mark.parametrize("marked", [False, True])
async def test_collection_allowance_never_relaxes_unmarked_step_business_depth(marked):
    child = _child("a")
    child["steps"]["work"]["result"] = _deep_value(65)
    output = _result([child], marked=marked)

    async def run(envelope):
        return output

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")), variant(run)
    )
    assert report.cases[0].status == "error" and report.cases[0].error_code == "invalid_input"


async def test_ordinary_payload_business_depth_remains_bounded_without_marked_records():
    raw = result().model_dump(mode="json")
    raw["payload"] = _deep_value(65)
    output = ExecutionResult.model_validate(raw, strict=True)

    async def run(envelope):
        return output

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")), variant(run)
    )
    assert report.cases[0].status == "error" and report.cases[0].error_code == "invalid_input"


async def test_sixteen_collection_levels_keep_full_business_value_depth():
    child = _child("leaf")
    child["result"] = child["steps"]["work"]["result"] = _deep_value(64)
    for _ in range(15):
        child = {
            "id": "nested",
            "flow": "child",
            "status": "completed",
            "attempt_count": 1,
            "result": None,
            "steps": {
                "nested": {
                    "kind": "flow_collection",
                    "status": "completed",
                    "result": {"items": [child]},
                }
            },
        }
    output = _result([child])

    async def run(envelope):
        return output

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")), variant(run)
    )
    assert report.checks.passed == 1 and len(report.cases[0].flows) == 17


async def test_evaluator_rejects_external_results_beyond_supported_collection_depth():
    child = _child("leaf")
    for _ in range(16):
        child = {
            "id": "nested",
            "flow": "child",
            "status": "completed",
            "attempt_count": 1,
            "result": None,
            "steps": {
                "nested": {
                    "kind": "flow_collection",
                    "status": "completed",
                    "result": {"items": [child]},
                }
            },
        }
    output = _result([child])

    async def run(envelope):
        return output

    report = await evaluate(
        suite(Expectation("status", "/execution/status", "completed")), variant(run)
    )
    assert report.cases[0].status == "error" and report.cases[0].error_code == "invalid_output"
