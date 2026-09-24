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
from foliqant.graph import render_document, render_dot, render_mermaid


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
    # Literal bindings stay hidden; authored condition operands are configuration.
    assert "PRIVATE-LITERAL" not in text and "PRIVATE-OPERAND" in text
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
    assert (
        'flow_lookup_fund -.->|"repeat ≤ 2 until status equals found"| flow_lookup_fund' in mermaid
    )
    assert 'flow_lookup_fund -->|"found"| flow_enrich' in mermaid
    assert 'flow_lookup_fund -.->|"retry"| flow_correct' in mermaid
    assert "stroke-dasharray: 6 4" in mermaid and "stroke-dasharray: 1 4" in mermaid
    assert 'outcome_completed(["completed"])' in mermaid
    dot = render_dot(graph)
    assert dot.startswith('digraph "intake" {')
    assert '"flow_lookup_fund" -> "flow_correct" [label="retry", style=dotted];' in dot
    assert '"flow_lookup_fund" -> "flow_manual" [label="review (default)", style=dashed];' in dot
    assert (
        '"flow_lookup_fund" -> "flow_lookup_fund" '
        '[label="repeat ≤ 2 until status equals found", style=dotted];'
    ) in dot
    assert "PRIVATE-LITERAL" not in mermaid + dot
    assert "/steps/lookup/result/status equals PRIVATE-OPERAND" not in mermaid  # step labels


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
    assert json.loads(completed.stdout)["error"]["code"] == "invalid_arguments"
    assert "--all" in completed.stderr


def _conditions_project(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    workflow = config / "intake"
    workflow.mkdir(parents=True)
    (config / "settings.yaml").write_text(
        yaml.safe_dump(
            {"handlers": {"echo": {"input_schema": {"type": "object"}, "output_schema": {}}}}
        ).replace("output_schema: {}", "output_schema: {}\n    effect: read")
    )

    def entry(condition: dict, flow_id: str) -> dict:
        return {"when": condition, "flow": flow_id}

    targets = ["a", "b", "c", "d", "e", "f"]
    route = [
        entry(when("/payload/kind", equals="fund"), "a"),
        entry(when("/payload/kind", **{"in": ["share", "bond"]}), "b"),
        entry(when("/payload/count", gt=3), "c"),
        entry(when("/payload/reference", matches="FOI-[0-9]{4}"), "d"),
        entry(when("/payload/owner", present=False), "e"),
        entry({"binding": {"pointer": "/payload/items"}, "length": {"gte": 2}}, "f"),
        {"flow": "a"},
    ]
    flows = {
        "start_here": flow(
            step("s", handler("echo", secret={"literal": "PRIVATE-LITERAL"})),
            input={"hidden": {"pointer": "/payload/x", "default": "PRIVATE-DEFAULT"}},
            transition={"route": route},
            on_unresolved={"outcome": "needs_review"},
        ),
        **{
            name: flow(step("x", handler("echo")), on_unresolved={"outcome": "needs_review"})
            for name in targets
        },
    }
    (workflow / "workflow.yaml").write_text(yaml.safe_dump({"start": "start_here", "flows": flows}))
    return config / "settings.yaml"


def test_graph_labels_show_authored_operands_but_never_bound_values(tmp_path):
    prepared = prepare_application(_conditions_project(tmp_path))
    graph = explain(prepared)
    labels = [edge.label for edge in graph.edges if edge.source == "start_here"]
    assert labels[:7] == [
        "0: /payload/kind equals fund",
        "1: /payload/kind in [share, bond]",
        "2: /payload/count gt 3",
        "3: /payload/reference matches /FOI-[0-9]{4}/",
        "4: /payload/owner present=false",
        "5: /payload/items length gte 2",
        "6: otherwise",
    ]
    mermaid = render_mermaid(graph)
    assert '"3: /payload/reference matches /FOI-[0-9]{4}/"' in mermaid
    document = json.dumps(graph.to_json())
    rendered = mermaid + render_dot(graph) + render_document(prepared) + document
    assert "PRIVATE-LITERAL" not in rendered and "PRIVATE-DEFAULT" not in rendered
    flows = {item["id"]: item for item in graph.to_json()["flows"]}
    route = flows["start_here"]["transition"]["route"]
    assert route[1]["when"]["operand"] == ["share", "bond"]
    assert route[1]["condition"] == "/payload/kind in"  # the telemetry label stays operand-free


def test_mermaid_escapes_label_characters(tmp_path):
    settings = _conditions_project(tmp_path)
    document = settings.parent / "intake" / "workflow.yaml"
    document.write_text(document.read_text().replace("FOI-[0-9]{4}", "'#[a|b]+'"))
    mermaid = render_mermaid(explain(prepare_application(settings)))
    assert "matches /#35;[a#124;b]+/" in mermaid


def test_render_document_has_one_section_per_workflow(tmp_path):
    settings = _project(tmp_path)
    other = settings.parent / "other"
    other.mkdir()
    (other / "workflow.yaml").write_text(
        yaml.safe_dump({"flows": {"only": flow(step("x", handler("echo")))}})
    )
    prepared = prepare_application(settings)
    text = render_document(prepared)
    assert text.startswith("# Workflows\n")
    assert text.index("\n## intake\n") < text.index("\n## other\n")
    assert "Start: `lookup_fund` when `/payload/identifier present=true`; otherwise `manual`." in (
        text
    )
    assert "Output: the accepted input payload." in text
    assert text.count("```mermaid\n") == 2 and "%%" not in text
    assert "- warning `review_ends_run` in `other/workflow.yaml:" in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert render_document(prepared, "dot").count("```dot\n") == 2


def test_cli_explain_all_writes_and_checks_the_document(tmp_path):
    settings = _project(tmp_path)
    output = tmp_path / "docs" / "workflows.md"
    base = [sys.executable, "-m", "foliqant", "explain", "--config", str(settings)]
    printed = subprocess.run(
        [*base, "--format", "mermaid", "--all"], capture_output=True, text=True, check=False
    )
    assert printed.returncode == 0 and printed.stdout.startswith("# Workflows\n")
    written = subprocess.run(
        [*base, "--format", "mermaid", "--all", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)["status"] == "written"
    assert output.read_text() == printed.stdout
    output.write_text(printed.stdout.replace("lookup_fund", "renamed"))
    stale = subprocess.run(
        [*base, "--format", "mermaid", "--all", "--output", str(output), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale.returncode == 1
    assert json.loads(stale.stdout)["error"]["code"] == "stale_output"
    for arguments in (["--check"], ["--all", "--workflow", "intake"]):
        rejected = subprocess.run([*base, *arguments], capture_output=True, text=True, check=False)
        assert rejected.returncode == 2
        assert json.loads(rejected.stdout)["error"]["code"] == "invalid_arguments"
