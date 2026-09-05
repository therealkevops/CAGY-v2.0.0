#!/usr/bin/env python3
"""
Lint Vault: Integrity audit & auto-healing engine for the Antigravity Knowledge Vault.
Usage:
    python3 lint_vault.py [--space <space>] [--heal] [--json] [--workspace <path>]
"""

import sys
import os
import argparse
import json
from pathlib import Path

# Resolve repo root and webui import path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

try:
    from api.vault import lint_vault, heal_vault, get_vault_dir, infer_space_from_workspace
except ImportError:
    sys.path.append(str(REPO_ROOT))
    from webui.api.vault import lint_vault, heal_vault, get_vault_dir, infer_space_from_workspace


def main():
    parser = argparse.ArgumentParser(description="Audit and heal Knowledge Vault link integrity.")
    parser.add_argument("--space", "-s", default=None, help="Scope audit to a specific space (e.g. cka-kb, global)")
    parser.add_argument("--heal", action="store_true", help="Automatically repair broken links with unambiguous fixes")
    parser.add_argument("--json", action="store_true", help="Output raw JSON results")
    parser.add_argument("--workspace", "-w", default=None, help="Workspace root directory")

    args = parser.parse_args()

    ws_path = Path(args.workspace) if args.workspace else REPO_ROOT
    if not ws_path.exists():
        ws_path = Path.cwd()

    vault_dir = get_vault_dir(ws_path)
    if not vault_dir.exists():
        print(f"Error: Knowledge vault not found at {vault_dir}", file=sys.stderr)
        sys.exit(1)

    space = args.space or infer_space_from_workspace(ws_path)

    if args.heal:
        print(f"🔧 Running Self-Healing Engine on Knowledge Vault ({vault_dir}, space: {space or 'all'})...")
        heal_res = heal_vault(vault_dir, workspace_path=ws_path, space=space)
        if args.json:
            print(json.dumps(heal_res, indent=2))
            return

        healed_items = heal_res.get("healed_items", [])
        print(f"\n✅ Healed {len(healed_items)} issue(s):")
        for item in healed_items:
            print(f"   - [{item['type']}] in {item['source']}:")
            print(f"       Old: {item['old']}")
            print(f"       New: \033[32m{item['new']}\033[0m")

        print(f"\nRemaining unresolved issues: {heal_res.get('remaining_issues', 0)}")
        if heal_res.get("rules_synced"):
            print("✨ Native Antigravity rules re-compiled successfully.")
        return

    # Lint audit mode
    print(f"🛡️  Auditing Knowledge Vault integrity ({vault_dir}, space: {space or 'all'})...\n")
    audit = lint_vault(vault_dir, workspace_path=ws_path, space=space)

    if args.json:
        print(json.dumps(audit, indent=2))
        return

    total_notes = audit.get("total_notes", 0)
    broken_wikilinks = audit.get("broken_wikilinks", [])
    broken_file_links = audit.get("broken_file_links", [])
    orphans = audit.get("orphans", [])
    healable = audit.get("healable_issues", [])

    print(f"Total Notes Scanned: {total_notes}")
    print(f"Total Issues Found:  {audit.get('issues_count', 0)}")
    print(f"Auto-Healable:       {len(healable)}")
    print("─" * 60)

    if broken_wikilinks:
        print(f"\n⚠️  Broken Wikilinks ({len(broken_wikilinks)}):")
        for b in broken_wikilinks:
            heal_tag = f"[\033[32mHEALABLE -> {b['suggested_fix']}\033[0m]" if b.get("auto_healable") else "[\033[31mUNRESOLVED\033[0m]"
            print(f"   • {b['source_note']}: [[{b['target']}]] {heal_tag}")

    if broken_file_links:
        print(f"\n⚠️  Broken Workspace File Hyperlinks ({len(broken_file_links)}):")
        for b in broken_file_links:
            heal_tag = f"[\033[32mHEALABLE -> {b['suggested_fix']}\033[0m]" if b.get("auto_healable") else "[\033[31mUNRESOLVED\033[0m]"
            print(f"   • {b['source_note']}:L{b.get('line')}: {b.get('label')} -> {b.get('missing_path')} {heal_tag}")

    if orphans:
        print(f"\n🔍 Orphan Notes ({len(orphans)}):")
        for o in orphans:
            print(f"   • {o}")

    if not broken_wikilinks and not broken_file_links and not orphans:
        print("\n🎉 100% HEALTHY! No broken links or orphan notes detected.")
    elif healable:
        print(f"\n💡 Tip: Run `python3 skills/knowledge-vault/scripts/lint_vault.py --heal` to automatically repair {len(healable)} link(s).")


if __name__ == "__main__":
    main()
