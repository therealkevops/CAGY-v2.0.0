"""Commands, crons, skills, MCP hub, subagents, vault, and plugins routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import ast
import datetime
import errno
import html
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api import webui_session_db
from api.config import (
    _cfg_lock,
    _get_config_path,
    _load_yaml_config_file,
    _save_yaml_config_file,
    get_config,
    get_config_for_profile_home,
    get_config_snapshot,
    load_settings,
    reload_config,
)
from api.helpers import _redact_text, bad, j, require
from api.models import _active_state_db_path, get_session
from api.profiles import (
    _SKILLS_STATS_CACHE,
    _is_isolated_profile_mode,
    _profiles_match,
    get_active_agy_home,
    get_active_profile_name as _get_active_profile_name,
)
from api.request_diagnostics import RequestDiagnostics
from api.route_config_models import _dashboard_plugin_enabled
from api.workspace import (
    get_last_workspace,
    get_profile_default_workspace,
    resolve_trusted_workspace,
)

logger = logging.getLogger(__name__)


def _handle_get_tools_and_mcp(handler, parsed):
    """Handle commands, crons, skills, MCP hub, subagents, vault, and plugins routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/commands":
        return j(
            handler,
            {
                "commands": [
                    {"name": "plan", "description": "Antigravity: Step-by-step implementation planning before coding", "category": "Antigravity"},
                    {"name": "goal", "description": "Antigravity: Autonomous long-running goal execution until completion", "category": "Antigravity"},
                    {"name": "grill-me", "description": "Antigravity: Interactive design interview to stress-test requirements", "category": "Antigravity"},
                    {"name": "learn", "description": "Antigravity: Persist behavioral guidelines & conventions", "category": "Antigravity"},
                    {"name": "schedule", "description": "Antigravity: Schedule recurring or one-shot task timer", "category": "Antigravity"},
                    {"name": "new", "description": "Start a new conversation", "category": "Session"},
                    {"name": "clear", "description": "Clear the active chat view", "category": "Session"},
                    {"name": "theme", "description": "Switch UI theme or skin", "category": "Settings"},
                    {"name": "help", "description": "Show available commands", "category": "Help"},
                ]
            },
        )

    if parsed.path == "/api/commands/bundles":
        return j(handler, {"bundles": []})

    if parsed.path == "/api/commands/moa/resolve":
        from api.commands import resolve_moa_config

        try:
            return j(handler, resolve_moa_config())
        except RuntimeError as e:
            return bad(handler, str(e), 503)

    if parsed.path == "/api/updates/check":
        settings = load_settings()
        if not settings.get("check_for_updates", True):
            return j(handler, {"disabled": True})
        include_agent_updates = not bool(settings.get("ignore_agent_updates"))
        qs = parse_qs(parsed.query)
        if (
            qs.get("simulate", ["0"])[0] == "1"
            and handler.client_address[0] == "127.0.0.1"
        ):
            return j(
                handler,
                {
                    "webui": {
                        "name": "webui",
                        "behind": 3,
                        "current_sha": "abc1234",
                        "latest_sha": "def5678",
                        "branch": "master",
                        "repo_url": "https://github.com/google-deepmind/antigravity",
                        "compare_url": "https://github.com/google-deepmind/antigravity",
                    },
                    "agent": {
                        "name": "agent",
                        "behind": 1 if include_agent_updates else 0,
                        "ignored": not include_agent_updates,
                        "current_sha": "aaa0001",
                        "latest_sha": "bbb0002",
                        "branch": "master",
                        "repo_url": "https://github.com/google-deepmind/antigravity",
                        "compare_url": "https://github.com/google-deepmind/antigravity",
                    },
                    "checked_at": 0,
                },
            )
        from api.updates import cached_update_status

        return j(handler, cached_update_status(include_agent=include_agent_updates))

    if parsed.path == "/api/sessions/gateway/stream":
        return _routes._handle_gateway_sse_stream(handler, parsed)

    if parsed.path == "/api/sessions/events":
        return _routes._handle_session_events_stream(handler)

    session_events_session_id = _routes._session_events_path_session_id(parsed.path)
    if session_events_session_id is not None:
        return _routes._handle_session_sse_stream_for_session(handler, parsed, session_events_session_id)

    if parsed.path == "/api/crons":
        _ensure_agent_cron_import_path()
        active_profile = _get_active_profile_name() or "default"
        try:
            active_jobs, other_jobs = _cron_jobs_cross_profile(active_profile)
        except ModuleNotFoundError as exc:
            if exc.name in ("cron", "cron.jobs"):
                return j(handler, {"jobs": [], "cron_unavailable": True})
            raise
        all_profiles = _routes._all_profiles_enabled(parsed)
        jobs = active_jobs + other_jobs if all_profiles else active_jobs
        hidden_other_count = 0 if all_profiles else len(other_jobs)
        return j(
            handler,
            {
                "jobs": jobs,
                "all_profiles": all_profiles,
                "active_profile": active_profile,
                "other_profile_count": hidden_other_count,
            },
        )

    if parsed.path == "/api/crons/output":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_output(handler, parsed)

    if parsed.path == "/api/crons/history":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_history(handler, parsed)

    if parsed.path == "/api/crons/run":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_run_detail(handler, parsed)

    if parsed.path == "/api/crons/recent":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_recent(handler, parsed)

    if parsed.path == "/api/crons/status":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_status(handler, parsed)

    if parsed.path == "/api/crons/delivery-options":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_delivery_options(handler)

    if parsed.path == "/api/skills":
        qs = parse_qs(parsed.query)
        category = qs.get("category", [None])[0]
        data = _skills_list_from_dir(_active_skills_dir(), category=category)
        skills = list(data.get("skills", []))
        try:
            from api.skills_wizard import list_workspace_rules

            rules = list_workspace_rules()
            skills.extend(rules)
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            pass
        return j(handler, {"skills": skills})

    if parsed.path == "/api/skills/usage":
        from api.skill_usage import read_skill_usage

        raw = read_skill_usage(_active_skills_dir())
        usage = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if not isinstance(v, dict):
                    usage[k] = {"use_count": 0, "view_count": 0, "patch_count": 0}
                    continue
                usage[k] = {
                    "use_count": (int(v["use_count"]) if v.get("use_count") is not None else 0),
                    "view_count": (int(v["view_count"]) if v.get("view_count") is not None else 0),
                    "patch_count": (int(v["patch_count"]) if v.get("patch_count") is not None else 0),
                }
                for meta_key in v:
                    if meta_key not in usage[k]:
                        usage[k][meta_key] = v[meta_key]
        skills_data = _skills_list_from_dir(_active_skills_dir()).get("skills", [])
        skill_names = sorted({s["name"] for s in skills_data})
        total = sum(
            e.get("use_count", 0) + e.get("view_count", 0) + e.get("patch_count", 0)
            for e in usage.values()
        )
        unique = sum(
            1 for e in usage.values()
            if e.get("use_count", 0) > 0 or e.get("view_count", 0) > 0 or e.get("patch_count", 0) > 0
        )
        return j(
            handler,
            {
                "usage": usage,
                "skill_names": skill_names,
                "total_invocations": total,
                "unique_skills_used": unique,
            },
        )

    if parsed.path == "/api/skills/content":
        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]
        if not name:
            return j(handler, {"error": "name required"}, status=400)
        if name.startswith("Rule: ") or "GEMINI.md" in name:
            from api.skills_wizard import list_workspace_rules

            for r in list_workspace_rules():
                if r["name"] == name and Path(r["path"]).exists():
                    c = Path(r["path"]).read_text(encoding="utf-8")
                    return j(handler, {"name": name, "content": c, "linked_files": {}})
        file_path = qs.get("file", [""])[0]
        if file_path:
            import re as _re

            if _re.search(r"[*?\[\]]", name):
                return bad(handler, "Invalid skill name", 400)
            skills_dir = _active_skills_dir()
            skill_dir, _skill_md = _find_skill_in_dirs(
                name, _active_skill_search_dirs(skills_dir)
            )
            if not skill_dir:
                return bad(handler, "Skill not found", 404)
            target = (skill_dir / file_path).resolve()
            try:
                target.relative_to(skill_dir.resolve())
            except ValueError:
                return bad(handler, "Invalid file path", 400)
            if not target.exists() or not target.is_file():
                return bad(handler, "File not found", 404)
            return j(
                handler,
                {"content": target.read_text(encoding="utf-8"), "path": file_path},
            )
        data = _skill_view_from_active_dir(name)
        if not isinstance(data.get("linked_files"), dict):
            data["linked_files"] = {}
        return j(handler, data)

    if parsed.path == "/api/memory":
        return _handle_memory_read(handler, parsed)

    if parsed.path == "/api/profiles":
        from api import profiles as profiles_api

        diag = RequestDiagnostics.maybe_start("GET", parsed.path, logger=logger, print_fn=getattr(handler, '_safe_webui_print', None))
        try:
            if diag:
                diag.stage("list_profiles_api")
            profiles_payload = profiles_api.list_profiles_api()
            if diag:
                diag.stage("active_profile_lookup")
            active = profiles_api.get_active_profile_name()
            if diag:
                diag.stage("isolated_mode_check")
            return j(
                handler,
                {
                    "profiles": profiles_payload,
                    "active": active,
                    "single_profile_mode": _is_isolated_profile_mode(),
                },
            )
        finally:
            if diag:
                diag.finish()

    if parsed.path == "/api/profile/active":
        from api import profiles as profiles_api

        active_profile_name = profiles_api.get_active_profile_name()
        try:
            _profile_default_workspace = get_profile_default_workspace()
        except Exception:
            logger.debug("Failed to resolve profile default workspace for /api/profile/active", exc_info=True)
            _profile_default_workspace = None
        return j(
            handler,
            {
                "name": active_profile_name,
                "path": str(profiles_api.get_active_agy_home()),
                "is_default": profiles_api._is_root_profile(active_profile_name),
                "default_workspace": _profile_default_workspace,
            },
        )

    if parsed.path == "/api/gateway/status":
        return j(handler, _routes._gateway_status_payload())

    if parsed.path == "/api/mcp/servers":
        return _handle_mcp_servers_list(handler)

    if parsed.path in ("/api/mcp/hub", "/api/mcp/hub/data"):
        from api.mcp_hub import list_mcp_hub_data

        return j(handler, list_mcp_hub_data())

    if parsed.path == "/api/mcp/tools":
        return _handle_mcp_tools_list(handler)

    if parsed.path in ("/api/subagents", "/api/subagents/list"):
        return _handle_subagents_list(handler, parsed)
    if parsed.path == "/api/subagents/sessions":
        from api.subagents import discover_all_swarm_sessions

        return j(handler, {"ok": True, "sessions": discover_all_swarm_sessions()})
    if parsed.path.startswith("/api/subagents/") and "transcript" in parsed.path:
        return _handle_subagent_transcript(handler, parsed)
    if parsed.path == "/api/subagents/detail":
        return _handle_subagent_transcript(handler, parsed)
    if parsed.path in ("/api/subagents/export",) or (parsed.path.startswith("/api/subagents/") and "export" in parsed.path):
        from api.subagents import export_subagent_transcript

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        sub_id = qs.get("id", [None])[0] or qs.get("subagent_id", [None])[0]
        if not sub_id and parsed.path.startswith("/api/subagents/"):
            sub_id = parsed.path.split("/api/subagents/", 1)[1].split("/")[0]
        data = export_subagent_transcript(sub_id or "")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Disposition", f'attachment; filename="subagent-{sub_id or "transcript"}.json"')
        payload = data.encode("utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
        return True

    if parsed.path in ("/api/artifacts", "/api/artifacts/list"):
        return _handle_artifacts_list(handler, parsed)
    if parsed.path == "/api/artifacts/content":
        return _handle_artifact_content(handler, parsed)
    if parsed.path in ("/api/vault/graph", "/api/vault/data"):
        from api.vault import get_vault_dir, scan_vault

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        space = qs.get("space", [None])[0]
        return j(handler, scan_vault(get_vault_dir(), space_filter=space))
    if parsed.path == "/api/vault/spaces":
        from api.vault import get_vault_dir, list_spaces

        return j(handler, {"ok": True, "spaces": list_spaces(get_vault_dir())})
    if parsed.path == "/api/vault/note":
        from api.vault import get_note, get_vault_dir

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        path = qs.get("path", [""])[0]
        if path.startswith("knowledge/"):
            path = path[len("knowledge/"):]
        return j(handler, get_note(get_vault_dir(), path))
    if parsed.path == "/api/vault/search":
        from api.vault import get_vault_dir, search_vault

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        q = qs.get("q", [""])[0]
        folder = qs.get("folder", [None])[0]
        tag = qs.get("tag", [None])[0]
        space = qs.get("space", [None])[0]
        try:
            limit = int(qs.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        return j(handler, search_vault(get_vault_dir(), query=q, folder=folder, tag=tag, limit=limit, space=space))
    if parsed.path == "/api/vault/health":
        from api.vault import get_vault_dir, get_vault_health

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        space = qs.get("space", [None])[0]
        return j(handler, get_vault_health(get_vault_dir(), space=space))
    if parsed.path == "/api/vault/template":
        from api.vault import get_next_adr_number, get_note_template, get_vault_dir

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        category = qs.get("category", ["notes"])[0]
        title = qs.get("title", [""])[0]
        space = qs.get("space", [None])[0]
        vdir = get_vault_dir()
        next_adr = get_next_adr_number(vdir, space=space) if category == "decisions" else None
        return j(handler, get_note_template(category=category, title=title, next_adr=next_adr, space=space))
    if parsed.path == "/api/vault/lint":
        from api.vault import get_vault_dir, infer_space_from_workspace, lint_vault

        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        space = qs.get("space", [None])[0]
        ws_param = qs.get("workspace", [None])[0]
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space:
            space = infer_space_from_workspace(ws_root)
        return j(handler, lint_vault(get_vault_dir(ws_root), workspace_path=ws_root, space=space))

    if parsed.path == "/api/analytics/efficiency":
        try:
            from api.analytics import compute_efficiency_metrics
        except ImportError:
            from webui.api.analytics import compute_efficiency_metrics
        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        sid = qs.get("session_id", [None])[0]
        return j(handler, compute_efficiency_metrics(session_id=sid))

    if parsed.path.startswith("/plugins/"):
        from api.plugins import _get_plugin_base

        plugin_base = _get_plugin_base()
        rel = parsed.path[len("/plugins/"):]
        allowed = {"plugin.css"}
        if rel not in allowed:
            return False
        safe = (plugin_base / rel).resolve()
        try:
            safe.relative_to(plugin_base.resolve())
        except ValueError:
            return False
        if safe.is_file():
            import os as _os

            data = safe.read_bytes()
            ext = _os.path.splitext(rel.lower())[1]
            ct = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".png": "image/png",
                ".svg": "image/svg+xml",
            }.get(ext, "application/octet-stream")
            handler.send_response(200)
            handler.send_header("Content-Type", ct)
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return True

    if parsed.path.startswith("/dashboard-plugins/"):
        parts = parsed.path.split("/", 3)
        if len(parts) >= 3:
            plugin_name = parts[2]
            rel_path = parts[3] if len(parts) > 3 else ""
            if not _dashboard_plugin_enabled(plugin_name):
                return False
            from api.plugins import serve_plugin_static

            result = serve_plugin_static(plugin_name, rel_path)
            if result:
                data, content_type = result
                handler.send_response(200)
                handler.send_header("Content-Type", content_type)
                handler.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups")
                handler.send_header("X-Content-Type-Options", "nosniff")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
                return True

    from api.plugins import PLUGIN_MANIFESTS, _PLUGIN_STATIC_ROOTS

    for name, manifest in PLUGIN_MANIFESTS.items():
        tab = manifest.get("tab", {})
        tab_path = tab.get("path", f"/{name}")
        if parsed.path == tab_path:
            if not _dashboard_plugin_enabled(name):
                return False
            dashboard_dir = _PLUGIN_STATIC_ROOTS.get(name)
            if dashboard_dir:
                index_html = dashboard_dir / "dist" / "index.html"
                if index_html.is_file():
                    data = index_html.read_bytes()
                    handler.send_response(200)
                    handler.send_header("Content-Type", "text/html; charset=utf-8")
                    handler.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups")
                    handler.send_header("Content-Length", str(len(data)))
                    handler.end_headers()
                    handler.wfile.write(data)
                    return True
                plugin_root = dashboard_dir.parent
                static_html = plugin_root / "static" / "index.html"
                if static_html.is_file():
                    data = static_html.read_bytes()
                    handler.send_response(200)
                    handler.send_header("Content-Type", "text/html; charset=utf-8")
                    handler.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups")
                    handler.send_header("Content-Length", str(len(data)))
                    handler.end_headers()
                    handler.wfile.write(data)
                    return True
                index_js = dashboard_dir / "dist" / "index.js"
                if index_js.is_file():
                    label = html.escape(manifest.get("label") or name)
                    css = html.escape(manifest.get("css", ""))
                    name_escaped = html.escape(name)
                    css_tag = f'<link rel="stylesheet" href="/dashboard-plugins/{name_escaped}/{css}">' if css else ""
                    html_content = (
                        f"<!doctype html>\n"
                        f"<html lang=\"en\">\n"
                        f"<head>\n"
                        f"  <meta charset=\"utf-8\">\n"
                        f"  <title>{label}</title>\n"
                        f"  {css_tag}\n"
                        f"</head>\n"
                        f"<body>\n"
                        f'  <div id="pluginPageContainer"></div>\n'
                        f'  <script src="/dashboard-plugins/{name_escaped}/dist/index.js"></script>\n'
                        f"</body>\n"
                        f"</html>\n"
                    ).encode("utf-8")
                    handler.send_response(200)
                    handler.send_header("Content-Type", "text/html; charset=utf-8")
                    handler.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups")
                    handler.send_header("Content-Length", str(len(html_content)))
                    handler.end_headers()
                    handler.wfile.write(html_content)
                    return True

    return None


def _handle_post_tools_and_mcp(handler, parsed, body, diag=None):
    """Handle POST tools, MCP, updates, extensions, crons, skills, memory, and gateway routes.
    Returns True/response if handled, None if unhandled.
    """
    from api import routes as _routes

    _sanitize_error = _routes._sanitize_error
    _handle_gateway_lifecycle = _routes._handle_gateway_lifecycle
    ensure_agent_runtime_current = _routes.ensure_agent_runtime_current
    require_ai_agent_class = _routes.require_ai_agent_class
    if parsed.path == "/api/updates/check":
        settings = load_settings()
        if not settings.get("check_for_updates", True):
            force = bool(body.get("force", False)) if isinstance(body, dict) else False
            if force:
                pass
            else:
                return j(handler, {"disabled": True})
        include_agent_updates = not bool(settings.get("ignore_agent_updates"))
        force = bool(body.get("force", False))
        channel = body.get("channel") if isinstance(body, dict) else None
        if channel not in ("stable", "experimental"):
            channel = settings.get("update_channel")
        from api.updates import check_for_updates

        logger.info("checking for updates (force=%s, include_agent=%s, channel=%s)", force, include_agent_updates, channel)
        try:
            payload = check_for_updates(force=force, include_agent=include_agent_updates, channel=channel)
        except Exception:
            logger.exception("update check failed unexpectedly (defensive guard caught exception)")
            return bad(handler, "Update check failed, see server log for details", status=500)
        logger.info("update check completed")
        return j(handler, payload)

    if parsed.path == "/api/extensions/toggle":
        from api.extensions import ExtensionToggleError, set_extension_user_enabled

        try:
            return j(
                handler,
                set_extension_user_enabled(body.get("id"), body.get("enabled")),
            )
        except ExtensionToggleError as exc:
            return bad(handler, str(exc), status=exc.status)
        except Exception:
            logger.exception("extension toggle failed")
            return bad(handler, "Failed to update extension state", status=500)

    if parsed.path == "/api/extensions/sidecar-proxy-consent":
        from api.extensions import (
            ExtensionSidecarProxyError,
            set_extension_sidecar_proxy_consent,
        )

        try:
            return j(
                handler,
                set_extension_sidecar_proxy_consent(
                    body.get("id"),
                    body.get("approved"),
                ),
            )
        except ExtensionSidecarProxyError as exc:
            return bad(handler, str(exc), status=exc.status)
        except Exception:
            logger.exception("extension sidecar proxy consent update failed")
            return bad(handler, "Failed to update extension state", status=500)

    if parsed.path == "/api/extensions/install":
        from api.extensions import ExtensionInstallError, install_extension

        try:
            return j(
                handler,
                install_extension(body.get("id"), body.get("download_url"), body.get("sha256")),
            )
        except ExtensionInstallError as exc:
            return bad(handler, str(exc), status=exc.status)
        except Exception:
            logger.exception("extension install failed")
            return bad(handler, "Failed to install extension", status=500)

    if parsed.path == "/api/extensions/uninstall":
        from api.extensions import ExtensionInstallError, uninstall_extension

        try:
            return j(
                handler,
                uninstall_extension(body.get("id")),
            )
        except ExtensionInstallError as exc:
            return bad(handler, str(exc), status=exc.status)
        except Exception:
            logger.exception("extension uninstall failed")
            return bad(handler, "Failed to uninstall extension", status=500)

    if parsed.path == "/api/crons/create":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_create(handler, body)

    if parsed.path == "/api/crons/update":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_update(handler, body)

    if parsed.path == "/api/crons/delete":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_delete(handler, body)

    if parsed.path == "/api/crons/run":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_run(handler, body)

    if parsed.path == "/api/crons/pause":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_pause(handler, body)

    if parsed.path == "/api/crons/resume":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _ensure_agent_cron_import_path()
            return _handle_cron_resume(handler, body)

    if parsed.path == "/api/commands/bundles/resolve":
        from api.commands import resolve_bundle_command

        command = str(body.get("command", "") or "").strip()
        if not command:
            return bad(handler, "command is required")

        try:
            return j(handler, resolve_bundle_command(command))
        except KeyError:
            return bad(handler, "Bundle command not found", 404)
        except ValueError as e:
            return bad(handler, str(e), 400)
        except RuntimeError as e:
            return bad(handler, _sanitize_error(e), 500)

    if parsed.path == "/api/commands/exec":
        from api.commands import execute_agent_command, execute_plugin_command

        command = str(body.get("command", "") or "").strip()
        if not command:
            return bad(handler, "command is required")

        try:
            return j(handler, {"output": execute_agent_command(command)})
        except KeyError:
            pass
        except ValueError as e:
            return bad(handler, str(e), 400)
        except RuntimeError as e:
            return bad(handler, _sanitize_error(e), 500)

        try:
            return j(handler, {"output": execute_plugin_command(command)})
        except ValueError as e:
            return bad(handler, str(e), 400)
        except KeyError:
            return bad(handler, "Plugin command not found", 404)
        except RuntimeError as e:
            return bad(handler, _sanitize_error(e), 500)

    # ── Skills (POST) ──
    if parsed.path == "/api/skills/save":
        return _handle_skill_save(handler, body)

    if parsed.path == "/api/skills/delete":
        return _handle_skill_delete(handler, body)

    if parsed.path == "/api/skills/toggle":
        return _handle_skill_toggle(handler, body)

    # ── Memory (POST) ──
    if parsed.path == "/api/memory/write":
        return _handle_memory_write(handler, body)

    if parsed.path in {"/api/gateway/start", "/api/gateway/stop", "/api/gateway/restart"}:
        return _handle_gateway_lifecycle(handler, parsed.path.rsplit("/", 1)[-1], body)

    # ── Updates lifecycle (POST) ──
    if parsed.path == "/api/updates/apply":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        _apply_channel = body.get("channel") if isinstance(body, dict) else None
        if _apply_channel not in ("stable", "experimental"):
            _apply_channel = None
        from api.updates import apply_update

        return j(handler, apply_update(target, _apply_channel))

    if parsed.path == "/api/updates/force":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        _force_channel = body.get("channel") if isinstance(body, dict) else None
        if _force_channel not in ("stable", "experimental"):
            _force_channel = None
        from api.updates import apply_force_update

        return j(handler, apply_force_update(target, _force_channel))

    if parsed.path == "/api/updates/clear_lock":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_clear_lock

        return j(handler, apply_clear_lock(target))

    if parsed.path == "/api/updates/summary":
        from api.updates import summarize_update_payload

        updates = body.get("updates") if isinstance(body, dict) else {}
        target = body.get("target") if isinstance(body, dict) else None

        def _llm_update_summary(system_prompt: str, user_prompt: str) -> str:
            from api import profiles as profiles_api

            active_profile = profiles_api.get_active_profile_name() or "default"

            with profiles_api.profile_env_for_background_worker(
                active_profile,
                "update summary",
                logger_override=logger,
            ):
                from api.config import (
                    get_effective_default_model,
                    resolve_model_provider,
                    resolve_custom_provider_connection,
                )

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]

                _main_model, _main_provider, _main_base_url = resolve_model_provider(get_effective_default_model())
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
                    logger.debug("update summary runtime provider resolution failed: %s", _e)
                if isinstance(_main_provider, str) and _main_provider.startswith("custom:"):
                    _cp_key, _cp_base = resolve_custom_provider_connection(_main_provider)
                    if not _main_api_key and _cp_key:
                        _main_api_key = _cp_key
                    if not _main_base_url and _cp_base:
                        _main_base_url = _cp_base

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
                    logger.debug("update summary auxiliary model failed; falling back to main model: %s", _e)

                AIAgent = require_ai_agent_class()

                agent = AIAgent(
                    model=_main_model,
                    provider=_main_provider,
                    base_url=_main_base_url,
                    api_key=_main_api_key,
                    platform="webui",
                    quiet_mode=True,
                    enabled_toolsets=[],
                    session_id=f"updates-summary-{uuid.uuid4().hex[:8]}",
                )
                result = agent.run_conversation(
                    user_message=user_prompt,
                    system_message=system_prompt,
                    conversation_history=[],
                    task_id=f"updates-summary-{uuid.uuid4().hex[:8]}",
                )
                return str(result.get("final_response") or "").strip()

        return j(handler, summarize_update_payload(updates, llm_callback=_llm_update_summary, target=target))

    return None


