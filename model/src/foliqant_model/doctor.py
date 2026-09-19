"""Probe native backend capabilities without importing GPU code in the CLI."""

from __future__ import annotations

import platform
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import get_args

from .contracts import DoctorWorkerRequest, VersionedComponent, WorkerRequest
from .contracts.base import Command
from .contracts.cli import DoctorResult
from .errors import ModelError
from .execution import run_worker

_GPU_COMMANDS = {"quantize", "train", "customize", "evaluate", "merge"}


def diagnose(implemented_commands: Sequence[Command]) -> DoctorResult:
    """Report commands usable in this execution environment, without downloading."""
    with tempfile.TemporaryDirectory(prefix="foliqant-doctor-") as temporary:
        workspace = Path(temporary)
        free_disk = shutil.disk_usage(workspace).free
        response = run_worker(
            WorkerRequest(
                DoctorWorkerRequest(
                    schemaVersion=1,
                    requestId=uuid.uuid4().hex,
                    operation="doctor",
                )
            ),
            workspace=workspace,
            timeout_seconds=60,
        )
    value = response.root
    if not value.ok or value.operation != "doctor":
        raise ModelError("BACKEND_FAILED", "Backend capability probe failed")
    probe = value.result
    components: list[VersionedComponent] = []
    for name, installed in (("mlx", probe.mlxVersion), ("mlx-lm", probe.mlxLmVersion)):
        if installed is not None:
            components.append(VersionedComponent(name=name, version=installed, role="backend"))
    for name in ("pydantic", "huggingface-hub", "safetensors", "scipy", "jsonschema", "gguf"):
        try:
            installed = version(name)
        except PackageNotFoundError:
            continue
        components.append(VersionedComponent(name=name, version=installed, role="library"))
    usable = set(implemented_commands)
    if not (probe.mlxImportAvailable and probe.metalAvailable):
        usable -= _GPU_COMMANDS
    return DoctorResult(
        command="doctor",
        pythonVersion=platform.python_version(),
        platform=platform.system(),
        machine=platform.machine(),
        mlxAvailable=probe.mlxImportAvailable,
        mlxDeviceAvailable=probe.metalAvailable,
        freeDiskBytes=free_disk,
        availableCommands=sorted(usable),
        unavailableCommands=sorted(set(get_args(Command.__value__)) - usable),
        components=sorted(components, key=lambda item: (item.role, item.name)),
    )
