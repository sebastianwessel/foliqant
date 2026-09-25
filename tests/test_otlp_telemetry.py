"""Real local OTLP transport, credential boundaries, and protobuf privacy checks."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from foliqant.adapters.telemetry.observation import WorkflowTelemetry
from foliqant.adapters.telemetry.privacy import TelemetryLabels
from foliqant.adapters.telemetry.runtime import TelemetryRuntime, _span_exporter
from foliqant.contracts.telemetry import TelemetryConfig
from foliqant.core.observation import observe
from foliqant.ports.observation import TraceContext


@pytest.mark.integration
@pytest.mark.parametrize("redirect", [False, True])
async def test_local_otlp_filters_content_and_does_not_follow_redirects(
    redirect: bool, caplog
) -> None:
    received: list[tuple[str, str | None, bytes]] = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, self.headers.get("x-api-key"), body))
            self.send_response(307 if self.path == "/redirect" else 200, "PRIVATE_SERVER_REASON")
            if self.path == "/redirect":
                self.send_header(
                    "Location", f"http://localhost:{self.server.server_port}/credential-leak"
                )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    labels = TelemetryLabels(
        workflows=frozenset({"inbox"}), flows=frozenset({"triage"}), steps=frozenset({"classify"})
    )
    runtime = TelemetryRuntime.build(
        TelemetryConfig(
            service_name="test",
            allow_insecure_http=True,
            traces_endpoint=f"http://127.0.0.1:{server.server_port}/"
            + ("redirect" if redirect else "v1/traces"),
            traces_headers={"x-api-key": "$OTLP_TEST_KEY"},
            span_schedule_delay=3600.0,
            export_timeout=0.5,
        ),
        labels=labels,
        environment={"OTLP_TEST_KEY": "PRIVATE_CREDENTIAL"},
    )
    observer = WorkflowTelemetry(runtime.tracer_provider, labels=labels)
    try:
        with observe(
            observer, "inbox", trace=TraceContext("00-" + "a" * 32 + "-" + "b" * 16 + "-01")
        ):
            with observe(observer, "inbox", flow="triage"):
                with observe(observer, "inbox", flow="triage", step="classify"):
                    with runtime.tracer_provider.get_tracer("private").start_as_current_span(
                        "PRIVATE_PROMPT", attributes={"prompt": "PRIVATE_PAYLOAD"}
                    ):
                        pass
        assert await asyncio.to_thread(runtime.tracer_provider.force_flush, 1000)
        assert len(received) == 1
        assert received[0][0] == ("/redirect" if redirect else "/v1/traces")
        assert received[0][1] == "PRIVATE_CREDENTIAL"
        assert "PRIVATE_SERVER_REASON" not in caplog.text
        assert b"PRIVATE" not in received[0][2]
        data = ExportTraceServiceRequest.FromString(received[0][2])
        spans = [
            span
            for resource in data.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        assert len(spans) == 4
        by_name = {span.name: span for span in spans}
        assert all(span.trace_id.hex() == "a" * 32 for span in spans)
        assert set(by_name) == {
            "workflow inbox",
            "flow triage",
            "step classify",
            "external.operation",
        }
        assert by_name["workflow inbox"].parent_span_id.hex() == "b" * 16
        assert by_name["flow triage"].parent_span_id == by_name["workflow inbox"].span_id
        assert by_name["step classify"].parent_span_id == by_name["flow triage"].span_id
        assert by_name["external.operation"].parent_span_id == by_name["step classify"].span_id
    finally:
        assert await runtime.aclose(timeout=1)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.integration
async def test_collector_header_stall_is_bounded_and_transport_error_is_safe(caplog) -> None:
    from time import monotonic

    from opentelemetry.sdk.trace.export import SpanExportResult

    release = threading.Event()
    calls = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            calls.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            release.wait(timeout=2)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = _span_exporter(
        endpoint=f"http://127.0.0.1:{server.server_port}/PRIVATE_ENDPOINT",
        headers={"x-api-key": "PRIVATE_SECRET"},
        timeout=0.1,
    )
    try:
        started = monotonic()
        assert await asyncio.to_thread(exporter.export, ()) is SpanExportResult.FAILURE
        assert monotonic() - started < 1
        assert calls == ["/PRIVATE_ENDPOINT"]
        assert "PRIVATE" not in caplog.text
    finally:
        release.set()
        exporter.shutdown()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.integration
async def test_collector_body_is_not_read_and_reason_is_not_logged(caplog) -> None:
    from opentelemetry.sdk.trace.export import SpanExportResult

    release = threading.Event()

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200, "PRIVATE_RESPONSE_REASON")
            self.send_header("Content-Length", "100000000")
            self.end_headers()
            self.wfile.flush()
            release.wait(timeout=2)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = _span_exporter(
        endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces",
        headers={},
        timeout=0.1,
    )
    try:
        # The collector advertises a huge body and sends none. Export can succeed
        # only by ignoring the body; eager reads time out and fail this assertion.
        assert await asyncio.to_thread(exporter.export, ()) is SpanExportResult.SUCCESS
        assert not release.is_set()
        assert "PRIVATE_RESPONSE_REASON" not in caplog.text
    finally:
        release.set()
        exporter.shutdown()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)
