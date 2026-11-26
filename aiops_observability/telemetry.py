
import os

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)

_initialized = False


def setup_telemetry(service_name="aiops-platform"):
    global _initialized

    if _initialized:
        return

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://localhost:4317"
    )

    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv(
            "ENVIRONMENT", "development"
        ),
    })

    tracer_provider = TracerProvider(resource=resource)

    span_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=True,
    )

    tracer_provider.add_span_processor(
        BatchSpanProcessor(span_exporter)
    )

    trace.set_tracer_provider(tracer_provider)

    metric_exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=True,
    )

    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=5000,
    )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )

    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_exporter = OTLPLogExporter(
        endpoint=endpoint,
        insecure=True,
    )
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(logger_exporter)
    )
    _logs.set_logger_provider(logger_provider)

    _initialized = True


def get_tracer(name=__name__):
    return trace.get_tracer(name)


def get_meter(name=__name__):
    return metrics.get_meter(name)

from functools import wraps
import time
from opentelemetry.trace import Status, StatusCode


def observe(operation_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            setup_telemetry()

            tracer = get_tracer(func.__module__)
            meter = get_meter(func.__module__)

            calls = meter.create_counter(
                f"aiops_{operation_name}_calls_total"
            )
            errors = meter.create_counter(
                f"aiops_{operation_name}_errors_total"
            )
            duration = meter.create_histogram(
                f"aiops_{operation_name}_duration_seconds",
                unit="s",
            )

            start = time.perf_counter()
            calls.add(1)

            with tracer.start_as_current_span(operation_name) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("operation.success", True)
                    return result

                except Exception as exc:
                    errors.add(1)
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR))
                    raise

                finally:
                    duration.record(time.perf_counter() - start)

        return wrapper
    return decorator

# ---------------- OPENTELEMETRY LOGS ----------------

from opentelemetry import _logs
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter


def get_logger(name="aiops-platform"):
    setup_telemetry()
    return _logs.get_logger_provider().get_logger(name)



