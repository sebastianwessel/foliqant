"""Examples expose real repeat/persistence options without opening invalid runs."""

import json
import stat
from pathlib import Path

import pytest
from examples import common
from examples.http_workflow import evaluate as http
from examples.public_request_mcp import evaluate as mcp
from examples.support_triage import evaluate as support


@pytest.mark.parametrize("module", [support, http, mcp])
@pytest.mark.parametrize("repeat", [0, -1, True])
async def test_invalid_repeat_does_not_open_example_clients(module, repeat, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid repeat must be rejected before opening clients")

    monkeypatch.setattr(module, "open_example", forbidden)
    with pytest.raises(ValueError, match="repeat"):
        await module.run_evaluations(repeat=repeat)


@pytest.mark.parametrize("module", [support, http, mcp])
async def test_default_report_persists_every_repeated_observation(module, tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ROOT", tmp_path)
    summary = await module.run_evaluations(repeat=2)
    assert summary["ok"] is True
    assert "reports" not in summary  # Private results belong in the artifact, not stdout.
    path = Path(summary["report"])
    assert path.is_relative_to(tmp_path / ".foliqant")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    artifact = json.loads(path.read_text())
    assert artifact["mode"] in {"offline_wiring", "offline_asgi", "local_stdio"}
    for report in artifact["reports"]:
        assert report["repeat"] == 2
        assert report["attempt_count"] == 2 * report["source_case_count"]
        ids = list(dict.fromkeys(case["id"] for case in report["cases"]))
        assert len(ids) == report["source_case_count"]
        for identity in ids:
            attempts = [case for case in report["cases"] if case["id"] == identity]
            assert [case["repetition"] for case in attempts] == [1, 2]
            assert attempts[0]["details"]["input"] == attempts[1]["details"]["input"]
            assert all(case["details"]["result"] is not None for case in attempts)
