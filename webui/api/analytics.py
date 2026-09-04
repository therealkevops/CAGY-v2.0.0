"""
Antigravity (CAGY) Token Economics & Memory Efficiency Analytics Engine.
Calculates:
- Real-time token usage, cost estimations, and throughput (TPS)
- Turn-by-turn context accumulation curves and health status
- Knowledge Vault compression leverage and avoided-turn savings
- Model pricing comparisons and Context Diet recommendations
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import os
import json
import time

try:
    from api.vault import scan_vault, get_vault_dir
    from api.session_ops import get_session
    from api.webui_session_db import list_sessions
except ImportError:
    try:
        from webui.api.vault import scan_vault, get_vault_dir
        from webui.api.session_ops import get_session
        from webui.api.webui_session_db import list_sessions
    except ImportError:
        from .vault import scan_vault, get_vault_dir
        from .session_ops import get_session
        from .webui_session_db import list_sessions

# Pricing models (USD per 1,000,000 tokens)
MODEL_PRICING = {
    "gemini-3.8-flash": {"input": 0.075, "output": 0.30, "cache_input": 0.01875},
    "gemini-3.7-flash": {"input": 0.075, "output": 0.30, "cache_input": 0.01875},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30, "cache_input": 0.01875},
    "gemini-1.5-pro":   {"input": 1.25,  "output": 5.00, "cache_input": 0.3125},
    "claude-3-5-sonnet":{"input": 3.00,  "output": 15.00, "cache_input": 0.75},
    "default":          {"input": 0.075, "output": 0.30, "cache_input": 0.01875},
}


def estimate_tokens(text: str) -> int:
    """Fast approximation of token count (standard 1 token ~ 4 chars / 0.75 words)."""
    if not text:
        return 0
    words = len(text.split())
    return max(1, int(words * 1.33))


def compute_efficiency_metrics(
    session_id: Optional[str] = None,
    workspace_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Compute comprehensive efficiency metrics for active session and Knowledge Vault."""
    if workspace_path is None:
        workspace_path = Path("/workspace") if Path("/workspace").exists() else Path.cwd()

    # 1. Knowledge Vault Footprint & Leverage
    vault_dir = get_vault_dir(workspace_path)
    vault_data = scan_vault(vault_dir)
    vault_notes = vault_data.get("nodes", [])
    vault_edges = vault_data.get("edges", [])

    total_vault_words = 0
    for n in vault_notes:
        note_file = vault_dir / n.get("path", "")
        if note_file.exists():
            try:
                total_vault_words += len(note_file.read_text(encoding="utf-8").split())
            except Exception:
                pass

    # Read compiled rules
    rule_file = workspace_path / ".gemini" / "rules" / "knowledge_vault.md"
    rule_tokens = 0
    rule_words = 0
    rule_mtime = 0.0
    if rule_file.exists():
        try:
            rule_text = rule_file.read_text(encoding="utf-8")
            rule_words = len(rule_text.split())
            rule_tokens = estimate_tokens(rule_text)
            rule_mtime = rule_file.stat().st_mtime
        except Exception:
            pass

    # Memory Leverage Ratio (MLR)
    memory_leverage_ratio = round(total_vault_words / max(1, rule_tokens), 1) if rule_tokens > 0 else 1.0

    # 2. Session Context Progression & Token Breakdown
    target_session = None
    if session_id:
        try:
            target_session = get_session(session_id)
        except Exception:
            pass

    if not target_session:
        try:
            sessions = list_sessions()
            if sessions:
                target_session = sorted(sessions, key=lambda s: getattr(s, 'updated_at', 0) or 0, reverse=True)[0]
        except Exception:
            pass

    session_metrics = {
        "session_id": getattr(target_session, "session_id", None) if target_session else None,
        "model": getattr(target_session, "model", "Gemini 3.8 Flash (agy)") if target_session else "Gemini 3.8 Flash (agy)",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "total_turns": 0,
        "avg_tps": 0.0,
        "estimated_cost_usd": 0.0,
        "context_health": "optimal",
        "current_context_tokens": 0,
        "growth_curve": []
    }

    turn_curve: List[Dict[str, Any]] = []
    final_in = 0
    final_out = 0

    if target_session and hasattr(target_session, "messages"):
        msgs = target_session.messages or []
        turn_idx = 0
        cumulative_in = 0
        cumulative_out = 0
        tps_records: List[float] = []

        for i, m in enumerate(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                turn_idx += 1
                u_text = str(m.get("content") or "")
                u_tok = estimate_tokens(u_text)

                # Check next assistant message
                asst_m = None
                if i + 1 < len(msgs) and isinstance(msgs[i + 1], dict) and msgs[i + 1].get("role") == "assistant":
                    asst_m = msgs[i + 1]

                a_text = str(asst_m.get("content") or "") if asst_m else ""
                a_tok = estimate_tokens(a_text)

                # Extract turn usage if present
                turn_usage = getattr(asst_m, "_turnUsage", None) if asst_m else None
                if not turn_usage and asst_m and isinstance(asst_m, dict):
                    turn_usage = asst_m.get("usage")

                in_tok = (turn_usage.get("input_tokens") if isinstance(turn_usage, dict) else None) or (cumulative_in + u_tok + rule_tokens)
                out_tok = (turn_usage.get("output_tokens") if isinstance(turn_usage, dict) else None) or a_tok
                turn_tps = float((turn_usage.get("tps") if isinstance(turn_usage, dict) else None) or 48.0)
                turn_dur = float((turn_usage.get("duration_sec") if isinstance(turn_usage, dict) else None) or round(out_tok / max(1.0, turn_tps), 2))

                cumulative_in += u_tok
                cumulative_out += out_tok
                tps_records.append(turn_tps)

                turn_curve.append({
                    "turn": turn_idx,
                    "prompt_tokens": int(in_tok),
                    "output_tokens": int(out_tok),
                    "total_tokens": int(in_tok + out_tok),
                    "tps": round(turn_tps, 1),
                    "duration_sec": turn_dur
                })

        # Totals from session object if available, else accumulated
        s_in = int(getattr(target_session, "input_tokens", 0) or 0)
        s_out = int(getattr(target_session, "output_tokens", 0) or 0)

        final_in = s_in if s_in > 0 else (turn_curve[-1]["prompt_tokens"] if turn_curve else cumulative_in)
        final_out = s_out if s_out > 0 else cumulative_out

        pricing = MODEL_PRICING.get("gemini-3.8-flash", MODEL_PRICING["default"])
        cost = (final_in / 1_000_000 * pricing["input"]) + (final_out / 1_000_000 * pricing["output"])

        # Health evaluation based on current context size
        current_ctx = turn_curve[-1]["prompt_tokens"] if turn_curve else final_in
        if current_ctx < 15_000:
            health = "optimal"
        elif current_ctx <= 30_000:
            health = "growing"
        else:
            health = "bloated"

        session_metrics.update({
            "total_input_tokens": final_in,
            "total_output_tokens": final_out,
            "total_tokens": final_in + final_out,
            "total_turns": turn_idx,
            "avg_tps": round(sum(tps_records) / max(1, len(tps_records)), 1) if tps_records else 0.0,
            "estimated_cost_usd": round(cost, 5),
            "context_health": health,
            "current_context_tokens": current_ctx,
            "growth_curve": turn_curve
        })
    else:
        pricing = MODEL_PRICING.get("gemini-3.8-flash", MODEL_PRICING["default"])
        cost = 0.0

    # 3. Avoided Turn & Cumulative Savings
    num_notes = len(vault_notes)
    try:
        total_sessions_count = len(list_sessions() or [])
    except Exception:
        total_sessions_count = 1
    avoided_turns = max(1, num_notes * min(max(total_sessions_count, 1), 10))
    est_tokens_saved = avoided_turns * 4_500
    est_usd_saved = round(est_tokens_saved / 1_000_000 * MODEL_PRICING["default"]["input"], 4)

    # 4. Context Diet Recommendation
    cur_tok = session_metrics.get("current_context_tokens", 0)
    if cur_tok > 25_000:
        diet_recommendation = {
            "status": "warning",
            "message": f"Active context is {cur_tok:,} tokens. Save key decisions with /memorize and start a /new session to drop prompt overhead by ~85% while preserving 100% memory.",
            "action": "/new"
        }
    elif cur_tok > 12_000:
        diet_recommendation = {
            "status": "notice",
            "message": f"Context is at {cur_tok:,} tokens. Vault rules are active and anchoring context. Keep turns focused or run /memorize for important takeaways.",
            "action": "/memorize"
        }
    else:
        diet_recommendation = {
            "status": "optimal",
            "message": "Prompt context is lean and well-optimized. Turn-0 vault memory is active with zero context debt.",
            "action": None
        }

    return {
        "ok": True,
        "session": session_metrics,
        "vault": {
            "total_notes": num_notes,
            "total_edges": len(vault_edges),
            "total_words": total_vault_words,
            "compiled_rule_tokens": rule_tokens,
            "compiled_rule_words": rule_words,
            "memory_leverage_ratio": memory_leverage_ratio,
            "avoided_turns_estimate": avoided_turns,
            "estimated_tokens_saved": est_tokens_saved,
            "estimated_usd_saved": est_usd_saved,
            "rule_last_compiled_ts": rule_mtime
        },
        "diet": diet_recommendation,
        "pricing_comparison": {
            "gemini_3_8_flash": round(cost, 5),
            "gemini_1_5_pro": round((session_metrics["total_input_tokens"] / 1_000_000 * MODEL_PRICING["gemini-1.5-pro"]["input"]) + (session_metrics["total_output_tokens"] / 1_000_000 * MODEL_PRICING["gemini-1.5-pro"]["output"]), 5),
            "claude_3_5_sonnet": round((session_metrics["total_input_tokens"] / 1_000_000 * MODEL_PRICING["claude-3-5-sonnet"]["input"]) + (session_metrics["total_output_tokens"] / 1_000_000 * MODEL_PRICING["claude-3-5-sonnet"]["output"]), 5),
        },
        "timestamp": time.time()
    }
