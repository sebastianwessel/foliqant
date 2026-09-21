# Generated JSON schemas

`foliqant/` describes the installable Python library. Its canonical Pydantic
models live in `src/foliqant/contracts/`; use `scripts/generate_schemas.py` to
regenerate and check them. Native decision definitions live in
`src/foliqant/decisions/` and are reused by the model tooling.

`model/` describes training and curation artifacts. Its canonical schema registry
is `model/src/foliqant_model/schemas.py`; use `scripts/generate_model_schemas.py`.

Schemas do not replace semantic validation, evidence checks or category-key
normalization. Do not edit generated files or commit actual datasets here.
