# ADR 006: Streaming Step-Decomposition & Phase Decoupling

- **Date**: 2026-09-18
- **Status**: Accepted
- **Tags**: #decisions #adr #streaming #refactoring #architecture #testing

## Context & Problem Statement
The central background streaming engine function `_run_agent_streaming` in `webui/api/streaming.py` grew over time into a 4,261-line mega-function containing over 65 closure-scoped variables and deep nested try/finally blocks.
This led to:
1. **Unbounded Variable Coupling**: State like `agent_lock`, `cancel_event`, `_metering_stop`, `s`, and `agent` was shared across disparate concerns (context prep, tool callbacks, agent execution, post-run writeback, error formatting, and process environment teardown).
2. **Untestable Sub-Steps**: Individual intermediate phases (token calculations, SSE callbacks, context preparation, credential retries, and persistence) could not be tested in isolation.
3. **Cognitive Overhead**: Tracing execution flow or diagnosing race conditions required navigating thousands of lines in a single giant function.

## Decision Drivers
- Deconstruct the monolithic function into cohesive, independently testable phases without altering external behavior, SSE framing, or thread safety invariants.
- Maintain 100% backward compatibility and keep the automated test suite green at every step.
- Ensure the top-level orchestrator is concise, readable, and easy to maintain.

## Considered Options
1. **Status Quo (Monolithic Function)**: Retain the single function to avoid touching the sensitive streaming pipeline. (Rejected: Impeded maintainability and violated codebase health standards).
2. **Ad-Hoc Multi-Parameter Helper Functions**: Extract chunks by passing 15–20 positional/keyword parameters to each helper. (Rejected: Fragile signatures, high parameter coupling, and high probability of subtle regression).
3. **Unified `StreamTurnContext` State Encapsulation**: Introduce a dataclass (`StreamTurnContext`) holding all per-turn state and IPC channels, then decompose execution into 6 sequential phases receiving `ctx`.

## Decision Outcome
**Option 3 was chosen** and implemented across 6 low-risk micro-sprints:
1. **Sprint D1 (`StreamTurnContext` & `StreamingUsageCollector`)**: Encapsulated state, default lock factories, live prompt estimation, and background metering thread ticker.
2. **Sprint D2 (`StreamingCallbacks`)**: Extracted streaming event emitter controller (`on_token`, `on_reasoning`, `on_tool`, `on_tool_start`, `on_tool_complete`, and reasoning chunk flushes).
3. **Sprint D3 (`_phase_prepare_context`)**: Extracted validation, session locking, MCP tool resolution, workspace prompt loading, and periodic checkpoint thread management.
4. **Sprint D4 (`_phase_execute_agent` & `_phase_execute_ephemeral`)**: Extracted core conversation execution, cancellation checks, credential self-heal retry hook, and ephemeral `/btw` queries.
5. **Sprint D5 (`_phase_finalize_writeback`)**: Extracted post-run DB writeback currency checks, assistant message settling, context compression snapshot migration, telemetry computation, and terminal SSE dispatch.
6. **Sprint D6 (`_phase_handle_stream_error`, `_phase_teardown_stream`, and Orchestrator Condensation)**: Extracted provider error classification/handling, symmetric metering and thread context cleanup, and condensed the top-level `_run_agent_streaming` orchestrator to ~150 lines.

## Consequences & Linked Systems
- The test suite expanded from 127 to 150 automated tests (including 35 comprehensive tests in `tests/test_streaming.py`).
- Pre-turn rule synchronization compiles this ADR into `.gemini/rules/knowledge_vault.md` per [[architecture/cagy_unified]].
- Complements [[decisions/adr_004_git_checkpoints_and_zero_risk_rollback]] by ensuring all streaming operations have deterministic teardown and rollback semantics.
