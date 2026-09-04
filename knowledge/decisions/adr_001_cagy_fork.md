# ADR 001: Fork and Decouple Hermes into Clean Antigravity (CAGY)

- **Date**: 2026-09-04
- **Status**: Accepted & Released (`v2.0-cagy`)

## Context & Problem Statement
The original codebase contained ~48,000 lines of legacy Hermes UI, multi-provider logic, dead chat commands, and unneeded assets, obscuring the primary mission: a high-performance, containerized environment for Google Antigravity.

## Decision Drivers
- Need for deterministic `stream-json` bridge to Google `agy` CLI.
- Footprint reduction and automated CI pipeline.
- Alignment with [[user/profile]] and [[user/conventions]].

## Considered Options
1. Retain legacy Hermes code alongside AGY bridge.
2. Complete purge of dead Hermes code and rebranding to CAGY.

## Decision Outcome
Option 2: Completely pruned 48,000+ lines of dead Hermes code, tagged official `v2.0-cagy` release, and architected the unified system detailed in [[architecture/cagy_unified]].
