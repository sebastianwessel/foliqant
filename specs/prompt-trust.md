# Prompt trust and cache policy

## Boundaries and acceptance

Trusted authored instructions and compiler-generated question definitions are
separate from source data. Bound inputs, tool output and previous model results
remain lower-trust data, even if labeled policy or metadata. JSON serialization
prevents structural delimiter breakout; neither encoding nor instruction text
provides a semantic security guarantee. Legitimate customer imperatives remain
evidence of intent; embedded requests to change criteria, reveal instructions,
expand permissions or choose a forced result must not control execution.

Fresh model conversations are the default for each step. Tool use within a step
retains valid SDK message ordering. Email chronology is business input, not a
persistent AI transcript. Only explicitly selected context is forwarded; previous
rationales are claims, not verified facts. No extra screening model or automatic
history/summarization store is introduced.

Host validation enforces schemas, IDs, cardinality and permitted operations;
it cannot prove a schema-valid answer is correct. Inference cannot change tool
allowlists or workflow topology. High-impact host actions require their own
business checks rather than relying solely on valid JSON or evidence strength.

Cache optimization preserves stable instructions, catalogs and schemas before
changing data where evaluation supports that layout. Keep source chronology and
permissions unchanged. Provider cache controls belong in provider adapters only
when measured; no semantic answer cache, padded instructions or per-request
random delimiter scheme is introduced. Cache hits alone do not prove accuracy,
latency improvement or privacy isolation.

Compare candidate layouts on identical family-separated EN/DE cases, including
benign imperative text, direct source overrides, fake delimiters/roles, poisoned
derived results, conflicts and multiple intents. Measure false rejection and
business accuracy before token/cache/latency benefits. Preserve source results
and errors privately, serialize local inference, and stop after timeout until
backend state is verified. Baseline retention is required when evidence is
inconclusive; synthetic checks cannot establish enterprise reliability.
