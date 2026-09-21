import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from foliqant_model.cli import main
from foliqant_model.contracts.cli import CurateResult
from foliqant_model.curation.runtime import CurationControl

DIGEST = "a" * 64


def test_help_does_not_load_gpu_libraries(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "setup" in output
    assert "curate" in output
    assert "mlx.core" not in sys.modules


def test_invalid_arguments_have_redacted_machine_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["private-secret-argument"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    result = json.loads(output.err)
    assert result["error"]["code"] == "ARGUMENT_INVALID"
    assert "private-secret-argument" not in output.err


def test_entrypoint_is_executable_offline() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "foliqant_model", "setup", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert process.returncode == 0
    assert "--workspace" in process.stdout
    assert process.stderr == ""


@pytest.mark.parametrize("repair,continuation", [(False, False), (True, False), (False, True)])
def test_curate_dispatches_all_bounded_operation_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repair: bool,
    continuation: bool,
) -> None:
    from foliqant_model.curation import runner

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "curation.yaml"
    workspace = tmp_path / "workspace"

    def run(
        config_path: Path,
        selected_workspace: Path | None = None,
        *,
        prepare_only: bool = False,
        offline: bool = False,
        repair_from: Path | None = None,
        continue_from: Path | None = None,
        extend_projections_from: Path | None = None,
        projection_plan: Path | None = None,
        control: CurationControl | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CurateResult:
        assert environment is not None
        assert all(key.startswith("FOLIQANT_CURATION_") for key in environment)
        assert config_path == config
        assert selected_workspace == workspace
        assert prepare_only is not repair
        assert repair_from == (workspace / "curation/parent" if repair else None)
        assert continue_from == (workspace / "curation/parent" if continuation else None)
        assert extend_projections_from is None
        assert projection_plan is None
        assert offline is True
        assert control is not None
        control.report("preparing", workspace / "curation/run")
        return CurateResult(
            command="curate",
            status="completed" if repair else "prepared",
            runPath=str(workspace / "curation/run"),
            configurationSha256=DIGEST,
            sourceRecords=12,
            generatedAccepted=0,
            generatedQuarantined=0,
            datasets=[
                {
                    "outputPath": str(workspace / "curation/run/datasets/source-corpus"),
                    "artifactId": DIGEST,
                    "kind": "dataset",
                    "stage": "none",
                    "customer": None,
                }
            ],
            reportPath=str(workspace / "curation/run/prepared-report.json"),
            warnings=["Preparation completed without inference."],
        )

    monkeypatch.setattr(runner, "run_curation", run)
    assert (
        main(
            [
                "curate",
                "--config",
                str(config),
                "--workspace",
                str(workspace),
                *(
                    ["--repair-from", str(workspace / "curation/parent")]
                    if repair
                    else ["--prepare-only"]
                ),
                *(["--continue-from", str(workspace / "curation/parent")] if continuation else []),
                "--offline",
                "--progress",
                "always",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["command"] == "curate"
    assert output["result"]["status"] == ("completed" if repair else "prepared")
    assert output["result"]["sourceRecords"] == 12
    assert "phase=preparing" in captured.err
    assert "phase=preparing" not in captured.out
