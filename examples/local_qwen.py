"""Load the explicitly configured local Qwen profile used by model examples."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from foliqant.contracts.models import ModelProfiles
from foliqant.core.errors import ErrorCode, ServiceError

_KEYS = (
    "FOLIQANT_CURATION_ENDPOINT_URL",
    "FOLIQANT_CURATION_MODEL",
    "FOLIQANT_CURATION_REASONING_EFFORT",
    "FOLIQANT_CURATION_MAX_TOKENS",
    "FOLIQANT_CURATION_TEMPERATURE",
    "FOLIQANT_CURATION_TIMEOUT_SECONDS",
)


def _selected_dotenv(path: Path) -> dict[str, str]:
    """Read only known, non-secret settings without interpolation."""

    try:
        if not path.exists():
            return {}
        values = dotenv_values(path, interpolate=False)
        return {key: value for key in _KEYS if (value := values.get(key)) is not None}
    except (OSError, UnicodeError, ValueError):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None


def local_qwen_profiles(
    repository_root: Path, environment: Mapping[str, str] = os.environ
) -> ModelProfiles:
    """Build one explicit profile from the model-curation settings."""

    values = _selected_dotenv(repository_root / ".env")
    values.update({key: environment[key] for key in _KEYS if key in environment})
    try:
        endpoint = values["FOLIQANT_CURATION_ENDPOINT_URL"]
        model = values["FOLIQANT_CURATION_MODEL"]
        if not endpoint.strip() or not model.strip():
            raise ValueError("empty endpoint or model")
        profile = {
            "models": {
                "local_qwen": {
                    "provider": "openai_compatible",
                    "api": "chat",
                    "model": model,
                    "base_url": endpoint,
                    "allow_insecure_http": endpoint.startswith("http://"),
                    "api_key_env": None,
                    "output_mode": "native",
                    "supports_text": True,
                    "supports_json_schema": True,
                    "supports_tools": False,
                    "concurrency": 1,
                    "queue_limit": 0,
                    "request_timeout": float(values["FOLIQANT_CURATION_TIMEOUT_SECONDS"]),
                    "options": {
                        "max_tokens": int(values["FOLIQANT_CURATION_MAX_TOKENS"]),
                        "temperature": float(values["FOLIQANT_CURATION_TEMPERATURE"]),
                        "reasoning_effort": values["FOLIQANT_CURATION_REASONING_EFFORT"],
                    },
                }
            }
        }
        return ModelProfiles.model_validate(profile, strict=True)
    except (KeyError, TypeError, ValueError):
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
