"""
Hermetic test suite verifying the efficacy, isolation, context compression,
and active learning of the Antigravity Second Brain (Knowledge Vault).
"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api.vault import (
    sync_vault_to_rules,
    memorize_insight,
    get_next_adr_number,
    infer_space_from_workspace,
    scan_vault,
    lint_vault,
    heal_vault,
    rename_note,
    get_note,
    get_vault_health,
    weave_wikilinks,
    digest_note,
    sync_deepmode_rule,
    is_deepmode_enabled,
    DEEPMODE_RULES_CONTENT,
)


class TestSecondBrainEfficacy(unittest.TestCase):
    """Rigorous behavioral and architectural benchmarks for the Second Brain engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(dir=Path.home())
        self.workspace_dir = Path(self.temp_dir) / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.vault_dir = self.workspace_dir / "knowledge"
        self.vault_dir.mkdir(parents=True, exist_ok=True)

        # 1. Global User Layer
        user_dir = self.vault_dir / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "profile.md").write_text(
            "# User Profile\n\n"
            "- Role: Lead Systems Architect\n"
            "- Principles: Strict typing, comprehensive error boundaries, automated tests.\n"
        )
        (user_dir / "conventions.md").write_text(
            "# Engineering Conventions\n\n"
            "1. Conventional commits format.\n"
            "2. Zero plain text secrets in repository.\n"
        )

        # 2. Space A: cka-kb (Kubernetes)
        self.space_a_dir = self.vault_dir / "spaces" / "cka-kb"
        (self.space_a_dir / "decisions").mkdir(parents=True, exist_ok=True)
        (self.space_a_dir / "architecture").mkdir(parents=True, exist_ok=True)
        (self.space_a_dir / "decisions" / "adr_001_etcd_backup.md").write_text(
            "# ADR-001: Automate ETCD Snapshot Backups\n\n"
            "## Status\nAccepted\n\n"
            "## Context\nControl plane high availability.\n\n"
            "## Decision\nRun scheduled etcdctl snapshot saves to S3 before major updates.\n"
        )
        (self.space_a_dir / "decisions" / "adr_002_containerd_runtime.md").write_text(
            "# ADR-002: Containerd Runtime Migration\n\n"
            "## Status\nAccepted\n\n"
            "## Decision\nMigrate all worker nodes from Dockershim to containerd.\n"
        )

        # 3. Space B: cloud-telemetry (FastAPI & Structlog)
        self.space_b_dir = self.vault_dir / "spaces" / "cloud-telemetry"
        (self.space_b_dir / "decisions").mkdir(parents=True, exist_ok=True)
        (self.space_b_dir / "architecture").mkdir(parents=True, exist_ok=True)
        (self.space_b_dir / "decisions" / "adr_001_fastapi_framework.md").write_text(
            "# ADR-001: Adopt FastAPI and Pydantic v2\n\n"
            "## Status\nAccepted\n\n"
            "## Decision\nUse FastAPI with Pydantic v2 for high-throughput async processing.\n"
        )
        (self.space_b_dir / "decisions" / "adr_002_structured_json_logging.md").write_text(
            "# ADR-002: Structured JSON Logging with Structlog\n\n"
            "## Status\nAccepted\n\n"
            "## Decision\nMandate structlog JSON lines to stdout. Do not use print().\n"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cross_space_isolation_and_zero_contamination(self):
        """Verify that rule compilation for Space A strictly excludes Space B and vice-versa."""
        # Compile rules for Space A (cka-kb)
        res_a = sync_vault_to_rules(self.vault_dir, self.workspace_dir, space="cka-kb")
        self.assertTrue(res_a["ok"])
        rule_content_a = (self.workspace_dir / ".gemini" / "rules" / "knowledge_vault.md").read_text()

        # Space A must contain its own ADRs and global user profile
        self.assertIn("Lead Systems Architect", rule_content_a)
        self.assertIn("Conventional commits format", rule_content_a)
        self.assertIn("ADR-001: Automate ETCD Snapshot Backups", rule_content_a)
        self.assertIn("Containerd Runtime Migration", rule_content_a)

        # Space A must have ZERO references to Space B
        self.assertNotIn("FastAPI", rule_content_a)
        self.assertNotIn("Pydantic", rule_content_a)
        self.assertNotIn("structlog", rule_content_a)

        # Compile rules for Space B (cloud-telemetry)
        res_b = sync_vault_to_rules(self.vault_dir, self.workspace_dir, space="cloud-telemetry")
        self.assertTrue(res_b["ok"])
        rule_content_b = (self.workspace_dir / ".gemini" / "rules" / "knowledge_vault.md").read_text()

        # Space B must contain its own ADRs and global user profile
        self.assertIn("Lead Systems Architect", rule_content_b)
        self.assertIn("ADR-001: Adopt FastAPI and Pydantic v2", rule_content_b)
        self.assertIn("Structured JSON Logging with Structlog", rule_content_b)

        # Space B must have ZERO references to Space A
        self.assertNotIn("etcd", rule_content_b)
        self.assertNotIn("Dockershim", rule_content_b)
        self.assertNotIn("containerd", rule_content_b)

    def test_dynamic_space_inference_from_workspace_path(self):
        """Verify that workspace paths correctly resolve their respective vault spaces."""
        ws_cka = self.workspace_dir / "projects" / "cka-kb"
        ws_telemetry = self.workspace_dir / "projects" / "cloud-telemetry"
        ws_root = self.workspace_dir

        self.assertEqual(infer_space_from_workspace(ws_cka), "cka-kb")
        self.assertEqual(infer_space_from_workspace(ws_telemetry), "cloud-telemetry")
        self.assertEqual(infer_space_from_workspace(ws_root), "global")

    def test_autonomous_sequential_adr_numbering_per_space(self):
        """Verify ADR numbering increments autonomously within each space container."""
        # Both spaces currently have adr_001 and adr_002
        next_a = get_next_adr_number(self.vault_dir, space="cka-kb")
        next_b = get_next_adr_number(self.vault_dir, space="cloud-telemetry")

        self.assertEqual(next_a, 3)
        self.assertEqual(next_b, 3)

        # Add adr_003 to space A
        (self.space_a_dir / "decisions" / "adr_003_flannel_cni.md").write_text("# ADR-003: Flannel CNI\n")

        # Verify Space A is now 4 while Space B remains 3
        self.assertEqual(get_next_adr_number(self.vault_dir, space="cka-kb"), 4)
        self.assertEqual(get_next_adr_number(self.vault_dir, space="cloud-telemetry"), 3)

    def test_active_learning_memorize_and_instant_rule_sync(self):
        """Verify /memorize categorizes, saves sequentially, and re-compiles rules immediately."""
        insight = "Adopt Redis 7 as the distributed caching layer for rate limiting with 60-second TTL"
        res = memorize_insight(
            self.vault_dir,
            insight,
            category="decisions",
            workspace_path=self.workspace_dir,
            space="cloud-telemetry",
        )

        self.assertTrue(res["ok"])
        self.assertEqual(res["category"], "decisions")
        self.assertEqual(res["space"], "cloud-telemetry")
        self.assertIn("adr_003", res["path"])
        self.assertTrue(res["rules_synced"])

        # Check file exists in space B (res['rel_path'] is relative to vault_dir)
        saved_file = self.vault_dir / res["rel_path"]
        self.assertTrue(saved_file.exists())
        self.assertIn("Redis 7", saved_file.read_text())

        # Check that rules were re-compiled and include the new insight
        rules_content = (self.workspace_dir / ".gemini" / "rules" / "knowledge_vault.md").read_text()
        self.assertIn("Redis 7", rules_content)
        self.assertIn("ADR 003", rules_content)

    def test_tiered_context_diet_compression_engine(self):
        """Verify that vaults exceeding the 20 KB limit trigger high-density Matrix distillation."""
        # Synthesize multiple large ADRs in space B to exceed 20 KB
        for i in range(4, 15):
            large_body = (
                f"# ADR-{i:03d}: Comprehensive Architectural Subsystem Specification {i}\n\n"
                f"## Status\nAccepted\n\n"
                f"## Context\n"
                f"Detailed design evaluation across trade-offs, network topologies, memory allocation, "
                f"and database shard architectures for high availability in region us-east-1. " * 15 + "\n\n"
                f"## Decision\n"
                f"Implement subsystem {i} with asynchronous event queues and dead-letter topics.\n\n"
                f"## Consequences\n"
                f"Guaranteed delivery at the expense of slight tail latency under heavy spikes.\n"
            )
            (self.space_b_dir / "decisions" / f"adr_{i:03d}_subsystem_{i}.md").write_text(large_body)

        # Compile rules with diet limit of 15 KB (15360 bytes)
        res = sync_vault_to_rules(self.vault_dir, self.workspace_dir, space="cloud-telemetry", max_full_bytes=15360)
        self.assertTrue(res["ok"])
        self.assertTrue(res["diet_mode"], "Diet mode must activate when space bytes exceed limit")

        rule_content = (self.workspace_dir / ".gemini" / "rules" / "knowledge_vault.md").read_text()

        # 1. User profile must be preserved verbatim
        self.assertIn("User Profile", rule_content)
        self.assertIn("Lead Systems Architect", rule_content)

        # 2. Callout alert must inform agent of diet mode
        self.assertIn("Tiered Context Diet Active", rule_content)

        # 3. High-density decisions matrix must be rendered
        self.assertIn("Architectural Decisions Matrix", rule_content)
        self.assertIn("| ADR | Title | Status | Summary & Key Decision |", rule_content)

        # 4. Total rule bytes should be heavily compressed compared to raw vault content
        raw_space_bytes = sum(f.stat().st_size for f in (self.space_b_dir / "decisions").glob("*.md"))
        compiled_rule_bytes = len(rule_content.encode("utf-8"))
        compression_ratio = raw_space_bytes / compiled_rule_bytes

        self.assertGreater(compression_ratio, 2.0, f"Compression ratio {compression_ratio:.2f}x should be > 2x")

    def test_self_healing_link_refactoring_and_linter(self):
        """Verify that linting discovers issues and refactoring updates wikilinks on rename."""
        # Create note with a link to adr_001
        guide_note = self.space_b_dir / "architecture" / "pipeline.md"
        guide_note.write_text(
            "# Ingestion Pipeline\n\n"
            "See [[spaces/cloud-telemetry/decisions/adr_001_fastapi_framework|ADR-001]] for framework choice.\n"
        )

        # Rename adr_001 to adr_001_fastapi_core.md
        old_rel = "spaces/cloud-telemetry/decisions/adr_001_fastapi_framework.md"
        new_rel = "spaces/cloud-telemetry/decisions/adr_001_fastapi_core.md"

        rename_res = rename_note(self.vault_dir, old_rel, new_rel)
        self.assertTrue(rename_res["ok"])

        # Verify link in pipeline.md was automatically refactored
        updated_content = guide_note.read_text()
        self.assertIn("adr_001_fastapi_core", updated_content)
        self.assertNotIn("adr_001_fastapi_framework", updated_content)

        # Run linter across vault
        lint_res = lint_vault(self.vault_dir)
        self.assertTrue(lint_res["ok"])
        self.assertEqual(lint_res["broken_wikilinks_count"], 0)

    def test_conversational_recall_search_and_metadata(self):
        """Verify conversational recall search across spaces, scoring, snippets, and slash command bindings."""
        from api.vault import search_vault

        # 1. Search for 'fastapi' in vault - should match ADR-001 in space B
        search_res = search_vault(self.vault_dir, query="fastapi")
        self.assertTrue(search_res["ok"])
        self.assertEqual(search_res["total_matches"], 1)
        match = search_res["results"][0]
        self.assertIn("fastapi", match["id"])
        self.assertEqual(match["space"], "cloud-telemetry")
        self.assertGreater(len(match["snippets"]), 0)
        self.assertTrue(any("<mark>" in s["highlighted"].lower() for s in match["snippets"]))

        # 2. Search for 'etcd' in vault - should match ADR-001 in space A
        search_etcd = search_vault(self.vault_dir, query="etcd")
        self.assertTrue(search_etcd["ok"])
        self.assertEqual(search_etcd["total_matches"], 1)
        self.assertEqual(search_etcd["results"][0]["space"], "cka-kb")

        # 3. Verify slash command and palette bindings in static assets
        commands_js = (WEBUI_DIR / "static" / "commands.js").read_text(encoding="utf-8")
        slash_palette_js = (WEBUI_DIR / "static" / "slash_palette.js").read_text(encoding="utf-8")

        self.assertIn("name:'recall'", commands_js)
        self.assertIn("fn:cmdRecall", commands_js)
        self.assertIn("async function cmdRecall", commands_js)
        self.assertIn("function insertVaultNoteIntoComposer", commands_js)
        self.assertIn("cmd: '/recall'", slash_palette_js)
        self.assertIn("Recall from Knowledge Vault", slash_palette_js)

    def test_obsidian_quick_switcher_and_editor_hotkeys(self):
        """Verify Obsidian Quick Switcher modal, keyboard navigation, and Markdown Cmd+S editor hotkeys."""
        index_html = (WEBUI_DIR / "static" / "index.html").read_text(encoding="utf-8")
        vault_js = (WEBUI_DIR / "static" / "vault.js").read_text(encoding="utf-8")
        workspace_js = (WEBUI_DIR / "static" / "workspace.js").read_text(encoding="utf-8")
        ui_js = (WEBUI_DIR / "static" / "ui.js").read_text(encoding="utf-8")
        messages_js = (WEBUI_DIR / "static" / "messages.js").read_text(encoding="utf-8")

        # 1. Quick Switcher Modal in HTML
        self.assertIn('id="vaultQuickSwitcherModal"', index_html)
        self.assertIn('id="vaultQuickSwitcherInput"', index_html)
        self.assertIn('id="vaultQuickSwitcherList"', index_html)
        self.assertIn('openVaultQuickSwitcher()', index_html)

        # 2. Quick Switcher Functions and Cmd+O in vault.js
        self.assertIn("function openVaultQuickSwitcher", vault_js)
        self.assertIn("function closeVaultQuickSwitcher", vault_js)
        self.assertIn("function filterVaultQuickSwitcher", vault_js)
        self.assertIn("handleVaultQuickSwitcherKeydown", vault_js)
        self.assertIn("e.key === 'o' || e.key === 'O'", vault_js)

        # 3. Markdown Editor Cmd+S and Escape hotkeys in workspace.js
        self.assertIn("function handlePreviewEditKeydown", workspace_js)
        self.assertIn("e.key === 's' || e.key === 'S'", workspace_js)
        self.assertIn("e.key === 'Escape'", workspace_js)

        # 4. Vault protocol handling in ui.js and messages.js
        self.assertIn("vault:\\/\\/", ui_js)
        self.assertIn("vault-insert", ui_js)
        self.assertIn("a[href^=\"#vault=\"]", ui_js)
        self.assertIn("a[href^=\"#vault-insert=\"]", ui_js)
        self.assertIn("vault:\\/\\/", messages_js)
        self.assertIn("vault-insert", messages_js)

    def test_space_scoped_vault_health_and_gap_analysis(self):
        """Verify that get_vault_health accurately partitions gaps, stubs, and orphans per space."""
        # 1. Create a note in cka-kb referencing missing stubs: 'flannel_cni' and 'coredns_config'
        (self.space_a_dir / "architecture" / "networking.md").write_text(
            "# Cluster Networking\n\n"
            "Networking requires [[spaces/cka-kb/notes/flannel_cni|Flannel]] overlay and "
            "[[spaces/cka-kb/notes/coredns_config|CoreDNS]] service discovery.\n"
            "Also integrates with [[spaces/cka-kb/decisions/adr_001_etcd_backup|ETCD Backup]].\n"
        )

        # 2. Create an orphan note in cka-kb with 0 incoming or outgoing links
        (self.space_a_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.space_a_dir / "notes" / "orphan_troubleshooting.md").write_text(
            "# Kubernetes Troubleshooting Guide\n\n"
            "General runbook for debugging crashlooping pods without any links.\n"
        )

        # 3. Create a missing stub in cloud-telemetry space to verify space boundary filtering
        (self.space_b_dir / "architecture" / "metrics.md").write_text(
            "# Metrics Collector\n\n"
            "Pushes metrics to [[spaces/cloud-telemetry/notes/clickhouse_sink|ClickHouse]].\n"
        )

        # Run health check scoped to cka-kb
        health_a = get_vault_health(self.vault_dir, space="cka-kb")
        self.assertTrue(health_a["ok"])
        unresolved_a = [u["target"] for u in health_a["unresolved_links"]]
        self.assertIn("spaces/cka-kb/notes/flannel_cni", unresolved_a)
        self.assertIn("spaces/cka-kb/notes/coredns_config", unresolved_a)
        # Verify cross-space stub is NOT leaked
        self.assertNotIn("spaces/cloud-telemetry/notes/clickhouse_sink", unresolved_a)

        orphans_a = [o["path"] for o in health_a["orphans"]]
        self.assertTrue(any("orphan_troubleshooting.md" in p for p in orphans_a))

        # Run health check for cloud-telemetry
        health_b = get_vault_health(self.vault_dir, space="cloud-telemetry")
        self.assertTrue(health_b["ok"])
        unresolved_b = [u["target"] for u in health_b["unresolved_links"]]
        self.assertIn("spaces/cloud-telemetry/notes/clickhouse_sink", unresolved_b)
        self.assertNotIn("spaces/cka-kb/notes/flannel_cni", unresolved_b)

    def test_auto_wikilink_weaving_and_digest_engine(self):
        """Verify atomic note digest, markdown syntax protection, and turn-0 rule sync."""
        raw_text = (
            "### Pod Disruption Budgets in Production\n\n"
            "Pod Disruption Budgets ensure minimum replica availability during voluntary disruptions. "
            "They coordinate with etcd backups to guarantee high availability.\n\n"
            "```bash\n# Fenced code block should not be modified: etcd snapshot save\netcdctl snapshot save /tmp/backup.db\n```\n\n"
            "Inline code like `etcdctl` or `fastapi` must stay clean.\n"
            "Existing link [[spaces/cka-kb/decisions/adr_001_etcd_backup|etcd]] must not be double-linked.\n"
        )

        # 1. Test weave_wikilinks in isolation
        woven = weave_wikilinks(self.vault_dir, raw_text, space="cka-kb")
        self.assertTrue(woven["ok"])
        woven_content = woven["content"]

        # Code blocks and inline spans must remain protected
        self.assertIn("`etcdctl`", woven_content)
        self.assertIn("etcdctl snapshot save /tmp/backup.db", woven_content)
        # Prose reference to etcd backups should be woven
        self.assertIn("[[spaces/cka-kb/decisions/adr_001_etcd_backup|", woven_content)
        # Should not link fastapi when scoped to cka-kb
        self.assertNotIn("[[spaces/cloud-telemetry/", woven_content)

        # 2. Test digest_note end-to-end with active turn-0 rule sync
        digest_res = digest_note(
            self.vault_dir,
            raw_text,
            space="cka-kb",
            title="Pod Disruption Budgets",
            category="notes",
            workspace_path=self.workspace_dir
        )
        self.assertTrue(digest_res["ok"])
        self.assertEqual(digest_res["title"], "Pod Disruption Budgets")
        self.assertEqual(digest_res["space"], "cka-kb")
        self.assertEqual(digest_res["category"], "notes")
        self.assertIn("pod_disruption_budgets.md", digest_res["path"])

        saved_file = self.vault_dir / digest_res["path"]
        self.assertTrue(saved_file.exists())
        saved_text = saved_file.read_text(encoding="utf-8")
        self.assertIn("# Pod Disruption Budgets", saved_text)
        self.assertIn("#cka-kb", saved_text)
        self.assertIn("#notes", saved_text)

        # Verify instant turn-0 rule sync
        rules_content = (self.workspace_dir / ".gemini" / "rules" / "knowledge_vault.md").read_text()
        self.assertIn("Pod Disruption Budgets", rules_content)

    def test_gaps_and_digest_slash_commands_and_protocols(self):
        """Verify /gaps, /digest slash commands, action badge links, and CSS styles."""
        commands_js = (WEBUI_DIR / "static" / "commands.js").read_text(encoding="utf-8")
        slash_palette_js = (WEBUI_DIR / "static" / "slash_palette.js").read_text(encoding="utf-8")
        ui_js = (WEBUI_DIR / "static" / "ui.js").read_text(encoding="utf-8")
        messages_js = (WEBUI_DIR / "static" / "messages.js").read_text(encoding="utf-8")
        style_css = (WEBUI_DIR / "static" / "style.css").read_text(encoding="utf-8")
        vault_js = (WEBUI_DIR / "static" / "vault.js").read_text(encoding="utf-8")

        # 1. Commands & Palette registrations
        self.assertIn("name:'gaps'", commands_js)
        self.assertIn("fn:cmdGaps", commands_js)
        self.assertIn("name:'digest'", commands_js)
        self.assertIn("fn:cmdDigest", commands_js)
        self.assertIn("window.cmdGaps = cmdGaps", commands_js)
        self.assertIn("window.cmdDigest = cmdDigest", commands_js)

        self.assertIn("cmd: '/gaps'", slash_palette_js)
        self.assertIn("cmd: '/digest'", slash_palette_js)

        # 2. UI protocols & click listeners
        self.assertIn("vault-create:\\/\\/", ui_js)
        self.assertIn("vault-weave:\\/\\/", ui_js)
        self.assertIn("vault-search:\\/\\/", ui_js)
        self.assertIn("a[href^=\"#vault-create=\"]", ui_js)
        self.assertIn("a[href^=\"#vault-weave=\"]", ui_js)
        self.assertIn("a[href^=\"#vault-search=\"]", ui_js)

        # 3. Message rendering & sanitization
        self.assertIn("vault-create:\\/\\/", messages_js)
        self.assertIn("vault-weave:\\/\\/", messages_js)
        self.assertIn("vault-search:\\/\\/", messages_js)
        self.assertIn("vault-create", messages_js)

        # 4. Vault creation with space
        self.assertIn("initialSpace", vault_js)

        # 5. CSS Action Badges
        self.assertIn("a[href^=\"#vault-create=\"]", style_css)
        self.assertIn("a[href^=\"#vault-weave=\"]", style_css)
        self.assertIn("a[href^=\"#vault-search=\"]", style_css)

    def test_graph_zoom_and_node_centering_mechanics(self):
        """Verify graph zoom centers on highlighted node and viewport instead of top-left corner."""
        vault_js = (WEBUI_DIR / "static" / "vault.js").read_text(encoding="utf-8")

        # 1. Highlighted node lookup & centering helpers
        self.assertIn("function getHighlightedGraphNode()", vault_js)
        self.assertIn("function centerGraphOnNode(nodeOrId)", vault_js)
        self.assertIn("_vaultPan.x = cx - (targetNode.x * _vaultZoom)", vault_js)
        self.assertIn("_vaultPan.y = cy - (targetNode.y * _vaultZoom)", vault_js)

        # 2. highlightVaultGraphNode calls centerGraphOnNode
        self.assertIn("function highlightVaultGraphNode(pathOrId, center = true)", vault_js)
        self.assertIn("centerGraphOnNode(clean)", vault_js)

        # 3. zoomGraph centers on highlighted node or canvas center (not 0, 0)
        self.assertIn("function zoomGraph(factor)", vault_js)
        self.assertIn("_vaultPan.x = focalSx - (highlighted.x * newZoom)", vault_js)
        self.assertIn("_vaultPan.x = cx - (wx * newZoom)", vault_js)

        # 4. Wheel listener centers on highlighted node or mouse pointer (not 0, 0)
        self.assertIn("_vaultPan.x = mouseX - (wx * newZoom)", vault_js)

        # 5. Window exports
        self.assertIn("window.centerGraphOnNode = centerGraphOnNode", vault_js)
        self.assertIn("window.getHighlightedGraphNode = getHighlightedGraphNode", vault_js)
        self.assertIn("window.zoomGraph = zoomGraph", vault_js)
        self.assertIn("window.resetGraphView = resetGraphView", vault_js)

    def test_deepmode_behavioral_engine_and_slash_command(self):
        """Verify Antigravity DeepMode autonomous execution engine, rule synchronization, and slash command integration."""
        # 1. Enable DeepMode and verify rule file creation & environment state
        res_enable = sync_deepmode_rule(self.workspace_dir, enabled=True)
        self.assertTrue(res_enable.get("ok"))
        self.assertTrue(res_enable.get("deepmode"))
        self.assertEqual(res_enable.get("effort"), "high")

        deepmode_file = self.workspace_dir / ".gemini" / "rules" / "deepmode.md"
        self.assertTrue(deepmode_file.exists())
        rule_content = deepmode_file.read_text(encoding="utf-8")

        # Verify the 5 core pillars of Solar/Hermes autonomous discipline
        self.assertIn("Zero Fluff & Fluff-Free Communication", rule_content)
        self.assertIn("Autonomous Bias to Action & Proactive Tool Use", rule_content)
        self.assertIn("Relentless Verification Loop (Test & Prove)", rule_content)
        self.assertIn("No Meta-Apologies or Explanatory Hand-Wringing", rule_content)
        self.assertIn("Code-First Dense Engineering", rule_content)

        # Check status check helper
        self.assertTrue(is_deepmode_enabled(self.workspace_dir))
        self.assertEqual(os.environ.get("AGY_DEEPMODE"), "1")
        self.assertEqual(os.environ.get("AGY_DEFAULT_EFFORT"), "high")

        # 2. Disable DeepMode and verify file cleanup & effort reset
        res_disable = sync_deepmode_rule(self.workspace_dir, enabled=False)
        self.assertTrue(res_disable.get("ok"))
        self.assertFalse(res_disable.get("deepmode"))
        self.assertFalse(deepmode_file.exists())
        self.assertFalse(is_deepmode_enabled(self.workspace_dir))
        self.assertEqual(os.environ.get("AGY_DEEPMODE"), "0")
        self.assertEqual(os.environ.get("AGY_DEFAULT_EFFORT"), "medium")

        # 3. Verify AIAgent prompt interception and high effort defaulting in run_agent.py
        run_agent_src = (WEBUI_DIR / "run_agent.py").read_text(encoding="utf-8")
        self.assertIn('clean_prompt.startswith("/deepmode")', run_agent_src)
        self.assertIn("⚡ DEEPMODE: Execute with Solar/Hermes autonomous discipline", run_agent_src)
        self.assertIn("is_deepmode_enabled(self.workspace)", run_agent_src)
        self.assertIn('effort = "high"', run_agent_src)

        # 4. Verify WebUI slash command palette registration
        palette_js = (WEBUI_DIR / "static" / "slash_palette.js").read_text(encoding="utf-8")
        self.assertIn("cmd: '/deepmode'", palette_js)
        self.assertIn("DeepMode Autonomous Execution", palette_js)
        self.assertIn("category: 'Workflows'", palette_js)

        # 5. Verify commands.js command registration and exports
        commands_js = (WEBUI_DIR / "static" / "commands.js").read_text(encoding="utf-8")
        self.assertIn("name:'deepmode'", commands_js)
        self.assertIn("fn:cmdDeepMode", commands_js)
        self.assertIn("function cmdDeepMode(args)", commands_js)
        self.assertIn("window.cmdDeepMode = cmdDeepMode", commands_js)
        self.assertIn("_handleDeepModeToggle", commands_js)

        # 6. Verify ui.js and messages.js link handlers & safe schemes
        ui_js = (WEBUI_DIR / "static" / "ui.js").read_text(encoding="utf-8")
        self.assertIn('a[href^="#deepmode="]', ui_js)
        self.assertIn("deepmode:\\/\\/", ui_js)
        self.assertIn("#deepmode=", ui_js)

        messages_js = (WEBUI_DIR / "static" / "messages.js").read_text(encoding="utf-8")
        self.assertIn("deepmode:\\/\\/", messages_js)
        self.assertIn("deepmode", messages_js)

        # 7. Verify style.css action chip styling
        style_css = (WEBUI_DIR / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn('a[href^="#deepmode="]', style_css)


if __name__ == "__main__":
    unittest.main()

