import os
import re
from pathlib import Path
from typing import List, Dict, Any

class SkillManager:
    """Discovers, parses, and loads SKILL.md definitions."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()

    def discover_skills(self) -> List[Dict[str, Any]]:
        """Search for SKILL.md files in common skill directories."""
        search_dirs = [
            self.workspace_root / ".gemini" / "skills",
            self.workspace_root / ".skills",
            self.workspace_root / "skills",
            Path.home() / ".gemini" / "antigravity-cli" / "builtin" / "skills",
            Path.home() / ".gemini" / "skills"
        ]

        skills = []
        seen_names = set()

        for sdir in search_dirs:
            if not sdir.exists() or not sdir.is_dir():
                continue

            for skill_file in sdir.rglob("SKILL.md"):
                parsed = self.parse_skill_file(skill_file)
                if parsed and parsed["name"] not in seen_names:
                    seen_names.add(parsed["name"])
                    skills.append(parsed)

        return skills

    def parse_skill_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse frontmatter and markdown body from SKILL.md."""
        try:
            content = file_path.read_text(encoding="utf-8")
            frontmatter = {}
            body = content

            # Match YAML frontmatter (between --- and ---)
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if fm_match:
                fm_text, body = fm_match.groups()
                for line in fm_text.splitlines():
                    line = line.strip()
                    if ":" in line:
                        k, v = line.split(":", 1)
                        frontmatter[k.strip()] = v.strip().strip("\"'")

            name = frontmatter.get("name", file_path.parent.name)
            description = frontmatter.get("description", "Custom agent skill")

            return {
                "name": name,
                "description": description,
                "path": str(file_path),
                "relative_path": str(file_path.relative_to(self.workspace_root)) if str(file_path).startswith(str(self.workspace_root)) else str(file_path),
                "content": content,
                "body": body.strip(),
                "frontmatter": frontmatter
            }
        except Exception as e:
            return None

    def get_skill_by_name(self, name: str) -> Dict[str, Any]:
        """Retrieve a specific skill by its name."""
        for skill in self.discover_skills():
            if skill["name"].lower() == name.lower():
                return skill
        return None
