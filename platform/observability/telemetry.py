
import os
import time
from functools import wraps

from opentelemetry import trace, metrics, _logs
from opentelemetry.sdk.resources import Resource

# Traces
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.trace import Status, StatusCode

# Metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)

# Logs
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
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

    # TRACES
    tracer_provider = TracerProvider(resource=resource)

    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                insecure=True,
            )
        )
    )

    trace.set_tracer_provider(tracer_provider)

    # METRICS
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=endpoint,
            insecure=True,
        ),
        export_interval_millis=5000,
    )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )

    metrics.set_meter_provider(meter_provider)
    # Configuration OpenTelemetry Logs
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


def get_logger(name=__name__):
    return _logs.get_logger(name)


def observe(operation_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            setup_telemetry()

            tracer = get_tracer(func.__module__)
            meter = get_meter(func.__module__)
            logger = get_logger(func.__module__)

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

            logger.emit(
                severity_text="INFO",
                body=f"{operation_name} started",
                attributes={"operation.name": operation_name},
            )

            with tracer.start_as_current_span(operation_name) as span:
                try:
                    result = func(*args, **kwargs)

                    elapsed = time.perf_counter() - start
                    span.set_attribute("operation.success", True)

                    logger.emit(
                        severity_text="INFO",
                        body=f"{operation_name} completed",
                        attributes={
                            "operation.name": operation_name,
                            "operation.duration_seconds": elapsed,
                        },
                    )

                    return result

                except Exception as exc:
                    errors.add(1)
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR))

                    logger.emit(
                        severity_text="ERROR",
                        body=f"{operation_name} failed: {type(exc).__name__}",
                        attributes={
                            "operation.name": operation_name,
                            "error.type": type(exc).__name__,
                        },
                    )

                    raise

                finally:
                    duration.record(time.perf_counter() - start)

        return wrapper
    return decorator
