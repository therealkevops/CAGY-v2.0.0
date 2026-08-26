"""Antigravity Web UI (AGY Bridge) - Update & Version management."""
import logging

logger = logging.getLogger(__name__)

WEBUI_VERSION = "2.0-agy"
AGENT_VERSION = "2.0"


def _read_update_channel() -> str:
    return "stable"


def channel_version_badge() -> str:
    return "AGY Bridge"


def cached_update_status() -> dict:
    return {
        "webui": {"update_available": False, "version": WEBUI_VERSION},
        "agent": {"update_available": False, "version": AGENT_VERSION},
        "checked_at": 0,
        "up_to_date": True,
    }


def check_for_updates(
    force: bool = False,
    include_agent: bool = True,
    channel: str | None = None,
) -> dict:
    return cached_update_status()


def apply_update(
    component: str,
    force: bool = False,
    channel: str | None = None,
) -> dict:
    return {"status": "ok", "message": "Antigravity Bridge is managed locally."}


def apply_force_update(component: str, channel: str | None = None) -> dict:
    return {"status": "ok", "message": "Antigravity Bridge is managed locally."}


def apply_clear_lock(component: str) -> dict:
    return {"status": "ok"}


def summarize_update_payload(payload: dict) -> dict:
    return {"summary": ""}
