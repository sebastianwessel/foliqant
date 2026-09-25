"""Persisted report comparison is strict, descriptive, and completely offline."""

import json
import stat
from pathlib import Path

import pytest

from foliqant.cli import main
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.execution import RunResult, TokenUsage, Usage
from foliqant.core.json import freeze_json
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    MetricSpec,
    compare_reports,
    evaluate,
)
from foliqant.evaluation.artifact import write_report


def _result(payload: object, *, input_tokens: int) -> ExecutionResult:
    return to_execution_result(
        RunResult(
            "invocation",
            "demo",
            "workflow-revision",
            "completed",
            freeze_json(payload),
            {},
            (),
            Usage(model_requests=1, tokens=TokenUsage(input_tokens, 1)),
        )
    )


async def _artifact(
    path: Path,
    payload: dict[str, object],
    *,
    variant: str,
    configuration: str,
    input_tokens: int,
    repeat: int = 1,
) -> None:
    suite = EvaluationSuite(
        "comparison",
        "gold-1",
        (
            EvaluationCase(
                "case",
                Envelope(payload={"value": 1}, metadata={"language": "en"}),
                (
                    Expectation("label", "/payload/label", "right"),
                    Expectation("reason", "/payload/reason", "expected"),
                ),
            ),
        ),
    )

    async def invoke(envelope: Envelope) -> ExecutionResult:
        del envelope
        return _result(payload, input_tokens=input_tokens)

    report = await evaluate(
        suite,
        EvaluationVariant(variant, variant, invoke, configuration, workflow="demo"),
        include_details=True,
        repeat=repeat,
        metrics=(MetricSpec("label", "/payload/label", "classification", ("right", "wrong")),),
    )
    write_report(
        path,
        (report,),
        mode="execution",
        dataset_name="private-gold",
        dataset_revision="1",
    )


async def test_compare_reports_derives_mixed_changes_metrics_and_usage(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {"label": "wrong", "reason": "expected"},
        variant="baseline",
        configuration="config-a",
        input_tokens=10,
        repeat=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "changed"},
        variant="candidate",
        configuration="config-b",
        input_tokens=7,
        repeat=2,
    )

    comparison = compare_reports(candidate, baseline).to_dict()
    assert comparison["case_changes"] == {
        "improved": 0,
        "regressed": 0,
        "mixed": 2,
        "unchanged": 0,
    }
    suite = comparison["suites"][0]
    assert suite["checks"]["pass_rate_delta"] == 0
    assert suite["metrics"][0]["accuracy_delta"] == 1
    tokens = suite["usage"]["input_tokens"]
    assert tokens["baseline"]["total"] == 20
    assert tokens["candidate"]["total"] == 14
    assert tokens["total_delta"] == -6
    assert (
        comparison["interpretation"]
        == "descriptive_paired_bootstrap_intervals_no_release_threshold"
    )


async def test_compare_ignores_tampered_aggregates_but_rejects_changed_semantics(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {"label": "wrong", "reason": "expected"},
        variant="baseline",
        configuration="a",
        input_tokens=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "expected"},
        variant="candidate",
        configuration="b",
        input_tokens=2,
    )
    document = json.loads(candidate.read_text())
    document["reports"][0]["checks"] = {
        "total": 999,
        "passed": 0,
        "failed": 999,
        "missing": 0,
        "skipped": 0,
        "errors": 0,
    }
    document["reports"][0]["metrics"][0]["accuracy"] = 0
    candidate.write_text(json.dumps(document))
    compared = compare_reports(candidate, baseline).to_dict()["suites"][0]
    assert compared["checks"]["total"] == 2
    assert compared["metrics"][0]["candidate"]["accuracy"] == 1

    # JSON booleans and numbers are distinct evaluation inputs despite Python equality.
    document["reports"][0]["cases"][0]["details"]["input"]["payload"]["value"] = True
    candidate.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="input or gold differs"):
        compare_reports(candidate, baseline)


async def test_compare_rejects_catalog_or_gold_drift_and_reports_replay_timing_unavailable(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {"label": "wrong", "reason": "expected"},
        variant="baseline",
        configuration="a",
        input_tokens=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "expected"},
        variant="candidate",
        configuration="b",
        input_tokens=2,
    )
    document = json.loads(candidate.read_text())
    document["mode"] = "replay"
    candidate.write_text(json.dumps(document))
    latency = compare_reports(candidate, baseline).to_dict()["suites"][0]["latency"]
    assert latency == {"available": False, "reason": "replay_timing_not_comparable"}

    document["reports"][0]["metrics"][0]["labels"].append("new")
    candidate.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="metric catalog differs"):
        compare_reports(candidate, baseline)

    document["mode"] = "invented"
    candidate.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="invalid evaluation report mode"):
        compare_reports(candidate, baseline)


