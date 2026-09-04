# Token Economics & Memory ROI Analytics Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Analytics
- **Tags**: #analytics #token-economics #memory-roi #context-diet

## Overview
The Token Economics and Memory ROI analytics engine (`webui/api/analytics.py` and `webui/static/analytics.js`) quantifies prompt token consumption, calculates real-time API expenditure, and measures the concrete efficiency dividends delivered by the [[architecture/knowledge_vault_and_graph]].

It is accessible in the Web UI via `/analytics`, `/efficiency`, or the left navigation rail.

---

## Core Analytics Modules & Formulas

### 1. Memory Leverage Ratio (MLR)
Quantifies how effectively comprehensive architectural documentation is compressed into high-signal turn-0 rules:

$$\text{MLR} = \frac{\text{Total Words in Knowledge Vault}}{\text{Tokens in Compiled Knowledge Vault Rule}}$$

A high MLR indicates strong semantic density, ensuring the agent operates with vast domain awareness without consuming excessive prompt tokens.

### 2. Cumulative Avoided Context Debt
Measures the tokens and conversation turns saved across all sessions by having permanent turn-0 vault recall:
- Instead of repeatedly re-explaining conventions, system topologies, or ADRs in lengthy prompts, the agent loads compiled rules automatically.
- Tracks estimated cumulative prompt tokens avoided and dollars saved against commercial LLM pricing tiers.

### 3. Context Accumulation Curve & Debt Threshold
- An interactive SVG visualizer charting prompt token growth across conversation turns in the active session.
- Establishes the **Context Debt Threshold at 25,000 tokens**.
- When context accumulation crosses 25k tokens, the **Context Diet Advisor** triggers proactive guidance to:
  1. `/memorize` active takeaways into the [[architecture/knowledge_vault_and_graph]].
  2. Start a clean `/new` session with zero context baggage while retaining 100% memory continuity.

### 4. Real-Time Pricing & Multi-Model Comparative Matrix
Calculates session costs based on token telemetry ($0.075/1M input, $0.30/1M output on Gemini 3.8 Flash) and projects comparative costs on larger/frontier models:
- Gemini 1.5 Pro (~16.6x multiplier)
- Claude 3.5 Sonnet (~40x multiplier)

### 5. Inline Message Telemetry Chips
Displays per-message token counts, duration, and generation speed (tok/s) in assistant message footers, toggleable via `/usage`.

---

## Related Notes & References
- Quantifies memory efficiency for [[architecture/knowledge_vault_and_graph]].
- Decision rationale documented in [[decisions/adr_003_token_economics_and_context_diet]].
- Configured for the user profile in [[user/profile]].
