"""Antigravity Web UI -- Gateway chat bridge stub.

Antigravity operates directly via the agy CLI runner (run_agent.py).
The legacy Hermes gateway chat proxy is disabled.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GATEWAY_RUN_ID_WAIT_TIMEOUT = 0.1
_STREAM_RUN_IDS: dict[str, str] = {}


def webui_gateway_chat_enabled(config_data: Any = None, environ: Optional[dict[str, str]] = None) -> bool:
    """Always False: Antigravity uses native CLI runner."""
    return False


def gateway_chat_config_status(config_data: Any = None, environ: Optional[dict[str, str]] = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "legacy",
        "base_url": "http://127.0.0.1:8642",
        "api_key_configured": False,
    }


def _run_gateway_chat_streaming(*args: Any, **kwargs: Any) -> None:
    raise NotImplementedError("Gateway chat is not enabled in Antigravity mode.")


def _mark_gateway_run_starting(stream_id: str) -> None:
    pass


def _finish_gateway_run_starting(stream_id: str) -> None:
    pass


def _clear_gateway_run_starting(stream_id: str) -> None:
    pass


def wait_for_gateway_run_id(stream_id: str, timeout: float = 0.1) -> Tuple[bool, Optional[str]]:
    return False, None


def stop_gateway_run(run_id: str) -> bool:
    return False


def _gateway_api_key(environ: Optional[dict[str, str]] = None) -> str:
    return ""


def _gateway_base_url(config_data: Any = None, environ: Optional[dict[str, str]] = None) -> str:
    return "http://127.0.0.1:8642"
