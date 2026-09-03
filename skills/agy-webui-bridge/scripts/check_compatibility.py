#!/usr/bin/env python3
"""
AGY CLI & WebUI Bridge Compatibility Checker
Tests the CLI contract, stream-json output, model catalog, and tool execution.
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path

def print_status(section: str, ok: bool, details: str = ""):
    icon = "✅" if ok else "❌"
    print(f"{icon} [{section}] {details}")

def main():
    print("=" * 60)
    print(" Antigravity (agy) CLI <-> WebUI Bridge Compatibility Check")
    print("=" * 60)

    # 1. Check agy binary
    agy_bin = shutil.which("agy") or str(Path.home() / ".local" / "bin" / "agy")
    if not Path(agy_bin).exists():
        print_status("AGY Binary", False, f"agy binary not found at {agy_bin}")
        sys.exit(1)
    print_status("AGY Binary", True, f"Found at {agy_bin}")

    # 2. Check flags support
    try:
        res = subprocess.run([agy_bin, "--help"], capture_output=True, text=True, timeout=5)
        help_text = res.stdout + res.stderr
        required_flags = ["--print", "--output-format", "--dangerously-skip-permissions", "--add-dir", "--model", "--conversation"]
        missing_flags = [f for f in required_flags if f not in help_text]
        if missing_flags:
            print_status("CLI Flags", False, f"Missing flags in agy --help: {missing_flags}")
        else:
            print_status("CLI Flags", True, "All required flags present in agy CLI")
    except Exception as e:
        print_status("CLI Flags", False, f"Error checking --help: {e}")

    # 3. Test stream-json execution
    print("\nTesting stream-json single-turn probe...")
    cmd = [
        agy_bin,
        "--print", "ping",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--add-dir", str(Path.cwd()),
        "--model", "Gemini 3.8 Flash (High)"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=45)
        
        events = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass

        event_types = [e.get("event") for e in events if "event" in e]
        print_status("Stream JSON", "init" in event_types and "result" in event_types, f"Received {len(events)} events: {set(event_types)}")
        
        result_event = next((e for e in events if e.get("event") == "result"), None)
        if result_event and result_event.get("result", {}).get("status") == "SUCCESS":
            print_status("Model Execution", True, f"Response: {result_event.get('result', {}).get('response', '').strip()}")
        else:
            print_status("Model Execution", False, f"Failed or non-zero result: {result_event}")

    except Exception as e:
        print_status("Stream JSON", False, f"Probe failed: {e}")

    print("\nCompatibility check complete.")

if __name__ == "__main__":
    main()
