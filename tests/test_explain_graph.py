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


def test_mermaid_renders_flows_as_subgraphs_with_step_nodes(tmp_path):
    graph = explain(prepare_application(_project(tmp_path)))
    mermaid = render_mermaid(graph)
    lines = mermaid.splitlines()
    assert lines[:2] == ["flowchart TD", "  start__((start))"]
    # One subgraph per flow; the repeat is annotated in the title.
    assert '  subgraph lookup_fund["lookup_fund  ·  repeat ≤ 2 until status equals found"]' in lines
    assert '    lookup_fund__lookup["lookup<br/>handler"]' in lines
    # A conditional step: `?`, the conditional class and its condition on the incoming edge.
    assert '    lookup_fund__note["note?<br/>handler"]' in lines
    assert (
        '    lookup_fund__lookup -->|"when status equals PRIVATE-OPERAND"| lookup_fund__note'
        in lines
    )
    assert "  class lookup_fund__note conditional" in lines
    assert "  linkStyle 0 stroke-dasharray: 4 3" in lines
    # Callable and retry flows sit in one group after the routed flows.
    group = lines.index('  subgraph callable__["callable flows"]')
    assert lines.index('  subgraph manual["manual"]') < group
    assert lines[group + 2] == '    subgraph correct["correct  ·  retry for lookup_fund"]'
    # Flow edges connect subgraphs; outcomes are stadium nodes.
    assert '  outcome_completed__(["completed"])' in lines
    assert '  start__ -->|"0: /payload/identifier present=true"| lookup_fund' in lines
    assert '  lookup_fund -->|"found"| enrich' in lines
    assert '  lookup_fund -.->|"review (default)"| manual' in lines
    assert '  lookup_fund -.->|"retry"| correct' in lines
    assert '  manual -.->|"review"| outcome_needs_review__' in lines
    assert "  classDef handler fill:#f2f2f2,stroke:#666666,color:#1f2328" in lines
    assert "  classDef conditional stroke-dasharray: 4 3" in lines
    assert "  class enrich,lookup_fund,manual,correct flow" in lines
    assert "  class callable__ group" in lines
    assert "  classDef llm" not in mermaid  # only classes that are used
    edges = [line for line in lines if "-->" in line or "-.->" in line]
    review = [str(index) for index, line in enumerate(edges) if '|"review' in line]
    assert f"  linkStyle {','.join(review)} stroke-dasharray: 6 4" in lines
    retry = next(index for index, line in enumerate(edges) if '|"retry' in line)
    assert f"  linkStyle {retry} stroke-dasharray: 1 4" in lines
    assert lines[-1].startswith("  %% warning condition_always_false ")
    assert "%%" not in render_mermaid(graph, diagnostics=False)
    assert "PRIVATE-LITERAL" not in mermaid
    again = explain(prepare_application(_project(tmp_path / "again")))
    assert render_mermaid(again) == mermaid


def test_dot_mirrors_the_mermaid_structure(tmp_path):
    graph = explain(prepare_application(_project(tmp_path)))
    dot = render_dot(graph)
    lines = dot.splitlines()
    assert lines[:3] == ['digraph "intake" {', "  rankdir=TB;", "  compound=true;"]
    assert '  subgraph "cluster_lookup_fund" {' in lines
    assert '    label="lookup_fund  ·  repeat ≤ 2 until status equals found";' in lines
    assert (
        '    "lookup_fund__note" [shape=box, style="dashed,filled", fillcolor="#f2f2f2", '
        'color="#666666", label="note?\\nhandler"];'
    ) in lines
    assert (
        '    "lookup_fund__lookup" -> "lookup_fund__note" '
        '[label="when status equals PRIVATE-OPERAND", style=dashed];'
    ) in lines
    group = lines.index('  subgraph "cluster_callable__" {')
    assert lines.index('    subgraph "cluster_correct" {') > group
    assert (
        '  "lookup_fund__note" -> "correct__fix" [ltail="cluster_lookup_fund", '
        'lhead="cluster_correct", label="retry", style=dotted];'
    ) in lines
    assert (
        '  "lookup_fund__note" -> "manual__m" [ltail="cluster_lookup_fund", '
        'lhead="cluster_manual", label="review (default)", style=dashed];'
    ) in lines
    assert '  "start__" -> "lookup_fund__lookup" [lhead="cluster_lookup_fund", ' in dot
    assert '  "manual__m" -> "outcome_completed__" [ltail="cluster_manual"];' in lines
    assert "cluster_legend__" not in dot and "cluster_legend__" in render_dot(graph, legend=True)
    assert lines[-1].startswith("// warning condition_always_false ")
    assert "PRIVATE-LITERAL" not in dot


