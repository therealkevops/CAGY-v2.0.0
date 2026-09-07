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


if __name__ == "__main__":
    unittest.main()
