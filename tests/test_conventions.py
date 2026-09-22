"""File conventions reduce declarations without changing process semantics."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from foliqant.bootstrap import prepare_application
from foliqant.compiler import CompilationError


def project(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    triage = config / "intake" / "triage"
    triage.mkdir(parents=True)
    (config / "settings.yaml").write_text(
        "models:\n  local:\n    provider: openai_compatible\n    model: $MODEL_ID\n"
        "    base_url: $MODEL_BASE_URL\n    output_mode: native\n"
    )
    (config / "intake" / "workflow.yaml").write_text(
        "defaults: {model: local}\nflows:\n  triage:\n"
        "    input: {message: {pointer: /payload/message}}\n"
        "    transition: {outcome: completed}\n"
    )
    (triage / "flow.yaml").write_text("steps: [classify, extract]\n")
    (triage / "classify.step.md").write_text(
        "---\ntype: llm\ninput: {message: {pointer: /payload/message}}\noutput: text\n"
        "---\nClassify the message.\n"
    )
    (triage / "extract").mkdir()
    (triage / "extract" / "step.md").write_text(
        "---\ntype: llm\ninput: {message: {pointer: /payload/message}}\n"
        "output: {schema: output.schema.json}\n---\nExtract the details.\n"
    )
    (triage / "extract" / "output.schema.json").write_text('{"type": "object"}')
    return config / "settings.yaml"


def test_discovery_infers_ids_and_start_but_preserves_declared_sequence(tmp_path):
    path = project(tmp_path)
    prepared = prepare_application(path)
    assert tuple(prepared.plans) == ("intake",)
    assert prepared.config.workflows == {"intake": "intake"}
    workflow = prepared.plans["intake"]
    assert workflow.start == "triage"
    assert [step.name for step in workflow.flow("triage").steps] == ["classify", "extract"]
    flow = path.parent / "intake/triage/flow.yaml"
    flow.write_text("steps: [extract, classify]\n")
    reordered = prepare_application(path).plans["intake"]
    assert [step.name for step in reordered.flow("triage").steps] == ["extract", "classify"]
    assert reordered.revision != workflow.revision


def test_discovery_ignores_unrelated_and_hidden_files_without_reading_them(tmp_path):
    path = project(tmp_path)
    before = prepare_application(path)
    (path.parent / "unrelated.yaml").write_text("!unsafe")
    (path.parent / "intake/triage/unused.step.md").write_text("!unsafe")
    hidden = path.parent / ".private"
    hidden.mkdir()
    (hidden / "workflow.yaml").write_text("!unsafe")
    after = prepare_application(path)
    assert before.configuration_digest == after.configuration_digest


@pytest.mark.parametrize("extra", ["classify.step.yaml", "classify/step.md"])
def test_ambiguous_step_files_fail_instead_of_picking_a_precedence(tmp_path, extra):
    path = project(tmp_path)
    duplicate = path.parent / "intake/triage" / extra
    duplicate.parent.mkdir(exist_ok=True)
    duplicate.write_text("type: llm\n")
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "ambiguous_step_file"


def test_missing_step_is_reported_before_any_provider_resolution(tmp_path):
    path = project(tmp_path)
    (path.parent / "intake/triage/classify.step.md").unlink()
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "missing_step_file"


def test_explicit_definition_disambiguates_intentionally(tmp_path):
    path = project(tmp_path)
    triage = path.parent / "intake/triage"
    (triage / "classify.step.yaml").write_text("!unsafe")
    (triage / "flow.yaml").write_text(
        "steps:\n  - id: classify\n    definition: classify.step.md\n  - extract\n"
    )
    assert len(prepare_application(path).plans["intake"].flow("triage").steps) == 2


def test_conventional_symlinks_cannot_escape_flow_or_config_roots(tmp_path):
    path = project(tmp_path)
    step = path.parent / "intake/triage/classify.step.md"
    outside = tmp_path / "external.md"
    outside.write_bytes(step.read_bytes())
    step.unlink()
    step.symlink_to(outside)
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "invalid_step_path"
    step.unlink()
    directory = path.parent / "escape"
    directory.symlink_to(tmp_path)
    (tmp_path / "workflow.yaml").write_text("flows: {}")
    with pytest.raises(CompilationError):
        prepare_application(path)


def test_multiple_flows_require_explicit_start(tmp_path):
    path = project(tmp_path)
    workflow = path.parent / "intake/workflow.yaml"
    document = yaml.safe_load(workflow.read_text())
    document["flows"]["second"] = deepcopy(document["flows"]["triage"])
    workflow.write_text(yaml.safe_dump(document))
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "missing_start"


def test_explicit_workflow_registry_does_not_also_discover(tmp_path):
    path = project(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["workflows"] = {"intake": "intake"}
    path.write_text(yaml.safe_dump(document))
    other = path.parent / "unexpected"
    other.mkdir()
    (other / "workflow.yaml").write_text("!unsafe")
    assert tuple(prepare_application(path).plans) == ("intake",)


def test_missing_workflow_registry_and_no_discoverable_workflows_fail(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("models: {}\n")
    with pytest.raises(CompilationError):
        prepare_application(path)


@pytest.mark.parametrize("target", ["settings.yaml", ".env"])
def test_nonregular_settings_inputs_reject_before_open(tmp_path, monkeypatch, target):
    import os

    from foliqant.core.errors import ServiceError
    from foliqant.settings import load_environment

    path = tmp_path / target
    os.mkfifo(path)
    original = Path.open

    def guarded_open(selected, *args, **kwargs):
        if selected == path:
            pytest.fail("opening a FIFO may block configuration loading")
        return original(selected, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    if target == "settings.yaml":
        with pytest.raises(CompilationError):
            prepare_application(path)
    else:
        with pytest.raises(ServiceError):
            load_environment(tmp_path / "settings.yaml", {})


@pytest.mark.parametrize("target", ["settings", "workflow", "environment"])
def test_symlink_loops_produce_sanitized_configuration_errors(tmp_path, target):
    from foliqant.core.errors import ServiceError
    from foliqant.settings import load_environment

    path = project(tmp_path)
    if target == "workflow":
        workflow = path.parent / "intake/workflow.yaml"
        workflow.unlink()
        workflow.symlink_to(workflow.name)
    else:
        path.unlink()
        path.symlink_to(path.name)
    if target == "environment":
        with pytest.raises(ServiceError) as caught:
            load_environment(path, {})
    else:
        with pytest.raises(CompilationError) as caught:
            prepare_application(path)
    assert str(tmp_path) not in str(caught.value)


def test_broken_conventional_candidate_is_not_silently_ignored(tmp_path):
    path = project(tmp_path)
    extra = path.parent / "intake/triage/classify.step.yaml"
    extra.symlink_to("missing-target.yaml")
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "ambiguous_step_file"


def test_explicit_and_discovered_registry_have_same_revision(tmp_path):
    path = project(tmp_path)
    discovered = prepare_application(path)
    document = yaml.safe_load(path.read_text())
    document["workflows"] = {"intake": "intake"}
    path.write_text(yaml.safe_dump(document))
    explicit = prepare_application(path)
    assert explicit.configuration_digest == discovered.configuration_digest
    assert explicit.plans["intake"].revision == discovered.plans["intake"].revision