# ── MCP, Subagents, and Artifacts Handlers (Sprint M4.1) ──────────────────────

def _mask_secrets(obj):
    """Mask sensitive values in env vars and headers."""
    if not isinstance(obj, dict):
        return obj
    sensitive = ("auth", "token", "key", "secret", "password", "credential")
    masked = {}
    for k, v in obj.items():
        if isinstance(v, str) and any(s in k.lower() for s in sensitive):
            masked[k] = "••••••"
        elif isinstance(v, dict):
            masked[k] = _mask_secrets(v)
        else:
            masked[k] = v
    return masked


def _parse_mcp_enabled(value) -> bool:
    """Parse Hermes MCP ``enabled`` values without raising on bad config."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return True


def _mcp_runtime_status_by_name() -> dict[str, dict]:
    """Return already-known MCP runtime status without starting servers.

    ``tools.mcp_tool.get_mcp_status()`` only reads the existing MCP registry and
    configuration; it does not probe or spawn MCP subprocesses. If Hermes Agent
    is unavailable, fall back to an empty map so the API remains safe.
    """
    try:
        from tools.mcp_tool import get_mcp_status
        statuses = get_mcp_status()
    except Exception:
        logger.debug("Silent exception in _mcp_runtime_status_by_name", exc_info=True)
        return {}
    if not isinstance(statuses, list):
        return {}
    return {
        str(entry.get("name")): entry
        for entry in statuses
        if isinstance(entry, dict) and entry.get("name")
    }


def _server_summary(name, cfg, runtime_status=None):
    """Return a safe summary of an MCP server config."""
    runtime_status = runtime_status if isinstance(runtime_status, dict) else {}
    out = {"name": name}
    if not isinstance(cfg, dict):
        out.update({
            "transport": "invalid",
            "timeout": 120,
            "connect_timeout": 60,
            "enabled": False,
            "active": False,
            "status": "invalid_config",
            "tool_count": None,
        })
        return out

    enabled = _parse_mcp_enabled(cfg.get("enabled", True))
    connected = bool(runtime_status.get("connected")) if enabled else False
    if "url" in cfg:
        out["transport"] = "http"
        # Mask auth headers
        if "headers" in cfg:
            out["headers"] = _mask_secrets(cfg["headers"])
        out["url"] = cfg["url"]
    elif "command" in cfg:
        out["transport"] = "stdio"
        out["command"] = cfg.get("command", "")
        out["args"] = cfg.get("args", [])
        if "env" in cfg:
            out["env"] = _mask_secrets(cfg["env"])
    else:
        out["transport"] = "invalid"
        enabled = False
        connected = False

    out["timeout"] = cfg.get("timeout", 120)
    out["connect_timeout"] = cfg.get("connect_timeout", 60)
    out["enabled"] = enabled
    out["active"] = connected
    if out["transport"] == "invalid":
        out["status"] = "invalid_config"
    elif not enabled:
        out["status"] = "disabled"
    elif connected:
        out["status"] = "active"
    else:
        out["status"] = "configured"
    out["tool_count"] = runtime_status.get("tools") if runtime_status else None
    return out


def _mcp_safe_display_text(value, *, limit: int) -> str:
    """Return redacted, bounded MCP text safe for WebUI inventory rows."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = _redact_text(value).strip()
    value = re.sub(r"Authorization:\s*Bearer\s+\S+", "[REDACTED CREDENTIAL]", value, flags=re.I)
    if len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value


