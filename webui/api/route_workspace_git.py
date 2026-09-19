"""Workspace projects, prompts, files, git, and rollback routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import json
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import bad, j, require
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


def _handle_post_workspace_and_git(handler, parsed, body):
    """Handle POST workspace, git, file, and project operations.
    Returns True/response if handled, None if unhandled.
    """
    from api import routes as _routes

    _handle_git_stage = _routes._handle_git_stage
    _handle_git_unstage = _routes._handle_git_unstage
    _handle_git_discard = _routes._handle_git_discard
    _handle_git_commit_message = _routes._handle_git_commit_message
    _handle_git_commit_message_selected = _routes._handle_git_commit_message_selected
    _handle_git_commit = _routes._handle_git_commit
    _handle_git_commit_selected = _routes._handle_git_commit_selected
    _handle_git_remote_action = _routes._handle_git_remote_action
    _handle_git_checkout = _routes._handle_git_checkout
    _handle_git_stash_checkout = _routes._handle_git_stash_checkout
    _handle_file_delete = _routes._handle_file_delete
    _handle_file_save = _routes._handle_file_save
    _handle_office_file_save = _routes._handle_office_file_save
    _handle_file_create = _routes._handle_file_create
    _handle_file_rename = _routes._handle_file_rename
    _handle_file_move = _routes._handle_file_move
    _handle_create_dir = _routes._handle_create_dir
    _handle_file_reveal = _routes._handle_file_reveal
    _handle_file_path = _routes._handle_file_path
    _handle_file_open_vscode = _routes._handle_file_open_vscode
    _handle_workspace_add = _routes._handle_workspace_add
    _handle_workspace_remove = _routes._handle_workspace_remove
    _handle_workspace_rename = _routes._handle_workspace_rename
    _handle_workspace_reorder = _routes._handle_workspace_reorder
    load_projects = _routes.load_projects
    save_projects = _routes.save_projects
    resolve_trusted_workspace = _routes.resolve_trusted_workspace
    get_active_profile_name = _routes.get_active_profile_name
    _profiles_match = _routes._profiles_match
    SESSION_INDEX_FILE = _routes.SESSION_INDEX_FILE
    _active_stream_ids = _routes._active_stream_ids
    LOCK = _routes.LOCK
    SESSIONS = _routes.SESSIONS
    get_session = _routes.get_session
    # ── Git workspace ops (POST) ──
    if parsed.path == "/api/git/stage":
        return _handle_git_stage(handler, body)

    if parsed.path == "/api/git/unstage":
        return _handle_git_unstage(handler, body)

    if parsed.path == "/api/git/discard":
        return _handle_git_discard(handler, body)

    if parsed.path == "/api/git/commit-message":
        return _handle_git_commit_message(handler, body)

    if parsed.path == "/api/git/commit-message-selected":
        return _handle_git_commit_message_selected(handler, body)

    if parsed.path == "/api/git/commit":
        return _handle_git_commit(handler, body)

    if parsed.path == "/api/git/commit-selected":
        return _handle_git_commit_selected(handler, body)

    if parsed.path == "/api/git/fetch":
        return _handle_git_remote_action(handler, body, "fetch")

    if parsed.path == "/api/git/pull":
        return _handle_git_remote_action(handler, body, "pull")

    if parsed.path == "/api/git/push":
        return _handle_git_remote_action(handler, body, "push")

    if parsed.path == "/api/git/checkout":
        return _handle_git_checkout(handler, body)

    if parsed.path == "/api/git/stash-checkout":
        return _handle_git_stash_checkout(handler, body)

    # ── File ops (POST) ──
    if parsed.path == "/api/file/delete":
        return _handle_file_delete(handler, body)

    if parsed.path == "/api/file/save":
        return _handle_file_save(handler, body)

    if parsed.path == "/api/file/office-save":
        return _handle_office_file_save(handler, body)

    if parsed.path == "/api/file/create":
        return _handle_file_create(handler, body)

    if parsed.path == "/api/file/rename":
        return _handle_file_rename(handler, body)

    if parsed.path == "/api/file/move":
        return _handle_file_move(handler, body)

    if parsed.path == "/api/file/create-dir":
        return _handle_create_dir(handler, body)

    if parsed.path == "/api/file/reveal":
        return _handle_file_reveal(handler, body)

    if parsed.path == "/api/file/path":
        return _handle_file_path(handler, body)

    if parsed.path == "/api/file/open-vscode":
        return _handle_file_open_vscode(handler, body)

    # ── Workspace management (POST) ──
    if parsed.path == "/api/workspaces/add":
        return _handle_workspace_add(handler, body)

    if parsed.path == "/api/workspaces/remove":
        return _handle_workspace_remove(handler, body)

    if parsed.path == "/api/workspaces/rename":
        return _handle_workspace_rename(handler, body)

    if parsed.path == "/api/workspaces/reorder":
        return _handle_workspace_reorder(handler, body)

    # ── Project CRUD (POST) ──
    if parsed.path == "/api/projects/create":
        try:
            require(body, "name")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re

        name = body["name"].strip()[:128]
        if not name:
            return bad(handler, "name required")
        color = body.get("color")
        if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
            return bad(handler, "Invalid color format")
        projects = load_projects()
        _requested_profile = str(body.get('profile') or "").strip()
        if _requested_profile and _requested_profile != "default":
            from api.profiles import _PROFILE_ID_RE
            if not _PROFILE_ID_RE.fullmatch(_requested_profile):
                return bad(handler, "invalid profile")
        raw_ws = body.get("default_workspace")
        if raw_ws and isinstance(raw_ws, str) and raw_ws.strip():
            try:
                default_ws = str(resolve_trusted_workspace(raw_ws.strip()))
            except Exception:
                logger.warning("Silent exception in handle_post", exc_info=True)
                default_ws = raw_ws.strip()
        else:
            default_ws = None
        proj = {
            "project_id": uuid.uuid4().hex[:12],
            "name": name,
            "color": color,
            "profile": _requested_profile or get_active_profile_name() or 'default',
            "default_workspace": default_ws,
            "created_at": time.time(),
        }
        projects.append(proj)
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path in ("/api/projects/rename", "/api/projects/update"):
        try:
            require(body, "project_id")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re

        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        active_profile = get_active_profile_name()
        if not _profiles_match(proj.get("profile"), active_profile):
            return bad(handler, "Project not found", 404)
        if "name" in body and body["name"]:
            proj["name"] = body["name"].strip()[:128]
        if "color" in body:
            color = body["color"]
            if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
                return bad(handler, "Invalid color format")
            proj["color"] = color
        if "default_workspace" in body:
            raw_ws = body.get("default_workspace")
            if raw_ws and isinstance(raw_ws, str) and raw_ws.strip():
                try:
                    proj["default_workspace"] = str(resolve_trusted_workspace(raw_ws.strip()))
                except Exception:
                    logger.warning("Silent exception in handle_post", exc_info=True)
                    proj["default_workspace"] = raw_ws.strip()
            else:
                proj["default_workspace"] = None
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path == "/api/projects/delete":
        try:
            require(body, "project_id")
        except ValueError as e:
            return bad(handler, str(e))
        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        active_profile = get_active_profile_name()
        if not _profiles_match(proj.get("profile"), active_profile):
            return bad(handler, "Project not found", 404)
        projects = [p for p in projects if p["project_id"] != body["project_id"]]
        save_projects(projects)
        if SESSION_INDEX_FILE.exists():
            try:
                index = json.loads(SESSION_INDEX_FILE.read_bytes())
                active_ids = _active_stream_ids()
                deferred_to_stream = []
                for entry in index:
                    if entry.get("project_id") != body["project_id"]:
                        continue
                    sid = entry.get("session_id")
                    try:
                        if entry.get("active_stream_id") in active_ids:
                            cleared_in_cache = False
                            with LOCK:
                                cached = SESSIONS.get(sid)
                                if cached is not None:
                                    cached.project_id = None
                                    cleared_in_cache = True
                            if cleared_in_cache:
                                deferred_to_stream.append(sid)
                                continue
                        s = get_session(sid)
                        s.project_id = None
                        s.save()
                    except Exception:
                        logger.debug("Failed to update session %s", sid)
                if deferred_to_stream:
                    logger.info(
                        "projects/delete: cleared project_id on %d streaming session(s) "
                        "in-cache; streaming thread will persist: %s",
                        len(deferred_to_stream), deferred_to_stream,
                    )
            except Exception:
                logger.debug("Failed to load session index for project unlink")
        return j(handler, {"ok": True})

    # ── Rollback checkpoint (POST) ──
    if parsed.path == "/api/rollback/restore":
        if not body:
            return bad(handler, "request body is required")
        workspace = body.get("workspace", "")
        checkpoint = body.get("checkpoint", "")
        if not workspace or not checkpoint:
            return bad(handler, "workspace and checkpoint are required")
        try:
            from api.rollback import restore_checkpoint
            return j(handler, restore_checkpoint(workspace, checkpoint))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/restore failed")
            return bad(handler, str(e), status=500)

    return None