async def test_compare_rejects_inconsistent_attempts_status_and_usage(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {"label": "right", "reason": "expected"},
        variant="baseline",
        configuration="a",
        input_tokens=2,
        repeat=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "expected"},
        variant="candidate",
        configuration="b",
        input_tokens=2,
        repeat=2,
    )
    original = json.loads(candidate.read_text())

    changed = json.loads(json.dumps(original))
    changed["reports"][0]["cases"][1]["repetition"] = 1
    candidate.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="repetition order"):
        compare_reports(candidate, baseline)

    changed = json.loads(json.dumps(original))
    changed["reports"][0]["cases"][0]["status"] = "failed"
    candidate.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="result and status"):
        compare_reports(candidate, baseline)

    changed = json.loads(json.dumps(original))
    changed["reports"][0]["cases"][0]["usage"]["input_tokens"] = 999
    candidate.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="usage differs"):
        compare_reports(candidate, baseline)


async def test_compare_rejects_different_flow_targets(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {"label": "right", "reason": "expected"},
        variant="baseline",
        configuration="a",
        input_tokens=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "expected"},
        variant="candidate",
        configuration="b",
        input_tokens=2,
    )
    changed = json.loads(candidate.read_text())
    changed["reports"][0]["target_flow"] = "main"
    candidate.write_text(json.dumps(changed))

    with pytest.raises(ValueError, match="suite or target differs"):
        compare_reports(candidate, baseline)


