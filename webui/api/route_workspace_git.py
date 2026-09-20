"""Workspace projects, prompts, files, git, and rollback routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import base64
import json
import logging
import os
import platform
import shutil
import stat as _stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.agent_runtime import (
    AgentRuntimeChangedError,
    ensure_agent_runtime_current,
    require_ai_agent_class,
)
from api.helpers import _sanitize_error, bad, j, require, safe_resolve
from api.config import MAX_FILE_BYTES
from api.models import get_session, get_session_for_file_ops, load_projects
from api.profiles import _is_isolated_profile_mode, _profiles_match
from api.workspace import (
    _home_path,
    _is_blocked_system_path,
    _is_within,
    _strip_surrounding_quotes,
    get_last_workspace,
    list_workspace_suggestions,
    load_workspaces,
    make_anchored_dir,
    open_anchored_create_fd,
    open_anchored_fd,
    open_anchored_write_fd,
    rename_anchored,
    rmtree_anchored,
    save_workspaces,
    unlink_anchored,
    validate_workspace_to_add,
)

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
        return _handle_git_status(handler, parsed)

    if parsed.path == "/api/git/branches":
        return _handle_git_branches(handler, parsed)

    if parsed.path == "/api/git/diff":
        return _handle_git_diff(handler, parsed)

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
            return _git_bad(handler, e)
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



# ── Git Operations Handlers (Sprint M5.1) ────────────────────────────────────
def _git_session(handler, session_id: str):
    if not session_id:
        bad(handler, "session_id required")
        return None
    try:
        return get_session(session_id)
    except KeyError:
        bad(handler, "Session not found", 404)
        return None


def _git_session_workspace(handler, session_id: str):
    session = _git_session(handler, session_id)
    if session is None:
        return None
    return Path(session.workspace)


def _git_session_and_workspace(handler, session_id: str):
    session = _git_session(handler, session_id)
    if session is None:
        return None, None
    return session, Path(session.workspace)


def _git_locked_by_active_stream(session) -> bool:
    stream_id = getattr(session, "active_stream_id", None)
    if not stream_id:
        return False
    try:
        from api.config import STREAMS, STREAMS_LOCK

        with STREAMS_LOCK:
            return stream_id in STREAMS
    except Exception:
        logger.debug("Silent exception in _git_locked_by_active_stream", exc_info=True)
        return False


def _git_reject_destructive_if_unsafe(handler, session) -> bool:
    from api.workspace_git import (
        GitWorkspaceError,
        WORKSPACE_GIT_DESTRUCTIVE_ENV,
        workspace_git_destructive_enabled,
    )

    if not workspace_git_destructive_enabled():
        _git_bad(
            handler,
            GitWorkspaceError(
                f"Destructive workspace Git operations are disabled. Set {WORKSPACE_GIT_DESTRUCTIVE_ENV}=1 to enable them.",
                "destructive_git_disabled",
            ),
            status=403,
        )
        return True
    if _git_locked_by_active_stream(session):
        _git_bad(
            handler,
            GitWorkspaceError(
                "A session run is active. Wait for it to finish before running this Git operation.",
                "active_stream",
            ),
            status=409,
        )
        return True
    return False


def _handle_git_status(handler, parsed):
    qs = parse_qs(parsed.query)
    workspace = _git_session_workspace(handler, qs.get("session_id", [""])[0])
    if workspace is None:
        return True
    try:
        from api.workspace_git import GitWorkspaceError, git_status

        return j(handler, {"git": git_status(workspace)})
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_branches(handler, parsed):
    qs = parse_qs(parsed.query)
    workspace = _git_session_workspace(handler, qs.get("session_id", [""])[0])
    if workspace is None:
        return True
    try:
        from api.workspace_git import GitWorkspaceError, git_branches

        return j(handler, {"branches": git_branches(workspace)})
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_diff(handler, parsed):
    qs = parse_qs(parsed.query)
    workspace = _git_session_workspace(handler, qs.get("session_id", [""])[0])
    if workspace is None:
        return True
    path = qs.get("path", [""])[0]
    kind = qs.get("kind", ["unstaged"])[0]
    if not path:
        return bad(handler, "path required")
    try:
        from api.workspace_git import GitWorkspaceError, git_diff

        return j(handler, {"diff": git_diff(workspace, path, kind)})
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _git_bad(handler, err, status: int = 400):
    return j(
        handler,
        {
            "error": _sanitize_error(err),
            "code": getattr(err, "code", "git_failed") or "git_failed",
        },
        status=status,
    )


def _git_paths_from_body(body) -> list[str]:
    raw_paths = body.get("paths")
    if raw_paths is None and body.get("path"):
        raw_paths = [body.get("path")]
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, list):
        raise ValueError("paths must be a list")
    return [str(path) for path in raw_paths]


def _handle_git_stage(handler, body):
    try:
        require(body, "session_id")
        paths = _git_paths_from_body(body)
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_stage

        return j(handler, {"ok": True, "git": git_stage(workspace, paths)})
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_unstage(handler, body):
    try:
        require(body, "session_id")
        paths = _git_paths_from_body(body)
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_unstage

        return j(handler, {"ok": True, "git": git_unstage(workspace, paths)})
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_discard(handler, body):
    try:
        require(body, "session_id")
        paths = _git_paths_from_body(body)
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_discard

        return j(
            handler,
            {
                "ok": True,
                "git": git_discard(
                    workspace,
                    paths,
                    delete_untracked=bool(body.get("delete_untracked")),
                ),
            },
        )
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _llm_git_commit_message(system_prompt: str, user_prompt: str, session=None) -> str:
    from api import profiles as profiles_api

    active_profile = profiles_api.get_active_profile_name() or "default"
    with profiles_api.profile_env_for_background_worker(
        active_profile,
        "git commit message",
        logger_override=logger,
    ):
        from api.config import (
            get_effective_default_model,
            model_with_provider_context,
            resolve_custom_provider_connection,
            resolve_model_provider,
        )

        session_model = str(getattr(session, "model", "") or "").strip()
        session_provider = str(getattr(session, "model_provider", "") or "").strip() or None
        model_for_resolution = (
            model_with_provider_context(session_model, session_provider)
            if session_model
            else get_effective_default_model()
        )
        _main_model, _main_provider, _main_base_url = resolve_model_provider(model_for_resolution)
        _main_api_key = None
        try:
            from api.oauth import resolve_runtime_provider_with_anthropic_env_lock
            from hermes_cli.runtime_provider import resolve_runtime_provider

            _rt = resolve_runtime_provider_with_anthropic_env_lock(
                resolve_runtime_provider,
                requested=_main_provider,
            )
            _main_api_key = _rt.get("api_key")
            if not _main_provider:
                _main_provider = _rt.get("provider")
            if not _main_base_url:
                _main_base_url = _rt.get("base_url")
        except Exception as _e:
            logger.debug("git commit message runtime provider resolution failed: %s", _e)
        if isinstance(_main_provider, str) and _main_provider.startswith("custom:"):
            _cp_key, _cp_base = resolve_custom_provider_connection(_main_provider)
            if not _main_api_key and _cp_key:
                _main_api_key = _cp_key
            if not _main_base_url and _cp_base:
                _main_base_url = _cp_base

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        main_runtime = {
            "provider": _main_provider,
            "model": _main_model,
            "base_url": _main_base_url,
            "api_key": _main_api_key,
        }
        ensure_agent_runtime_current()
        try:
            from agent.auxiliary_client import get_text_auxiliary_client

            aux_client, aux_model = get_text_auxiliary_client(
                "compression",
                main_runtime=main_runtime,
            )
            if aux_client is not None and aux_model:
                response = aux_client.chat.completions.create(
                    model=aux_model,
                    messages=messages,
                )
                return str(response.choices[0].message.content or "").strip()
        except Exception as _e:
            logger.debug("git commit message auxiliary model failed; falling back to main model: %s", _e)

        AIAgent = require_ai_agent_class()

        agent = AIAgent(
            model=_main_model,
            provider=_main_provider,
            base_url=_main_base_url,
            api_key=_main_api_key,
            platform="webui",
            quiet_mode=True,
            enabled_toolsets=[],
            session_id=f"git-commit-message-{uuid.uuid4().hex[:8]}",
        )
        result = agent.run_conversation(
            user_message=user_prompt,
            system_message=system_prompt,
            conversation_history=[],
            task_id=f"git-commit-message-{uuid.uuid4().hex[:8]}",
        )
        return str(result.get("final_response") or "").strip()


def _handle_git_commit_message(handler, body):
    from api.workspace_git import (
        GitWorkspaceError,
        clean_generated_commit_message,
        staged_commit_message_prompt,
    )

    try:
        require(body, "session_id")
        session = get_session(body["session_id"])
        workspace = Path(session.workspace)

        prompt = staged_commit_message_prompt(workspace)
        message = clean_generated_commit_message(
            _llm_git_commit_message(prompt["system_prompt"], prompt["user_prompt"], session=session)
        )
        if not message:
            raise GitWorkspaceError("No commit message was generated")
        return j(handler, {"ok": True, "message": message, "truncated": bool(prompt.get("truncated"))})
    except KeyError:
        return bad(handler, "Session not found", 404)
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)
    except AgentRuntimeChangedError as e:
        return j(handler, {
            "error": str(e),
            "type": "agent_runtime_stale",
            "retryable": True,
        }, status=409)
    except Exception as e:
        logger.exception("git commit message generation failed")
        return bad(handler, _sanitize_error(e), 500)


def _handle_git_commit_message_selected(handler, body):
    from api.workspace_git import (
        GitWorkspaceError,
        clean_generated_commit_message,
        selected_commit_message_prompt,
    )

    try:
        require(body, "session_id")
        paths = _git_paths_from_body(body)
        session = get_session(body["session_id"])
        workspace = Path(session.workspace)

        prompt = selected_commit_message_prompt(workspace, paths)
        message = clean_generated_commit_message(
            _llm_git_commit_message(prompt["system_prompt"], prompt["user_prompt"], session=session)
        )
        if not message:
            raise GitWorkspaceError("No commit message was generated")
        return j(handler, {"ok": True, "message": message, "truncated": bool(prompt.get("truncated"))})
    except KeyError:
        return bad(handler, "Session not found", 404)
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)
    except AgentRuntimeChangedError as e:
        return j(handler, {
            "error": str(e),
            "type": "agent_runtime_stale",
            "retryable": True,
        }, status=409)
    except Exception as e:
        logger.exception("selected git commit message generation failed")
        return bad(handler, _sanitize_error(e), 500)


def _handle_git_commit(handler, body):
    try:
        require(body, "session_id", "message")
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_commit

        return j(handler, git_commit(workspace, body.get("message", "")))
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_commit_selected(handler, body):
    try:
        require(body, "session_id", "message")
        paths = _git_paths_from_body(body)
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_commit_selected

        return j(handler, git_commit_selected(workspace, body.get("message", ""), paths))
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_remote_action(handler, body, action: str):
    try:
        require(body, "session_id")
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if action in {"pull", "push"} and _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_fetch, git_pull, git_push

        actions = {
            "fetch": git_fetch,
            "pull": git_pull,
            "push": git_push,
        }
        return j(handler, actions[action](workspace))
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_checkout(handler, body):
    try:
        require(body, "session_id", "ref", "mode")
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_checkout

        result = git_checkout(
            workspace,
            str(body.get("ref", "")),
            str(body.get("mode", "local")),
            new_branch=body.get("new_branch"),
            track=bool(body.get("track")),
            dirty_mode=str(body.get("dirty_mode", "block")),
        )
        return j(
            handler,
            {
                "ok": True,
                "git": result.get("status"),
                "branches": result.get("branches"),
                "current_branch": result.get("current_branch"),
                "message": result.get("message", ""),
            },
        )
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


def _handle_git_stash_checkout(handler, body):
    try:
        require(body, "session_id", "ref", "mode")
        session, workspace = _git_session_and_workspace(handler, body["session_id"])
        if workspace is None:
            return True
        if _git_reject_destructive_if_unsafe(handler, session):
            return True
        from api.workspace_git import GitWorkspaceError, git_stash_and_checkout

        result = git_stash_and_checkout(
            workspace,
            str(body.get("ref", "")),
            str(body.get("mode", "local")),
            new_branch=body.get("new_branch"),
            track=bool(body.get("track")),
        )
        return j(
            handler,
            {
                "ok": True,
                "git": result.get("status"),
                "branches": result.get("branches"),
                "current_branch": result.get("current_branch"),
                "message": result.get("message", ""),
                "stash_name": result.get("stash_name", ""),
                "stashed": bool(result.get("stashed")),
                "restored_stash": result.get("restored_stash"),
                "restore_failed": bool(result.get("restore_failed")),
                "restore_error": result.get("restore_error", ""),
                "restore_stash": result.get("restore_stash"),
            },
        )
    except ValueError as e:
        return bad(handler, str(e))
    except GitWorkspaceError as e:
        return _git_bad(handler, e)


# ── File & Workspace POST Handlers (Sprint M5.2a) ───────────────────────────
def _read_anchored_file_bytes(ws_root: Path, target: Path) -> bytes:
    fd = open_anchored_fd(ws_root, target, want_dir=False)
    with os.fdopen(fd, "rb", closefd=True) as fh:
        st = os.fstat(fh.fileno())
        if not _stat.S_ISREG(st.st_mode):
            raise FileNotFoundError(f"Not a file: {target}")
        if st.st_size > MAX_FILE_BYTES:
            raise ValueError(f"File too large ({st.st_size} bytes, max {MAX_FILE_BYTES})")
        return fh.read(MAX_FILE_BYTES + 1)


def _handle_file_delete(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        target = safe_resolve(ws_root, body["path"])
        # Reject a symlinked entry BEFORE the follow-based exists() check: a
        # dangling symlink resolves to a missing target, so an exists()-first
        # order would misclassify it as 404 "File not found" and leave it
        # permanently undeletable. is_symlink() is a no-follow lstat on the
        # lexically-requested path, so it catches both live and dangling links.
        if (ws_root / body["path"]).is_symlink():
            return bad(handler, "Cannot delete a symlinked entry")
        if not target.exists():
            return bad(handler, "File not found", 404)
        if target.is_dir():
            if not body.get("recursive"):
                return bad(handler, "Set recursive=true to delete directories")
            rmtree_anchored(ws_root, target)
        else:
            unlink_anchored(ws_root, target)
        return j(handler, {"ok": True, "path": body["path"]})
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_save(handler, body):
    rel = body.get("path")
    if not rel:
        return bad(handler, "path is required")
    sid = body.get("session_id", "")
    ws_root = Path("/workspace")
    if sid:
        try:
            s = get_session_for_file_ops(sid)
            if s and hasattr(s, "workspace") and s.workspace:
                cand = Path(s.workspace)
                if cand.exists():
                    ws_root = cand
        except KeyError:
            return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(ws_root, body["path"])
        if (ws_root / body["path"]).is_symlink():
            return bad(handler, "Cannot save to a symlinked entry")
        if not target.exists():
            return bad(handler, "File not found", 404)
        if target.is_dir():
            return bad(handler, "Cannot save: path is a directory")
        if Path(str(body["path"])).suffix.lower() in {".docx", ".xlsx", ".pptx"}:
            return bad(handler, "Use /api/file/office-save for Office documents")
        data = str(body.get("content", "")).encode("utf-8")
        fd = open_anchored_write_fd(ws_root, target)
        with os.fdopen(fd, "wb", closefd=True) as fh:
            fh.write(data)

        # Auto-sync vault rules if saving a note in knowledge/
        clean_path = str(body["path"]).strip().lstrip("/")
        if clean_path.startswith("knowledge/"):
            try:
                from api.vault import sync_vault_to_rules, get_vault_dir
                sync_vault_to_rules(get_vault_dir(ws_root), ws_root)
            except Exception:
                logger.warning("Silent exception in _handle_file_save", exc_info=True)
                pass

        return j(
            handler, {"ok": True, "path": body["path"], "size": len(data)}
        )
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_office_file_save(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        target = safe_resolve(ws_root, body["path"])
        if (ws_root / body["path"]).is_symlink():
            return bad(handler, "Cannot save to a symlinked entry")
        if not target.exists():
            return bad(handler, "File not found", 404)
        if target.is_dir():
            return bad(handler, "Cannot save: path is a directory")
        if Path(str(body["path"])).suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
            return bad(handler, "Office save is only available for .docx, .xlsx, and .pptx files")
        from api.office_documents import save_office_document

        current_bytes = _read_anchored_file_bytes(ws_root, target)
        preview, updated_bytes = save_office_document(body["path"], current_bytes, body.get("content", ""))
        fd = open_anchored_write_fd(ws_root, target)
        with os.fdopen(fd, "wb", closefd=True) as fh:
            fh.write(updated_bytes)
        preview.update({"ok": True, "path": body["path"], "size": len(updated_bytes)})
        return j(handler, preview)
    except ImportError as e:
        return bad(handler, str(e), 503)
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_create(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        target = safe_resolve(ws_root, body["path"])
        if target.exists():
            return bad(handler, "File already exists")
        data = str(body.get("content", "")).encode("utf-8")
        fd = open_anchored_create_fd(ws_root, target)
        with os.fdopen(fd, "wb", closefd=True) as fh:
            fh.write(data)
        return j(
            handler, {"ok": True, "path": target.relative_to(ws_root.resolve()).as_posix()}
        )
    except FileExistsError:
        return bad(handler, "File already exists")
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_rename(handler, body):
    try:
        require(body, "session_id", "path", "new_name")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        ws_root_resolved = ws_root.resolve()
        source = safe_resolve(ws_root, body["path"])
        # Reject a symlinked entry BEFORE the follow-based exists() check (see
        # _handle_file_delete): a dangling symlink would otherwise 404 and stay
        # unrenameable. is_symlink() is a no-follow lstat on the requested path.
        if (ws_root / body["path"]).is_symlink():
            return bad(handler, "Cannot rename a symlinked entry")
        if not source.exists():
            return bad(handler, "File not found", 404)
        new_name = body["new_name"].strip()
        if not new_name or "/" in new_name or "\\" in new_name or ".." in new_name:
            return bad(handler, "Invalid file name")
        dest = source.parent / new_name
        if dest.exists():
            return bad(handler, f'A file named "{new_name}" already exists')
        rename_anchored(ws_root, source, dest)
        new_rel = dest.relative_to(ws_root_resolved).as_posix()
        return j(handler, {"ok": True, "old_path": body["path"], "new_path": new_rel})
    except FileExistsError:
        return bad(handler, f'A file named "{body.get("new_name", "")}" already exists')
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_move(handler, body):
    try:
        require(body, "session_id", "path", "dest_dir")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        # safe_resolve() returns paths under the RESOLVED root, so compute
        # returned relative paths against the resolved root too — otherwise a
        # symlinked workspace root (e.g. macOS /tmp -> /private/tmp) makes
        # dest.relative_to(ws_root) raise after a successful on-disk move,
        # returning a confusing 400 for a move that actually happened.
        ws_root_resolved = ws_root.resolve()
        source = safe_resolve(ws_root, body["path"])
        # Reject a symlinked SOURCE entry BEFORE the follow-based exists() check.
        # safe_resolve() follows the final symlink, so source.name/source.parent
        # would point at the link's TARGET, not the dragged entry — moving
        # link.txt would silently move dir/real.txt and leave link.txt dangling.
        # Detect the symlink on the lexically-requested final component (lstat,
        # no-follow) and refuse; running this before exists() also means a
        # dangling symlink is rejected (400) rather than misclassified as 404
        # (matches the delete/rename ordering).
        if (ws_root / body["path"]).is_symlink():
            return bad(handler, "Cannot move a symlinked entry")
        if not source.exists():
            return bad(handler, "File not found", 404)
        dest_dir_raw = (body.get("dest_dir") or ".").strip()
        if not dest_dir_raw:
            dest_dir_raw = "."
        if ".." in dest_dir_raw.split("/"):
            return bad(handler, "Invalid destination")
        dest_parent = safe_resolve(ws_root, dest_dir_raw)
        if not dest_parent.is_dir():
            return bad(handler, "Destination folder not found", 404)
        if source.is_dir():
            try:
                dest_parent.resolve().relative_to(source.resolve())
                return bad(handler, "Cannot move a folder into itself or its subfolder")
            except ValueError:
                pass
        dest = dest_parent / source.name
        if dest.resolve() == source.resolve():
            new_rel = source.relative_to(ws_root_resolved).as_posix()
            return j(
                handler,
                {"ok": True, "old_path": body["path"], "new_path": new_rel},
            )
        # Perform the move race-safely. The path-based checks above can be raced
        # (TOCTOU): between validating dest_parent and renaming, dest_dir could be
        # swapped to a symlink pointing outside the workspace, and a path-based
        # rename would follow it. Open BOTH parent directories as workspace-anchored
        # fds (openat + O_NOFOLLOW — every component verified non-symlink), do the
        # collision check by fd, then rename via src_dir_fd/dst_dir_fd so the kernel
        # operates on the verified directories, not re-resolved pathnames.
        leaf = source.name
        if os.open in getattr(os, "supports_dir_fd", set()):
            src_parent_fd = open_anchored_fd(ws_root, source.parent, want_dir=True)
            try:
                dst_parent_fd = open_anchored_fd(ws_root, dest_parent, want_dir=True)
                try:
                    try:
                        os.stat(leaf, dir_fd=dst_parent_fd, follow_symlinks=False)
                        return bad(
                            handler,
                            f'A file named "{leaf}" already exists in that folder',
                        )
                    except FileNotFoundError:
                        pass
                    os.rename(
                        leaf, leaf,
                        src_dir_fd=src_parent_fd, dst_dir_fd=dst_parent_fd,
                    )
                finally:
                    os.close(dst_parent_fd)
            finally:
                os.close(src_parent_fd)
        else:
            # Windows / no openat: no new race protection available, but creating
            # symlinks needs admin there. Fall back to the path-based rename.
            if dest.exists():
                return bad(
                    handler,
                    f'A file named "{source.name}" already exists in that folder',
                )
            source.rename(dest)
        new_rel = dest.relative_to(ws_root_resolved).as_posix()
        return j(
            handler,
            {"ok": True, "old_path": body["path"], "new_path": new_rel},
        )
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_create_dir(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        ws_root = Path(s.workspace)
        target = safe_resolve(ws_root, body["path"])
        if target.exists():
            return bad(handler, "Path already exists")
        make_anchored_dir(ws_root, target)
        return j(
            handler, {"ok": True, "path": target.relative_to(ws_root.resolve()).as_posix()}
        )
    except (ValueError, FileNotFoundError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_reveal(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if not target.exists():
            # Include the resolved server-side path in the error message so
            # the frontend toast can show *which* file the system expected.
            # Useful when a stale session row still references a deleted file
            # (#1764 — Cygnus's screenshot showed a "Failed to reveal: not
            # found" toast that dropped the path entirely, leaving no clue
            # what was missing).
            return bad(handler, f"File not found: {target}", 404)

        target_str = str(target)

        # Optional Docker host/container path translation (mirrors _handle_file_open_vscode).
        from api.config import get_config as _get_cfg  # noqa: PLC0415
        vscode_cfg = _get_cfg().get("vscode", {})
        if not isinstance(vscode_cfg, dict):
            vscode_cfg = {}
        container_prefix = vscode_cfg.get("container_path_prefix", "")
        host_prefix = vscode_cfg.get("host_path_prefix", "")
        if container_prefix and host_prefix:
            _norm = container_prefix.rstrip('/') + '/'
            if target_str.startswith(_norm) or target_str == container_prefix.rstrip('/'):
                target_str = host_prefix + target_str[len(container_prefix):]

        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", "-R", target_str])
        elif system == "Windows":
            subprocess.Popen(["explorer.exe", "/select," + target_str])
        else:
            # Linux / other — open parent directory
            subprocess.Popen(["xdg-open", str(Path(target_str).parent)])

        return j(handler, {"ok": True, "path": body["path"]})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_path(handler, body):
    """Resolve a relative workspace-rooted path into an absolute on-disk path.

    The right-click "Copy file path" action (#1764) wants to put the
    absolute path on the user's clipboard so they can paste it into a
    terminal, editor, or anywhere else without having to round-trip through
    the OS file browser. The frontend can't compute the absolute path on
    its own — `safe_resolve` joins against the session's workspace root
    which only the server knows. The handler here is a thin lookup; no
    filesystem mutation, no OS-specific dispatch. We do NOT require the
    target to exist (unlike `_handle_file_reveal`) — copying the path of a
    just-deleted file is still useful, and refusing would force callers
    to special-case 404s for an action that cannot fail destructively.
    """
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        return j(handler, {"ok": True, "path": str(target)})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_open_vscode(handler, body):
    """Open a workspace file or folder in VS Code (#2735).

    Reads optional ``vscode`` config block from config.yaml:

        vscode:
          command: code          # executable on PATH; defaults to "code"
          host_path_prefix: /home/user/projects       # Docker host path
          container_path_prefix: /app/workspace       # matching container path

    If ``host_path_prefix`` and ``container_path_prefix`` are both set,
    paths that begin with ``container_path_prefix`` are translated to the
    host prefix before being handed to VS Code.  This lets users running
    Hermes WebUI inside Docker still open files in their local editor.
    """
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session_for_file_ops(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if not target.exists():
            return bad(handler, f"File not found: {target}", 404)

        target_str = str(target)

        # Optional Docker host/container path translation
        from api.config import get_config as _get_cfg  # noqa: PLC0415
        vscode_cfg = _get_cfg().get("vscode", {})
        if not isinstance(vscode_cfg, dict):
            vscode_cfg = {}
        container_prefix = vscode_cfg.get("container_path_prefix", "")
        host_prefix = vscode_cfg.get("host_path_prefix", "")
        if container_prefix and host_prefix:
            _norm = container_prefix.rstrip('/') + '/'
            if target_str.startswith(_norm) or target_str == container_prefix.rstrip('/'):
                target_str = host_prefix + target_str[len(container_prefix):]

        cmd = vscode_cfg.get("command", "code")
        # Resolve the command to an absolute path so subprocess.Popen finds it
        # even when the server process inherits a minimal PATH (e.g. when
        # launched via start.sh on macOS where /usr/local/bin may be absent).
        resolved_cmd = shutil.which(cmd)
        if resolved_cmd is None:
            # Try common VS Code installation paths as fallback.
            # macOS: /usr/local/bin/code (symlink) or app bundle CLI
            # Linux: /usr/bin/code or snap
            # Windows: user-install under %LOCALAPPDATA%, system-install under %PROGRAMFILES%
            _local_app_data = os.environ.get("LOCALAPPDATA", "")
            _prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
            _prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
            _vscode_fallbacks = [
                # macOS
                "/usr/local/bin/code",
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                # Linux
                "/usr/bin/code",
                "/snap/bin/code",
                # Windows (user install)
                os.path.join(_local_app_data, "Programs", "Microsoft VS Code", "bin", "code.cmd"),
                # Windows (system install)
                os.path.join(_prog_files, "Microsoft VS Code", "bin", "code.cmd"),
                os.path.join(_prog_files_x86, "Microsoft VS Code", "bin", "code.cmd"),
            ]
            for fb in _vscode_fallbacks:
                if fb and Path(fb).exists():
                    resolved_cmd = fb
                    break
        if resolved_cmd is None:
            return bad(
                handler,
                f"VS Code command not found: {cmd!r}. "
                "Install VS Code and ensure the 'code' CLI is on PATH, "
                "or set vscode.command in config.yaml to the full path.",
            )
        subprocess.Popen([resolved_cmd, target_str])

        return j(handler, {"ok": True, "path": body["path"]})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_workspace_add(handler, body):
    # Strip surrounding paired quotes BEFORE any further processing — macOS
    # Finder's "Copy as Pathname" wraps paths in single quotes, and users
    # routinely paste those quoted strings into the Add Space input.
    # Doing this at the route entry means every downstream check (blocked
    # system path, validate_workspace_to_add, duplicate detection) sees the
    # cleaned form.
    path_str = _strip_surrounding_quotes(body.get("path", "").strip())
    name = body.get("name", "").strip()
    auto_create = body.get("create", False)
    if not path_str:
        return bad(handler, "path is required")
    # Validate the path is NOT a blocked system root BEFORE any filesystem mutation.
    # This prevents creating orphan directories on rejected paths (#782 review).
    # _is_blocked_system_path honours user-tmp carve-outs (e.g. /var/folders on
    # macOS) so pytest's tmp_path_factory paths and other legit user-tmp dirs
    # still register cleanly.
    try:
        candidate = Path(path_str).expanduser().resolve()
    except (ValueError, OSError, RuntimeError) as e:
        # Invalid path (e.g. embedded null byte) — fail closed with a clean 400
        # instead of letting .resolve() raise an uncaught 500.
        return bad(handler, f"Invalid path: {_sanitize_error(e)}")
    if _is_blocked_system_path(candidate):
        # Home-directory carve-out, mirroring the validators
        # (resolve_trusted_workspace / validate_workspace_to_add): a workspace
        # at or under the active user's home must stay allowed even when that
        # home lives under an otherwise-blocked root (e.g. systemd-homed
        # /var/home/<user>/...). Without this the route rejects valid
        # /var/home workspaces before validate_workspace_to_add()'s carve-out
        # can run.
        _home = _home_path()
        if not (_home != Path("/") and (candidate == _home or _is_within(candidate, _home))):
            return bad(handler, f"Path points to a system directory: {candidate}")
    # Now safe to create the directory if requested
    if auto_create:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            return bad(handler, f"Could not create directory: {_sanitize_error(e)}")
    # Full validation (exists, is_dir) — should pass now that dir exists
    try:
        p = validate_workspace_to_add(path_str)
    except ValueError as e:
        return bad(handler, str(e))
    wss = load_workspaces()
    if any(w["path"] == str(p) for w in wss):
        return bad(handler, "Workspace already in list")
    wss.append({"path": str(p), "name": name or p.name})
    save_workspaces(wss)
    try:
        from api.vault import sync_vault_to_rules, get_vault_dir, infer_space_from_workspace
        sp = infer_space_from_workspace(p)
        if sp and sp != "global":
            sync_vault_to_rules(get_vault_dir(p), p, space=sp)
    except Exception:
        logger.warning("Silent exception in _handle_workspace_add", exc_info=True)
        pass
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_remove(handler, body):
    path_str = body.get("path", "").strip()
    if not path_str:
        return bad(handler, "path is required")
    wss = load_workspaces()
    wss = [w for w in wss if w["path"] != path_str]
    save_workspaces(wss)
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_rename(handler, body):
    path_str = body.get("path", "").strip()
    name = body.get("name", "").strip()
    if not path_str or not name:
        return bad(handler, "path and name are required")
    wss = load_workspaces()
    for w in wss:
        if w["path"] == path_str:
            w["name"] = name
            break
    else:
        return bad(handler, "Workspace not found", 404)
    save_workspaces(wss)
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_reorder(handler, body):
    """Reorder workspaces by providing an ordered list of paths.

    Accepts {"paths": ["path1", "path2", ...]}. The workspaces list is
    rewritten so that entries appear in the given order. Any workspace
    not included in the request is appended at the end (preserves data).
    """
    paths = body.get("paths", [])
    if not paths or not isinstance(paths, list):
        return bad(handler, "paths is required and must be a list")
    wss = load_workspaces()
    by_path = {w["path"]: w for w in wss}
    # Build reordered list: given order first, then any omitted entries
    reordered = []
    seen = set()
    for p in paths:
        p = p.strip()
        if p in by_path and p not in seen:
            reordered.append(by_path[p])
            seen.add(p)
    # Append any workspaces not mentioned (safety net)
    for w in wss:
        if w["path"] not in seen:
            reordered.append(w)
    save_workspaces(reordered)
    return j(handler, {"ok": True, "workspaces": reordered})
