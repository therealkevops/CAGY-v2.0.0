"""Antigravity Web UI -- Passkeys stub.

Antigravity uses standard token/password authentication (AGY_WEBUI_PASSWORD).
Hardware passkey authentication is disabled.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


class PasskeyError(ValueError):
    pass


class PasskeyRateLimitError(PasskeyError):
    pass


def passkeys_available() -> bool:
    return False


def registered_credentials() -> List[Dict[str, Any]]:
    return []


def clear_credentials() -> None:
    pass


def delete_credential(cred_id: str) -> None:
    pass


def registration_options(handler: Any) -> Dict[str, Any]:
    raise PasskeyError("Passkeys are disabled in Antigravity mode.")


def finish_registration(handler: Any, payload: Any) -> Dict[str, Any]:
    raise PasskeyError("Passkeys are disabled in Antigravity mode.")


def authentication_options(handler: Any) -> Dict[str, Any]:
    raise PasskeyError("Passkeys are disabled in Antigravity mode.")


def finish_login(handler: Any, payload: Any) -> Tuple[str, str]:
    raise PasskeyError("Passkeys are disabled in Antigravity mode.")
