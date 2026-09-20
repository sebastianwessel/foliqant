# Shared contracts

Language-neutral JSON Schema contracts belong here. The root `triage-*.schema.json`
files are illustrative draft contracts used by the financial-triage proposal;
they do not define an implemented service API.

`model/` contains generated schemas for the implemented Python model tooling.
Its canonical definitions are the Pydantic classes registered in
`model/src/foliqant_model/schemas.py`. The category-catalog authoring schema
describes accepted raw configuration; Python normalization and semantic checks
produce unique canonical keys. JSON Schema validation alone does not transform
IDs or detect collisions between differently formatted keys.

The input/result fixtures are synthetic. Schema validation does not verify evidence truth, calibration, identity, or legal applicability. The future runtime must perform semantic checks separately. A complete workflow-bundle and service-configuration schema will be added before a loader is implemented.
