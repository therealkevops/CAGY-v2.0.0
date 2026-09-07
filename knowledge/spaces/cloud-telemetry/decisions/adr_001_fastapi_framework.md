# ADR-001: Adopt FastAPI and Pydantic v2 for Ingestion Microservice

## Status
Accepted

## Date
2026-09-07

## Context
The Cloud Telemetry microservice ingests high-throughput telemetry events and trace payloads from multiple edge collectors. We require an asynchronous runtime with rapid JSON deserialization, built-in validation schemas, and automated OpenAPI documentation generation.

## Decision
We adopt **FastAPI** with **Pydantic v2** as the core web framework.
- All HTTP endpoints must be async def handlers.
- Request and response payloads must be defined using Pydantic v2 `BaseModel` classes with explicit type annotations.
- Do **NOT** use Flask, Django, or raw `aiohttp.web`.

## Consequences
- High request throughput with native asynchronous I/O (`uvicorn`).
- Strict schema enforcement at the boundary prevents malformed payloads from polluting downstream storage.
- Auto-generated `/docs` Swagger UI for developer self-service.

## Tags
#decisions #architecture #fastapi #pydantic #python
