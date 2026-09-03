"""Antigravity Web UI -- OIDC auth stub.

Antigravity uses standard token/password authentication (AGY_WEBUI_PASSWORD).
Enterprise OpenID Connect (OIDC) SSO is disabled.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class OIDCError(Exception):
    pass


class OIDCConfigError(OIDCError):
    pass


class OIDCAuthError(OIDCError):
    pass


def is_oidc_enabled() -> bool:
    return False


def build_authorization_redirect(handler: Any) -> str:
    raise OIDCConfigError("OIDC is disabled in Antigravity mode.")


def complete_authorization_code_flow(handler: Any) -> str:
    raise OIDCAuthError("OIDC is disabled in Antigravity mode.")


def _normalize_allow_values(val: Any) -> List[str]:
    return []


_ALLOW_VALUES_WHITESPACE_WARNING: Optional[str] = None
