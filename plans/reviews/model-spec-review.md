# Model lifecycle specification review

Date: 2026-09-19. Reviewer: independent gpt-6-astra agent /root/lifecycle_spec_review, high reasoning. Scope: lifecycle, operations and pinned backend contracts. This record is review evidence, not human approval or implementation completion.

| Finding | Disposition in canonical specs | Required implementation evidence |
|---|---|---|
| Row-level binomial bounds counted correlated records | Select predetermined min-ID representative per component for policy/audit | Grouped examples do not inflate risk sample count; exact small-count bound tests |
| Customer QLoRA parent was unreachable | Accept quantized shared parent directly derived from merged shared | Dense and quantized customization succeed; customer ancestry rejected |
| Shared training could consume customer lineage or data without specific consent | No customer ancestors; every new source explicitly permits sharedTrainingAllowed | Negative lineage and source-permission tests |
| Separate customer dataset could leak ancestor training examples | Preserve transitive exposure indexes and reject calibration/test overlaps | Cross-dataset group/prompt/content/ID overlap tests including warm starts |
| Plain-text correctness was invalidated by JSON parsing | Require JSON only for configured structured checks | Plain text passes; bool/number, missing field, invalid evidence cases fail appropriately |
| Token loss included first padding token in pinned backend | Public trainer loss hook with completion-mask-v1 | Real MLX token-mask tests with unequal lengths and supervised-token boundaries |
| Exact sampled-row exposure would need unnecessary instrumentation | Conservative index of all submitted train/validation rows | Exposure indexes survive merge, quantization, export and warm start |

The second read-only pass confirmed the first six lifecycle semantics consistently and identified the loss-mask issue from the tagged trainer source. Root applied the explicit loss-hook resolution and conservative exposure policy. Exact durable contracts are still a separate review item. Real install/import, training and export acceptance must be executed and recorded later; no unit double qualifies as that evidence.

## Durable-contract review

An additional independent pass found validation-split leakage checks applied too broadly, unbound external JSON-schema references, missing inherited exposure bytes for standalone union verification, unspecified dataset file paths, mismatched numeric bounds and an oversized maxExamples edge case. The contract author applied corrections to all six. The gold-label field comparison is explicitly separate from prediction-only acceptance eligibility, preventing biased risk selection. Exact doctor worker and tokenizer/template identity rules were added. Canonical Python/schema materialization and executable tests remain in progress; passing the draft structural checker does not approve implementation readiness.