async def test_compare_exposes_operational_regression_even_when_checks_stay_unavailable(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await _artifact(
        baseline,
        {},
        variant="baseline",
        configuration="a",
        input_tokens=2,
    )
    await _artifact(
        candidate,
        {},
        variant="candidate",
        configuration="b",
        input_tokens=2,
    )
    changed = json.loads(candidate.read_text())
    case = changed["reports"][0]["cases"][0]
    case["status"] = "error"
    case["error_code"] = "run_timeout"
    case["workflow"] = None
    case["workflow_revision"] = None
    case["usage"] = None
    case["details"]["result"] = None
    candidate.write_text(json.dumps(changed))

    suite = compare_reports(candidate, baseline).to_dict()["suites"][0]
    assert suite["case_changes"]["regressed"] == 1
    assert suite["cases"][0]["check_outcome"] == "unchanged"
    assert suite["cases"][0]["baseline_status"] == "completed"
    assert suite["cases"][0]["candidate_status"] == "error"
    assert suite["execution"]["failure_rate_delta"] == 1
    assert suite["execution"]["baseline_failures_by_code"] == {}
    assert suite["execution"]["candidate_failures_by_code"] == {"run_timeout": 1}


async def test_compare_source_span_reports_use_shared_recorded_scoring(tmp_path: Path) -> None:
    suite = EvaluationSuite(
        "source-spans",
        "gold-1",
        (
            EvaluationCase(
                "case",
                Envelope(payload={"message": "Cancel renewal now"}),
                (
                    Expectation(
                        "extract",
                        "/payload/extract",
                        {
                            "input_path": "/payload/message",
                            "required": [0, 6],
                            "allowed": [0, 14],
                        },
                        "source_span",
                    ),
                ),
            ),
        ),
    )

    async def save(path: Path, extracted: str, name: str) -> None:
        async def invoke(envelope: Envelope) -> ExecutionResult:
            del envelope
            return _result({"extract": extracted}, input_tokens=1)

        report = await evaluate(
            suite,
            EvaluationVariant(name, name, invoke, name, workflow="demo"),
            include_details=True,
        )
        write_report(
            path,
            (report,),
            mode="execution",
            dataset_name="source-spans",
            dataset_revision="1",
        )

    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    await save(baseline, "Cancel renewal", "baseline")
    await save(candidate, "renewal", "candidate")
    compared = compare_reports(candidate, baseline).to_dict()["suites"][0]
    assert compared["case_changes"]["regressed"] == 1
    assert compared["cases"][0]["checks"] == {
        "total": 1,
        "improved": 0,
        "regressed": 1,
        "unchanged": 0,
        "baseline_covered": 1,
        "candidate_covered": 1,
        "baseline_passed": 1,
        "candidate_passed": 0,
        "baseline_outcomes": {
            "passed": 1,
            "failed": 0,
            "missing": 0,
            "skipped": 0,
            "error": 0,
        },
        "candidate_outcomes": {
            "passed": 0,
            "failed": 1,
            "missing": 0,
            "skipped": 0,
            "error": 0,
        },
    }


async def test_compare_cli_never_loads_configuration_and_writes_private_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output = tmp_path / "comparison.json"
    await _artifact(
        baseline,
        {"label": "wrong", "reason": "expected"},
        variant="baseline",
        configuration="a",
        input_tokens=2,
    )
    await _artifact(
        candidate,
        {"label": "right", "reason": "expected"},
        variant="candidate",
        configuration="b",
        input_tokens=2,
    )

    def no_configuration(path: Path) -> object:
        pytest.fail(f"comparison loaded configuration {path}")

    monkeypatch.setattr("foliqant.cli._prepare", no_configuration)
    assert (
        main(
            [
                "evaluate",
                "--compare",
                str(candidate),
                "--baseline",
                str(baseline),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "compare" and summary["improved"] == 1
    assert "case" not in json.dumps(summary)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text())["kind"] == "evaluation_report_comparison"
    before = output.read_bytes()
    assert (
        main(
            [
                "evaluate",
                "--compare",
                str(candidate),
                "--baseline",
                str(baseline),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert output.read_bytes() == before
    capsys.readouterr()
    assert (
        main(
            ["evaluate", "--compare", str(candidate), "--baseline", str(baseline), "--repeat", "2"]
        )
        == 2
    )


async def _field_artifact(path: Path, outputs: list[dict[str, object]], name: str) -> None:
    labels = ["a", "b"] * (len(outputs) // 2)
    suite = EvaluationSuite(
        "fields",
        "gold-1",
        tuple(
            EvaluationCase(
                str(index),
                Envelope(payload={"index": index}),
                (
                    Expectation("label", "/payload/label", labels[index]),
                    Expectation("client", "/payload/fields/client", "Acme", "text"),
                    Expectation("isin", "/payload/fields/isin", None, absent_as_null=True),
                ),
            )
            for index in range(len(outputs))
        ),
    )

    async def invoke(envelope: Envelope) -> ExecutionResult:
        return _result(outputs[envelope.payload["index"]], input_tokens=1)

    report = await evaluate(
        suite,
        EvaluationVariant(name, name, invoke, name, workflow="demo"),
        include_details=True,
        metrics=(
            MetricSpec("label", "/payload/label", "classification", ("a", "b")),
            MetricSpec("fields", "/payload/fields", "fields", ("client", "isin")),
        ),
    )
    write_report(path, (report,), mode="execution", dataset_name="gold", dataset_revision="1")


async def test_compare_reports_paired_intervals_fields_deltas_and_cost(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    wrong = {"label": "x", "fields": {"client": "Other", "isin": "DE0001"}}
    right_a = {"label": "a", "fields": {"client": " ACME "}}
    right_b = {"label": "b", "fields": {"client": "acme"}}
    await _field_artifact(baseline, [wrong] * 20, "baseline")
    await _field_artifact(candidate, [right_a, right_b] * 10, "candidate")
    suite = compare_reports(candidate, baseline, resamples=500).to_dict()["suites"][0]
    case_pass = suite["case_pass"]
    assert (case_pass["baseline_rate"], case_pass["candidate_rate"]) == (0, 1)
    interval = case_pass["rate_delta_interval"]
    assert interval["low"] == interval["high"] == 1 and interval["excludes_zero"] is True
    assert interval["resamples"] == 500 and interval["method"] == "percentile_bootstrap"
    fields = next(item for item in suite["metrics"] if item["name"] == "fields")
    assert fields["field_accuracy_delta"] == 1
    assert fields["field_accuracy_delta_interval"]["excludes_zero"] is True
    assert suite["checks"]["pass_rate_delta_interval"]["low"] == 1
    # Unpriced usage stays unknown instead of becoming a zero cost.
    assert suite["usage"]["cost"]["baseline"]["unknown"] == 20
    assert suite["usage"]["cost"]["total_delta"] is None
    # The same artifact against itself has a zero-width interval at zero.
    same = compare_reports(baseline, baseline, resamples=100).to_dict()["suites"][0]
    assert same["case_pass"]["rate_delta_interval"]["excludes_zero"] is False
