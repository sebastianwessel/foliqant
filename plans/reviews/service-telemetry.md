# Safe telemetry implementation review

Status: verified telemetry slice; full-service goal remains incomplete.
Date: 2026-09-21. Parent baseline: `1bf6a8d`.

## Scope and evidence

The review covers explicit OTLP/HTTP configuration, exporter/provider ownership,
prequeue span privacy, workflow/step observation, PydanticAI model instrumentation,
MCP SDK propagation and the optional embedded example. It does not qualify a live
collector/provider deployment or replace pending durability/transport acceptance.

- 505 service tests pass, including actual SDK spans/metrics, exporter doubles,
  concurrent workflow observers and a subprocess using real MCP ASGI transport.
  No additional model inference or external collector call was made.
- Spans are sanitized before the batch queue. Tests exercise raw content,
  identity, errors, tool descriptions, status text, scope/resource/link attributes
  and tracestate sentinels. The real SDK exception path also passes through safe
  JSON logging; captured stderr contains none of the synthetic secrets.
- W3C client/server spans retain correct parentage; each actual MCP request
  carries its SDK client-operation span rather than a stale workflow span.
  Caller identities/traces are isolated; baggage and arbitrary metadata stay out.
- Host model metrics retain unknown versus explicitly measured zero, cap bounded
  numeric values and omit duplicate subset counts. SDK model metrics are disabled.
  One client inference span is emitted for each actual request, without agent
  parent totals. Missing span usage must not be interpreted as measured zero.
- Disabled signals construct no exporters, readers or network clients; malicious
  ambient endpoint/header values cannot enable export. Unsupported ambient
  credential settings and global-provider autoload are rejected safely.
- Exporter/observer failures do not change business outcomes. Shutdown is off-loop,
  has a bounded caller wait and reuses one owned daemon worker after timeout.
- Strict mypy, Ruff lint/format, nine service schema snapshots, 27 unchanged native
  schema snapshots, documentation and skill validation pass. An isolated offline
  `uv sync --locked --no-dev --extra telemetry` installation contains 37 packages
  and no pytest/mypy/Ruff/Torch/MLX. The optional example completes with zero model
  requests and no collector endpoints.

## Findings and resolutions

Independent review by a separate implementation agent found that explicit
metadata could override a transport-extracted parent. The runner and observation
port now accept a separate `transport_trace`. The W3C adapter chooses valid
transport, then valid metadata, without combining fields; invalid supplied
carriers start a fresh trace. Tests cover conflicting valid carriers, invalid
metadata, invalid transport fallback and unchanged original metadata.

The review also identified duplicate reads of mutable SDK token usage. Usage is
now validated and snapshotted once, then shared by metrics and budget accounting.

Two upstream limitations remain explicit, not hidden behind custom SDK patches:

1. OTel 1.44 public batch shutdown has no timeout argument. The caller gets false
   at its deadline while one owned daemon cleanup worker may continue the SDK's
   fixed batch wait of up to 30 seconds. A malicious custom exporter could outlive
   even that wait; Python cannot safely kill such a thread. Built-in HTTP export
   has an explicit request timeout. No private OTel attributes are modified.
2. PydanticAI 2.46 omits zero token attributes on spans. Foliqant metrics preserve
   measured zero versus unavailable, so use those metrics for presence-sensitive
   accounting. Provider-reported estimates are not a billing verification.

## Remaining acceptance

The goal still requires executable service bootstrap/CLI, authenticated HTTP,
durable PostgreSQL/Redis recovery and effect identity, child dispatch/join,
production operations, remaining provider conformance and complete end-to-end
review. Global tracing is process-owned: embedded hosts must explicitly arrange
safe MCP SDK tracing instead of replacing an existing provider. No deployment,
push, dataset mutation or model-generation interruption occurred.
