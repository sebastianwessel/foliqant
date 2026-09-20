# Input-answerability research review

Date: 2026-09-19. Scope: literature research and concept refinement only.

## Deliverables and requirement coverage

| Requested outcome | Evidence |
|---|---|
| Measure whether supplied input supports an answer, not model self-confidence | `specs/research/input-answerability-and-reliability.md`, sections 1 and 4: question-relative input target; separate actual-answer adequacy; numeric estimates unavailable until qualified. |
| Handle correct-but-incomplete single classifications | Sections 2 and 3: cardinality, distinct request units including repeated categories, multiplicity versus ambiguous alternatives. |
| Handle missing facts and realistic financial cases | Sections 6 and 8: targeted retrieval/clarification, missing attachments, withdrawals, conflicting evidence, jurisdictions, units, no-match, and partial resolution. |
| Preserve typed decisions and explanations/reasoning | Sections 2 and 5: choice, predicate, ordinal score, multiple questions, supported explanations and bounded solver mode. |
| Research current primary literature using agents | Separate answerability and statistical-risk notes; 2024–2025 peer-reviewed work, clearly labelled May 2026 preprints, and foundational risk methods. Two agents used for bounded independent research. |
| Improve the existing concept rather than add detached notes | Model-research owner, workflow-service proposal, and specs index link and incorporate the refinement. |
| Keep standard inference and honest implementation status | Sections 2 and 9: ordinary structured inference, no custom head/server, source-inspected gaps, no claim that existing calibration implements this target. |

## Source and implementation checks

Inspected `risk.py`, `scoring.py`, and `evaluation.py`: current risk selection uses mean generated-token log probability and exact correctness; evidence checks establish substring occurrence, not entailment or request completeness. These are documented baseline limitations, not repaired or relabelled in this research task.

Primary references were read through their publisher/arXiv pages by the research agents and main agent. Central claims were cross-checked against Sufficient Context, ConfuseBench, Conformal Risk Control, and the two May 2026 ambiguity preprints. No published result is presented as a measured Foliqant capability. No paper establishes a universal per-input safety guarantee.

The research distinguishes runtime observable checks from semantic predictions and offline gold metrics. It does not use known gold request counts or retrieval recall as deployment features. Calibration, threshold selection and final audit remain separate, with distribution and sampling assumptions stated.

Independent concept review identified and resolved seven issues: replaced the stale category-only result example; assigned assessment ownership and disagreement handling; fixed the numerical target to adjudicated sufficiency rather than reviewer-vote propensity; clarified no-match/unknown semantics; separated multi-select applicability from categorical normalization; added conditional/dependent requests; and corrected wording that implied model-proposed support was an observed fact. Statistical review additionally separated learned flags from trusted metadata, prohibited runtime gold retrieval recall, separated probability calibration from policy selection, scoped adequacy to the requested question, and added the conformal candidate-family feasibility caveat.

## Verification and boundary

- Documentation/CLI reference checker passed: 22 documents, 31 CLI examples.
- Additional research check passed: six changed/new research/index files, 21 local links and two valid JSON illustrations.
- Tracked-data audit passed; no datasets, weights, customer records, or generated outputs added.
- Whitespace diff check passed.

No runtime code or generated schema changed; no model execution, training, downloads, or new performance measurements were required. This is not an implementation-readiness approval or production qualification. The next empirical step is a blinded annotated baseline and comparison of assessors; numeric reliability claims remain unqualified until that work succeeds.
