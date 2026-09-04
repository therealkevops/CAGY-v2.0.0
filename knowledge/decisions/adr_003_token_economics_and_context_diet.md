# ADR 003: Implement Token Economics Engine & 25k Context Debt Threshold

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #analytics #token-economics #context-diet

## Context & Problem Statement
Long-running conversational sessions inevitably accumulate massive prompt token contexts. In extended coding sessions, carrying 30k–80k tokens of conversational history introduces three critical liabilities:
1. **Context Bloat & Degradation**: LLM attention and instruction following degrade over long horizons ("needle in a haystack" problem).
2. **Exponential Token Inefficiency**: Re-sending 50k tokens on every turn wastes API quota and multiplies cost.
3. **Delayed Recovery**: When errors occur deep in a session, recovering is slower and more expensive than starting fresh.

## Decision Drivers
- Make prompt token consumption and generation throughput transparent in real time.
- Establish a clear threshold for when a session should be pruned or refreshed.
- Quantify the financial and token ROI delivered by the [[architecture/knowledge_vault_and_graph]].

## Considered Options
1. **Silent Sliding Window Truncation**: Silently drop older conversation messages when reaching a token limit. (Rejected: Causes abrupt context loss and hallucination).
2. **Context Diet Strategy with Explicit 25k Threshold & Vault Handoff**: Monitor prompt token accumulation, flag the 25k token boundary as "Context Debt", and provide 1-click tools to `/memorize` critical takeaways into the vault and start a clean `/new` session.

## Decision Outcome
**Option 2 was chosen**:
- Built the Token Economics and Memory ROI analytics engine (`/analytics`).
- Established the **25,000-token Context Debt Threshold**.
- Introduced the **Memory Leverage Ratio (MLR)** to track memory compression density.
- Integrated the **Context Diet Advisor** to guide human operators when to snapshot decisions and restart with zero debt.

## Consequences & Linked Systems
- Architecture detailed in [[architecture/token_economics_and_analytics]].
- Memory handoff backed by [[architecture/knowledge_vault_and_graph]].
- Aligned with developer workflow preferences in [[user/profile]].
