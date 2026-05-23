"""OpenTelemetry tracer wiring for the kiclaude agent service.

Mirrors the kiserver layout (one module-level
[`TracerProvider`][opentelemetry.sdk.trace.TracerProvider] + a
default in-memory exporter + a `reset_for_tests` seam). Every agent
lifecycle hook wraps its body in `tracer.start_as_current_span(...)`
so the M1-Q-04 span-coverage gate can assert that every hook produced
a span with the contract attributes:

- ``session_id``
- ``parent_session_id`` (set when a subagent invoked this hook)
- ``tool_name`` (PreToolUse / PostToolUse only)
- ``duration_ms`` (PostToolUse only — Pre/Start/End carry their own)

Tests get the latest exporter via `reset_for_tests()`; production
code reads `tracer` directly.
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

_RESOURCE = Resource.create({"service.name": "kiclaude-agent"})

# Prefer the existing global tracer provider if another module
# (kiserver, when both are imported in-process for tests) already set
# one up. Otherwise install a fresh one and register it as the
# global. OTel's `set_tracer_provider` is one-shot — re-calling it
# silently drops the new provider, so reusing the existing one is
# the only way both services can coexist in a single test process.
_existing = trace.get_tracer_provider()
if isinstance(_existing, TracerProvider):
    _PROVIDER = _existing
else:
    _PROVIDER = TracerProvider(resource=_RESOURCE)
    trace.set_tracer_provider(_PROVIDER)

_DEFAULT_EXPORTER = InMemorySpanExporter()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_DEFAULT_EXPORTER))

tracer = trace.get_tracer("kiclaude.agent")

_TEST_EXPORTER: InMemorySpanExporter | None = None
_TEST_PROCESSOR: SimpleSpanProcessor | None = None


def reset_for_tests() -> InMemorySpanExporter:
    """Attach a fresh `InMemorySpanExporter` to the global tracer
    provider for the duration of one test. Re-invoking detaches the
    previous test exporter so each test sees a clean span stream."""
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
    """The always-on in-memory exporter — useful for production code
    that wants recent spans without standing up a collector."""
    return _DEFAULT_EXPORTER


def iter_spans(exporter: InMemorySpanExporter) -> Iterator[ReadableSpan]:
    yield from exporter.get_finished_spans()


__all__ = [
    "installed_exporter",
    "iter_spans",
    "reset_for_tests",
    "tracer",
]
