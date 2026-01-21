"""
OpenTelemetry configuration for the Chronicle Demo.

Provides tracing instrumentation for observability.
"""

from __future__ import annotations

import os
from typing import Optional

# Check if OpenTelemetry is available
OTEL_AVAILABLE = False
tracer = None
meter = None

try:
    from opentelemetry import trace, metrics
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    OTEL_AVAILABLE = True
except ImportError:
    pass


def setup_telemetry(
    service_name: str = "chronicle-demo",
    otlp_endpoint: Optional[str] = None,
    console_export: bool = True,
) -> bool:
    """
    Set up OpenTelemetry tracing and metrics.

    Args:
        service_name: Name of the service for telemetry
        otlp_endpoint: Optional OTLP endpoint (e.g., "localhost:4317")
        console_export: Whether to export to console (useful for demos)

    Returns:
        True if setup succeeded, False otherwise
    """
    global tracer, meter

    if not OTEL_AVAILABLE:
        print("OpenTelemetry not available. Install with: pip install opentelemetry-api opentelemetry-sdk")
        return False

    # Create resource
    resource = Resource.create({
        SERVICE_NAME: service_name,
        "service.version": "0.1.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })

    # Set up tracing
    trace_provider = TracerProvider(resource=resource)

    # Console exporter for visibility
    if console_export:
        console_processor = BatchSpanProcessor(ConsoleSpanExporter())
        trace_provider.add_span_processor(console_processor)

    # OTLP exporter for production use
    if otlp_endpoint:
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            otlp_processor = BatchSpanProcessor(otlp_exporter)
            trace_provider.add_span_processor(otlp_processor)
        except Exception as e:
            print(f"Failed to set up OTLP trace exporter: {e}")

    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(__name__)

    # Set up metrics
    metric_readers = []

    if console_export:
        console_reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(),
            export_interval_millis=60000,  # Every minute
        )
        metric_readers.append(console_reader)

    if otlp_endpoint:
        try:
            otlp_metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
            otlp_reader = PeriodicExportingMetricReader(otlp_metric_exporter)
            metric_readers.append(otlp_reader)
        except Exception as e:
            print(f"Failed to set up OTLP metric exporter: {e}")

    if metric_readers:
        meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        metrics.set_meter_provider(meter_provider)
        meter = metrics.get_meter(__name__)

    # Instrument libraries
    try:
        RequestsInstrumentor().instrument()
    except Exception:
        pass  # May already be instrumented

    try:
        SQLite3Instrumentor().instrument()
    except Exception:
        pass  # May already be instrumented

    return True


def get_tracer():
    """Get the configured tracer."""
    global tracer
    if tracer is None and OTEL_AVAILABLE:
        tracer = trace.get_tracer(__name__)
    return tracer


def get_meter():
    """Get the configured meter."""
    global meter
    if meter is None and OTEL_AVAILABLE:
        meter = metrics.get_meter(__name__)
    return meter


def create_span(name: str, attributes: Optional[dict] = None):
    """
    Context manager to create a span.

    Usage:
        with create_span("my_operation", {"key": "value"}):
            # ... do work ...
    """
    t = get_tracer()
    if t:
        return t.start_as_current_span(name, attributes=attributes)
    else:
        # Return a no-op context manager
        from contextlib import nullcontext
        return nullcontext()


# Demo-specific metrics
class DemoMetrics:
    """Metrics for the Chronicle demo."""

    def __init__(self):
        self._meter = get_meter()
        self._counters = {}
        self._histograms = {}

        if self._meter:
            self._counters["tasks_created"] = self._meter.create_counter(
                "chronicle_demo_tasks_created",
                description="Number of tasks created",
            )
            self._counters["tasks_completed"] = self._meter.create_counter(
                "chronicle_demo_tasks_completed",
                description="Number of tasks completed",
            )
            self._counters["tasks_failed"] = self._meter.create_counter(
                "chronicle_demo_tasks_failed",
                description="Number of tasks failed",
            )
            self._counters["captures"] = self._meter.create_counter(
                "chronicle_demo_captures",
                description="Number of function captures",
            )
            self._histograms["task_duration"] = self._meter.create_histogram(
                "chronicle_demo_task_duration_ms",
                description="Task processing duration in milliseconds",
            )

    def record_task_created(self, priority: str):
        """Record a task creation."""
        if "tasks_created" in self._counters:
            self._counters["tasks_created"].add(1, {"priority": priority})

    def record_task_completed(self, duration_ms: float):
        """Record a task completion."""
        if "tasks_completed" in self._counters:
            self._counters["tasks_completed"].add(1)
        if "task_duration" in self._histograms:
            self._histograms["task_duration"].record(duration_ms)

    def record_task_failed(self, error_type: str):
        """Record a task failure."""
        if "tasks_failed" in self._counters:
            self._counters["tasks_failed"].add(1, {"error_type": error_type})

    def record_capture(self, function_name: str, has_error: bool):
        """Record a function capture."""
        if "captures" in self._counters:
            self._counters["captures"].add(
                1,
                {"function": function_name, "has_error": str(has_error)},
            )


# Global metrics instance
_metrics: Optional[DemoMetrics] = None


def get_demo_metrics() -> DemoMetrics:
    """Get the demo metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = DemoMetrics()
    return _metrics
