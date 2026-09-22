# Typed decisions and evidence strength

This example asks five questions about a customer message: single-category
triage, multiple labels, a charge-dispute predicate, priority, and request units.
Triage and labels interpret card-freeze or statement **communication purposes**;
request units require **actionable instructions**. Priority assesses timing,
not support strength. The dispute question measures a separate dimension.

`evidence_strength` describes support for the whole reported assessment.
“Freeze my card and send my statement” decisively exceeds single-choice
cardinality: triage abstains with `multiple_valid_options` and **strong** strength.
Multiselect returns both purposes with strong strength. A supported abstention,
a false predicate, and an explicitly empty collection can all be strong.
`null` means no strength assessment was made; it is not an abstention marker.

The interpretive questions permit a best-fitting purpose suggested by a concrete
service difficulty. A clear goal to review transactions plus an explicitly missing
statement strongly supports the broad statement-obtaining purpose, even when the
customer has not chosen an action to request. Purpose and executable action are
separate assessments.

The limited examples instead describe missing bank paperwork with an uncertain
recollection of transaction listings: it could be another kind of notice. This
weakens the **purpose category itself**. The gold permits a tentative statement
interpretation without claiming the document is a statement or inventing an
instruction to send one. These are reviewed business judgments, not universal
labels for particular wording. Politeness, low urgency, and indirect inference do
not automatically weaken support. A supplied rule plus facts may decisively
establish an action. For collections, strength covers every material claim,
including returned members and unresolved parts; answerability describes
completeness separately. No strength value is a probability or accuracy guarantee.

## Inspect and run

Read [workflow.yaml](workflow.yaml), [gold.py](gold.py), and
[foliqant.yaml](foliqant.yaml). The [isolated predicate](predicate/workflow.yaml)
uses the same criterion on two development messages to expose task mixing. It
uses the public single-question shorthand, whose result ID is the step name.
All workflows use the existing in-memory runtime and evaluator.

From the repository root, check configuration and authored gold without inference:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate
```

The default command reports `offline_check`; it does not fabricate predictions
or establish model quality. Configure the root `.env` as described in
[support triage](../support_triage/README.md), then explicitly run local Qwen:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate --live
```

Development contains 15 grouped messages (75 strength ratings) and two repeated
single-predicate controls (two ratings). The reported 17 cases therefore do not
represent 17 independent inputs. Families cover direct support, irrelevant text
and repetition invariance, bilingual multi-intent, explicit no-action, partial
conflict, explicit correction, explicitly absent facts, purpose/action separation,
uncertain document purpose, mixed support, and decisive inference. The uncertain
purpose family's English, German, and mixed-context variants stay together.
Grouped gold contains 69 strong and six limited ratings; the two predicate controls
add two strong ratings. Each case includes its rationale in `gold.py`.

Four validation messages form two additional EN/DE families, kept separate from
development. Their 20 strength ratings are all strong, so this split does not
estimate limited-label recall. Reserve their predictions for after prompt selection:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate --live --validation
```

Translations and invariance variants stay in one family. Once validation failures
influence a prompt, that family is no longer held out for the next comparison.
These small synthetic suites demonstrate framework behavior; they do not estimate
financial accuracy. No artificial business case requests an unassessed null
strength; boundary tests cover that contract value.

Live evaluation makes sequential calls, without hidden retries. Strict response
validation rejects inconsistent results, and failed gold checks fail the command.
Private reports retain answers, reasons, strengths, issues, routes, and available
usage. Strength confusion matrices declare `[limited, strong, null]`: an actual
null is an observed unassessed outcome, while absent fields and failed executions
remain separate. Null has no positive gold support in these fixtures. Inspect
reasons and request descriptions manually. Checks cover unit counts, expected
positions, subjects, categories and statuses. Nonempty counts use the existing
Python custom-scorer API; reusable `build_suite(gold, spec)` and `SCORERS` expose
that check for other Python evaluation runners.

`--output` selects a new private report path without overwriting. Export revision
4 gold to use the existing JSON evaluation interface. JSON export retains the
positional checks and exact empty collections but cannot register the Python
count scorer, so it does not detect additional units in nonempty collections:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate \
  --write-dataset .foliqant/evaluation/decision-evidence-r4.json
uv run --no-sync foliqant evaluate \
  --config examples/decision_evidence/foliqant.yaml --check
```

To embed the workflow, use `prepare_application`, `open_application`, and
`app.run("decision_evidence", Envelope(payload={"message": "..."}))` as shown in
the [runtime guide](../../docs/getting-started/runtime.md). Any unresolved question
follows `review`; otherwise the graph follows `done`. Strength introduces no
hidden routing threshold.
