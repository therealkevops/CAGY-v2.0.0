"""
Skill & Rule Scaffolding Wizard for Antigravity (AGY) & CAGY WebUI.
Creates standard AGY skills (/workspace/skills/*/SKILL.md) and rules (/workspace/.gemini/rules/*.md).
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List

def _resolve_workspace() -> Path:
    ws_env = os.environ.get("WORKSPACE_DIR")
    if ws_env and os.path.exists(ws_env):
        return Path(ws_env)
    return Path.cwd().parent if Path.cwd().name == "webui" else Path.cwd()

def scaffold_skill_or_rule(data: Dict[str, Any]) -> Dict[str, Any]:
    """Scaffold a new skill directory or rule file."""
    kind = str(data.get("kind", "skill")).lower().strip()
    raw_name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    preset = str(data.get("preset", "general")).lower().strip()
    include_dirs = data.get("include_dirs", [])
    custom_content = data.get("content", "").strip()

    if not raw_name:
        return {"ok": False, "error": "Name is required"}

    # Normalize name to slug
    slug = re.sub(r'[^a-zA-Z0-9_\-\.]', '-', raw_name).strip('-').lower()
    ws = _resolve_workspace()

    if kind == "rule":
        rules_dir = ws / ".gemini" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rule_file = rules_dir / f"{slug}.md"

        if not custom_content:
            custom_content = f"""# {raw_name.title()} Rule

## Overview
{description or 'Define workspace constraints, coding standards, or behavior rules for Antigravity agents.'}

## Guidelines & Enforcement
- **Constraint 1**: Maintain strict compliance with project architecture.
- **Constraint 2**: Verify all modifications before completion.
"""
        rule_file.write_text(custom_content, encoding="utf-8")
        return {
            "ok": True,
            "kind": "rule",
            "name": slug,
            "path": str(rule_file),
            "relative_path": f".gemini/rules/{slug}.md"
        }

    else:
        # Standard AGY Skill scaffolding
        skills_dir = ws / "skills" / slug
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skills_dir / "SKILL.md"

        if not custom_content:
            custom_content = f"""---
name: {slug}
description: {description or f'Specialized skill for {raw_name}'}
---

# {raw_name.title()}

## Overview
{description or f'Detailed instructions and reference workflows for {raw_name}.'}

## Available Scripts & Workflows
- Refer to `scripts/` for automated helper utilities.
- Refer to `references/` for full technical documentation and schemas.

## Instructions
1. Inspect input parameters and requirements.
2. Execute workflow steps deterministically.
3. Validate output before responding.
"""
        skill_md.write_text(custom_content, encoding="utf-8")

        created_dirs = []
        for d in include_dirs:
            if d in ("scripts", "examples", "references", "resources"):
                subdir = skills_dir / d
                subdir.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)
                if d == "scripts" and not (subdir / "example.sh").exists():
                    (subdir / "example.sh").write_text("#!/usr/bin/env bash\n# Helper script for " + slug + "\nset -euo pipefail\necho 'Running " + slug + " script...'\n", encoding="utf-8")
                elif d == "references" and not (subdir / "reference.md").exists():
                    (subdir / "reference.md").write_text(f"# {raw_name} Reference Guide\n\nDetailed specifications and API parameters.\n", encoding="utf-8")

        return {
            "ok": True,
            "kind": "skill",
            "name": slug,
            "path": str(skill_md),
            "relative_path": f"skills/{slug}/SKILL.md",
            "created_dirs": created_dirs
        }
