"""Commands, crons, skills, MCP hub, subagents, vault, and plugins routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import html
import logging
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.config import load_settings
from api.helpers import bad, j
from api.profiles import _is_isolated_profile_mode
from api.request_diagnostics import RequestDiagnostics
from api.route_config_models import _dashboard_plugin_enabled
from api.workspace import get_profile_default_workspace

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
        _routes._ensure_agent_cron_import_path()
        active_profile = _routes._get_active_profile_name() or "default"
        try:
            active_jobs, other_jobs = _routes._cron_jobs_cross_profile(active_profile)
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
            _routes._ensure_agent_cron_import_path()
            return _routes._handle_cron_output(handler, parsed)

    if parsed.path == "/api/crons/history":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _routes._ensure_agent_cron_import_path()
            return _routes._handle_cron_history(handler, parsed)

    if parsed.path == "/api/crons/run":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _routes._ensure_agent_cron_import_path()
            return _routes._handle_cron_run_detail(handler, parsed)

    if parsed.path == "/api/crons/recent":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _routes._ensure_agent_cron_import_path()
            return _routes._handle_cron_recent(handler, parsed)

    if parsed.path == "/api/crons/status":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _routes._handle_cron_status(handler, parsed)

    if parsed.path == "/api/crons/delivery-options":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            _routes._ensure_agent_cron_import_path()
            return _routes._handle_cron_delivery_options(handler)

    if parsed.path == "/api/skills":
        qs = parse_qs(parsed.query)
        category = qs.get("category", [None])[0]
        data = _routes._skills_list_from_dir(_routes._active_skills_dir(), category=category)
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

        raw = read_skill_usage(_routes._active_skills_dir())
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
        skills_data = _routes._skills_list_from_dir(_routes._active_skills_dir()).get("skills", [])
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
            skills_dir = _routes._active_skills_dir()
            skill_dir, _skill_md = _routes._find_skill_in_dirs(
                name, _routes._active_skill_search_dirs(skills_dir)
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
        data = _routes._skill_view_from_active_dir(name)
        if not isinstance(data.get("linked_files"), dict):
            data["linked_files"] = {}
        return j(handler, data)

    if parsed.path == "/api/memory":
        return _routes._handle_memory_read(handler, parsed)

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
        return _routes._handle_mcp_servers_list(handler)

    if parsed.path in ("/api/mcp/hub", "/api/mcp/hub/data"):
        from api.mcp_hub import list_mcp_hub_data

        return j(handler, list_mcp_hub_data())

    if parsed.path == "/api/mcp/tools":
        return _routes._handle_mcp_tools_list(handler)

    if parsed.path in ("/api/subagents", "/api/subagents/list"):
        return _routes._handle_subagents_list(handler, parsed)
    if parsed.path == "/api/subagents/sessions":
        from api.subagents import discover_all_swarm_sessions

        return j(handler, {"ok": True, "sessions": discover_all_swarm_sessions()})
    if parsed.path.startswith("/api/subagents/") and "transcript" in parsed.path:
        return _routes._handle_subagent_transcript(handler, parsed)
    if parsed.path == "/api/subagents/detail":
        return _routes._handle_subagent_transcript(handler, parsed)
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
        return _routes._handle_artifacts_list(handler, parsed)
    if parsed.path == "/api/artifacts/content":
        return _routes._handle_artifact_content(handler, parsed)
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

    _ensure_agent_cron_import_path = _routes._ensure_agent_cron_import_path
    _handle_cron_create = _routes._handle_cron_create
    _handle_cron_update = _routes._handle_cron_update
    _handle_cron_delete = _routes._handle_cron_delete
    _handle_cron_run = _routes._handle_cron_run
    _handle_cron_pause = _routes._handle_cron_pause
    _handle_cron_resume = _routes._handle_cron_resume
    _sanitize_error = _routes._sanitize_error
    _handle_skill_save = _routes._handle_skill_save
    _handle_skill_delete = _routes._handle_skill_delete
    _handle_skill_toggle = _routes._handle_skill_toggle
    _handle_memory_write = _routes._handle_memory_write
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

