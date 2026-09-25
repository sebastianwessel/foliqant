"""Declare handler contracts in a test settings file from host registrations."""

from collections.abc import Mapping
from pathlib import Path

import yaml

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.json import thaw_json


def declare(settings: Path, handlers: Mapping[str, HandlerRegistration]) -> Path:
    """Set the `handlers:` section to mirror each registration's schemas and effect."""
    document = yaml.safe_load(settings.read_text(encoding="utf-8")) or {}
    document["handlers"] = {
        name: {
            "input_schema": thaw_json(registration.input_schema or {}),
            "output_schema": thaw_json(registration.output_schema or {}),
            "effect": registration.effect,
        }
        for name, registration in handlers.items()
    }
    settings.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return settings