def _mcp_schema_type(schema) -> str:
    """Return a compact, non-sensitive display type for a JSON schema node."""
    if not isinstance(schema, dict):
        return "unknown"
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = "/".join(str(t) for t in typ if t)
    if isinstance(typ, str) and typ:
        return typ
    for composite in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(composite), list) and schema[composite]:
            return composite
    if "enum" in schema:
        return "enum"
    return "unknown"


def _mcp_schema_summary(schema, *, limit: int = 12) -> list[dict]:
    """Summarize an MCP input schema without exposing raw defaults/examples.

    The WebUI only needs searchable/displayable argument hints. Returning raw
    JSON Schema can overexpose server-provided defaults, examples, enums, or
    vendor extensions, so this strips each parameter down to name/type/required
    and a redacted description.
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    out = []
    for name, prop in properties.items():
        if len(out) >= limit:
            break
        if not isinstance(name, str):
            continue
        prop = prop if isinstance(prop, dict) else {}
        desc = prop.get("description", "")
        if not isinstance(desc, str):
            desc = ""
        desc = _mcp_safe_display_text(desc, limit=180)
        out.append({
            "name": name,
            "type": _mcp_schema_type(prop),
            "required": name in required_names,
            "description": desc,
        })
    return out


def _mcp_tool_schema_from_payload(tool):
    if not isinstance(tool, dict):
        return {}
    for key in ("parameters", "inputSchema", "input_schema", "schema"):
        value = tool.get(key)
        if isinstance(value, dict):
            if key == "schema" and isinstance(value.get("parameters"), dict):
                return value["parameters"]
            return value
    return {}


def _mcp_tool_summary(name, tool, server_summary):
    """Return a safe global inventory row for one MCP tool."""
    server_summary = server_summary if isinstance(server_summary, dict) else {}
    if isinstance(tool, str):
        tool = {"name": tool}
    elif not isinstance(tool, dict):
        tool = {}
    tool_name = str(tool.get("name") or name or "")
    description = tool.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    description = _mcp_safe_display_text(description, limit=360)
    return {
        "name": tool_name,
        "server": str(server_summary.get("name") or ""),
        "description": description,
        "active": bool(server_summary.get("active")),
        "enabled": bool(server_summary.get("enabled")),
        "status": server_summary.get("status") or "unknown",
        "schema_summary": _mcp_schema_summary(_mcp_tool_schema_from_payload(tool)),
    }


def _mcp_tools_from_runtime_status(runtime_by_name, server_summaries):
    """Read detailed MCP tool payloads from runtime status when available."""
    tools = []
    if not isinstance(runtime_by_name, dict):
        return tools
    for server_name, runtime in runtime_by_name.items():
        if not isinstance(runtime, dict):
            continue
        raw_tools = runtime.get("tools")
        if not isinstance(raw_tools, list):
            raw_tools = runtime.get("tool_schemas")
        if not isinstance(raw_tools, list):
            continue
        server_summary = server_summaries.get(str(server_name), {"name": str(server_name)})
        for index, tool in enumerate(raw_tools):
            fallback_name = f"{server_name}:{index}"
            summary = _mcp_tool_summary(fallback_name, tool, server_summary)
            if summary["name"]:
                tools.append(summary)
    return tools


def _mcp_tools_from_registry(server_summaries):
    """Read already-registered MCP tool schemas without probing MCP servers."""
    try:
        from tools.registry import registry
    except Exception:
        logger.debug("Silent exception in _mcp_tools_from_registry", exc_info=True)
        return []
    tools = []
    try:
        names = registry.get_all_tool_names()
    except Exception:
        logger.debug("Silent exception in _mcp_tools_from_registry", exc_info=True)
        return []
    for tool_name in names:
        try:
            toolset = registry.get_toolset_for_tool(tool_name)
        except Exception:
            logger.debug("Silent exception in _mcp_tools_from_registry", exc_info=True)
            continue
        if not isinstance(toolset, str) or not toolset.startswith("mcp-"):
            continue
        server_name = toolset[len("mcp-"):]
        schema = registry.get_schema(tool_name) or {}
        server_summary = server_summaries.get(server_name, {
            "name": server_name,
            "enabled": True,
            "active": False,
            "status": "configured",
        })
        tools.append(_mcp_tool_summary(tool_name, schema, server_summary))
    return tools


def _handle_mcp_tools_list(handler):
    """List known MCP tools from already-available runtime inventory only."""
    cfg = get_config_for_profile_home(get_active_agy_home())
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    runtime = _mcp_runtime_status_by_name()
    server_summaries = {
        str(name): _server_summary(str(name), scfg, runtime.get(str(name)))
        for name, scfg in servers.items()
    }
    tools = _mcp_tools_from_runtime_status(runtime, server_summaries)
    source = "mcp_runtime_status"
    if not tools:
        tools = _mcp_tools_from_registry(server_summaries)
        source = "tool_registry" if tools else "none"
    tools.sort(key=lambda row: (row.get("server", ""), row.get("name", "")))
    unavailable_servers = [
        summary["name"] for summary in server_summaries.values()
        if summary.get("enabled") and not summary.get("active")
    ]
    return j(handler, {
        "tools": tools,
        "total": len(tools),
        "source": source,
        "inventory_scope": "already_known_runtime_only",
        "unavailable_servers": unavailable_servers,
    })


def _webui_truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _external_notes_sources_enabled(config_data: dict | None = None) -> bool:
    """Return whether the third-party notes drawer is explicitly enabled.

    The Memory panel is a primary surface, so this power-user drawer stays
    default-off unless a deployment opts in through config or environment.
    """
    env_value = os.getenv("AGY_WEBUI_EXTERNAL_NOTES_SOURCES") or os.getenv("HERMES_WEBUI_EXTERNAL_NOTES_SOURCES", "")
    if env_value:
        return _webui_truthy(env_value)
    cfg = config_data if isinstance(config_data, dict) else get_config()
    if not isinstance(cfg, dict):
        return False
    return _webui_truthy(
        cfg.get("webui_external_notes_sources")
        or cfg.get("external_notes_sources")
        or cfg.get("notes_sources_drawer")
    )


_NOTES_SOURCE_SERVER_HINTS = {
    "joplin", "obsidian", "notion", "llm-wiki", "llmwiki", "wiki",
    "notes", "note", "knowledge", "kb", "readwise", "logseq",
}
_NOTES_SOURCE_TOOL_HINTS = {
    "note", "notes", "notebook", "page", "pages", "wiki", "knowledge",
    "search_notes", "get_note", "list_notes", "read_note",
}
_NOTES_SOURCE_CONFIGURED_TOOL_HINTS = {
    "joplin": [
        {"name": "search_notes", "description": "Search Joplin notes by keyword."},
        {"name": "list_notes", "description": "List notes from a Joplin notebook."},
        {"name": "get_note", "description": "Read a specific Joplin note by ID."},
    ],
    "obsidian": [
        {"name": "search_notes", "description": "Search Obsidian notes by keyword."},
        {"name": "read_note", "description": "Read a specific Obsidian note or file."},
    ],
    "notion": [
        {"name": "search_pages", "description": "Search Notion pages or databases."},
        {"name": "get_page", "description": "Read a specific Notion page."},
    ],
    "llm-wiki": [
        {"name": "query_knowledge_base", "description": "Query the LLM Wiki knowledge base."},
        {"name": "read_page", "description": "Read a specific wiki page."},
    ],
    "llmwiki": [
        {"name": "query_knowledge_base", "description": "Query the LLM Wiki knowledge base."},
        {"name": "read_page", "description": "Read a specific wiki page."},
    ],
}


def _note_source_label(name: str) -> str:
    labels = {
        "joplin": "Joplin",
        "obsidian": "Obsidian",
        "notion": "Notion",
        "llm-wiki": "LLM Wiki",
        "llmwiki": "LLM Wiki",
        "readwise": "Readwise",
        "logseq": "Logseq",
    }
    lowered = str(name or "").strip().lower()
    return labels.get(lowered, str(name or "").replace("_", " ").replace("-", " ").title())


def _looks_like_notes_source(server_name: str, tool_rows: list[dict]) -> bool:
    server_l = str(server_name or "").lower()
    if any(hint in server_l for hint in _NOTES_SOURCE_SERVER_HINTS):
        return True
    for tool in tool_rows:
        haystack = " ".join([
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
        ]).lower()
        if any(hint in haystack for hint in _NOTES_SOURCE_TOOL_HINTS):
            return True
    return False


def _configured_note_tool_hints(server_name: str) -> list[dict]:
    """Return safe expected note-tool hints for configured known sources."""
    server_l = str(server_name or "").strip().lower()
    hints = _NOTES_SOURCE_CONFIGURED_TOOL_HINTS.get(server_l)
    if hints is None:
        if any(hint in server_l for hint in ("wiki", "knowledge", "kb")):
            hints = [
                {"name": "search", "description": "Search this configured knowledge source."},
                {"name": "read", "description": "Read an item from this configured knowledge source."},
            ]
        elif any(hint in server_l for hint in ("note", "notes")):
            hints = [
                {"name": "search_notes", "description": "Search this configured notes source."},
                {"name": "read_note", "description": "Read a note from this configured notes source."},
            ]
        else:
            hints = []
    return [
        {
            "name": _mcp_safe_display_text(row.get("name") or "", limit=96),
            "description": _mcp_safe_display_text(row.get("description") or "", limit=180),
            "inferred": True,
        }
        for row in hints
        if isinstance(row, dict)
    ]


def _notes_sources_from_mcp_inventory(server_summaries: dict, tools: list[dict]) -> list[dict]:
    """Build a safe notes/knowledge-source inventory from MCP servers/tools.

    Some WebUI deployments can read ``mcp_servers`` from config before their
    local runtime/tool registry has hydrated MCP tool metadata.  Still show
    configured note/knowledge servers (for example Joplin) in that case so the
    drawer reflects connection/configuration state instead of appearing empty.
    """
    by_server: dict[str, list[dict]] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        server = str(tool.get("server") or "").strip()
        if not server:
            continue
        by_server.setdefault(server, []).append(tool)

    if isinstance(server_summaries, dict):
        for server, summary in server_summaries.items():
            server_name = str(server or "").strip()
            if not server_name or server_name in by_server:
                continue
            if _looks_like_notes_source(server_name, []):
                by_server.setdefault(server_name, [])

    sources = []
    for server, tool_rows in by_server.items():
        if not _looks_like_notes_source(server, tool_rows):
            continue
        summary = server_summaries.get(server, {"name": server}) if isinstance(server_summaries, dict) else {"name": server}
        safe_tools = []
        tool_source = "runtime"
        for tool in tool_rows[:8]:
            desc = _mcp_safe_display_text(tool.get("description") or "", limit=180)
            desc = re.sub(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", "[REDACTED]", desc)
            safe_tools.append({
                "name": _mcp_safe_display_text(tool.get("name") or "", limit=96),
                "description": desc,
            })
        if not safe_tools:
            safe_tools = _configured_note_tool_hints(server)
            if safe_tools:
                tool_source = "configured_hint"
        sources.append({
            "name": server,
            "label": _note_source_label(server),
            "enabled": bool(summary.get("enabled", True)),
            "active": bool(summary.get("active")),
            "status": summary.get("status") or "unknown",
            "tool_count": len(safe_tools),
            "tool_source": tool_source,
            "tools": safe_tools,
        })
    sources.sort(key=lambda row: (not row.get("active"), row.get("label", "")))
    return sources


def _handle_mcp_servers_list(handler):
    """List configured MCP servers with safe, read-only runtime visibility."""
    cfg = get_config_for_profile_home(get_active_agy_home())
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    runtime = _mcp_runtime_status_by_name()
    result = [
        _server_summary(name, scfg, runtime.get(str(name)))
        for name, scfg in servers.items()
    ]
    return j(handler, {
        "servers": result,
        "toggle_supported": True,
        "reload_required": True,
    })


def _handle_mcp_server_delete(handler, name):
    """Delete an MCP server by name."""
    from urllib.parse import unquote
    name = unquote(name)
    if not name:
        return bad(handler, "name is required")
    cfg = get_config()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    if name not in servers:
        return bad(handler, f"MCP server '{name}' not found", 404)
    del servers[name]
    cfg["mcp_servers"] = servers
    _save_yaml_config_file(_get_config_path(), cfg)
    reload_config()
    return j(handler, {"ok": True, "deleted": name})


def _handle_mcp_server_toggle(handler, name, body):
    """Toggle enabled state for an MCP server (PATCH /api/mcp/servers/{name})."""
    from urllib.parse import unquote
    name = unquote(name)
    if not name:
        return bad(handler, "name is required")
    if "enabled" not in body:
        return bad(handler, "enabled field is required")
    enabled = bool(body["enabled"])
    cfg = get_config()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    if name not in servers:
        return bad(handler, f"MCP server '{name}' not found", 404)
    if not isinstance(servers[name], dict):
        return bad(handler, f"MCP server '{name}' has invalid config", 400)
    servers[name]["enabled"] = enabled
    cfg["mcp_servers"] = servers
    _save_yaml_config_file(_get_config_path(), cfg)
    reload_config()
    return j(handler, {"ok": True, "name": name, "enabled": enabled})


_MASKED_PLACEHOLDER = "••••••"


def _strip_masked_values(submitted, existing):
    """Remove masked placeholder values from submitted dict, keeping originals."""
    if not isinstance(submitted, dict) or not isinstance(existing, dict):
        return submitted
    cleaned = {}
    for k, v in submitted.items():
        if isinstance(v, str) and v == _MASKED_PLACEHOLDER:
            if k in existing and isinstance(existing[k], str):
                cleaned[k] = existing[k]  # preserve original real value
                continue
        elif isinstance(v, dict) and k in existing and isinstance(existing[k], dict):
            cleaned[k] = _strip_masked_values(v, existing[k])
        else:
            cleaned[k] = v
    return cleaned


def _handle_mcp_server_update(handler, name, body):
    """Add or update an MCP server."""
    from urllib.parse import unquote
    name = unquote(name)
    if not name:
        return bad(handler, "name is required")
    # Validate: must have url (http) or command (stdio)
    server_cfg = {}
    cfg = get_config()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    existing_cfg = servers.get(name, {})
    if body.get("url"):
        server_cfg["url"] = body["url"].strip()
        if body.get("headers"):
            server_cfg["headers"] = _strip_masked_values(body["headers"], existing_cfg.get("headers", {}))
    elif body.get("command"):
        server_cfg["command"] = body["command"].strip()
        if body.get("args"):
            server_cfg["args"] = body["args"] if isinstance(body["args"], list) else [body["args"]]
        if body.get("env"):
            server_cfg["env"] = _strip_masked_values(body["env"], existing_cfg.get("env", {}))
    else:
        return bad(handler, "url or command is required")
    if body.get("timeout") is not None:
        try:
            server_cfg["timeout"] = int(body["timeout"])
        except (ValueError, TypeError):
            pass
    servers[name] = server_cfg
    cfg["mcp_servers"] = servers
    _save_yaml_config_file(_get_config_path(), cfg)
    reload_config()
    return j(handler, {"ok": True, "server": _server_summary(name, server_cfg)})


def _handle_subagents_list(handler, parsed):
    """List subagents and hierarchy for current session or conversation."""
    from api.subagents import list_subagents
    qs = parse_qs(parsed.query or "")
    session_id = qs.get("session_id", [""])[0]
    conv_id = qs.get("conv_id", [""])[0]
    try:
        return j(handler, list_subagents(session_id=session_id, conv_id=conv_id))
    except Exception as e:
        logger.exception("Failed to list subagents")
        return bad(handler, str(e), status=500)


def _handle_subagent_transcript(handler, parsed):
    """Retrieve full transcript and steps for a specific subagent."""
    from api.subagents import get_subagent_transcript
    qs = parse_qs(parsed.query or "")
    subagent_id = qs.get("id", [""])[0]
    if not subagent_id and parsed.path.startswith("/api/subagents/"):
        subagent_id = parsed.path.split("/api/subagents/", 1)[1].split("/")[0]
    if not subagent_id:
        return bad(handler, "Subagent ID is required", status=400)
    try:
        return j(handler, get_subagent_transcript(subagent_id))
    except Exception as e:
        logger.exception("Failed to get subagent transcript")
        return bad(handler, str(e), status=500)


def _handle_artifacts_list(handler, parsed):
    """List artifacts for session or workspace."""
    from api.artifacts import list_artifacts
    qs = parse_qs(parsed.query or "")
    session_id = qs.get("session_id", [""])[0]
    conv_id = qs.get("conv_id", [""])[0]
    try:
        return j(handler, list_artifacts(session_id=session_id, conv_id=conv_id))
    except Exception as e:
        logger.exception("Failed to list artifacts")
        return bad(handler, str(e), status=500)


def _handle_artifact_content(handler, parsed):
    """Retrieve artifact content or binary data."""
    from api.artifacts import get_artifact_content
    qs = parse_qs(parsed.query or "")
    path = qs.get("path", [""])[0]
    if not path:
        return bad(handler, "path query parameter is required", status=400)
    try:
        return j(handler, get_artifact_content(path))
    except Exception as e:
        logger.exception("Failed to get artifact content")
        return bad(handler, str(e), status=500)


# ── Skills System Handlers & Helpers (Sprint M4.2a) ──────────────────────────

def _active_skills_dir() -> Path:
    """Return the active skills directory for Antigravity skills."""
    ws = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
    if not ws.exists():
        ws = Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()
    skills_dir = ws / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def _skill_path_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except (OSError, ValueError):
        return False


def _skill_category_from_path(
    skill_md: Path,
    skills_dirs: list[Path],
    local_skills_dir: Path | None = None,
) -> str | None:
    path_str = str(skill_md)
    if "builtin" in path_str and "antigravity-cli" in path_str:
        return "Built-in (AGY)"
    if "/skills/" in path_str or "skills/nc2-advisor" in path_str or "skills/agy-webui-bridge" in path_str or "/workspace/skills" in path_str:
        return "Custom (Workspace)"
    if ".gemini" in path_str:
        return "Antigravity"
    if ".hermes" in path_str:
        return "Antigravity (Global)"
    return "Custom"


def _active_skill_search_dirs(skills_dir: Path) -> list[Path]:
    dirs = [skills_dir]
    ws = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
    if not ws.exists():
        ws = Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()

    candidates = [
        ws / "skills",
        ws / ".gemini" / "skills",
        ws / "workspace" / "skills",
        Path.home() / ".gemini" / "antigravity-cli" / "builtin" / "skills",
        Path.home() / ".gemini" / "antigravity-cli" / "skills",
        Path.home() / ".gemini" / "skills",
        Path("/workspace/skills"),
        Path("/workspace/.gemini/skills"),
        Path.cwd() / "skills",
        Path.cwd() / ".gemini" / "skills",
        Path.cwd().parent / "skills",
        Path.cwd().parent / ".gemini" / "skills",
    ]
    for d in candidates:
        if d.exists() and d not in dirs:
            dirs.append(d)
    return [p for p in dirs if p.exists()]


def _active_profile_config_path() -> Path:
    """Return config.yaml for the request's active WebUI profile.

    Skills endpoints are profile-scoped UI actions: both the visible disabled
    toggle state and toggle writes must follow the cookie/thread-local active
    Hermes home, not process-global HERMES_HOME or HERMES_CONFIG_PATH values
    captured at server startup.
    """
    test_override_module = getattr(_get_config_path, "__module__", "")
    if test_override_module != "api.config":
        return _get_config_path()
    try:
        from api.profiles import get_active_agy_home

        return Path(get_active_agy_home()) / "config.yaml"
    except Exception:
        logger.debug("Silent exception in _active_profile_config_path", exc_info=True)
        return _get_config_path()


def _get_disabled_skill_names_for_profile() -> set:
    """Read disabled skill names from the active profile's config.yaml.

    Unlike ``tools.skills_tool._get_disabled_skill_names`` which reads from
    the process-global ``HERMES_HOME``, this uses ``_get_config_path()`` which
    resolves against the WebUI's active profile.  Checks
    ``skills.platform_disabled.webui`` first, falling back to
    ``skills.disabled``.
    """
    config_path = _active_profile_config_path()
    if not config_path.exists():
        return set()
    try:
        cfg = _load_yaml_config_file(config_path)
    except Exception:
        logger.debug("Silent exception in _get_disabled_skill_names_for_profile", exc_info=True)
        return set()
    if not isinstance(cfg, dict):
        return set()
    skills_cfg = cfg.get("skills")
    if not isinstance(skills_cfg, dict):
        return set()
    # Check platform_disabled.webui first (mirrors agent platform resolution)
    platform_disabled = skills_cfg.get("platform_disabled")
    if isinstance(platform_disabled, dict) and "webui" in platform_disabled:
        return _normalize_disabled_set(platform_disabled["webui"])
    return _normalize_disabled_set(skills_cfg.get("disabled"))


def _parse_config_string_list(value) -> list:
    """Decode a config value that may hold a JSON-array string into a list.

    ``hermes config set`` (and JSON-mode editor saves) store lists as quoted
    JSON strings (``'[\"a\",\"b\"]'`` or the Python-literal ``\"['a']\"``), so a
    disabled list read from ``config.yaml`` can arrive as a single string
    instead of a YAML list. Treating it as one literal name makes the Skills
    panel show every skill as enabled and makes the toggle write a destructive
    single-entry list (hermes-webui#7120).

    Reuses ``agent.skill_utils.parse_config_string_list`` (hermes-agent #86661
    fix) when the bundled agent source is importable, and mirrors its logic
    otherwise so the two surfaces cannot drift. A scalar string still means one
    name.
    """
    try:
        from agent.skill_utils import parse_config_string_list

        return parse_config_string_list(value)
    except ImportError:
        pass

    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                parsed = None
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    return []


def _normalize_disabled_set(values) -> set:
    """Normalize a YAML disabled list into a set of stripped strings."""
    if values is None:
        return set()
    if isinstance(values, str):
        values = _parse_config_string_list(values)
    return {str(v).strip() for v in values if str(v).strip()}


MAX_DESCRIPTION_LENGTH = 300
_EXCLUDED_SKILL_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea"}


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content or not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    frontmatter_raw = parts[1]
    body = parts[2]
    data = {}
    try:
        import yaml
        data = yaml.safe_load(frontmatter_raw) or {}
    except Exception:
        logger.debug("Silent exception in _parse_frontmatter", exc_info=True)
        for line in frontmatter_raw.strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip().strip("'\"")
    return data if isinstance(data, dict) else {}, body


def _parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def skill_matches_platform(frontmatter: dict) -> bool:
    platforms = frontmatter.get("platforms") or frontmatter.get("platform")
    if not platforms:
        return True
    if isinstance(platforms, str):
        platforms = [p.strip().lower() for p in platforms.split(",")]
    elif isinstance(platforms, list):
        platforms = [str(p).strip().lower() for p in platforms]
    current = "macos" if sys.platform == "darwin" else ("windows" if sys.platform == "win32" else "linux")
    return current in platforms or "all" in platforms or sys.platform in platforms


def _sort_skills(skills: list[dict]) -> list[dict]:
    return sorted(skills, key=lambda s: s.get("name", "").lower())


def iter_skill_index_files(scan_dir: Path, filename: str = "SKILL.md"):
    if not scan_dir.exists():
        return
    try:
        for path in scan_dir.rglob(filename):
            if not any(part in _EXCLUDED_SKILL_DIRS for part in path.parts):
                yield path
    except Exception:
        logger.debug("Silent exception in iter_skill_index_files", exc_info=True)
        pass


def _skills_list_from_dir(skills_dir: Path, category: str | None = None) -> dict:
    """List skills across all search directories."""
    all_skills = []
    seen_names: set[str] = set()
    disabled = _get_disabled_skill_names_for_profile()
    search_dirs = _active_skill_search_dirs(skills_dir)

    for scan_dir in search_dirs:
        for skill_md in iter_skill_index_files(scan_dir, "SKILL.md"):
            if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                continue
            skill_dir = skill_md.parent
            try:
                content = skill_md.read_text(encoding="utf-8")[:4000]
                frontmatter, body = _parse_frontmatter(content)
                if not skill_matches_platform(frontmatter):
                    continue
                name = frontmatter.get("name", skill_dir.name)[:64]
                if name in seen_names:
                    continue
                description = frontmatter.get("description", "")
                if not description:
                    for line in body.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            description = line
                            break
                if len(description) > MAX_DESCRIPTION_LENGTH:
                    description = description[: MAX_DESCRIPTION_LENGTH - 3] + "..."
                seen_names.add(name)
                all_skills.append(
                    {
                        "name": name,
                        "description": description,
                        "category": _skill_category_from_path(
                            skill_md, search_dirs, local_skills_dir=skills_dir
                        ),
                        "disabled": name in disabled,
                    }
                )
            except (UnicodeDecodeError, PermissionError) as e:
                logger.debug("Failed to read skill file %s: %s", skill_md, e)
            except Exception as e:
                logger.debug(
                    "Skipping skill at %s: failed to parse: %s", skill_md, e, exc_info=True
                )

    if category:
        all_skills = [s for s in all_skills if s.get("category") == category]
    all_skills = _sort_skills(all_skills)
    categories = sorted(set(s.get("category") for s in all_skills if s.get("category")))
    result = {
        "success": True,
        "skills": all_skills,
        "categories": categories,
        "count": len(all_skills),
    }
    if all_skills:
        result["hint"] = "Use skill_view(name) to see full content, tags, and linked files"
    else:
        result["message"] = "No skills found."
    return result


def _find_skill_in_dirs(name: str, skills_dirs: list[Path]) -> tuple[Path | None, Path | None]:
    """Resolve a WebUI skill name inside explicit skills directories."""
    raw_name = str(name or "").strip().strip("/")
    if not raw_name:
        return None, None

    candidate_names = [raw_name]
    if ":" in raw_name:
        namespace, bare = raw_name.split(":", 1)
        if namespace and bare:
            candidate_names.append(f"{namespace}/{bare}")

    for skills_dir in skills_dirs:
        if not skills_dir.exists():
            continue
        for candidate_name in candidate_names:
            direct_path = skills_dir / candidate_name
            if not _skill_path_within(skills_dir, direct_path):
                continue
            if direct_path.is_dir() and (direct_path / "SKILL.md").exists():
                return direct_path, direct_path / "SKILL.md"
            legacy_md = direct_path.with_suffix(".md")
            if legacy_md.exists() and _skill_path_within(skills_dir, legacy_md):
                return legacy_md.parent, legacy_md

        for skill_md in iter_skill_index_files(skills_dir, "SKILL.md"):
            if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                continue
            skill_dir = skill_md.parent
            if skill_dir.name == raw_name:
                return skill_dir, skill_md
            try:
                frontmatter, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8")[:4000])
                if frontmatter.get("name") == raw_name:
                    return skill_dir, skill_md
            except Exception:
                logger.debug("Silent exception in _find_skill_in_dirs", exc_info=True)
                continue

        for legacy_md in skills_dir.rglob("*.md"):
            if legacy_md.name == "SKILL.md":
                continue
            if legacy_md.stem == raw_name and _skill_path_within(skills_dir, legacy_md):
                return legacy_md.parent, legacy_md
    return None, None


def _find_skill_in_dir(name: str, skills_dir: Path) -> tuple[Path | None, Path | None]:
    """Resolve a WebUI skill name inside an explicit skills directory."""
    return _find_skill_in_dirs(name, [skills_dir])


def _skill_not_found_payload(name: str, skills_dir: Path) -> dict:
    available = [s["name"] for s in _skills_list_from_dir(skills_dir).get("skills", [])[:20]]
    return {
        "success": False,
        "error": f"Skill '{name}' not found.",
        "available_skills": available,
        "hint": "Use skills_list to see all available skills",
    }


def _linked_files_for_skill(skill_dir: Path | None) -> dict:
    if not skill_dir or not (skill_dir / "SKILL.md").exists():
        return {}
    linked_files: dict[str, list[str]] = {}

    references_dir = skill_dir / "references"
    if references_dir.exists():
        refs = [str(f.relative_to(skill_dir)) for f in references_dir.glob("*.md")]
        if refs:
            linked_files["references"] = sorted(refs)

    templates_dir = skill_dir / "templates"
    if templates_dir.exists():
        templates = []
        for ext in ["*.md", "*.py", "*.yaml", "*.yml", "*.json", "*.tex", "*.sh"]:
            templates.extend(str(f.relative_to(skill_dir)) for f in templates_dir.rglob(ext))
        if templates:
            linked_files["templates"] = sorted(set(templates))

    assets_dir = skill_dir / "assets"
    if assets_dir.exists():
        assets = [str(f.relative_to(skill_dir)) for f in assets_dir.rglob("*") if f.is_file()]
        if assets:
            linked_files["assets"] = sorted(assets)

    scripts_dir = skill_dir / "scripts"
    if scripts_dir.exists():
        scripts = []
        for ext in ["*.py", "*.sh", "*.bash", "*.js", "*.ts", "*.rb"]:
            scripts.extend(str(f.relative_to(skill_dir)) for f in scripts_dir.glob(ext))
        if scripts:
            linked_files["scripts"] = sorted(set(scripts))

    return linked_files


def _skill_view_from_file(skill_dir: Path | None, skill_md: Path) -> dict:
    content = skill_md.read_text(encoding="utf-8")
    frontmatter, _body = _parse_frontmatter(content)
    if not skill_matches_platform(frontmatter):
        return {"success": False, "error": "Skill is not available on this platform."}

    metadata = frontmatter.get("metadata")
    hermes_meta = metadata.get("hermes", {}) if isinstance(metadata, dict) else {}
    tags = _parse_tags(hermes_meta.get("tags") or frontmatter.get("tags", ""))
    related_skills = _parse_tags(
        hermes_meta.get("related_skills") or frontmatter.get("related_skills", "")
    )
    try:
        path = str(skill_md.relative_to((skill_dir or skill_md.parent).parent))
    except ValueError:
        path = str(skill_md)

    return {
        "success": True,
        "name": frontmatter.get("name", skill_md.stem if not skill_dir else skill_dir.name),
        "description": frontmatter.get("description", ""),
        "tags": tags,
        "related_skills": related_skills,
        "content": content,
        "path": path,
        "skill_dir": str(skill_dir) if skill_dir else None,
        "linked_files": _linked_files_for_skill(skill_dir),
    }


def _skill_view_from_active_dir(name: str) -> dict:
    skills_dir = _active_skills_dir()
    search_dirs = _active_skill_search_dirs(skills_dir)
    skill_dir, skill_md = _find_skill_in_dirs(name, search_dirs)
    if not skill_md:
        return _skill_not_found_payload(name, skills_dir)
    return _skill_view_from_file(skill_dir, skill_md)


def _handle_skill_save(handler, body):
    try:
        require(body, "name", "content")
    except ValueError as e:
        return bad(handler, str(e))
    skill_name = body["name"].strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        return bad(handler, "Invalid skill name")
    category = body.get("category", "").strip()
    if category and ("/" in category or ".." in category):
        return bad(handler, "Invalid category")
    skills_dir = _active_skills_dir()

    if category:
        skill_dir = skills_dir / category / skill_name
    else:
        skill_dir = skills_dir / skill_name
    # Validate resolved path stays within the active profile skills dir.
    try:
        skill_dir.resolve().relative_to(skills_dir.resolve())
    except ValueError:
        return bad(handler, "Invalid skill path")
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    if skill_file.is_symlink():
        return bad(handler, "Cannot save to a symlinked skill file")
    skill_file.write_text(body["content"], encoding="utf-8")
    _SKILLS_STATS_CACHE.clear()
    return j(handler, {"ok": True, "name": skill_name, "path": str(skill_file)})


def _handle_skill_delete(handler, body):
    try:
        require(body, "name")
    except ValueError as e:
        return bad(handler, str(e))

    skill_name = str(body["name"]).strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        return bad(handler, "Invalid skill name")
    skills_dir = _active_skills_dir()
    matches = [p for p in skills_dir.rglob("SKILL.md") if p.parent.name == skill_name]
    if not matches:
        return bad(handler, "Skill not found", 404)
    skill_dir = matches[0].parent
    shutil.rmtree(str(skill_dir))
    _SKILLS_STATS_CACHE.clear()
    return j(handler, {"ok": True, "name": body["name"]})


def _normalize_names_list(names) -> list[str]:
    """Normalize a config value (None/str/list) into a deduplicated str list."""
    if names is None:
        return []
    if isinstance(names, str):
        names = _parse_config_string_list(names)
    elif not isinstance(names, list):
        names = list(names) if names else []
    return list(dict.fromkeys(str(d).strip() for d in names if str(d).strip()))


def _toggle_name_in_list(names, name: str, enabled: bool) -> list[str]:
    """Add or remove *name* from *names*, returning a new list."""
    names = _normalize_names_list(names)
    if enabled:
        return [d for d in names if d != name]
    if name not in names:
        names.append(name)
    return names


def _handle_skill_toggle(handler, body):
    """Toggle a skill's enabled/disabled state in the active profile's config.yaml.

    Writes through to ``skills.platform_disabled.webui`` when that key exists
    so the toggle takes effect for WebUI sessions (the agent's
    ``get_disabled_skill_names`` checks platform-specific lists first when
    ``HERMES_SESSION_PLATFORM`` is set).
    """
    try:
        require(body, "name", "enabled")
    except ValueError as e:
        return bad(handler, str(e))

    name = body["name"].strip()
    enabled = bool(body["enabled"])

    # Validate the skill exists in the filesystem
    skills_dir = _active_skills_dir()
    search_dirs = _active_skill_search_dirs(skills_dir)
    skill_dir, skill_md = _find_skill_in_dirs(name, search_dirs)
    if not skill_md:
        return bad(handler, f"Skill '{name}' not found", 404)

    config_path = _active_profile_config_path()
    with _cfg_lock:
        cfg = _load_yaml_config_file(config_path)

        # Ensure skills section exists as a dict
        if "skills" not in cfg or not isinstance(cfg["skills"], dict):
            cfg["skills"] = {}
        skills_cfg = cfg["skills"]

        # Always update the global disabled list
        skills_cfg["disabled"] = _toggle_name_in_list(
            skills_cfg.get("disabled"), name, enabled
        )

        # Write-through to platform_disabled.webui if it exists so that the
        # toggle takes effect for WebUI sessions (the agent checks the
        # platform-specific list first when HERMES_SESSION_PLATFORM=webui).
        platform_disabled = skills_cfg.get("platform_disabled")
        if isinstance(platform_disabled, dict) and "webui" in platform_disabled:
            platform_disabled["webui"] = _toggle_name_in_list(
                platform_disabled["webui"], name, enabled
            )

        cfg["skills"] = skills_cfg
        _save_yaml_config_file(config_path, cfg)

    reload_config()  # outside with block — reload_config() acquires the lock itself
    _SKILLS_STATS_CACHE.clear()
    return j(handler, {"ok": True, "name": name, "enabled": enabled})


# ── Cron System State, Helpers & Handlers (Sprint M4.2b) ─────────────────────

_RUNNING_CRON_JOBS: dict[str, float] = {}  # job_id → start_timestamp
_RUNNING_CRON_LOCK = threading.Lock()
_CRON_CREATE_SNAPSHOT_LOCK = threading.Lock()
_CRON_OUTPUT_CONTENT_LIMIT = 8000
_CRON_OUTPUT_HEADER_CONTEXT = 200

_AGENT_CRON_IMPORT_PATH_LOCK = threading.Lock()
_AGENT_CRON_IMPORT_PATH_READY: str | None = None


def _normalize_cron_job_ids(job_ids) -> list[str]:
    seen = set()
    normalized = []
    for job_id in job_ids or []:
        jid = str(job_id or "").strip()
        if not jid or jid in seen:
            continue
        seen.add(jid)
        normalized.append(jid)
    return normalized


def _latest_cron_session_info_for_jobs(
    job_ids, completed_job_ids=None
) -> dict[str, dict[str, int | str | None]]:
    """Return newest persisted cron session info keyed by completed cron job id."""
    normalized = _normalize_cron_job_ids(job_ids)
    requested = _normalize_cron_job_ids(completed_job_ids if completed_job_ids is not None else job_ids)
    if not requested:
        return {}
    if not normalized:
        return {jid: {"session_id": "", "message_count": None} for jid in requested}
    db_path = _active_state_db_path()
    if not db_path or not Path(db_path).exists():
        return {jid: {"session_id": "", "message_count": None} for jid in requested}
    try:
        with webui_session_db.open_db_readonly(db_path) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(sessions)")
            session_cols = {row[1] for row in cur.fetchall()}
            if "id" not in session_cols or "source" not in session_cols:
                return {jid: {"session_id": "", "message_count": None} for jid in requested}
            select_message_count = (
                "s.message_count AS message_count"
                if "message_count" in session_cols
                else "NULL AS message_count"
            )
            if "started_at" in session_cols:
                query = f"""
                    SELECT s.id,
                           {select_message_count}
                    FROM sessions s
                    WHERE LOWER(COALESCE(s.source, '')) = 'cron'
                    ORDER BY COALESCE(s.started_at, 0) DESC, s.id DESC  -- newest start, not last activity
                """
            else:
                query = f"""
                    SELECT s.id,
                           {select_message_count}
                    FROM sessions s
                    WHERE LOWER(COALESCE(s.source, '')) = 'cron'
                    ORDER BY s.id DESC
                """
            cur.execute(query)
            results = {
                jid: {"session_id": "", "message_count": None} for jid in requested
            }
            requested_ids = set(requested)
            prefixes = {jid: f"cron_{jid}_" for jid in normalized}
            for row in cur.fetchall():
                sid = str(row["id"] or "")
                if not sid:
                    continue
                matches = [
                    jid
                    for jid in normalized
                    if sid.startswith(prefixes[jid])
                ]
                if matches:
                    jid = max(matches, key=len)
                    if jid not in requested_ids or results[jid]["session_id"]:
                        continue
                    results[jid] = {
                        "session_id": sid,
                        "message_count": (
                            int(row["message_count"])
                            if row["message_count"] is not None
                            else None
                        ),
                    }
                if all(info["session_id"] for info in results.values()):
                    break
            return results
    except sqlite3.Error:
        return {jid: {"session_id": "", "message_count": None} for jid in requested}


def _mark_cron_running(job_id: str):
    with _RUNNING_CRON_LOCK:
        _RUNNING_CRON_JOBS[job_id] = time.time()


def _mark_cron_done(job_id: str):
    with _RUNNING_CRON_LOCK:
        _RUNNING_CRON_JOBS.pop(job_id, None)


def _is_cron_running(job_id: str) -> tuple[bool, float]:
    """Return (is_running, elapsed_seconds)."""
    with _RUNNING_CRON_LOCK:
        t = _RUNNING_CRON_JOBS.get(job_id)
        if t is None:
            return False, 0.0
        return True, time.time() - t


def _cron_response_marker_index(text: str) -> int:
    """Return the start index of a markdown Response heading, if present."""
    candidates = []
    for heading in ("## Response", "# Response"):
        if text.startswith(heading):
            candidates.append(0)
        idx = text.find(f"\n{heading}")
        if idx >= 0:
            candidates.append(idx + 1)
    return min(candidates) if candidates else -1


def _cron_output_content_window(text: str, limit: int = _CRON_OUTPUT_CONTENT_LIMIT) -> str:
    """Return a bounded cron output window that preserves useful response text.

    Cron output files can contain large skill dumps in the Prompt section. The
    UI already extracts ``## Response`` when present, so keep that section in
    the API payload instead of blindly returning the first ``limit`` chars.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text

    response_idx = _cron_response_marker_index(text)
    if response_idx >= 0:
        header = text[:min(_CRON_OUTPUT_HEADER_CONTEXT, response_idx)].rstrip()
        response = text[response_idx:].lstrip("\n")
        content = f"{header}\n...\n{response}" if header else response
        return content[:limit]

    return text[-limit:]


def _cron_job_for_api(job: dict) -> dict:
    """Return a cron job payload with optional UI settings normalized.

    Legacy jobs intentionally persist without ``profile`` so they keep the
    scheduler's server-default behavior. The API still returns ``profile: None``
    so the UI can label that state explicitly instead of guessing.

    ``toast_notifications`` is a WebUI preference for completion toasts. Legacy
    jobs default to enabled so existing behavior is preserved unless a job is
    explicitly muted.
    """
    payload = dict(job or {})
    payload.setdefault("profile", None)
    payload["toast_notifications"] = payload.get("toast_notifications") is not False
    return payload


def _cron_jobs_for_api(jobs) -> list[dict]:
    return [_cron_job_for_api(job) for job in (jobs or [])]


def _ensure_agent_cron_import_path() -> None:
    """Prefer the agent's cron package over unrelated top-level cron packages."""
    try:
        from api import config as api_config
    except Exception:
        logger.debug("Silent exception in _ensure_agent_cron_import_path", exc_info=True)
        return

    agent_dir = getattr(api_config, "_AGENT_DIR", None)
    if not agent_dir:
        return
    agent_path = str(Path(agent_dir).expanduser().resolve())
    agent_cron_path = str(Path(agent_path) / "cron")

    global _AGENT_CRON_IMPORT_PATH_READY
    with _AGENT_CRON_IMPORT_PATH_LOCK:
        cron_mod = sys.modules.get("cron")
        cron_file = str(getattr(cron_mod, "__file__", "") or "") if cron_mod else ""
        cron_is_agent = bool(cron_mod is not None and cron_file.startswith(agent_cron_path + os.sep))
        if _AGENT_CRON_IMPORT_PATH_READY == agent_path and (cron_mod is None or cron_is_agent):
            return

        while agent_path in sys.path:
            sys.path.remove(agent_path)
        shadow_indexes = [
            idx
            for idx, path_entry in enumerate(sys.path)
            if path_entry
            and Path(path_entry).resolve() != Path(agent_path)
            and (Path(path_entry) / "cron" / "__init__.py").exists()
        ]
        if shadow_indexes:
            sys.path.insert(min(shadow_indexes), agent_path)
        else:
            sys.path.append(agent_path)
        _AGENT_CRON_IMPORT_PATH_READY = agent_path

        # Keep in-memory test doubles or namespace stubs intact; only evict a
        # real on-disk shadow package so the agent's cron package can import.
        if cron_mod is not None and cron_file and not cron_is_agent:
            for name in list(sys.modules):
                if name == "cron" or name.startswith("cron."):
                    sys.modules.pop(name, None)


def _cron_jobs_cross_profile(active_profile: str) -> tuple[list[dict], list[dict]]:
    """Return active-profile rows plus foreign rows for the Tasks panel.

    Row ownership is intentionally distinct from a cron job's persisted
    ``profile`` field. The persisted field controls where the job executes;
    ``owner_profile`` tells the UI which profile home the row came from.
    """
    from cron.jobs import list_jobs
    from api.profiles import (
        cron_profile_context_for_home,
        get_agy_home_for_profile,
        list_profiles_api,
    )

    def _home_key(path: Path) -> str:
        try:
            return str(Path(path).expanduser().resolve(strict=False))
        except Exception:
            logger.debug("Silent exception in _home_key", exc_info=True)
            return str(Path(path).expanduser())

    names: list[str] = []
    seen_names: set[str] = set()

    def _add_name(raw_name) -> None:
        name = str(raw_name or "").strip()
        if not name:
            return
        folded = name.casefold()
        if folded in seen_names:
            return
        seen_names.add(folded)
        names.append(name)

    _add_name(active_profile)
    for row in list_profiles_api():
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        if row.get("visible") is False and not _profiles_match(name, active_profile):
            continue
        _add_name(name)

    active_jobs: list[dict] = []
    other_jobs: list[dict] = []
    seen_homes: set[str] = set()
    for owner_profile in names:
        home = Path(get_agy_home_for_profile(owner_profile))
        home_key = _home_key(home)
        if home_key in seen_homes:
            continue
        seen_homes.add(home_key)
        is_active = _profiles_match(owner_profile, active_profile)
        try:
            with cron_profile_context_for_home(home):
                jobs = _cron_jobs_for_api(list_jobs(include_disabled=True))
        except Exception:
            logger.debug("Silent exception in _cron_jobs_cross_profile", exc_info=True)
            if not is_active:
                continue
            raise
        for job in jobs:
            row = dict(job)
            row["owner_profile"] = owner_profile
            row["read_only"] = not is_active
            if is_active:
                active_jobs.append(row)
            else:
                other_jobs.append(row)
    return active_jobs, other_jobs


def _available_cron_profile_names() -> set[str]:
    from api.profiles import list_profiles_api

    names = {"default"}
    for profile in list_profiles_api():
        try:
            name = str(profile.get("name") or "").strip()
        except AttributeError:
            continue
        if name:
            names.add(name)
    return names


def _normalize_cron_profile_value(value) -> str | None:
    if value is None:
        return None
    profile = str(value).strip()
    if not profile:
        return None
    if profile not in _available_cron_profile_names():
        raise ValueError(f"Unknown profile: {profile}")
    return profile


def _profile_home_for_cron_job(job: dict):
    """Resolve the execution profile for a cron job, with graceful fallback.

    A missing/blank profile preserves legacy server-default behavior. If a job
    points at a profile that was deleted after save, fall back to the active
    server profile and log a warning instead of crashing the Run Now path.
    """
    from api.profiles import get_active_agy_home, get_agy_home_for_profile

    raw = str((job or {}).get("profile") or "").strip()
    if not raw:
        return get_active_agy_home()
    if raw not in _available_cron_profile_names():
        logger.warning(
            "Cron job %s references missing profile %r; falling back to server default",
            (job or {}).get("id", "?"), raw,
        )
        return get_active_agy_home()
    return get_agy_home_for_profile(raw)


def _event_profile_for_cron_job(job: dict) -> str | None:
    """Return the profile identity browsers should refresh for a manual cron run."""
    raw = str((job or {}).get("profile") or "").strip()
    if not raw:
        return None
    if raw not in _available_cron_profile_names():
        return None
    return raw


def _cron_job_subprocess_main(job, execution_profile_home, result_queue):
    """Run one cron job inside a child process pinned to a profile home."""
    try:
        def _run():
            from cron.scheduler import run_job

            return run_job(job)

        if execution_profile_home is None:
            result = _run()
        else:
            from api.profiles import cron_profile_context_for_home

            with cron_profile_context_for_home(execution_profile_home):
                result = _run()
        result_queue.put(("ok", result))
    except BaseException as exc:  # pragma: no cover - surfaced in parent
        import traceback

        result_queue.put(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))


def _cron_subprocess_result_timeout_seconds(job):
    """Return how long the manual-run parent waits for child result payloads."""
    for key in ("timeout_seconds", "max_runtime_seconds", "timeout"):
        raw = (job or {}).get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return max(60.0, value + 30.0)
    # Manual cron jobs can legitimately run for a long time.  Keep a recovery
    # path for wedged children without truncating normal long-running jobs.
    return 6 * 60 * 60.0


def _run_cron_job_in_profile_subprocess(job, execution_profile_home):
    """Execute cron.scheduler.run_job without holding the parent cron env lock.

    cron.scheduler/cron.jobs still rely on process-global HERMES_HOME and module
    constants, so running the job body in a child process gives each long cron
    execution its own globals. The parent process only uses cron_profile_context
    for short metadata reads/writes and remains responsive to unrelated cron UI
    and API calls while the job runs.
    """
    import multiprocessing
    import queue

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_cron_job_subprocess_main,
        args=(job, execution_profile_home, result_queue),
    )
    process.start()

    result_timeout = _cron_subprocess_result_timeout_seconds(job)
    status = "error"
    payload = ["cron run subprocess failed before producing a result", ""]
    try:
        try:
            # Drain the potentially large pickled result before joining.  If the
            # child puts >~64 KiB on a multiprocessing.Queue, joining first can
            # deadlock while the child's feeder thread waits for the parent to
            # read from the pipe.
            status, *payload = result_queue.get(timeout=result_timeout)
        except queue.Empty:
            status = "error"
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                payload = [
                    f"cron run subprocess produced no result within {result_timeout:g}s and was terminated",
                    "",
                ]
            else:
                payload = [
                    f"cron run subprocess exited with code {process.exitcode} without producing a result",
                    "",
                ]
        finally:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if status == "ok":
                    status = "error"
                    payload = [
                        "cron run subprocess did not exit after returning a result",
                        "",
                    ]
    finally:
        result_queue.close()
        result_queue.join_thread()

    if status == "ok":
        return payload[0]

    message = payload[0]
    traceback_text = payload[1] if len(payload) > 1 else ""
    if traceback_text:
        logger.error("Manual cron subprocess failed:\n%s", traceback_text)
    raise RuntimeError(message)


