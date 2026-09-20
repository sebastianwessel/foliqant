# Datasets for evidence-backed business processes

Checked: 2026-09-20. Scope: primary-source metadata, papers, dataset cards and
terms research and small public viewer samples only. No full corpus or model
acquisition, training, or inference calls were made.
These candidates are not additions to the pinned acquisition manifest or current
generation. [Specification 10](../10-business-decisions-and-processes.md) owns
the target semantics; [existing source research](curation-sources.md) owns the
currently selected curation sources.

## Recommendation

Prioritize NLU++ for compositional banking labels, MultiDoGO insurance for another
domain, SGD/SGD-X for multi-service dialogue state, and Doc2Dial/MultiDoc2Dial for
public-service evidence. Add native German public-sector material through CIMT
and the Public Service Encounter Corpus only after version/annotation/rights
review. MAILEx is unusually relevant to evolving email threads but requires
separate underlying-message rights review. ABCD is valuable for process/action
semantics even though its domain is retail.

Use Tobi-Bueck support tickets only as a possible supplementary synthetic source
after the source-specific checks below. For the next acquisition, prefer NLU++;
follow with MAILEx for thread/event work once its externally hosted files and
underlying-message terms are pinned. Keep existing projection validation and
repair separate from adding new sources.

Use fund filings and process event logs for structured facts, validation and
process design, not as substitutes for annotated correspondence. No inspected
public source jointly supplies real banking/insurance/public-sector threads,
all independent requests and withdrawals, per-answer evidence, child-process
execution truth, and English/German coverage. A separately authorized,
human-adjudicated target-domain qualification set remains necessary. Existing source labels may be projected without relabeling where their semantics
match. New request-unit, subflow or evidence labels require separately recorded
provenance and verification; they are not supplied as gold by unrelated source
annotations. The current automated generation command remains unchanged.

## Language and conversation candidates

Terms below record publisher declarations, not a legal clearance of every
underlying document or of a future model release. Review exact pinned versions,
attribution, redistribution and downstream use before acquisition/adoption.

