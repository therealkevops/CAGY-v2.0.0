"""Tests for Obsidian-style Knowledge Vault & Graph Memory System."""
import os
import sys
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import vault
from server import QuietHTTPServer, Handler


class TestVaultEngine(unittest.TestCase):
    """Test the core vault scanning, wikilink resolution, and rule synchronization engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_dir = Path(self.temp_dir) / "knowledge"
        self.vault_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WORKSPACE_ROOT"] = self.temp_dir
        os.environ["WORKSPACE_DIR"] = self.temp_dir
        self.addCleanup(lambda: os.environ.pop("AGY_WORKSPACE_ROOT", None))
        self.addCleanup(lambda: os.environ.pop("WORKSPACE_DIR", None))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Seed sample notes
        (self.vault_dir / "user").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "architecture").mkdir(parents=True, exist_ok=True)

        (self.vault_dir / "user" / "profile.md").write_text(
            "# User Profile\n\nRole: Lead Engineer\nReferences: [[cagy_unified|CAGY Platform]] and [[conventions]].\n",
            encoding="utf-8"
        )
        (self.vault_dir / "user" / "conventions.md").write_text(
            "# Coding Conventions\n\nRules: Use hermetic unittests.\nSee [[cagy_unified#Container]].\n",
            encoding="utf-8"
        )
        (self.vault_dir / "architecture" / "cagy_unified.md").write_text(
            "# Unified Architecture\n\nRuns in a single container. Adheres to [[conventions]].\n",
            encoding="utf-8"
        )

    def test_extract_wikilinks(self):
        sample = "Check [[Note A]], [[Note B|Alias B]], [[Note C#Section]], and [[Note D#Section|Alias D]]."
        links = vault.extract_wikilinks(sample)
        targets = [l["target"] for l in links]
        self.assertEqual(targets, ["Note A", "Note B", "Note C", "Note D"])
        self.assertEqual(links[1]["alias"], "Alias B")

    def test_extract_tags(self):
        sample = "# Note Title\n\nThis is a note with #infrastructure, #nutanix/nc2 and #docker-debian tags.\n## Heading Two\nNot a tag: # Heading"
        tags = vault.extract_tags(sample)
        self.assertIn("infrastructure", tags)
        self.assertIn("nutanix/nc2", tags)
        self.assertIn("docker-debian", tags)
        self.assertNotIn("heading", tags)

    def test_scan_vault_and_build_graph(self):
        graph = vault.scan_vault(self.vault_dir)
        self.assertEqual(graph["stats"]["total_notes"], 3)
        self.assertGreaterEqual(graph["stats"]["total_edges"], 2)

        node_map = {n["path"]: n for n in graph["nodes"]}
        self.assertIn("user/profile.md", node_map)
        self.assertIn("architecture/cagy_unified.md", node_map)
        self.assertIn("user/conventions.md", node_map)

        profile_node = node_map["user/profile.md"]
        self.assertEqual(profile_node["title"], "User Profile")
        self.assertEqual(profile_node["folder"], "user")

        # CAGY unified should have incoming backlinks from profile and conventions
        cagy_node = node_map["architecture/cagy_unified.md"]
        self.assertIn("user/profile", cagy_node["backlinks"])
        self.assertIn("user/conventions", cagy_node["backlinks"])

    def test_note_crud_lifecycle(self):
        # Read note
        note = vault.get_note(self.vault_dir, "user/profile.md")
        self.assertIsNotNone(note)
        self.assertEqual(note["title"], "User Profile")
        self.assertIn("Lead Engineer", note["content"])

        # Save existing note
        save_res = vault.save_note(self.vault_dir, "user/profile.md", "# User Profile\n\nUpdated role: Principal Architect\n")
        self.assertTrue(save_res["ok"])
        updated = vault.get_note(self.vault_dir, "user/profile.md")
        self.assertIn("Principal Architect", updated["content"])

        # Create new note in subfolder
        create_res = vault.save_note(self.vault_dir, "decisions/adr_002.md", "# ADR 002\n\nLink to [[profile]].\n")
        self.assertTrue(create_res["ok"])
        new_note = vault.get_note(self.vault_dir, "decisions/adr_002.md")
        self.assertEqual(new_note["title"], "ADR 002")

        # Delete note
        del_res = vault.delete_note(self.vault_dir, "decisions/adr_002.md")
        self.assertTrue(del_res["ok"])
        self.assertFalse(vault.get_note(self.vault_dir, "decisions/adr_002.md")["exists"])

    def test_path_traversal_protection(self):
        # Attempt traversal
        res = vault.get_note(self.vault_dir, "../outside.md")
        self.assertFalse(res["ok"])
        save_res = vault.save_note(self.vault_dir, "../outside.md", "Malicious content")
        self.assertFalse(save_res["ok"])
        del_res = vault.delete_note(self.vault_dir, "../outside.md")
        self.assertFalse(del_res["ok"])

    def test_sync_vault_to_rules(self):
        res = vault.sync_vault_to_rules(self.vault_dir, Path(self.temp_dir))
        self.assertTrue(res["ok"])
        rule_file = Path(self.temp_dir) / ".gemini" / "rules" / "knowledge_vault.md"
        self.assertTrue(rule_file.exists())
        content = rule_file.read_text(encoding="utf-8")
        self.assertIn("# Antigravity Knowledge Vault & Long-Term Memory", content)
        self.assertIn("User Profile", content)
        self.assertIn("Unified Architecture", content)

    def test_memorize_insight_user_preference(self):
        res = vault.memorize_insight(
            self.vault_dir,
            "I prefer concise, direct answers and Python 3.11 with hermetic unittests.",
            workspace_path=Path(self.temp_dir)
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["category"], "user")
        self.assertTrue(res["rules_synced"])
        self.assertTrue((self.vault_dir / res["rel_path"]).exists())
        content = (self.vault_dir / res["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("Python 3.11", content)

    def test_memorize_insight_decision_adr(self):
        res = vault.memorize_insight(
            self.vault_dir,
            "We decided to adopt Nutanix NC2 on AWS as the primary cloud architecture.",
            workspace_path=Path(self.temp_dir)
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["category"], "decisions")
        self.assertTrue(res["rel_path"].startswith("decisions/adr_"))
        self.assertTrue((self.vault_dir / res["rel_path"]).exists())
        content = (self.vault_dir / res["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("Nutanix NC2", content)
        self.assertIn("Status**: Accepted", content)

    def test_memorize_insight_wikilink_synthesis(self):
        res = vault.memorize_insight(
            self.vault_dir,
            "All unified container services must adhere to user conventions and profile requirements.",
            workspace_path=Path(self.temp_dir)
        )
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["links_discovered"], 1)
        content = (self.vault_dir / res["rel_path"]).read_text(encoding="utf-8")
        self.assertTrue("[[user/conventions]]" in content or "[[user/profile]]" in content or "[[architecture/cagy_unified]]" in content)

    def test_agent_pre_turn_syncs_vault(self):
        from run_agent import AIAgent
        agent = AIAgent(workspace=self.temp_dir)
        rule_file = Path(self.temp_dir) / ".gemini" / "rules" / "knowledge_vault.md"
        if rule_file.exists():
            rule_file.unlink()
        agent._sync_agy_memory()
        self.assertTrue(rule_file.exists())
        content = rule_file.read_text(encoding="utf-8")
        self.assertIn("# Antigravity Knowledge Vault & Long-Term Memory", content)

    def test_search_vault_full_text(self):
        res = vault.search_vault(self.vault_dir, "container")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["total_matches"], 1)
        matched = res["results"][0]
        self.assertIn("snippets", matched)
        self.assertGreaterEqual(len(matched["snippets"]), 1)
        self.assertIn("<mark>", matched["snippets"][0]["highlighted"])
        self.assertIn("line", matched["snippets"][0])

    def test_vault_health_orphans_and_unresolved(self):
        # Add an orphan note (isolated, no links)
        (self.vault_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "notes" / "isolated.md").write_text("# Isolated Idea\n\nNo wikilinks here.\n", encoding="utf-8")

        # Add a note with unresolved wikilink
        (self.vault_dir / "notes" / "broken.md").write_text("# Broken Link\n\nLinks to [[non_existent_target]].\n", encoding="utf-8")

        health = vault.get_vault_health(self.vault_dir)
        self.assertTrue(health["ok"])
        self.assertGreaterEqual(health["orphan_count"], 1)
        orphan_ids = [o["id"] for o in health["orphans"]]
        self.assertIn("notes/isolated", orphan_ids)

        self.assertGreaterEqual(health["unresolved_count"], 1)
        unresolved_targets = [u["target"] for u in health["unresolved_links"]]
        self.assertIn("non_existent_target", unresolved_targets)

    def test_next_adr_numbering(self):
        # Empty decisions directory -> starts at 1
        num1 = vault.get_next_adr_number(self.vault_dir)
        self.assertEqual(num1, 1)

        # Create adr_001_initial.md and adr_002_storage.md
        dec_dir = self.vault_dir / "decisions"
        dec_dir.mkdir(parents=True, exist_ok=True)
        (dec_dir / "adr_001_initial.md").write_text("# ADR 001\n", encoding="utf-8")
        (dec_dir / "adr_002_storage.md").write_text("# ADR 002\n", encoding="utf-8")

        num2 = vault.get_next_adr_number(self.vault_dir)
        self.assertEqual(num2, 3)

    def test_note_template_generation(self):
        # Decisions ADR template
        adr_tpl = vault.get_note_template("decisions", "Graph Layout Optimization", next_adr=5)
        self.assertEqual(adr_tpl["category"], "decisions")
        self.assertEqual(adr_tpl["filename"], "adr_005_graph_layout_optimization.md")
        self.assertIn("ADR 005: Graph Layout Optimization", adr_tpl["template_content"])
        self.assertIn("## Context & Problem Statement", adr_tpl["template_content"])

        # Architecture template
        arch_tpl = vault.get_note_template("architecture", "Unified Namespace")
        self.assertEqual(arch_tpl["category"], "architecture")
        self.assertEqual(arch_tpl["filename"], "unified_namespace.md")
        self.assertIn("# Unified Namespace", arch_tpl["template_content"])

        # User template
        user_tpl = vault.get_note_template("user", "Coding Conventions")
        self.assertEqual(user_tpl["category"], "user")
        self.assertIn("## Principles & Preferences", user_tpl["template_content"])

    def test_tiered_context_diet_engine(self):
        # Under threshold test
        dec_dir = self.vault_dir / "decisions"
        dec_dir.mkdir(parents=True, exist_ok=True)
        (dec_dir / "adr_001_small.md").write_text(
            "# ADR 001: Small Decision\n\n- **Date**: 2026-09-05\n- **Status**: Accepted\n\n## Context & Decision\nAdopt lightweight solution.\n",
            encoding="utf-8"
        )
        res_full = vault.sync_vault_to_rules(self.vault_dir, Path(self.temp_dir), max_full_bytes=50000)
        self.assertTrue(res_full["ok"])
        self.assertFalse(res_full["diet_mode"])
        rule_content_full = (Path(self.temp_dir) / ".gemini" / "rules" / "knowledge_vault.md").read_text(encoding="utf-8")
        self.assertIn("Small Decision", rule_content_full)
        self.assertNotIn("Tiered Context Diet Active", rule_content_full)

        # Over threshold test (force diet mode with low max_full_bytes)
        (dec_dir / "adr_002_large.md").write_text(
            "# ADR 002: Distributed Storage\n\n- **Date**: 2026-09-05\n- **Status**: Proposed\n\n## Decision Outcome\nAdopt Ceph CSI for volume storage.\n" * 10,
            encoding="utf-8"
        )
        res_diet = vault.sync_vault_to_rules(self.vault_dir, Path(self.temp_dir), max_full_bytes=50)
        self.assertTrue(res_diet["ok"])
        self.assertTrue(res_diet["diet_mode"])
        rule_content_diet = (Path(self.temp_dir) / ".gemini" / "rules" / "knowledge_vault.md").read_text(encoding="utf-8")
        self.assertIn("Tiered Context Diet Active", rule_content_diet)
        self.assertIn("Architectural Decisions Matrix", rule_content_diet)
        self.assertIn("| ADR | Title | Status | Summary & Key Decision |", rule_content_diet)
        # User preferences must still be 100% full text!
        self.assertIn("Lead Engineer", rule_content_diet)
        self.assertIn("Coding Conventions", rule_content_diet)

    def test_refactor_note_links(self):
        # Setup notes with links
        (self.vault_dir / "architecture" / "old_service.md").write_text("# Old Service\n", encoding="utf-8")
        (self.vault_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "notes" / "consumer.md").write_text(
            "# Consumer\n\nReferences [[architecture/old_service]] and [[architecture/old_service|My Service]] and [[old_service#API]].\n",
            encoding="utf-8"
        )

        res = vault.refactor_note_links(self.vault_dir, "architecture/old_service.md", "architecture/new_service.md")
        self.assertTrue(res["ok"])
        self.assertEqual(res["files_modified"], 1)
        self.assertGreaterEqual(res["replacements_count"], 3)

        updated_consumer = (self.vault_dir / "notes" / "consumer.md").read_text(encoding="utf-8")
        self.assertIn("[[architecture/new_service]]", updated_consumer)
        self.assertIn("[[architecture/new_service|My Service]]", updated_consumer)
        self.assertIn("[[new_service#API]]", updated_consumer)
        self.assertNotIn("[[architecture/old_service]]", updated_consumer)

    def test_rename_note_and_refactor(self):
        (self.vault_dir / "architecture" / "gateway.md").write_text("# API Gateway\n", encoding="utf-8")
        (self.vault_dir / "user" / "profile.md").write_text(
            "# Profile\n\nUses [[architecture/gateway]].\n",
            encoding="utf-8"
        )

        res = vault.rename_note(self.vault_dir, "architecture/gateway.md", "architecture/api_gateway.md", workspace_path=Path(self.temp_dir))
        self.assertTrue(res["ok"])
        self.assertTrue((self.vault_dir / "architecture" / "api_gateway.md").exists())
        self.assertFalse((self.vault_dir / "architecture" / "gateway.md").exists())

        profile_content = (self.vault_dir / "user" / "profile.md").read_text(encoding="utf-8")
        self.assertIn("[[architecture/api_gateway]]", profile_content)

    def test_lint_and_heal_vault(self):
        (self.vault_dir / "architecture" / "auth_service.md").write_text("# Auth Service\n", encoding="utf-8")
        (self.vault_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "notes" / "client.md").write_text(
            "# Client Note\n\nCalls [[auth_service]] and also broken file [Guide](file:///workspace/subdocs/missing_guide.md).\n",
            encoding="utf-8"
        )
        # Create candidate file in workspace
        docs_dir = Path(self.temp_dir) / "subdocs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "missing_guide.md").write_text("# Found Guide\n", encoding="utf-8")

        # Break the file link path so lint catches it
        (self.vault_dir / "notes" / "client.md").write_text(
            "# Client Note\n\nCalls [[auth_service]] and also broken file [Guide](file:///workspace/wrong_dir/missing_guide.md).\n",
            encoding="utf-8"
        )

        lint_res = vault.lint_vault(self.vault_dir, workspace_path=Path(self.temp_dir))
        self.assertTrue(lint_res["ok"])
        self.assertGreaterEqual(lint_res["issues_count"], 1)
        self.assertGreaterEqual(lint_res["healable_count"], 1)

        heal_res = vault.heal_vault(self.vault_dir, workspace_path=Path(self.temp_dir))
        self.assertTrue(heal_res["ok"])
        self.assertGreaterEqual(heal_res["healed_count"], 1)

        healed_content = (self.vault_dir / "notes" / "client.md").read_text(encoding="utf-8")
        self.assertIn("file:///workspace/subdocs/missing_guide.md", healed_content)

    def test_enhanced_search_vault_ranking(self):
        (self.vault_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "notes" / "kubernetes_intro.md").write_text(
            "# Kubernetes Intro\n\nGeneral overview of container orchestration.\n",
            encoding="utf-8"
        )
        (self.vault_dir / "notes" / "exact_keyword_target.md").write_text(
            "# Exact Keyword Target\n\n#specialtag\nKey reference notes.\n",
            encoding="utf-8"
        )

        res_tag = vault.search_vault(self.vault_dir, "specialtag")
        self.assertTrue(res_tag["ok"])
        self.assertEqual(res_tag["results"][0]["id"], "notes/exact_keyword_target")
        self.assertGreater(res_tag["results"][0]["score"], 30)



class TestVaultApiEndpoints(unittest.TestCase):
    """Test HTTP API endpoints for Knowledge Vault and Graph."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)
        cls.workspace_dir = Path(cls.temp_dir) / "workspace"
        cls.vault_dir = cls.workspace_dir / "knowledge"
        cls.vault_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["AGY_WORKSPACE_ROOT"] = str(cls.workspace_dir)
        os.environ["WORKSPACE_DIR"] = str(cls.workspace_dir)
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)

        # Seed initial note
        (cls.vault_dir / "user").mkdir(parents=True, exist_ok=True)
        (cls.vault_dir / "user" / "profile.md").write_text(
            "# Dev Profile\n\nBio: AI Systems Engineer.\n", encoding="utf-8"
        )

        cls.httpd = QuietHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        except Exception:
            pass
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _get(self, path: str):
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def _post(self, path: str, body: dict = None):
        payload = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def test_get_vault_graph(self):
        status, data = self._get("/api/vault/graph")
        self.assertEqual(status, 200)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIn("stats", data)
        self.assertGreaterEqual(data["stats"]["total_notes"], 1)

    def test_get_vault_note(self):
        status, data = self._get("/api/vault/note?path=user/profile.md")
        self.assertEqual(status, 200)
        self.assertEqual(data["title"], "Dev Profile")
        self.assertIn("AI Systems Engineer", data["content"])

    def test_create_update_and_delete_vault_note(self):
        # Create
        status, res = self._post("/api/vault/note", {
            "path": "architecture/microservices.md",
            "content": "# Microservices\n\nOverview of services.\n"
        })
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])

        # Fetch newly created note
        status, data = self._get("/api/vault/note?path=architecture/microservices.md")
        self.assertEqual(status, 200)
        self.assertEqual(data["title"], "Microservices")

        # Sync rules
        status, sync_res = self._post("/api/vault/sync", {})
        self.assertEqual(status, 200)
        self.assertTrue(sync_res["ok"])
        self.assertGreaterEqual(sync_res["total_compiled_notes"], 2)

        # Delete note
        status, del_res = self._post("/api/vault/delete", {
            "path": "architecture/microservices.md"
        })
        self.assertEqual(status, 200)
        self.assertTrue(del_res["ok"])

    def test_memorize_endpoint(self):
        status, res = self._post("/api/vault/memorize", {
            "text": "We chose Docker buildx with multi-arch amd64 and arm64 targets for deployment."
        })
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["category"], "decisions")
        self.assertTrue(res["rules_synced"])
        self.assertTrue(res["rel_path"].startswith("decisions/adr_"))

    def test_api_vault_search(self):
        status, data = self._get("/api/vault/search?q=Engineer")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(len(data.get("results", [])), 1)
        self.assertIn("snippets", data["results"][0])

    def test_api_vault_health(self):
        status, data = self._get("/api/vault/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("orphan_count", data)
        self.assertIn("unresolved_count", data)

    def test_api_vault_template(self):
        status, data = self._get("/api/vault/template?category=decisions&title=Database%20Engine")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("template", data)
        self.assertIn("next_adr", data)
        self.assertIn("ADR", data["template"])

    def test_space_container_scanning_and_filtering(self):
        # Create notes in space containers
        (self.vault_dir / "spaces" / "cka-kb" / "decisions").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "spaces" / "payment-engine" / "decisions").mkdir(parents=True, exist_ok=True)

        (self.vault_dir / "spaces" / "cka-kb" / "decisions" / "adr_001_calico.md").write_text(
            "# ADR 001: Use Calico CNI\n\nStatus: Accepted\nWe chose Calico for network policy enforcement.\n",
            encoding="utf-8"
        )
        (self.vault_dir / "spaces" / "payment-engine" / "decisions" / "adr_001_stripe.md").write_text(
            "# ADR 001: Stripe Webhook Gateway\n\nStatus: Accepted\nWe chose Stripe webhook verification.\n",
            encoding="utf-8"
        )

        # Full scan
        scan_all = vault.scan_vault(self.vault_dir)
        self.assertIn("cka-kb", scan_all["spaces"])
        self.assertIn("payment-engine", scan_all["spaces"])

        # Filtered scan for cka-kb
        scan_cka = vault.scan_vault(self.vault_dir, space_filter="cka-kb")
        cka_node_ids = [n["id"] for n in scan_cka["nodes"]]
        self.assertIn("spaces/cka-kb/decisions/adr_001_calico", cka_node_ids)
        # Global user profile should be retained
        self.assertIn("user/profile", cka_node_ids)
        # Unrelated space should be omitted
        self.assertNotIn("spaces/payment-engine/decisions/adr_001_stripe", cka_node_ids)

    def test_multi_space_adr_numbering_and_memorize(self):
        # Initial space ADR
        res1 = vault.memorize_insight(
            self.vault_dir,
            "We decided to use CoreDNS autoscaling for high-load DNS queries in Kubernetes.",
            space="cka-kb"
        )
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["space"], "cka-kb")
        self.assertTrue(res1["rel_path"].startswith("spaces/cka-kb/decisions/adr_001_"))

        # Second space ADR in same space
        res2 = vault.memorize_insight(
            self.vault_dir,
            "We decided to enable containerd runc v2 runtime.",
            space="cka-kb"
        )
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["rel_path"].startswith("spaces/cka-kb/decisions/adr_002_"))

        # User preference remains global even when in space
        res_user = vault.memorize_insight(
            self.vault_dir,
            "User preference: Always use kubectl with dry-run client flag.",
            category="user",
            space="cka-kb"
        )
        self.assertTrue(res_user["ok"])
        self.assertEqual(res_user["space"], "user")
        self.assertTrue(res_user["rel_path"].startswith("user/"))

    def test_space_scoped_rule_compilation(self):
        # Create spaces
        (self.vault_dir / "spaces" / "cka-kb" / "decisions").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "spaces" / "finance" / "decisions").mkdir(parents=True, exist_ok=True)

        (self.vault_dir / "spaces" / "cka-kb" / "decisions" / "adr_001_kubeadm.md").write_text(
            "# ADR 001: Kubeadm Bootstrap\n\nBootstrap cluster with kubeadm init.\n",
            encoding="utf-8"
        )
        (self.vault_dir / "spaces" / "finance" / "decisions" / "adr_001_ledger.md").write_text(
            "# ADR 001: Double Entry Ledger\n\nEnsure immutable double entry transaction balance.\n",
            encoding="utf-8"
        )

        ws_dir = Path(self.temp_dir) / "projects" / "cka-kb"
        ws_dir.mkdir(parents=True, exist_ok=True)

        sync_res = vault.sync_vault_to_rules(self.vault_dir, workspace_path=ws_dir, space="cka-kb")
        self.assertTrue(sync_res["ok"])
        self.assertEqual(sync_res["space"], "cka-kb")

        rules_file = ws_dir / ".gemini" / "rules" / "knowledge_vault.md"
        self.assertTrue(rules_file.exists())
        rules_content = rules_file.read_text(encoding="utf-8")

        # CKA decision and user profile must be present
        self.assertIn("Kubeadm Bootstrap", rules_content)
        self.assertIn("AI Systems Engineer", rules_content)
        # Unrelated finance space must be absent to protect token diet
        self.assertNotIn("Double Entry Ledger", rules_content)

    def test_api_vault_spaces_and_template_space(self):
        (self.vault_dir / "spaces" / "cka-kb" / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "spaces" / "cka-kb" / "notes" / "cheat_sheet.md").write_text("# CKA Cheat Sheet\n", encoding="utf-8")

        status, spaces_data = self._get("/api/vault/spaces")
        self.assertEqual(status, 200)
        self.assertTrue(spaces_data.get("ok"))
        self.assertIn("cka-kb", spaces_data.get("spaces", []))

        status, t_data = self._get("/api/vault/template?category=decisions&title=Network%20Policy&space=cka-kb")
        self.assertEqual(status, 200)
        self.assertTrue(t_data.get("ok"))
        self.assertIn("spaces/cka-kb/decisions", t_data.get("rel_path", ""))

    def test_api_vault_lint_and_heal_endpoints(self):
        status, lint_data = self._get("/api/vault/lint")
        self.assertEqual(status, 200)
        self.assertTrue(lint_data.get("ok"))
        self.assertIn("total_notes", lint_data)
        self.assertIn("issues_count", lint_data)

        status, heal_data = self._post("/api/vault/heal", {})
        self.assertEqual(status, 200)
        self.assertTrue(heal_data.get("ok"))
        self.assertIn("healed_count", heal_data)

    def test_api_vault_rename_endpoint(self):
        (self.vault_dir / "notes").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "notes" / "old_api_note.md").write_text("# Old Note Title\n", encoding="utf-8")

        status, rename_data = self._post("/api/vault/rename", {
            "old_path": "notes/old_api_note.md",
            "new_path": "notes/new_api_note.md"
        })
        self.assertEqual(status, 200)
        self.assertTrue(rename_data.get("ok"))
        self.assertTrue((self.vault_dir / "notes" / "new_api_note.md").exists())
        self.assertFalse((self.vault_dir / "notes" / "old_api_note.md").exists())


if __name__ == "__main__":
    unittest.main()

