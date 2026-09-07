# Cloud Telemetry Architecture & Topology

## Overview
The Cloud Telemetry platform is a distributed metrics and trace aggregation pipeline. It receives OpenTelemetry protocol (OTLP) payloads from edge agents, validates payloads against strict schemas, and flushes data to persistent storage.

## Core Architectural Decisions
- Microservice Framework: [[spaces/cloud-telemetry/decisions/adr_001_fastapi_framework|ADR-001: FastAPI and Pydantic v2]]
- Observability Standard: [[spaces/cloud-telemetry/decisions/adr_002_structured_json_logging|ADR-002: Structured JSON Logging]]

## Data Pipeline
1. **Edge Collectors**: Forward telemetry batches over HTTP/2.
2. **FastAPI Ingest Router**: Validates payloads asynchronously using Pydantic models.
3. **Structured Logger**: Emits JSON log events via `structlog` for Loki ingestion.
4. **Timescale / PostgreSQL**: Long-term metric storage.

## Tags
#architecture #topology #telemetry #opentelemetry #postgres
