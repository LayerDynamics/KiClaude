# kiclaude observability

Optional OpenTelemetry preset for the agent's tool-call telemetry
(SPEC §8.8, §13 gate #9). Off by default — `services/agent` emits spans
to stdout JSONL out of the box; this wires an OTel Collector +
Prometheus + Grafana on top for dashboards.

## The span contract

`services/agent/src/agent/hooks/lifecycle.py` emits one span per
lifecycle hook:

| Span name | When | Key attributes |
|---|---|---|
| `agent.hook.pre_tool_use` | before each tool call | `tool_name`, `session_id`, `project_id`, `tool_use_id`, `hook_event` |
| `agent.hook.post_tool_use` | after each tool call | `+ duration_ms`, `ok` |
| `agent.hook.session_start` / `session_end` | session lifecycle | `session_id`, `parent_session_id` (subagents) |

These are the attributes the §13 gate #9 contract test asserts.

## Files

- [`otel-collector-config.yaml`](otel-collector-config.yaml) — OTLP
  receiver → `spanmetrics` connector (RED metrics) → Prometheus
  exporter (`:8889`) + stdout. Run with the **Collector Contrib**
  distribution (the `spanmetrics` connector isn't in the core distro):
  `otelcol-contrib --config docs/observability/otel-collector-config.yaml`
- [`grafana/kiclaude-agent-dashboard.json`](grafana/kiclaude-agent-dashboard.json) —
  importable Grafana dashboard: p95 tool latency (with the NFR-006
  ≤ 800 ms SLO line), per-tool call rate, tool-call distribution.

## Wiring it up

1. Run the collector (above).
2. Point the agent at it: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`
   (gRPC) when starting `services/agent`.
3. Add a Prometheus scrape job for `localhost:8889`.
4. Import the dashboard JSON into Grafana and pick that Prometheus as
   the `DS_PROMETHEUS` datasource.

Derived metrics (from `spanmetrics`): `calls_total` (counter) and
`duration_milliseconds_bucket` (histogram), labelled `span_name` +
`tool_name` / `project_id` / `hook_event`. `session_id` is intentionally
**not** a metric dimension (per-session cardinality belongs in traces,
not metrics); session-level analysis uses the raw spans.

## Privacy (SPEC §14)

Telemetry is opt-in and payload-free: spans carry tool *names* and
timings, never tool inputs/outputs. Keep it that way when extending the
dimensions.
