# Generated JSON schemas

`foliqant/runtime/` describes runtime boundaries. Its canonical Pydantic models
live in `src/foliqant/contracts/`. `foliqant/decisions/` describes the native
decision contracts shared by runtime and model tooling; its canonical types live
in `src/foliqant/decisions/`. Use `scripts/generate_schemas.py` to regenerate
and check both library-owned schema sets.

`../model/schemas/` describes training and curation artifacts. Its canonical
schema registry is `model/src/foliqant_model/schemas.py`; use
`scripts/generate_model_schemas.py`.

Schemas do not replace semantic validation, evidence checks or category-key
normalization. Do not edit generated files or commit actual datasets here.
