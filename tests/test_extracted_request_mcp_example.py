"""Extraction passes selected prior-step values to one real local MCP tool."""

import json
import os
import subprocess
from pathlib import Path
from typing import cast

from examples.extracted_request_mcp import offline
from examples.extracted_request_mcp.evaluate import dataset, run_evaluations
from examples.extracted_request_mcp.run import CONFIG_PATH, DEMO_PAYLOAD, run_example
from pydantic_ai.messages import ModelResponse, TextPart

from foliqant import prepare_application
from foliqant.core.json import JsonValue
from foliqant.core.plan import LlmStepPlan, McpStepPlan

ROOT = Path(__file__).resolve().parents[1]


def test_compiled_context_bindings_select_exact_fields() -> None:
    plan = prepare_application(CONFIG_PATH).plans["extracted_request_lookup"]
    extract = plan.step("extract")
    lookup = plan.step("lookup")
    assert isinstance(extract, LlmStepPlan)
    assert isinstance(lookup, McpStepPlan)
    assert [(name, binding.pointer) for name, binding in extract.input] == [
        ("language", "/payload/language"),
        ("message", "/payload/message"),
    ]
    assert [(name, binding.pointer) for name, binding in lookup.arguments] == [
        ("language", "/steps/extract/result/language"),
        ("reference", "/steps/extract/result/reference"),
    ]


async def test_scripted_extraction_calls_real_mcp_without_envelope_leak() -> None:
    result = await run_example(DEMO_PAYLOAD)
    assert result.execution.status == "completed"
    assert result.execution.usage.model_requests == 1
    assert result.execution.usage.tool_calls == 1
    assert result.decisions["extract"].result == {
        "reference": "FOI-2026-0142",
        "language": "en",
        "internal_summary": "English request-status lookup.",
    }
    assert (
        result.decisions["lookup"].result
        == result.payload
        == {
            "reference": "FOI-2026-0142",
            "language": "en",
            "status": "in_review",
            "due_date": "2026-10-05",
        }
    )
    serialized_lookup = json.dumps(result.decisions["lookup"].result)
    assert "contact_email" not in serialized_lookup
    assert "private@example.test" not in serialized_lookup
    assert "internal_summary" not in serialized_lookup


async def test_en_de_pipeline_and_isolated_step_evaluations(tmp_path: Path) -> None:
    gold = dataset()
    assert gold.revision == "2"
    assert [(spec.step, len(spec.gold_cases)) for spec in gold.suites] == [
        (None, 2),
        ("extract", 2),
        ("lookup", 2),
    ]
    assert {
        cast(dict[str, JsonValue], case.input.payload)["language"]
        for spec in gold.suites
        for case in spec.gold_cases
    } == {"en", "de"}

    summary = await run_evaluations(output=tmp_path / "context-report.json")
    assert summary["ok"] is True
    assert summary["mode"] == "offline_wiring"
    report_path = cast(str, summary["report"])
    reports = json.loads(Path(report_path).read_text())["reports"]
    assert [report["target_step"] for report in reports] == [None, "extract", "lookup"]
    assert all(report["case_pass_rate"] == 1 for report in reports)


async def test_summary_wording_is_not_gold_but_structured_fields_remain_gold(
    tmp_path: Path, monkeypatch
) -> None:
    original = offline.scripted_response

    def response_with(**changes: str):  # type: ignore[no-untyped-def]
        def respond(messages, info):  # type: ignore[no-untyped-def]
            response = original(messages, info)
            part = response.parts[0]
            assert isinstance(part, TextPart)
            value = json.loads(part.content)
            extracted = value["value"]
            extracted["internal_summary"] = (
                "A different valid summary of the same synthetic request."
            )
            extracted.update(changes)
            return ModelResponse(parts=[TextPart(json.dumps(value))])

        return respond

    monkeypatch.setattr(offline, "scripted_response", response_with())
    valid = await run_evaluations(output=tmp_path / "varied-summary.json")
    assert valid["ok"] is True

    def wrong_structured_fields(messages, info):  # type: ignore[no-untyped-def]
        response = original(messages, info)
        part = response.parts[0]
        assert isinstance(part, TextPart)
        value = json.loads(part.content)
        extracted = value["value"]
        if extracted["language"] == "en":
            extracted["reference"] = "FOI-2026-9999"
        else:
            extracted["language"] = "en"
        return ModelResponse(parts=[TextPart(json.dumps(value))])

    monkeypatch.setattr(offline, "scripted_response", wrong_structured_fields)
    invalid = await run_evaluations(output=tmp_path / "wrong-structured-fields.json")
    assert invalid["ok"] is False
    reports = json.loads(Path(cast(str, invalid["report"])).read_text())["reports"]
    failures = {
        (report["target_step"], case["id"], check["name"])
        for report in reports
        for case in report["cases"]
        for check in case["checks"]
        if check["outcome"] == "failed"
    }
    assert (None, "english_request", "reference") in failures
    assert ("extract", "english_request", "reference") in failures
    assert (None, "german_request", "language") in failures
    assert ("extract", "german_request", "language") in failures


def test_default_command_is_offline_except_for_bundled_stdio_mcp() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-m",
            "examples.extracted_request_mcp.run",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = json.loads(completed.stdout)
    assert output["ok"] is True
    usage = output["execution"]["usage"]
    assert usage["model_requests"] == usage["tool_calls"] == 1
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert output["payload"]["reference"] == "FOI-2026-0142"