def _run_cron_tracked(
    job,
    profile_home=None,
    execution_profile_home=None,
    event_profile=None,
):
    """Wrapper that tracks running state around cron.scheduler.run_job.

    ``profile_home`` is the cron store that owns the job row/output metadata.
    ``execution_profile_home`` is the selected per-job profile used to load
    agent config/.env while running. When no job profile is selected, both homes
    are the same and legacy server-default behavior is preserved.
    """
    import importlib

    from cron.jobs import mark_job_run, save_job_output

    _cron_scheduler = importlib.import_module("cron.scheduler")

    _silent_marker = getattr(_cron_scheduler, "SILENT_MARKER", "[SILENT]")
    _deliver_result = getattr(_cron_scheduler, "_deliver_result", None)

    job_id = job.get("id", "")
    execution_profile_home = execution_profile_home or profile_home

    def _with_cron_home(home, fn):
        if home is None:
            return fn()
        from api.profiles import cron_profile_context_for_home

        with cron_profile_context_for_home(home):
            return fn()

    try:
        success, output, final_response, error = _run_cron_job_in_profile_subprocess(
            job, execution_profile_home
        )

        # Persist output, deliver the same content the scheduled cron path would
        # send, and write run metadata back to the job's owning cron store even
        # when the selected execution profile is different.
        def _persist_success():
            save_job_output(job_id, output)

            deliver_content = (
                final_response
                if success
                else f"⚠️ Cron job '{job.get('name', job_id)}' failed:\n{error}"
            )
            should_deliver = bool(deliver_content)
            if should_deliver and success and _silent_marker in deliver_content.strip().upper():
                should_deliver = False

            delivery_error = None
            if should_deliver and _deliver_result is not None:
                try:
                    delivery_error = _deliver_result(job, deliver_content)
                except Exception as de:
                    delivery_error = str(de)
                    logger.error("Delivery failed for manual cron job %s: %s", job_id, de)

            # Match the scheduled cron path: an apparently successful run with no
            # final response should not leave the job looking healthy.
            _success, _error = success, error
            if _success and not final_response:
                _success = False
                _error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

            try:
                mark_job_run(job_id, _success, _error, delivery_error=delivery_error)
            except TypeError:
                # Older/fake cron.jobs modules used by focused WebUI tests may
                # not expose the newer delivery_error parameter. Real Hermes
                # scheduler builds do, so this is only a compatibility shim for
                # legacy test doubles and deployments.
                mark_job_run(job_id, _success, _error)

        _with_cron_home(profile_home, _persist_success)
    except Exception as e:
        logger.exception("Manual cron run failed for job %s", job_id)
        try:
            _with_cron_home(profile_home, lambda: mark_job_run(job_id, False, str(e)))  # noqa: F821  e is bound by the enclosing `except ... as e` and the lambda runs synchronously here
        except Exception:
            logger.debug("Failed to mark manual cron run failure for %s", job_id)
    finally:
        _mark_cron_done(job_id)
        try:
            from api.routes import _publish_session_list_changed

            _publish_session_list_changed("cron_complete", profile=event_profile)
        except Exception:
            logger.debug("Failed to publish session list changed for cron completion", exc_info=True)


