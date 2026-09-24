"""`explain` graph model and its JSON, Mermaid and Graphviz renderings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from workflow_documents import (
    IDENTIFIER_INPUT,
    LOOKUP_OUTPUT,
    callable_flow,
    flow,
    handler,
    step,
    when,
)

from foliqant import explain, prepare_application
from foliqant.core.errors import ServiceError
from foliqant.graph import render_dot, render_mermaid


def _project(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    workflow = config / "intake"
    workflow.mkdir(parents=True)
    (config / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "handlers": {
                    "lookup": {
                        "input_schema": IDENTIFIER_INPUT,
                        "output_schema": LOOKUP_OUTPUT,
                        "effect": "read",
                    },
                    "echo": {
                        "input_schema": {"type": "object"},
                        "output_schema": {},
                        "effect": "read",
                    },
                }
            }
        )
    )
    flows = {
        "lookup_fund": flow(
            step("lookup", handler("lookup", identifier="/payload/identifier")),
            step(
                "note",
                handler("echo", secret={"literal": "PRIVATE-LITERAL"}),
                when=when("/steps/lookup/result/status", equals="PRIVATE-OPERAND"),
            ),
            input={"identifier": {"pointer": "/payload/identifier"}},
            output={"pointer": "/steps/lookup/result"},
            repeat={
                "max_attempts": 2,
                "until": when("/flows/lookup_fund/result/status", equals="found"),
                "retry": {
                    "flow": "correct",
                    "input": {"lookup": {"pointer": "/flows/lookup_fund/result"}},
                },
            },
            transition={
                "binding": {"pointer": "/flows/lookup_fund/result/status"},
                "cases": {"found": {"flow": "enrich"}},
                "default": {"flow": "manual"},
                "default_covers": ["not_found", "ambiguous"],
            },
        ),
        "correct": callable_flow(step("fix", handler("echo"))),
        "enrich": flow(
            step("e", handler("echo")),
            input={"fund": {"pointer": "/flows/lookup_fund/result"}},
            transition={
                "route": [
                    {
                        "when": when("/flows/lookup_fund/result/fund", present=True),
                        "outcome": "completed",
                    },
                    {"flow": "manual"},
                ]
            },
        ),
        "manual": flow(step("m", handler("echo")), on_unresolved={"outcome": "needs_review"}),
    }
    (workflow / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "start": {
                    "route": [
                        {"when": when("/payload/identifier", present=True), "flow": "lookup_fund"},
                        {"flow": "manual"},
                    ]
                },
                "defaults": {"on_unresolved": {"flow": "manual"}},
                "flows": flows,
            }
        )
    )
    return config / "settings.yaml"


def test_graph_model_reports_topology_without_values(tmp_path):
    graph = explain(prepare_application(_project(tmp_path)))
    document = graph.to_json()
    text = json.dumps(document)
    assert "PRIVATE" not in text
    flows = {item["id"]: item for item in document["flows"]}
    lookup = flows["lookup_fund"]
    assert lookup["role"] == "routed" and flows["correct"]["role"] == "retry"
    assert flows["correct"]["retry_for"] == "lookup_fund"
    assert lookup["on_unresolved"] == {"flow": "manual"}
    assert lookup["on_unresolved_inherited"] is True
    assert flows["manual"]["on_unresolved"] == {"outcome": "needs_review"}
    assert lookup["repeat"]["max_attempts"] == 2
    assert lookup["repeat"]["until_condition"] == "/flows/lookup_fund/result/status equals"
    assert lookup["steps"][1]["condition"] == "/steps/lookup/result/status equals"
    assert lookup["steps"][1]["input"] == {"secret": {"literal": True}}
    assert lookup["transition"]["default_covers"] == ["not_found", "ambiguous"]
    assert flows["enrich"]["available_results"] == ["/flows/lookup_fund/result"]
    assert document["start"]["route"][0]["condition"] == "/payload/identifier present=true"
    edges = {
        (edge["source"], edge["target"], edge["kind"], edge["label"]) for edge in document["edges"]
    }
    assert ("start", "lookup_fund", "start", "0: /payload/identifier present=true") in edges
    assert ("start", "manual", "start", "1: otherwise") in edges
    assert ("lookup_fund", "enrich", "transition", "found") in edges
    assert ("lookup_fund", "manual", "transition", "default") in edges
    assert ("lookup_fund", "manual", "review", "review") in edges
    assert ("lookup_fund", "correct", "retry", "retry") in edges
    assert (
        "enrich",
        "outcome:completed",
        "transition",
        "0: /flows/lookup_fund/result/fund present=true",
    ) in edges
    assert ("manual", "outcome:needs_review", "review", "review") in edges
    inherited = [edge for edge in document["edges"] if edge.get("inherited")]
    assert {edge["source"] for edge in inherited} == {"lookup_fund", "enrich"}
    assert set(document["outcomes"]) == {"completed", "needs_review"}


def test_mermaid_and_dot_render_edge_styles_and_diagnostics(tmp_path):
    graph = explain(prepare_application(_project(tmp_path)))
    mermaid = render_mermaid(graph)
    assert mermaid.startswith("flowchart TD\n")
    assert 'flow_lookup_fund["lookup_fund<br/>lookup (handler)<br/>note? (handler)' in mermaid
    assert "repeat #lt;= 2 until /flows/lookup_fund/result/status equals" in mermaid
    assert 'flow_lookup_fund -->|"found"| flow_enrich' in mermaid
    assert 'flow_lookup_fund -.->|"retry"| flow_correct' in mermaid
    assert "stroke-dasharray: 6 4" in mermaid and "stroke-dasharray: 1 4" in mermaid
    assert 'outcome_completed(["completed"])' in mermaid
    assert 'flow_lookup_fund -.->|"repeat #lt;= 2"| flow_lookup_fund' in mermaid
    dot = render_dot(graph)
    assert dot.startswith('digraph "intake" {')
    assert '"flow_lookup_fund" -> "flow_correct" [label="retry", style=dotted];' in dot
    assert '"flow_lookup_fund" -> "flow_manual" [label="review (default)", style=dashed];' in dot
    assert '"flow_lookup_fund" -> "flow_lookup_fund" [label="repeat <= 2", style=dotted];' in dot
    assert "PRIVATE" not in mermaid + dot


@pytest.mark.parametrize("output_format", ["mermaid", "dot"])
def test_cli_prints_graph_text(tmp_path, output_format):
    settings = _project(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "foliqant",
            "explain",
            "--config",
            str(settings),
            "--format",
            output_format,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    expected = "flowchart TD" if output_format == "mermaid" else 'digraph "intake"'
    assert completed.stdout.startswith(expected)


def test_cli_json_includes_diagnostics(tmp_path):
    settings = _project(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "foliqant", "explain", "--config", str(settings)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    workflow = json.loads(completed.stdout)["workflows"][0]
    assert workflow["name"] == "intake"
    assert all(
        {"code", "level", "message", "location"} <= set(item) for item in workflow["diagnostics"]
    )


def test_explain_requires_a_workflow_when_several_are_configured(tmp_path):
    settings = _project(tmp_path)
    other = settings.parent / "other"
    other.mkdir()
    (other / "workflow.yaml").write_text(
        yaml.safe_dump({"flows": {"only": flow(step("x", handler("echo")))}})
    )
    prepared = prepare_application(settings)
    with pytest.raises(ServiceError):
        explain(prepared)
    assert explain(prepared, "other").name == "other"
    completed = subprocess.run(
        [sys.executable, "-m", "foliqant", "explain", "--config", str(settings), "--format", "dot"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stderr)["error"]["code"] == "invalid_arguments"
