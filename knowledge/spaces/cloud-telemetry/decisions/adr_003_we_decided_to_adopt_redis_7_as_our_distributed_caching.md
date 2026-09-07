# ADR 003: We decided to adopt Redis 7 as our distributed caching

- **Date**: 2026-09-07
- **Status**: Accepted

- **Space**: cloud-telemetry

## Context & Decision

We decided to adopt Redis 7 as our distributed caching layer for rate limiting with a 60-second TTL to protect downstream Postgres databases.
