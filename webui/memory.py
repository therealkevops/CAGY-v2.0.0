import os
from pathlib import Path
from typing import Dict, Any

class MemoryManager:
    """Manages persistent memory and persona files (e.g. MEMORY.md, SOUL.md)."""

    def __init__(self, workspace_root: str, data_dir: str = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else self.workspace_root / ".memory"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.memory_file = self.workspace_root / "MEMORY.md"
        if not self.memory_file.exists():
            # Create a default MEMORY.md if it doesn't exist
            self.memory_file.write_text(
                "# Long-Term Memory\n\n"
                "This file stores persistent memory, project context, and user preferences across sessions.\n\n"
                "## Project Context\n"
                "- Unified containerized environment for Antigravity & Hermes WebUI\n"
                "- Gemini 3.7 Agent with tool calling\n\n"
                "## User Preferences\n"
                "- Direct execution, high-signal responses\n"
                "- Clean code diffs and surgical edits\n",
                encoding="utf-8"
            )

    def get_memory_content(self) -> str:
        """Read the MEMORY.md content."""
        if self.memory_file.exists():
            try:
                return self.memory_file.read_text(encoding="utf-8")
            except Exception as e:
                return f"Error reading MEMORY.md: {e}"
        return ""

    def update_memory_content(self, content: str) -> Dict[str, Any]:
        """Update the MEMORY.md content."""
        try:
            self.memory_file.write_text(content, encoding="utf-8")
            return {"success": True, "message": "Memory updated successfully."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_system_prompt_addition(self) -> str:
        """Format persistent memory to be injected into system instructions."""
        mem = self.get_memory_content().strip()
        if not mem:
            return ""
        return (
            "\n\n<persistent_memory>\n"
            "The following is persistent memory and context stored in MEMORY.md:\n"
            f"{mem}\n"
            "</persistent_memory>\n"
        )
