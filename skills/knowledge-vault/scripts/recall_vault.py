#!/usr/bin/env python3
"""
Recall Vault: Query, search, and retrieve notes from the Antigravity Knowledge Vault.
Usage:
    python3 recall_vault.py "<query>" [--space <space>] [--folder <folder>] [--limit <n>] [--full] [--json]
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
    from api.vault import search_vault, get_note, get_vault_dir
except ImportError:
    # Fallback to direct import
    sys.path.append(str(REPO_ROOT))
    from webui.api.vault import search_vault, get_note, get_vault_dir


def main():
    parser = argparse.ArgumentParser(description="Query and recall notes from the Antigravity Knowledge Vault.")
    parser.add_argument("query", nargs="?", default="", help="Keyword or topic search terms")
    parser.add_argument("--space", "-s", default=None, help="Filter by space (e.g. cka-kb, global)")
    parser.add_argument("--folder", "-f", default=None, help="Filter by folder (user, architecture, decisions, notes)")
    parser.add_argument("--tag", "-t", default=None, help="Filter by tag (e.g. #infrastructure)")
    parser.add_argument("--limit", "-n", type=int, default=5, help="Maximum results to return (default: 5)")
    parser.add_argument("--full", action="store_true", help="Print full text of the top matching note")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--workspace", "-w", default=None, help="Workspace root directory")

    args = parser.parse_args()

    ws_path = Path(args.workspace) if args.workspace else None
    vault_dir = get_vault_dir(ws_path)

    if not vault_dir.exists():
        print(f"Error: Knowledge vault not found at {vault_dir}", file=sys.stderr)
        sys.exit(1)

    res = search_vault(
        vault_dir,
        query=args.query,
        folder=args.folder,
        tag=args.tag,
        limit=args.limit,
        space=args.space,
    )

    if args.json:
        print(json.dumps(res, indent=2))
        return

    results = res.get("results", [])
    total = len(results)

    if not results:
        print(f"No vault notes found matching '{args.query}' (scope: {args.space or 'all'}).")
        return

    print(f"🔍 Found {total} vault note(s) for query: '{args.query}' (vault: {vault_dir})\n")

    for i, r in enumerate(results, start=1):
        tags_str = " ".join(f"#{t}" for t in r.get("tags", []))
        space_badge = f"[{r.get('space', 'global')}]"
        print(f"{i}. \033[1m{r['title']}\033[0m {space_badge}")
        print(f"   Path:  knowledge/{r['path']}")
        print(f"   Score: {r['score']} pts | Tags: {tags_str or '(none)'}")

        snippets = r.get("snippets", [])
        if snippets:
            print("   Excerpts:")
            for snip in snippets[:3]:
                clean_text = snip["text"].replace("<mark>", "\033[33m").replace("</mark>", "\033[0m")
                print(f"     L{snip['line']}: {clean_text}")
        print()

    if args.full and results:
        top_note = results[0]
        note_data = get_note(vault_dir, top_note["path"])
        if note_data.get("ok"):
            print("═" * 70)
            print(f"📄 FULL NOTE: {note_data.get('title')} ({top_note['path']})")
            print("═" * 70)
            print(note_data.get("content", ""))
            print("═" * 70)


if __name__ == "__main__":
    main()