def _cron_output_usage_metadata(text: str) -> dict:
    """Extract optional token/cost metadata from a cron output markdown file."""
    head = text.split("## Response", 1)[0].split("# Response", 1)[0]
    usage: dict = {}

    def _intish(value: str):
        cleaned = re.sub(r"[^0-9]", "", value or "")
        return int(cleaned) if cleaned else None

    def _floatish(value: str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", (value or "").replace(",", ""))
        return float(match.group(0)) if match else None

    for raw_line in head.splitlines():
        line = raw_line.strip()
        model_match = re.match(r"\*\*(?:Model|Model Used):\*\*\s*(.+)$", line, re.I)
        if model_match:
            usage["model"] = model_match.group(1).strip()
            continue
        provider_match = re.match(r"\*\*Provider:\*\*\s*(.+)$", line, re.I)
        if provider_match:
            usage["provider"] = provider_match.group(1).strip()
            continue
        cost_match = re.match(r"\*\*(?:Estimated cost|Cost):\*\*\s*(.+)$", line, re.I)
        if cost_match:
            cost = _floatish(cost_match.group(1))
            if cost is not None:
                usage["estimated_cost_usd"] = cost
            continue
        duration_match = re.match(r"\*\*(?:Duration|Elapsed):\*\*\s*(.+)$", line, re.I)
        if duration_match:
            seconds = _floatish(duration_match.group(1))
            if seconds is not None:
                usage["duration_seconds"] = seconds
            continue
        tokens_match = re.match(r"\*\*Tokens:\*\*\s*(.+)$", line, re.I)
        if tokens_match:
            value = tokens_match.group(1)
            input_match = re.search(r"([0-9][0-9,]*)\s*(?:input|in)\b", value, re.I)
            output_match = re.search(r"([0-9][0-9,]*)\s*(?:output|out)\b", value, re.I)
            total_match = re.search(r"([0-9][0-9,]*)\s*(?:total\s*)?tokens?\b", value, re.I)
            if input_match:
                usage["input_tokens"] = _intish(input_match.group(1))
            if output_match:
                usage["output_tokens"] = _intish(output_match.group(1))
            if total_match and "total_tokens" not in usage:
                usage["total_tokens"] = _intish(total_match.group(1))

    if "total_tokens" not in usage:
        total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens"))
        if total:
            usage["total_tokens"] = total
    return usage


def _cron_output_snippet(text: str, limit: int = 600) -> str:
    """Extract the response body from a cron output .md file for preview."""
    lines = text.split("\n")
    response_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("## Response") or line.startswith("# Response"):
            response_idx = i
            break
    body = ("\n".join(lines[response_idx + 1:]) if response_idx >= 0 else "\n".join(lines)).strip()
    return body[:limit] or "(empty)"


def _handle_cron_history(handler, parsed):
    """List cron run output files with metadata (no content)."""
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    if not job_id:
        return j(handler, {"error": "job_id required"}, status=400)
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}", job_id) or job_id in (".", ".."):
        return j(handler, {"error": "invalid job_id"}, status=400)
    try:
        offset = max(0, int(qs.get("offset", ["0"])[0]))
        limit = max(1, min(500, int(qs.get("limit", ["50"])[0])))
    except (ValueError, TypeError):
        return j(handler, {"error": "offset and limit must be integers"}, status=400)
    out_dir = CRON_OUT / job_id
    runs = []
    total = 0
    if out_dir.exists():
        all_files = sorted(out_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        total = len(all_files)
        page = all_files[offset:offset + limit]
        for f in page:
            try:
                st = f.stat()
                usage = _cron_output_usage_metadata(
                    f.read_text(encoding="utf-8", errors="replace")
                )
                runs.append({
                    "filename": f.name,
                    "size": st.st_size,
                    "modified": st.st_mtime,
                    "usage": usage,
                })
            except OSError:
                logger.debug("Failed to stat cron output file %s", f)
    return j(handler, {"job_id": job_id, "runs": runs, "total": total, "offset": offset})


def _handle_cron_run_detail(handler, parsed):
    """Return full content of a single cron run output file."""
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    filename = qs.get("filename", [""])[0]
    if not job_id or not filename:
        return j(handler, {"error": "job_id and filename required"}, status=400)
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}", job_id) or job_id in (".", ".."):
        return j(handler, {"error": "invalid job_id"}, status=400)
    fpath = (CRON_OUT / job_id / filename).resolve()
    if not fpath.is_relative_to(CRON_OUT.resolve()):
        return j(handler, {"error": "invalid filename"}, status=400)
    if not fpath.exists():
        return j(handler, {"error": "file not found"}, status=404)
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        snippet = _cron_output_snippet(content)
        usage = _cron_output_usage_metadata(content)
        return j(handler, {"job_id": job_id, "filename": filename,
                           "content": content, "snippet": snippet,
                           "usage": usage})
    except Exception as e:
        logger.warning("Silent exception in _handle_cron_run_detail", exc_info=True)
        return j(handler, {"error": str(e)}, status=500)


