# Rejected-response retention and repair — 2026-09-20

Status: implemented and offline verified. No bulk generation, training, deployment,
commit or push performed. This change does not establish model quality.

The private outcome ledger now records per-attempt disposition, phase-specific
safe reasons, call identities and response previews. Complete final assistant
content is retained in the immutable call cache, including malformed JSON,
schema-invalid output and truncated completions where final content exists.
Previews and correction prompts are capped at 32,768 characters; the cached final
response is not truncated to that preview size. Transport response limits still
apply. Empty/absent final content is not fabricated, and separate hidden reasoning
fields, HTTP error bodies and credentials are excluded. Legacy parsed outputs are
labeled as canonical reconstructions, not original response text.

`--repair-from` creates an immutable child with the same effective recipe,
configuration, observed model metadata and frozen task plans. It copies accepted
outcomes and their available response cache, processes only quarantined jobs,
retains a parent link, and gives repaired jobs fresh request identities. Both
inline retries and separate passes use bounded phase-matching rejection feedback
without supplying the oracle. Ordinary validation, duplicate checks and coverage
still apply. Retry policy participates in the recipe digest. Repeated invocation
resumes the child; a later child can be selected as another parent. No overwrite,
automatic acceptance, human-review claim or recovery of never-retained text is
introduced.

Both generic and native runner tests exercise a mixed accepted/rejected first
pass, repair-only inference, unchanged accepted outcome bytes and parent files,
normal dataset publication, no inference on child resume, and rejection of a
changed configuration before inference. Additional tests cover retention beyond
32K, malformed final content, reasoning-only length completion, immutable failed
call replay, trace integrity and root `.env` key isolation. Test doubles verify
orchestration and failures; these are not live-model repair-quality measurements.

Final verification: 465 offline tests passed; 7 native MLX integration tests were
excluded by the normal test configuration. Ruff passed across source/tests/scripts;
mypy passed for 53 source files; 23 generated schemas matched; docs and tracked-data
checks passed; the model skill validated; `git diff --check` passed. The bundled
Git runtime was used because the system Git requires an unavailable Xcode license
acceptance. Spec manifest digest:
`sha256:80b7b77cecfebe9147be83a701057c4014425a87ba52a251405d1373b9d52897`.
No formal human spec approval or production qualification is asserted.

Separate real OpenRouter comparisons are documented in
[generator comparison](openrouter-generator-comparison.md). Their original responses
remain outside Git, including rejections and conservative validator false positives.
