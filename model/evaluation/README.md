# Evaluation and calibration

Keep reusable model, customization, and export evaluation here. Maintain separate training, development/calibration, and final audit partitions. Split complete thread/document families, issuer/customer groups, translations, and relevant time periods to prevent leakage.

Measure active-intent accuracy, urgency errors, catalog validity, evidence support, abstention/risk coverage, language slices, structured-output validity, latency, and memory. A model-generated confidence number is not a calibrated probability. Re-evaluate after quantization or template/runtime changes.

Only synthetic public fixtures and code belong in Git; real holdouts need controlled storage. No evaluation harness or calibration artifact is implemented yet.
