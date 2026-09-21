# Host-owned MCP review

Scope: strict MCP profiles, declared catalogs, host-authorized direct/model tools,
identity-scoped HTTP/stdio sessions, SDK OAuth integration and a runnable stdio
example. Full production durability and telemetry remain separate work.

Independent runtime review found four issues and verified the repair approach:

1. SDK timeouts were lost in generic dependency failures. Both discovery and
   invocation now map the typed `REQUEST_TIMEOUT` code to a safe timeout.
2. Discovery omitted protected trace/identity metadata. It now uses the same
   fresh metadata builder as calls; unrelated metadata never enters MCP `_meta`.
3. SDK output validation raised ambiguous RuntimeError failures. Invocation now
   uses the SDK's public typed `send_request`, preserving protocol validation and
   stamping while host catalog validators own output semantics. Malformed list
   and call responses map to invalid output; lifecycle faults remain dependencies.
4. Direct steps restarted operation timeouts for each phase. One absolute direct
   step deadline now spans connect/discovery/call. Model-tool sessions separately
   bound setup and individual calls within the original run deadline.

Additional regressions cover mutable caller arguments being frozen before awaits,
SDK task groups wrapping review signals, same-task cleanup deadlines, and SDK
baggage injection. Body errors/cancellation survive cleanup; application control
signals are rethrown after SDK context exit. Baggage is removed task-locally,
while trace/span context and the caller's ambient context are preserved.

Evidence includes real in-process SDK tool execution, a subprocess stdio server,
Streamable HTTP over ASGI, and mock HTTP OAuth discovery/registration/PKCE/state/
resource/refresh traffic. A real stateful SDK session test stalls DELETE cleanup
and verifies bounded failure plus owned HTTP client closure. Separate caller
sessions and credential scopes are tested. Required/named tools need validated
success in model context, then permit a final response; no auto interaction or
hidden model/tool retry is used. Input-required returns needs_review.

An isolated offline `uv sync --locked --no-dev --extra mcp` installation contained
37 distributions and no pytest, mypy, Ruff, Torch or MLX. Its interpreter ran the
stdio example successfully with one tool call and zero model requests.

Coordinating checks: 452 service tests passed; strict typing passed for service
and all examples; Ruff/format, eight service schema snapshots, unchanged native
schemas, documentation, skill and staged-data audits passed. No real inference,
external MCP endpoint, token issuance, dataset change or push was performed.

Limitations: writes are rejected until durable operation identity/reconciliation
exists. The host must provide protected-at-rest, fully scoped OAuth storage and
actual business authorization. Interactive continuation is not implemented.
Complete service CLI/HTTP, privacy-filtered OTel exporters, durable SQL/Redis,
child dispatch/join and production failure/load acceptance remain unfinished.