def _typed_project(tmp_path: Path, flow_id: str = "triage") -> Path:
    """Every step type, a reserved flow ID and a long step condition."""
    config = tmp_path / "config"
    workflow = config / "intake"
    workflow.mkdir(parents=True)
    (config / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {
                    "local": {
                        "provider": "openai_compatible",
                        "model": "m",
                        "base_url": "http://127.0.0.1:9/v1",
                        "allow_insecure_http": True,
                        "output_mode": "native",
                    }
                },
                "mcp": {
                    "records": {
                        "transport": {"type": "stdio", "command": "python3"},
                        "catalog": {
                            "tools": {
                                "find": {
                                    "input_schema": {"type": "object"},
                                    "output_schema": {"type": "object"},
                                    "effect": "read",
                                }
                            }
                        },
                    }
                },
                "handlers": {
                    "echo": {
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "effect": "read",
                    }
                },
            }
        )
    )
    long_value = "x" * 80
    flows = {
        flow_id: flow(
            step(
                "classify",
                {
                    "type": "decision",
                    "model": "local",
                    "instructions": "Pick one.",
                    "sources": {"text": {"pointer": "/payload/text"}},
                    "question": {
                        "type": "predicate",
                        "criteria": ["Answer yes when the text asks for help."],
                    },
                },
            ),
            step(
                "summarize",
                {
                    "type": "llm",
                    "model": "local",
                    "instructions": "Summarize {{ text }}.",
                    "input": {"text": {"pointer": "/payload/text"}},
                    "output": {"schema": {"type": "object"}},
                },
                when=when("/payload/text", equals=long_value),
            ),
            step(
                "find",
                {
                    "type": "mcp",
                    "server": "records",
                    "tool": "find",
                    "arguments": {"text": {"pointer": "/payload/text"}},
                },
            ),
            step(
                "each",
                {
                    "type": "flow_collection",
                    "items": {"pointer": "/payload/items"},
                    "flows": ["item"],
                },
            ),
            on_unresolved={"outcome": "needs_review"},
        ),
        "item": callable_flow(step("note", handler("echo", text="/payload/text"))),
    }
    (workflow / "workflow.yaml").write_text(yaml.safe_dump({"start": flow_id, "flows": flows}))
    return config / "settings.yaml"


def test_step_shapes_classes_and_long_conditions(tmp_path):
    mermaid = render_mermaid(explain(prepare_application(_typed_project(tmp_path))))
    lines = mermaid.splitlines()
    assert '    triage__classify{{"classify<br/>decision · predicate"}}' in lines
    assert '    triage__summarize(["summarize?<br/>llm"])' in lines
    assert '    triage__find[/"find<br/>mcp"/]' in lines
    assert '    triage__each[["each<br/>flow_collection → item"]]' in lines
    assert '      item__note["note<br/>handler"]' in lines
    assert '  triage -.->|"calls"| item' in lines
    assert "  start__ --> triage" in lines
    for css, fill, stroke in [
        ("decision", "#fff4e5", "#d68a1d"),
        ("llm", "#eef3ff", "#3b6fd6"),
        ("handler", "#f2f2f2", "#666666"),
        ("mcp", "#e9f8ee", "#2f9e5d"),
        ("collection", "#f6ecff", "#8a4fd6"),
    ]:
        assert f"  classDef {css} fill:{fill},stroke:{stroke},color:#1f2328" in lines
    edge = next(line for line in lines if line.endswith("| triage__summarize"))
    condition = edge.split('|"', 1)[1].split('"|', 1)[0]
    assert condition.startswith("when /payload/text equals xxx") and condition.endswith("…")
    assert len(condition) == len("when ") + 60


def test_reserved_flow_ids_stay_mermaid_safe(tmp_path):
    graph = explain(prepare_application(_typed_project(tmp_path, flow_id="end")))
    lines = render_mermaid(graph).splitlines()
    assert '  subgraph end___["end"]' in lines
    assert "  start__ --> end___" in lines
    assert '    end__classify{{"classify<br/>decision · predicate"}}' in lines
    assert '  subgraph "cluster_end" {' in render_dot(graph)


def test_legend_is_optional_and_lists_every_step_type(tmp_path):
    graph = explain(prepare_application(_project(tmp_path)))
    assert "legend__" not in render_mermaid(graph)
    lines = render_mermaid(graph, legend=True).splitlines()
    start = lines.index('  subgraph legend__["legend"]')
    assert lines[start + 1 : start + 8] == [
        "    direction LR",
        '    legend_decision__{{"decision"}}',
        '    legend_llm__(["llm"])',
        '    legend_handler__["handler"]',
        '    legend_mcp__[/"mcp"/]',
        '    legend_collection__[["flow_collection"]]',
        '    legend_conditional__["step?<br/>when …"]',
    ]
    assert "  class lookup_fund__note,legend_conditional__ conditional" in lines
    assert "  classDef collection fill:#f6ecff,stroke:#8a4fd6,color:#1f2328" in lines
    # The invisible legend links shift no index of the styled graph edges.
    assert [line for line in lines if line.startswith("  linkStyle")] == [
        line for line in render_mermaid(graph).splitlines() if line.startswith("  linkStyle")
    ]


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
    assert "legend" not in completed.stdout
    legend = subprocess.run(
        [*completed.args, "--legend"], capture_output=True, text=True, check=False
    )
    assert legend.returncode == 0, legend.stderr
    assert "legend_conditional__" in legend.stdout


def test_cli_legend_needs_a_graph_format(tmp_path):
    settings = _project(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "foliqant", "explain", "--config", str(settings), "--legend"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error"]["code"] == "invalid_arguments"
    assert "--legend" in completed.stderr


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
    assert '  start_here -->|"3: /payload/reference matches /FOI-[0-9]{4}/"| d' in mermaid
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
    text = document.read_text().replace("FOI-[0-9]{4}", "'#[a|b]+'")
    document.write_text(text.replace("equals: fund", "equals: <a&b>"))
    graph = explain(prepare_application(settings))
    mermaid = render_mermaid(graph)
    assert '|"3: /payload/reference matches /#35;[a#124;b]+/"|' in mermaid
    assert '|"0: /payload/kind equals #quot;#lt;a#amp;b#gt;#quot;"|' in mermaid
    assert '"0: /payload/kind equals \\"<a&b>\\""' in render_dot(graph)


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
    # One legend at the top, then one diagram per workflow.
    assert text.count("```mermaid\n") == 3 and text.count("subgraph legend__") == 1
    assert "%%" not in text
    assert text.index('subgraph legend__["legend"]') < text.index("\n## intake\n")
    assert "legend__" not in render_document(prepared, legend=False)
    assert "- warning `review_ends_run` in `other/workflow.yaml:" in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert render_document(prepared, "dot").count("```dot\n") == 3


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