| Candidate and primary evidence | Annotations and language | Intended use and important limits | Published terms / access |
| --- | --- | --- | --- |
| [NLU++](https://github.com/PolyAI-LDN/task-specific-datasets/tree/master/nlupp), [paper](https://arxiv.org/abs/2204.13021) | English; 3,080 utterances, including 2,071 banking examples. Compositional intent modules and slot spans, with canonical values for dates/times/numbers; banking averages 2.25 intent labels per example. | Highest-priority new multilabel/catalog-boundary source. Several labels can describe **one request**, e.g. change + overdraft + higher. Never translate label count into independent request/task count. Single utterances, not evolving email threads. | Publisher repository declares datasets CC BY 4.0. Review ontology and annotation provenance when pinning. |
| [MultiDoGO finance and insurance](https://github.com/awslabs/multi-domain-goal-oriented-dialogues-dataset), [paper](https://aclanthology.org/D19-1460/) | English elicited conversations. Released supervised customer-turn TSVs provide intent and token-aligned slot labels; the paper also describes agent dialogue-act annotation. Designed cases include multiple intents and intent/slot changes. | Extend the current finance audit to insurance. Annotated customer-turn TSVs must not be mistaken for complete annotated alternating conversations. Full conversations and annotated variants require verified linkage. Slots annotate entities, not intent rationales. | Project data uses CDLA-Permissive-1.0. Current redaction, original split and conversation-family safeguards still apply. |
| [Schema-Guided Dialogue and SGD-X](https://github.com/google-research-datasets/dstc8-schema-guided-dialogue) | English; over 20,000 dialogues across 20 domains including banks. Per-service frames, active intents, requested slots and slot values, character spans for non-categorical mentions, actions and service calls/results. SGD-X provides schema paraphrases. | Strong proxy for multiple service contexts in one case, prerequisites, changing state and unseen catalog wording. Simulator/crowdworker-derived dialogue, not real bank mail. Multiple frames do not prove simultaneous independent subflows. Preserve whole dialogue and schema-variant families. | Explicit CC BY-SA 4.0 for the data; adapted-data sharing obligations and intended downstream use need review. Official repository is archived; pin immutable revisions. |
| [ABCD](https://github.com/asappresearch/abcd), [paper](https://aclanthology.org/2021.naacl-main.239/) | English; over 10,000 simulated customer-service dialogues, 10 high-level flows, 55 subflows/intents, 30 actions, parameters and policy/guideline material. | Useful policy-constrained action sequence and prerequisite supervision. Retail domain; each dialogue follows one ground-truth subflow. It is not a concurrent-subflow gold dataset. | MIT at the repository root, which includes data; record the repository grant and confirm the exact data subset, rather than inventing a separately issued dataset license. |
| [Doc2Dial](https://huggingface.co/datasets/IBM/doc2dial), [MultiDoc2Dial](https://huggingface.co/datasets/IBM/multidoc2dial), [project](https://doc2dial.github.io/) | English public-service dialogues grounded in DMV, SSA, VA and FEMA documents. Turn/document references, spans and offsets; MultiDoc2Dial adds topics across documents. | Strong evidence selection and public-service prerequisite source. Crowd-authored assistance rather than real administrative outcomes; no child execution or complete withdrawal lifecycle. Preserve document-family and dialogue cross-links when splitting. | Doc2Dial data card: CC BY 3.0; baseline code: Apache-2.0. MultiDoc2Dial card: Apache-2.0. Audit underlying government/state web documents and attribution separately; do not assume all copied pages are public domain. |
| [MAILEx](https://github.com/salokr/Email-Event-Extraction), [paper](https://aclanthology.org/2023.emnlp-main.801/) | English; 1,500 Enron threads, 3,936 emails, 8,392 annotated events. Trigger/argument spans, sometimes discontinuous; request, delivery and amendment events. | Best inspected email-thread/evidence candidate, including multiple events and changes. Event types are not workflow routes; amendments are not complete request-state annotations. Treat linked thread prefixes as one family. | Dataset README declares CC BY-SA 4.0 for the released dataset; underlying Enron-message rights/privacy require separate review. Do not treat annotation terms as clearance for commercial use of message text. |
| [CIMT PartEval](https://aclanthology.org/2022.lrec-1.308/), [themes](https://github.com/juliaromberg/cimt-thematic-categorization-dataset), [arguments](https://github.com/juliaromberg/cimt-argument-mining-dataset), [concreteness](https://github.com/juliaromberg/cimt-argument-concreteness-dataset), [locations](https://github.com/juliaromberg/cimt-geographic-location-dataset) | Native German citizen contributions in municipal mobility planning; document-level thematic multilabels, sentence-level argument-component labels, sentence-span concreteness ratings and geographic token spans/coordinates. | Relevant German public-sector categorization, specificity and extraction. Mostly individual contributions; no complete case history, withdrawal or execution labels. Topic facets must not become separate citizen requests. | Data repositories state CC BY-SA 4.0 with source-specific terms. The paper's separate CC BY-NC license does not determine dataset rights. |
| [Public Service Encounter Corpus](https://doi.org/10.7910/DVN/Y8YU2O), [original paper](https://aclanthology.org/2024.lrec-main.1165/) | Native German, anonymized real citizen/official encounters. Current metadata reports 232 conversations, approximately 740,000 tokens; transcript/speaker structure rather than intent annotations. | Useful institutional language and long clarification sequences, not ready-made intent/evidence gold. Other-language passages may be placeholders, so do not claim multilingual supervision. Pin versions and account for participant withdrawals/deletions. | Current 2026 release metadata: CC BY 4.0. Original v1 paper describes CC0; do not transfer the old license to a later release. Review the actual adopted version and access conditions. |

## Structured operations and secondary candidates

### Support-ticket source inspection

`Tobi-Bueck/customer-support-tickets` at revision
`ddf1c81a5475992c4fa6752bf1e8b4e31f07bbeb` contains English/German tickets.
The [publisher](https://softoft.de/blog/ticket-dataset/) describes synthetic
generation; the [pinned release](https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets/tree/ddf1c81a5475992c4fa6752bf1e8b4e31f07bbeb)
declares CC BY-NC 4.0. This contradicts treating all Laya training sources as
human-annotated real correspondence. Preserve `origin=synthetic` and
`reviewed=false`; research restrictions follow derived artifacts.

The merged viewer reports 61,765 rows in one train split from three files with
different schemas. Type/answer fields are absent for 13,178 rows; version is
absent for 33,178. Observed priority values are `very_low`, `low`, `medium`,
`high`, and `critical`, while card prose describes only three levels. Type labels
are Incident, Request, Problem and Change: they do not directly distinguish
information requests from confirmations. There are no dedicated deadline,
timestamp or evidence-span annotations. Dates occurring in prose are not date
extraction gold. The small sample also contains questionable type/priority
assignments; this is a warning for source review, not a measured error rate.
Some sampled messages explicitly state a priority label. Evaluate performance
without those shortcuts and group related templates before splitting; random
row splits alone do not establish rubric generalization.

If adopted, pin individual file hashes and versions, preserve original columns,
and measure near-duplicate/template/translation families before splitting.
Exclude response `answer`, classification fields and tags from input when they
would reveal the target. Source labels are provisional references under their
original meaning; blind verification under our explicit catalog/rubric is needed
before use. Do not map Request to `information_request` or infer confirmation,
deadline, evidence, or independent request units without new annotations.
Start with a small language-balanced research sample; do not let this synthetic
corpus dominate the shared dataset. No importer is enabled by this assessment.

### Which Laya-associated sources to reuse

| Source | Decision for Foliqant |
| --- | --- |
| Tobi-Bueck support tickets | Supplementary synthetic phrasing candidate; require the per-file audit above. |
| AG News | Defer: topic classification offers little coverage of support-intent boundaries or evolving requests. |
| BoolQ | Optional later evidence-conditioned predicate challenger; it supplies no dialogue confirmation, priority or due-date labels. Current NLI/answerability sources take precedence. |
| Enron spam / phishing-email dataset | Defer unless spam/security routing becomes a measured requirement; source labels do not teach support triage. Underlying message provenance and privacy remain source-specific. |
| MS MARCO v1.1 | Defer: retrieval/QA is peripheral to the current intent/thread gap. [Official terms](https://microsoft.github.io/msmarco/Notice.html) restrict use to non-commercial research and distinguish underlying web-document rights. |

NLU++ has directly useful `request_info`, `affirm`, `deny`, and `acknowledge`
modules plus date/time slots, unlike generic QA. These are compositional
attributes, not a ready-made confirmation/action taxonomy. Its date normalization
uses a dataset reference date; retain that context rather than substituting the
current date. Inspection revision:
[`57ec275d8078af65b7731c2a98be812d844a6d6b`](https://github.com/PolyAI-LDN/task-specific-datasets/tree/57ec275d8078af65b7731c2a98be812d844a6d6b/nlupp).
MAILEx distinguishes Request, Deliver and Amend events with arguments including
dates and times; preserve its event semantics and linked threads. Repository
revision [`57507a458ce157378300d982914d7528448a381d`](https://github.com/salokr/Email-Event-Extraction/tree/57507a458ce157378300d982914d7528448a381d)
does not pin the externally hosted Google Drive data bytes. Record those hashes
before acquisition is reproducible.

### Other structured sources

| Source | Useful contribution | Limits and terms to preserve |
| --- | --- | --- |
| SEC [N-CEN](https://www.sec.gov/data-research/sec-markets-data/form-n-cen-data-sets), [N-CEN field guide](https://www.sec.gov/file/ncen_readme), [N-PORT](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets), [N-PORT catalog](https://catalog.data.gov/dataset/form-n-port-data-sets), [N-CEN catalog](https://catalog.data.gov/dataset/form-n-cen-data-sets) | Registered-fund structures, service providers and operational disclosures; holdings/reporting fields. Good source for field schemas, joins, literal extraction and deterministic checks. | No request, thread, intent or process-action labels. Values are as filed, not verified truth. Government catalog labels the datasets U.S. Public Domain; any added vendor/reference data has separate rights. Synthetic letters derived from these structures remain synthetic, not historical requests. |
| [BPI 2017](https://research.tue.nl/en/datasets/bpi-challenge-2017/) | Bank loan-application event log with applications, multiple offers and work events; useful parent/child identity and lifecycle examples. | No correspondence or evidence-span annotations. Primary catalog references 4TU General Terms of Use; exact commercial-training permissions remain unresolved. Do not substitute a third-party mirror's license. |
| [BPI 2020](https://www.tf-pm.org/competitions-awards/bpi-challenge/2020), [travel permits](https://data.4tu.nl/articles/dataset/BPI_Challenge_2020_Travel_Permit_Data/12718178/1) | Administrative approval/expense processes; several claims/declarations may relate to one travel permit. Useful branching, prerequisite and resubmission scenarios. | Structured event logs, not language/intent training. Primary travel-permit record declares CC BY-NC 4.0; review terms for each collection member. Exclude from commercial use/training absent additional permission. |
| [BPI 2016 Werkmap](https://research.tue.nl/en/datasets/bpi-challenge-2016-werkmap-messages/), [questions](https://research.tue.nl/en/datasets/bpi-challenge-2016-questions/) | Dutch Employee Insurance Agency cross-channel journeys and message/question metadata. | Do not infer actual message bodies from the word "messages". Schema/content inspection is still needed. Primary record does not expose a clear standard license; retrieve and review exact archived 4TU terms before use. Commercial permission unresolved; not an acquisition priority. |
| [GerPS ontology](https://doi.org/10.5281/zenodo.10670239), [FITKO service catalog concept](https://docs.fitko.de/fim/docs/leistungen_neu/fachkonzept/leistungskatalog/) | German public-service vocabulary for tasks, actors, evidence, deadlines and forms. Useful configuration/catalog design. | Small ontology with an example service, not a labeled correspondence corpus. Ontology data is CC BY 4.0; separate software grant is not interchangeable. |
| [Connecticut insurance complaints](https://data.ct.gov/d/t64r-mt64) | Coverage, complaint reason, disposition and recovery metadata; useful category/outcome design. | No narrative or intent spans. Publisher catalog declares Public Domain; cannot train email understanding from structured categories alone. |
| [DocILE](https://docile.rossum.ai/), [software](https://github.com/rossumai/docile) | Business-document field and line-item extraction with page/bounding-box/text annotations. | Research-purpose dataset access; software MIT does not license dataset use. Seek explicit permission for commercial training. Secondary because extraction is not the primary gap. |
| [InsuranceQA](https://github.com/shuzi/insuranceQA) | English insurance question/answer selection, useful as a terminology/retrieval challenger. | Research-purpose-only declaration; no thread, evidence or independent request-unit labels. Do not treat it as claims-processing or commercially cleared training data. |

## Bloomberg assessment

The [BloombergGPT paper](https://arxiv.org/abs/2303.17564) explicitly states that
FinPile cannot be released. It combines public, purchased and private financial
text and is not an accessible labeled fund-operations correspondence dataset.
Do not plan a FinPile download or rely on third-party mirrors of Bloomberg news
whose license tags do not establish rights to the original articles.

The official [EntSUM release](https://huggingface.co/datasets/bloomberg/entsum)
provides entity-focused summary annotations, but the underlying New York Times
articles require separately licensed corpus access. It is not specific to fund
operations; the software license does not convey article rights.

[Bloomberg Data License](https://professional.bloomberg.com/products/data/data-management/data-license/)
is a commercial product, not an open supervised corpus. A future client agreement
might permit particular retrieval/data uses; subscription access alone does not
establish training or redistribution permission. SEC fund datasets are a more
practical public starting point for operational structure, not a replacement for
annotated customer instructions.

## Mapping rules and next experiments

1. NLU++ maps to caller-defined multilabel **attributes**, not a count of
   independent requests. Verify slot spans and normalization separately. Keep
   the native `multiselect` type; no source-specific public task enum is needed.
2. MultiDoGO insurance requires a source audit before selecting its supervised
   form. Preserve conversation IDs, all intents, redaction alignment and raw
   annotations; quantify independent multi-request examples rather than relying
   on the paper's collection-bias counts.
3. SGD/SGD-X and ABCD support questions about state/prerequisites and permissible
   next actions under supplied policies. Do not turn observed service calls into
   permission to execute them. Preserve all dialogue/schema variant families;
   future turns and gold actions cannot enter earlier-decision prompts.
4. Doc2Dial and MAILEx support exact-source evidence experiments. Check annotation
   offsets against the pinned text and preserve discontinuous spans. Entity/slot
   annotations alone are not intent rationales. Keep evidence relevance and
   literal span validity as separate targets.
5. CIMT and Public Service Encounters extend native German language coverage;
   qualification still needs domain-specific, independently reviewed tasks.
6. Fund filings and BPI logs can inform deterministic checks and authored process
   fixtures. Any invented natural-language narrative or intent is explicitly
   synthetic. An event sequence does not uniquely determine the governing policy.

For one-process/multiple-task evaluation, annotate actual request instances,
not just labels: two independent requests; two instances of the same category;
one active plus one withdrawn; conditional alternatives; a shared prerequisite;
and a correction after one child has completed. Retain the configured mapping
and the expected parent/child outcomes separately from the language-model target.
Task count follows reviewed process configuration and request instances, not
label count or dialogue service-frame count.

Before any acquisition, record the exact revision/files, terms evidence,
annotation provenance, languages, source grouping and proposed projection with
exclusion rules. Prototype deterministic mappings on synthetic fixtures first.
Only a separately authorized new run may add downloaded sources or model-based
generation; never rewrite current frozen snapshots or use final-test annotations
as training data. Paid access, agreement acceptance and customer-data use remain
explicit owner decisions. Research-only sources are allowed when their terms
permit the intended research; restrictions must follow future model ancestry.
