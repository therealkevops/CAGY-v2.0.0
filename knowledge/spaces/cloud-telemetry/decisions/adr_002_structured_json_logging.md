# ADR-002: Structured JSON Logging with Structlog

## Status
Accepted

## Date
2026-09-07

## Context
Cloud-native telemetry platforms require centralized log aggregation (e.g. Grafana Loki, Datadog). Plain text or arbitrary `print()` statements cannot be queried or filtered by distributed tracing IDs or service labels.

## Decision
All logging across the service must utilize **`structlog`** configured to emit JSON lines to `sys.stdout`.
- Every log event must contain standard context: `timestamp`, `level`, `event`, and `service="cloud-telemetry"`.
- Requests must attach `trace_id` and `client_ip` to the logging context.
- Never use `print()` or Python default `logging.basicConfig()` string formatting.

## Consequences
- Machine-readable logs ingest without complex parsing regexes.
- Correlation across microservices via propagated `trace_id`.

## Tags
#decisions #logging #observability #json #structlog