def _handle_cron_output(handler, parsed):
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    if not job_id:
        return j(handler, {"error": "job_id required"}, status=400)
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}", job_id):
        return j(handler, {"error": "invalid job_id"}, status=400)
    try:
        limit = max(1, min(500, int(qs.get("limit", ["5"])[0])))
    except (ValueError, TypeError):
        limit = 5
    out_dir = CRON_OUT / job_id
    outputs = []
    if out_dir.exists():
        files = sorted(out_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        for f in files:
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                outputs.append({"filename": f.name, "content": _cron_output_content_window(txt)})
            except Exception:
                logger.debug("Failed to read cron output file %s", f)
    return j(handler, {"job_id": job_id, "outputs": outputs})


def _handle_cron_status(handler, parsed):
    """Return running status for one or all cron jobs."""
    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    if job_id:
        running, elapsed = _is_cron_running(job_id)
        return j(handler, {"job_id": job_id, "running": running, "elapsed": round(elapsed, 1)})
    with _RUNNING_CRON_LOCK:
        all_running = {jid: round(time.time() - t, 1) for jid, t in _RUNNING_CRON_JOBS.items()}
    return j(handler, {"running": all_running})


def _handle_cron_recent(handler, parsed):
    """Return cron jobs that have completed since a given timestamp."""
    qs = parse_qs(parsed.query)
    try:
        since = float(qs.get("since", ["0"])[0])
    except (ValueError, TypeError):
        since = 0.0
    try:
        from cron.jobs import list_jobs

        jobs = list_jobs(include_disabled=True)
        completions = []
        for job in jobs:
            job_id = str(job.get("id", "") or "")
            last_run = job.get("last_run_at")
            if not last_run:
                continue
            if isinstance(last_run, str):
                try:
                    ts = datetime.datetime.fromisoformat(
                        last_run.replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                ts = float(last_run)
            if ts > since:
                completions.append(
                    {
                        "job_id": job_id,
                        "name": job.get("name", "Unknown"),
                        "status": job.get("last_status", "unknown"),
                        "completed_at": ts,
                        "toast_notifications": job.get("toast_notifications") is not False,
                    }
                )
        latest_session_info = _latest_cron_session_info_for_jobs(
            [job.get("id", "") for job in jobs],
            [c["job_id"] for c in completions],
        )
        for completion in completions:
            info = latest_session_info.get(str(completion.get("job_id", "") or ""), {})
            completion["session_id"] = str(info.get("session_id", "") or "")
            if info.get("message_count") is not None:
                completion["message_count"] = int(info["message_count"])
        return j(handler, {"completions": completions, "since": since})
    except ImportError:
        return j(handler, {"completions": [], "since": since})


def _selected_profile_snapshot_updates(
    profile: str | None,
    *,
    provider,
    model,
) -> dict[str, str | None]:
    selected_profile = str(profile or "").strip()
    if not selected_profile or (provider is not None and model is not None):
        return {}

    try:
        from api.profiles import profile_env_for_background_worker
        from cron.jobs import _compute_provider_model_snapshots
    except Exception:
        logger.warning(
            "Selected-profile cron snapshot repair unavailable; saving ambient snapshots",
            exc_info=True,
        )
        return {}

    try:
        with _CRON_CREATE_SNAPSHOT_LOCK:
            with profile_env_for_background_worker(
                selected_profile,
                "cron create snapshot",
                logger_override=logger,
            ):
                provider_snapshot, model_snapshot = _compute_provider_model_snapshots(
                    provider=provider,
                    model=model,
                    base_url=None,
                    no_agent=False,
                )
    except Exception:
        logger.warning(
            "Selected-profile cron snapshot repair failed for %s; saving ambient snapshots",
            selected_profile,
            exc_info=True,
        )
        return {}

    updates = {}
    if provider is None:
        updates["provider_snapshot"] = provider_snapshot
    if model is None:
        updates["model_snapshot"] = model_snapshot
    return updates


def _handle_cron_create(handler, body):
    try:
        require(body, "prompt", "schedule")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from cron.jobs import create_job, update_job

        profile = _normalize_cron_profile_value(body.get("profile"))
        toast_notifications = body.get("toast_notifications") is not False
        requested_model = body.get("model") or None
        requested_provider = body.get("provider") or None
        job = create_job(
            prompt=body["prompt"],
            schedule=body["schedule"],
            name=body.get("name") or None,
            deliver=body.get("deliver") or "local",
            skills=body.get("skills") or [],
            model=requested_model,
            provider=requested_provider,
        )
        post_create_updates = {}
        if profile is not None:
            post_create_updates["profile"] = profile
            post_create_updates.update(
                _selected_profile_snapshot_updates(
                    profile,
                    provider=requested_provider,
                    model=requested_model,
                )
            )
        if not toast_notifications:
            post_create_updates["toast_notifications"] = False
        if post_create_updates:
            job = update_job(job["id"], post_create_updates) or job
        return j(handler, {"ok": True, "job": _cron_job_for_api(job)})
    except Exception as e:
        logger.warning("Silent exception in _handle_cron_create", exc_info=True)
        return j(handler, {"error": str(e)}, status=400)


def _handle_cron_delivery_options(handler):
    """Return available delivery platforms for cron jobs."""
    try:
        from cron.scheduler import _KNOWN_DELIVERY_PLATFORMS
    except Exception:
        logger.warning("Silent exception in _handle_cron_delivery_options", exc_info=True)
        _KNOWN_DELIVERY_PLATFORMS = frozenset()
    platforms = [
        {"value": "local", "label": "Local (save output only)"},
        {"value": "origin", "label": "Origin (reply to creator)"}
    ]
    for name in sorted(_KNOWN_DELIVERY_PLATFORMS):
        platforms.append({"value": name, "label": name.capitalize()})
    return j(handler, {"platforms": platforms})


def _handle_cron_update(handler, body):
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import update_job

    try:
        updates = {}
        for k, v in body.items():
            if k == "job_id":
                continue
            if k == "profile":
                updates[k] = _normalize_cron_profile_value(v)
            elif k in ("model", "provider"):
                updates[k] = v if v else None
            elif v is not None:
                updates[k] = v
    except ValueError as e:
        return bad(handler, str(e))
    job = update_job(body["job_id"], updates)
    if not job:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job": _cron_job_for_api(job)})


def _handle_cron_delete(handler, body):
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import remove_job

    ok = remove_job(body["job_id"])
    if not ok:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job_id": body["job_id"]})


def _handle_cron_run(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import get_job

    job = get_job(job_id)
    if not job:
        return bad(handler, "Job not found", 404)
    already_running, elapsed = _is_cron_running(job_id)
    if already_running:
        return j(handler, {"ok": False, "job_id": job_id, "status": "already_running",
                            "elapsed": round(elapsed, 1)})
    _mark_cron_running(job_id)
    from api.profiles import get_active_agy_home

    _profile_home = get_active_agy_home()
    _execution_profile_home = _profile_home_for_cron_job(job)
    _event_profile = _event_profile_for_cron_job(job)
    threading.Thread(target=_run_cron_tracked, args=(job, _profile_home, _execution_profile_home, _event_profile), daemon=True).start()
    return j(handler, {"ok": True, "job_id": job_id, "status": "running"})


def _handle_cron_pause(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import pause_job

    result = pause_job(job_id, reason=body.get("reason"))
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)


def _handle_cron_resume(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import resume_job

    result = resume_job(job_id)
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)


# ── Memory & Project Context Handlers & Helpers (Sprint M4.2b) ───────────────

_PROJECT_CONTEXT_HERMES_NAMES = (".hermes.md", "HERMES.md")
_PROJECT_CONTEXT_CWD_NAMES = (
    "AGENTS.md",
    "agents.md",
    "CLAUDE.md",
    "claude.md",
    ".cursorrules",
)
_PROJECT_CONTEXT_CURSOR_RULES_GLOB = ".cursor/rules/*.mdc"
_PROJECT_CONTEXT_MAX_BYTES = 20_000


def _strip_project_context_frontmatter(content: str) -> str:
    """Strip a leading YAML frontmatter block, mirroring the agent's loader."""
    if not content.startswith("---"):
        return content
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            return "".join(lines[idx + 1:]).lstrip("\n")
    return content


def _project_context_git_root(start: Path) -> Path | None:
    """Return the nearest git root for project context discovery."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _project_context_candidates(workspace: Path) -> list[Path]:
    """Mirror the agent's first-match project context file priority."""
    cwd = workspace.resolve()
    candidates: list[Path] = []
    git_root = _project_context_git_root(cwd)
    stop_at = git_root if git_root is not None else cwd

    for directory in [cwd, *cwd.parents]:
        for name in _PROJECT_CONTEXT_HERMES_NAMES:
            candidates.append(directory / name)
        if directory == stop_at:
            break

    for name in _PROJECT_CONTEXT_CWD_NAMES:
        candidates.append(cwd / name)

    try:
        candidates.extend(sorted(cwd.glob(_PROJECT_CONTEXT_CURSOR_RULES_GLOB)))
    except OSError:
        pass

    return candidates


def _memory_project_context_workspace(parsed) -> Path | None:
    qs = parse_qs(parsed.query or "") if parsed is not None else {}
    sid = qs.get("session_id", [""])[0]
    if sid:
        try:
            ws = (get_session(sid).workspace or "").strip()
            if not ws:
                return None
            return Path(ws).expanduser().resolve()
        except Exception:
            logger.debug("Silent exception in _memory_project_context_workspace", exc_info=True)
            return None

    raw_workspace = qs.get("workspace", [""])[0] or os.environ.get("TERMINAL_CWD", "") or get_last_workspace()
    if not raw_workspace:
        return None
    try:
        return Path(resolve_trusted_workspace(raw_workspace)).expanduser().resolve()
    except Exception:
        logger.debug("Skipping project context for untrusted workspace %s", raw_workspace, exc_info=True)
        return None


def _read_active_project_context(workspace: Path | None) -> dict:
    payload = {
        "content": "",
        "path": "",
        "mtime": None,
        "workspace": str(workspace) if workspace else "",
        "shadowed": [],
    }
    if not workspace:
        return payload
    try:
        if not workspace.exists() or not workspace.is_dir():
            return payload
    except OSError:
        return payload

    seen: set[str] = set()
    readable: list[dict] = []
    for candidate in _project_context_candidates(workspace):
        try:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            key = os.path.normcase(str(resolved)).casefold()
            if key in seen:
                continue
            seen.add(key)
            content = resolved.read_text(encoding="utf-8", errors="replace")
            content = _strip_project_context_frontmatter(content)
            if len(content) > _PROJECT_CONTEXT_MAX_BYTES:
                content = content[:_PROJECT_CONTEXT_MAX_BYTES]
            if not content.strip():
                continue
            readable.append(
                {
                    "name": resolved.name,
                    "path": str(resolved),
                    "content": content,
                    "mtime": resolved.stat().st_mtime,
                }
            )
        except Exception:
            logger.debug("Could not read project context candidate %s", candidate, exc_info=True)

    if not readable:
        return payload

    active = readable[0]
    payload.update(
        {
            "content": active["content"],
            "path": active["path"],
            "mtime": active["mtime"],
            "name": active["name"],
            "shadowed": [
                {
                    "name": item["name"],
                    "path": item["path"],
                    "mtime": item["mtime"],
                    "shadowed_by": active["name"],
                    "shadowed_by_path": active["path"],
                }
                for item in readable[1:]
            ],
        }
    )
    return payload


def _handle_memory_read(handler, parsed=None):
    try:
        from api.profiles import get_active_agy_home

        home = get_active_agy_home()
        mem_dir = home / "memories"
    except ImportError:
        home = Path.home() / ".hermes"
        mem_dir = home / "memories"

    cfg = get_config_snapshot()
    mem = cfg.get("memory") if isinstance(cfg, dict) else None
    mem_cfg = mem if isinstance(mem, dict) else {}
    memory_enabled = _webui_truthy(mem_cfg.get("memory_enabled", True))
    user_profile_enabled = _webui_truthy(mem_cfg.get("user_profile_enabled", True))

    ws_mem = Path(get_last_workspace()) / "MEMORY.md"
    mem_file = ws_mem if ws_mem.exists() else (mem_dir / "MEMORY.md" if memory_enabled else None)
    user_file = mem_dir / "USER.md" if user_profile_enabled else None
    soul_file = home / "SOUL.md"
    memory = (
        mem_file.read_text(encoding="utf-8", errors="replace")
        if mem_file and mem_file.exists()
        else ""
    )
    user = (
        user_file.read_text(encoding="utf-8", errors="replace")
        if user_file and user_file.exists()
        else ""
    )
    soul = (
        soul_file.read_text(encoding="utf-8", errors="replace")
        if soul_file.exists()
        else ""
    )
    ws_root = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
    if not ws_root.exists():
        ws_root = Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()

    gemini_file = ws_root / "GEMINI.md"
    gemini_rules = gemini_file.read_text(encoding="utf-8", errors="replace") if gemini_file.exists() else ""

    container_rules_file = ws_root / ".gemini" / "rules" / "container_confinement.md"
    container_rules = container_rules_file.read_text(encoding="utf-8", errors="replace") if container_rules_file.exists() else ""

    project_context = _read_active_project_context(_memory_project_context_workspace(parsed))
    return j(
        handler,
        {
            "memory": _redact_text(memory),
            "user": _redact_text(user),
            "soul": _redact_text(soul),
            "gemini_rules": _redact_text(gemini_rules),
            "container_rules": _redact_text(container_rules),
            "project_context": _redact_text(project_context["content"]),
            "memory_path": str(mem_file) if mem_file else "",
            "user_path": str(user_file) if user_file else "",
            "soul_path": str(soul_file),
            "gemini_rules_path": str(gemini_file),
            "container_rules_path": str(container_rules_file),
            "project_context_path": project_context["path"],
            "project_context_name": project_context.get("name", ""),
            "project_context_workspace": project_context["workspace"],
            "memory_mtime": mem_file.stat().st_mtime if mem_file and mem_file.exists() else None,
            "user_mtime": user_file.stat().st_mtime if user_file and user_file.exists() else None,
            "soul_mtime": soul_file.stat().st_mtime if soul_file.exists() else None,
            "gemini_rules_mtime": gemini_file.stat().st_mtime if gemini_file.exists() else None,
            "container_rules_mtime": container_rules_file.stat().st_mtime if container_rules_file.exists() else None,
            "project_context_mtime": project_context["mtime"],
            "project_context_shadowed": project_context["shadowed"],
            "external_notes_enabled": _external_notes_sources_enabled(cfg),
        },
    )


def _handle_memory_write(handler, body):
    try:
        require(body, "section", "content")
    except ValueError as e:
        return bad(handler, str(e))
    section = body["section"]

    cfg = get_config_snapshot()
    mem = cfg.get("memory") if isinstance(cfg, dict) else None
    mem_cfg = mem if isinstance(mem, dict) else {}
    if section == "memory":
        if not _webui_truthy(mem_cfg.get("memory_enabled", True)):
            return bad(handler, "Memory is disabled by configuration (memory_enabled: false)", 403)
    elif section == "user":
        if not _webui_truthy(mem_cfg.get("user_profile_enabled", True)):
            return bad(handler, "User profile is disabled by configuration (user_profile_enabled: false)", 403)

    try:
        from api.profiles import get_active_agy_home

        home = get_active_agy_home()
        mem_dir = home / "memories"
    except ImportError:
        home = Path.home() / ".hermes"
        mem_dir = home / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    if section == "memory":
        ws_mem = Path(get_last_workspace()) / "MEMORY.md"
        target = ws_mem if ws_mem.exists() else (mem_dir / "MEMORY.md")
    elif section == "user":
        target = mem_dir / "USER.md"
    elif section == "soul":
        target = home / "SOUL.md"
    elif section == "gemini_rules":
        ws_root = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
        if not ws_root.exists():
            ws_root = Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()
        target = ws_root / "GEMINI.md"
    else:
        return bad(handler, 'section must be "gemini_rules", "memory", "user", or "soul"')
    if target.is_symlink():
        return bad(handler, "Cannot write to a symlinked memory file")
    try:
        target.write_text(body["content"], encoding="utf-8")
        ws_root = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
        if not ws_root.exists():
            ws_root = Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()
        rules_dir = ws_root / ".gemini" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        if section == "soul":
            (rules_dir / "agent_soul.md").write_text(
                f"# Agent Persona & Identity (Soul)\n\n{body['content']}\n", encoding="utf-8"
            )
        elif section == "user":
            (rules_dir / "user_profile.md").write_text(
                f"# User Profile & Preferences\n\n{body['content']}\n", encoding="utf-8"
            )
        elif section == "memory":
            (rules_dir / "memory.md").write_text(
                f"# Persistent Memory & Project Context\n\n{body['content']}\n", encoding="utf-8"
            )
    except OSError as exc:
        if not isinstance(exc, PermissionError) and getattr(exc, "errno", None) != errno.EROFS:
            raise
        mode_hint = ""
        try:
            mode_hint = f" (mode {target.stat().st_mode & 0o777:o})"
        except OSError:
            pass
        return bad(
            handler,
            (
                f"{target.name} is not writable{mode_hint}: {target}. "
                "Run chmod 644 on the file or fix ownership on the shared volume."
            ),
            403,
        )
    return j(handler, {"ok": True, "section": section, "path": str(target)})



