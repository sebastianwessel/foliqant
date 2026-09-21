from __future__ import annotations

import json
from pathlib import Path

import pytest

from foliqant_model.cli import _parser, main
from foliqant_model.curation.runtime import CurationControl

DIGEST = "a" * 64


def _result(root: Path) -> dict[str, object]:
    return {
        "command": "migrate-decisions",
        "migrationPath": str(root),
        "datasetPath": str(root / "datasets/native-decisions"),
        "artifactId": DIGEST,
        "reportPath": str(root / "migration-report.json"),
        "records": 12,
        "pendingTasks": 3,
        "reviewItems": 2,
    }


def test_migration_command_help_exposes_only_offline_inputs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["migrate-decisions", "--help"])

    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "--from-run" in output
    assert "--output" in output
    assert "--progress" not in output


def test_rerun_parser_preserves_repeated_record_ids_and_progress() -> None:
    migration = Path("/tmp/migration")
    output = Path("/tmp/rerun")

    parsed = _parser().parse_args(
        [
            "rerun-migrated-decisions",
            "--from-migration",
            str(migration),
            "--output",
            str(output),
            "--limit",
            "2",
            "--job-id",
            "record-b",
            "--job-id",
            "record-a",
            "--progress",
            "always",
        ]
    )

    assert parsed.from_migration == migration
    assert parsed.output == output
    assert parsed.limit == 2
    assert parsed.job_ids == ["record-b", "record-a"]
    assert parsed.progress == "always"


def test_rerun_command_requires_a_migration_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["rerun-migrated-decisions", "--limit", "1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert '"code":"ARGUMENT_INVALID"' in captured.err


def test_migration_cli_adapts_the_public_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from foliqant_model.curation import migration

    parent = tmp_path / "parent"
    output = tmp_path / "migration"

    def migrate(from_run: Path, destination: Path | None = None) -> dict[str, object]:
        assert from_run == parent
        assert destination == output
        return _result(output)

    monkeypatch.setattr(migration, "migrate_decisions", migrate)
    assert main(["migrate-decisions", "--from-run", str(parent), "--output", str(output)]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["command"] == response["result"]["command"] == "migrate-decisions"
    assert response["result"]["records"] == 12


def test_rerun_cli_forwards_selection_and_removes_internal_report_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from foliqant_model.curation import migration_rerun

    parent = tmp_path / "migration"
    output = tmp_path / "rerun"

    def rerun(
        from_migration: Path,
        destination: Path | None = None,
        *,
        limit: int | None = None,
        job_ids: list[str] | None = None,
        control: CurationControl | None = None,
    ) -> dict[str, object]:
        assert from_migration == parent
        assert destination == output
        assert limit == 2
        assert job_ids == ["record-b", "record-a"]
        assert control is not None
        return {**_result(output), "rerun": {"accepted": 2}}

    monkeypatch.setattr(migration_rerun, "rerun_migration", rerun)
    assert (
        main(
            [
                "rerun-migrated-decisions",
                "--from-migration",
                str(parent),
                "--output",
                str(output),
                "--limit",
                "2",
                "--job-id",
                "record-b",
                "--job-id",
                "record-a",
                "--progress",
                "always",
            ]
        )
        == 0
    )

    response = json.loads(capsys.readouterr().out)
    assert response["command"] == "rerun-migrated-decisions"
    assert response["result"]["command"] == "rerun-migrated-decisions"
    assert "rerun" not in response["result"]
