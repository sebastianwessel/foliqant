"""Every example keeps a generated WORKFLOWS.md that matches its configuration."""

import json
from pathlib import Path

import pytest

from foliqant.bootstrap import prepare_application
from foliqant.cli import main
from foliqant.graph import render_document

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CONFIGURED = sorted(path.parents[1] for path in EXAMPLES.glob("*/config/settings.yaml"))


def test_every_configured_example_is_covered():
    assert len(CONFIGURED) >= 10


@pytest.mark.parametrize("example", CONFIGURED, ids=[path.name for path in CONFIGURED])
def test_workflow_document_is_current(example: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = example / "WORKFLOWS.md"
    settings = example / "config" / "settings.yaml"
    arguments = ["explain", "--format", "mermaid", "--all", "--config", str(settings)]
    code = main([*arguments, "--output", str(document), "--check"])
    captured = capsys.readouterr()
    assert code == 0, (
        f"{document.relative_to(EXAMPLES.parent)} is stale; regenerate it with "
        f"`foliqant {' '.join(arguments)} --output {document.name}` from the example directory"
    )
    assert json.loads(captured.out) == {
        "command": "explain",
        "status": "current",
        "output": str(document),
    }
    text = document.read_text(encoding="utf-8")
    prepared = prepare_application(settings)
    assert text == render_document(prepared)
    for name in prepared.plans:
        assert f"\n## {name}\n" in text


def test_check_reports_a_stale_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CONFIGURED[0] / "config" / "settings.yaml"
    stale = tmp_path / "WORKFLOWS.md"
    stale.write_text("# Workflows\n", encoding="utf-8")
    arguments = ["explain", "--format", "mermaid", "--all", "--config", str(settings)]
    assert main([*arguments, "--output", str(stale), "--check"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "stale_output"
    assert captured.err.startswith("foliqant: stale_output: ")
    assert stale.read_text(encoding="utf-8") == "# Workflows\n"
    assert main([*arguments, "--output", str(stale)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "written"
    assert main([*arguments, "--output", str(stale), "--check"]) == 0
