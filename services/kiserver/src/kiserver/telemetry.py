"""OpenTelemetry tracer wiring for kiserver (M1-P-01).

A single module-level [`TracerProvider`][opentelemetry.sdk.trace.TracerProvider]
is configured at import time so every FastAPI handler can call
`tracer.start_as_current_span(...)` without re-doing the boilerplate.

OTel's global `set_tracer_provider` is one-shot: subsequent calls
log a warning and don't replace the provider. To let tests capture
spans without fighting that constraint we keep the *single* global
provider and let tests attach (and reset) their own
[`InMemorySpanExporter`][InMemorySpanExporter] via
[`reset_for_tests`][reset_for_tests]. The exporter is an additional
span processor on the same provider, so it sees every span the real
handlers emit.
"""

from __future__ import annotations

from collections.abc import Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

_RESOURCE = Resource.create({"service.name": "kiclaude-kiserver"})

# Reuse the existing global TracerProvider if another module
# (`agent.telemetry`, when both services are imported in the same
# test process) has already installed one. OTel's
# `set_tracer_provider` is one-shot — re-calling it silently drops
# the new provider, so we must collaborate rather than race. Either
# way, this module attaches its own span processors so kiserver
# spans land in `_DEFAULT_EXPORTER` (production) and the
# `reset_for_tests` exporter (tests).
_existing = trace.get_tracer_provider()
if isinstance(_existing, TracerProvider):
    _PROVIDER = _existing
else:
    _PROVIDER = TracerProvider(resource=_RESOURCE)
    trace.set_tracer_provider(_PROVIDER)

# Production exporter slot — keeps spans alive even when no test is
# attached so structured logs can correlate them later. Tests add a
# second processor; both fire on every span.
_DEFAULT_EXPORTER = InMemorySpanExporter()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_DEFAULT_EXPORTER))

tracer = trace.get_tracer("kiclaude.kiserver")

_TEST_EXPORTER: InMemorySpanExporter | None = None
_TEST_PROCESSOR: SimpleSpanProcessor | None = None


def reset_for_tests() -> InMemorySpanExporter:
    """Attach a fresh `InMemorySpanExporter` to the global tracer
    provider for the duration of one test. Re-invoking detaches the
    previous test exporter first so each test sees a clean stream.
    """
    global _TEST_EXPORTER, _TEST_PROCESSOR
    if _TEST_PROCESSOR is not None:
        _TEST_PROCESSOR.shutdown()
    new_exporter = InMemorySpanExporter()
    new_processor = SimpleSpanProcessor(new_exporter)
    _PROVIDER.add_span_processor(new_processor)
    _TEST_EXPORTER = new_exporter
    _TEST_PROCESSOR = new_processor
    return new_exporter


def installed_exporter() -> InMemorySpanExporter:
    """The always-on in-memory exporter — handy for non-test code
    that just wants the recent spans without standing up a collector."""
    return _DEFAULT_EXPORTER


def iter_spans(exporter: InMemorySpanExporter) -> Iterator[ReadableSpan]:
    """Iterate over the captured spans in declaration order."""
    yield from exporter.get_finished_spans()


__all__ = [
    "installed_exporter",
    "iter_spans",
    "reset_for_tests",
    "tracer",
]
