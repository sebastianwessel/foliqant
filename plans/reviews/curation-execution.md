# Automated curation acceptance

Implemented and verified on the local Apple Silicon host. This accepts the
research-data tooling; it does not qualify a financial model or the source labels.
Exact counts and identities are in [measurements](curation-measurements.json).

## Real source and local-model evidence

Five pinned sources produced 4,998 records at the configured 1,000-per-source caps.
Families were frozen before generation; original holdouts stayed held out. The
catalog includes the typed-decisions source used by Laya and the SemIf WANLI test
selection, with WANLI training data kept separate. It is a bounded research
corpus, not a complete reproduction of either original model.

The final twil-lm3 eight-candidate run accepted one synthetic-regression case and
quarantined seven (six independent-answer mismatches; one changed-number case).
No training augmentation passed this pilot. Source-corpus and augmented-corpus
therefore contain the same 4,998 original records; synthetic-regression contains
20 authored seeds plus one locally generated, automatically checked variant.
Generated content remains origin=teacher and reviewed=false. The successful
rerun retained all 27 cached call hashes and mtimes and all three artifact IDs.
Independent retained-data verification passed for every published dataset.

Qwen's guided-schema probe returned empty final content with content in reasoning;
the adapter rejected it. A bounded standard chat request without response_format
returned valid final JSON. The added explicit prompt mode omits server-side grammar
and retains strict local JSON/schema validation, with no automatic fallback. A
four-candidate Qwen corpus pilot then hit its first configured 180-second request
deadline. It stopped with TIMEOUT, without publishing generated results. We did
not increase the budget indefinitely, accept reasoning as output, or change the
user's model-server settings. Closing the client does not establish that the
server stopped its GPU work.

A separate tiny-model compatibility check trained for two real MLX steps on five
curated BANKING77 records retaining frozen splits. It finished in 1.58 seconds,
loss 2.1437621116638184. This is a lifecycle smoke test, not a financial metric.

## Verification and limits

The final suite passed 257 offline tests; seven native integration tests are
excluded by the default test marker, not claimed as part of that count. The real
tiny training run above supplies targeted native evidence for this extension.
Strict mypy, Ruff, 21 generated schemas, documentation, skill, tracked-data and
spec audits passed. A built wheel imported independently outside the checkout,
matched the source package byte-for-byte, and included the catalog/profile and
both output modes without datasets or model weights.

Typed soft distributions and token-aligned MultiDoGO annotations remain source
records but are excluded from generic augmentation with counted reasons. A
paraphrase cannot safely inherit token positions or recreate a teacher's exact
probability distribution. MultiDoGO uses neutral redaction and discards all
cross-conversation duplicate payload copies rather than leaking labels or
joining almost every conversation through generic repeated turns.

English generation was exercised. German is configurable but not qualified by
these runs. Teacher agreement is not human adjudication. License evidence and
unknown model-output terms remain in lineage; no commercial clearance is claimed.
Source/model artifacts and all generated records remain outside Git. No paid
compute, upload, model download, deployment, commit or push occurred in this phase.

The next separate quality step is selecting/configuring a local generator with
an acceptable acceptance yield and latency, then a larger measured generation
run. The automation requires no manual record editing or labeling.
