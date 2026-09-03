#!/usr/bin/env python3
"""
Mock Google Antigravity (agy) CLI binary for hermetic testing.
Emulates `agy --help`, `agy models`, and `agy --output-format stream-json`.
"""
import sys
import json
import time

def main():
    args = sys.argv[1:]

    # 1. Help flag
    if "--help" in args or "-h" in args:
        print("""
Antigravity (agy) CLI - Mock Testing Utility

Usage: agy [OPTIONS] [COMMAND]

Options:
  --print TEXT                    Execute single prompt without interactive UI
  --output-format FORMAT          Output format: text, json, stream-json
  --dangerously-skip-permissions  Bypass interactive security prompts
  --add-dir PATH                  Mount extra directory to context
  --model MODEL                   Target LLM model ID or alias
  --conversation ID               Resume existing conversation ID
  --help                          Show this message and exit

Commands:
  models                          List available AI models
""")
        sys.exit(0)

    # 2. Models command
    if "models" in args:
        print("gemini-3.8-flash-high\tGemini 3.8 Flash (High)\ngemini-3.5-pro\tGemini 3.5 Pro\nclaude-3-7-sonnet\tClaude 3.7 Sonnet\n")
        sys.exit(0)

    # 3. Stream-json output format
    if "--output-format" in args:
        fmt_idx = args.index("--output-format")
        if fmt_idx + 1 < len(args) and args[fmt_idx + 1] == "stream-json":
            conv_id = "conv-mock-123456"
            if "--conversation" in args:
                c_idx = args.index("--conversation")
                if c_idx + 1 < len(args):
                    conv_id = args[c_idx + 1]

            prompt_text = "Hello from mock AGY"
            if "--print" in args:
                p_idx = args.index("--print")
                if p_idx + 1 < len(args):
                    prompt_text = f"Mock reply to: {args[p_idx + 1]}"

            # Emit stream-json events
            events = [
                {"event": "init", "conversation_id": conv_id},
                {"event": "step_update", "step_update": {"text_delta": prompt_text}},
                {"event": "result", "result": {"status": "SUCCESS", "response": prompt_text, "usage": {"input_tokens": 10, "output_tokens": 20}}}
            ]
            for e in events:
                print(json.dumps(e), flush=True)
            sys.exit(0)

    # 4. Standard print
    if "--print" in args:
        p_idx = args.index("--print")
        if p_idx + 1 < len(args):
            print(f"Mock reply to: {args[p_idx + 1]}")
        else:
            print("Mock AGY reply")
        sys.exit(0)

    print("Mock AGY ready.")
    sys.exit(0)

if __name__ == "__main__":
    main()
