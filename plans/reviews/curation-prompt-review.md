# Curation prompt contract review

Date: 2026-09-19. Scope: static review and offline unit tests for scoped candidate
generation. No model inference or dataset publication was performed for this review.

## Findings and disposition

The prior generator exposed too much task structure and could copy labels, rules,
or complete message wrappers into training inputs. The scoped contract now sends
only one editable source field and a broad answer-neutral task name. Immutable
label catalogs, evidence, tables, paragraphs, system instructions, prior turns,
and reference answers stay outside the generator request. Code reconstructs the
complete candidate task before independent checking and publication.

Answer-label copying, role wrappers, copied instructions, changed dates, numbers,
currencies, quoted spans, and substantial immutable-context copies have explicit
guards. These checks reduce known corruption modes; they are deterministic
filters, not proof that a paraphrase preserves every semantic fact. The independent
checker must still reproduce the complete reference answer before a record is
accepted, and generated records remain unreviewed teacher data.

Multi-turn records retain their ordered prior user and assistant messages. The
checker contract now states that this context precedes `candidateInput`, which is
the final user message. Source-specific answer-format hints disambiguate structural
requirements without revealing the reference answer or scenario subtype. In
particular, scenario evidence is always an array of exact quote strings, while the
TAT-QA hint records its source-observed span, arithmetic, count, origin, derivation,
and scale shapes.

The generation recipe identity binds generator and checker prompt versions,
schemas, system text, transformation text, answer formats, scenario recipe, and
the versioned task-input mappings and guards. Job IDs, run identity, and generated
record prompt provenance therefore change with these declared prompt-contract
inputs. Maintaining that property still requires new prompt behavior to be added
to the recipe descriptor rather than introduced as unversioned code.

## Remaining limits

The filters intentionally use bounded textual heuristics. They do not recognize
every possible paraphrase of an instruction, label leak, identifier, or quotation
style. Model agreement is also not human adjudication. Acceptance rates and model
quality require a separate bounded local generation run followed by artifact
inspection; old pilot outcomes must not be attributed to this revised recipe.
