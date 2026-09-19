"""Workspace projects, prompts, files, git, and rollback routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import logging
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import bad, j
from api.models import get_session, load_projects
from api.profiles import _is_isolated_profile_mode, _profiles_match
from api.workspace import get_last_workspace, list_workspace_suggestions, load_workspaces

logger = logging.getLogger(__name__)


def _handle_get_workspace_and_git(handler, parsed):
    """Handle workspace projects, prompts, files, git, and rollback routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/projects":
        from api import profiles as profiles_api

        active_profile = profiles_api.get_active_profile_name()
        all_projects = load_projects()
        isolated_profile_mode = _is_isolated_profile_mode()
        all_profiles = _routes._all_profiles_enabled(parsed)
        if all_profiles:
            scoped = all_projects
            other_profile_count = 0
        else:
            scoped = [p for p in all_projects if _profiles_match(p.get("profile"), active_profile)]
            other_profile_count = 0 if isolated_profile_mode else len(all_projects) - len(scoped)
        return j(
            handler,
            {
                "projects": scoped,
                "all_profiles": all_profiles,
                "active_profile": active_profile,
                "other_profile_count": other_profile_count,
            },
        )

    if parsed.path == "/api/prompts":
        return j(handler, {"prompts": _routes._load_saved_prompts()})

    if parsed.path == "/api/session/export":
        return _routes._handle_session_export(handler, parsed)

    if parsed.path == "/api/session/workspace_exports":
        qs = parse_qs(parsed.query)
        workspace = qs.get("workspace", [None])[0]
        subfolder = qs.get("subfolder", [None])[0]
        try:
            from api.session_workspace_export import list_workspace_session_exports

            res = list_workspace_session_exports(workspace_path=workspace, subfolder=subfolder)
            return j(handler, res)
        except ValueError as e:
            return bad(handler, str(e), 400)
        except Exception as e:
            logger.exception("Failed to list workspace session exports")
            return bad(handler, f"Internal error listing workspace exports: {e}", 500)

    if parsed.path == "/api/workspaces":
        return j(
            handler,
            {
                "workspaces": load_workspaces(),
                "last": get_last_workspace(),
                "terminal_remote_backend": _routes._terminal_remote_backend_enabled(),
            },
        )

    if parsed.path == "/api/workspaces/suggest":
        qs = parse_qs(parsed.query)
        prefix = qs.get("prefix", [""])[0]
        return j(
            handler,
            {
                "suggestions": list_workspace_suggestions(prefix),
                "prefix": prefix,
            },
        )

    if parsed.path == "/api/sessions/search":
        return _routes._handle_sessions_search(handler, parsed)

    if parsed.path == "/api/list":
        return _routes._handle_list_dir(handler, parsed)

    if parsed.path == "/api/escape/list":
        return _routes._handle_escape_list_dir(handler, parsed)

    if parsed.path == "/api/git/status":
        return _routes._handle_git_status(handler, parsed)

    if parsed.path == "/api/git/branches":
        return _routes._handle_git_branches(handler, parsed)

    if parsed.path == "/api/git/diff":
        return _routes._handle_git_diff(handler, parsed)

    if parsed.path == "/api/personalities":
        from api.config import reload_config as _reload_cfg

        _reload_cfg()
        from api.config import get_config as _get_cfg

        _cfg = _get_cfg()
        agent_cfg = _cfg.get("agent", {})
        raw_personalities = agent_cfg.get("personalities", {})
        personalities = []
        if isinstance(raw_personalities, dict):
            for name, value in raw_personalities.items():
                desc = ""
                if isinstance(value, dict):
                    desc = value.get("description", "")
                elif isinstance(value, str):
                    desc = value[:80] + ("..." if len(value) > 80 else "")
                personalities.append({"name": name, "description": desc})
        return j(handler, {"personalities": personalities})

    if parsed.path == "/api/git-info":
        qs = parse_qs(parsed.query)
        sid = qs.get("session_id", [""])[0]
        if not sid:
            return bad(handler, "session_id required")
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        from api.workspace_git import GitWorkspaceError, git_status

        try:
            status = git_status(Path(s.workspace))
        except GitWorkspaceError as e:
            return _routes._git_bad(handler, e)
        totals = status.get("totals") or {}
        info = (
            None
            if not status.get("is_git")
            else {
                "branch": status.get("branch"),
                "dirty": totals.get("changed", 0),
                "modified": (totals.get("staged", 0) or 0) + (totals.get("unstaged", 0) or 0),
                "untracked": totals.get("untracked", 0),
                "ahead": status.get("ahead", 0),
                "behind": status.get("behind", 0),
                "is_git": True,
            }
        )
        return j(handler, {"git": info})

    if parsed.path == "/api/media":
        return _routes._handle_media(handler, parsed)

    if parsed.path == "/api/file/raw":
        return _routes._handle_file_raw(handler, parsed)

    if parsed.path == "/api/escape/file/raw":
        return _routes._handle_escape_file_raw(handler, parsed)

    if parsed.path == "/api/folder/download":
        return _routes._handle_folder_download(handler, parsed)

    if parsed.path == "/api/file":
        return _routes._handle_file_read(handler, parsed)

    if parsed.path == "/api/escape/file/read":
        return _routes._handle_escape_file_read(handler, parsed)

    if parsed.path == "/api/diff/file":
        from api.diff_viewer import get_file_diff_against_head

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        path = qs.get("path", [""])[0]
        ws_path = Path("/workspace")
        if not ws_path.exists():
            ws_path = Path.cwd()
        return j(handler, get_file_diff_against_head(ws_path, path))

    if parsed.path == "/api/rollback/list":
        qs = parse_qs(parsed.query)
        workspace = qs.get("workspace", [""])[0]
        if not workspace:
            return bad(handler, "workspace query parameter is required")
        try:
            from api.rollback import list_checkpoints

            return j(handler, list_checkpoints(workspace))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/list failed")
            return bad(handler, str(e), status=500)

    if parsed.path == "/api/rollback/diff":
        qs = parse_qs(parsed.query)
        workspace = qs.get("workspace", [""])[0]
        checkpoint = qs.get("checkpoint", [""])[0]
        if not workspace or not checkpoint:
            return bad(handler, "workspace and checkpoint query parameters are required")
        try:
            from api.rollback import get_checkpoint_diff

            return j(handler, get_checkpoint_diff(workspace, checkpoint))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/diff failed")
            return bad(handler, str(e), status=500)

    return None
