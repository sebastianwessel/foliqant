"""Automated local dataset curation."""

from foliqant_decisions import (
    CategoryCatalog,
    CategoryDefinition,
    CategoryKey,
    normalize_category_key,
)

from .endpoint import (
    EndpointModelIdentity,
    GenerationResponse,
    LocalEndpointConfig,
    discover_models,
    generate_json,
)
from .sources import acquire_source, source_catalog_digest

__all__ = [
    "CategoryCatalog",
    "CategoryDefinition",
    "CategoryKey",
    "EndpointModelIdentity",
    "GenerationResponse",
    "LocalEndpointConfig",
    "acquire_source",
    "discover_models",
    "generate_json",
    "normalize_category_key",
    "source_catalog_digest",
]
