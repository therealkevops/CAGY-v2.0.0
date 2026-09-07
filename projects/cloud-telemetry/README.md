# Cloud Telemetry Microservice

A distributed telemetry ingestion pipeline built with FastAPI, Pydantic v2, and Structlog.

## Architecture & Second Brain
- Active Knowledge Vault Space: `knowledge/spaces/cloud-telemetry/`
- Architectural Decision Records:
  - [[spaces/cloud-telemetry/decisions/adr_001_fastapi_framework|ADR-001: Adopt FastAPI and Pydantic v2]]
  - [[spaces/cloud-telemetry/decisions/adr_002_structured_json_logging|ADR-002: Structured JSON Logging with Structlog]]
  - [[spaces/cloud-telemetry/decisions/adr_003_we_decided_to_adopt_redis_7_as_our_distributed_caching|ADR-003: Redis 7 Caching Layer]]
- System Topology: [[spaces/cloud-telemetry/architecture/topology|Architecture & Topology]]
