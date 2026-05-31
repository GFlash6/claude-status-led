#!/usr/bin/env python3
"""Install Claude Code hooks for Clawd Mochi Tank status animation."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_EVENTS = [
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "Stop",
    "StopFailure",
    "Notification",
    "UserPromptSubmit",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
]


def command_for(script: Path) -> str:
    python = Path(sys.executable)
    if os.name == "nt":
        return f'"{python}" "{script}"'
    return f"{shlex.quote(str(python))} {shlex.quote(str(script))}"


def hook_entry(command: str, event: str) -> dict:
    return {"hooks": [{"type": "command", "command": command}]}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        backup = SETTINGS_PATH.with_suffix(".json.bak")
        try:
            backup.write_text(SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        return {}


def install() -> None:
    script = Path(__file__).with_name("claude_clawd_hook.py").resolve()
    command = command_for(script)

    settings = load_settings()
    hooks = settings.setdefault("hooks", {})

    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        already = any(
            command in h.get("command", "")
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if not already:
            entries.append(hook_entry(command, event))

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Installed Clawd hooks in {SETTINGS_PATH}")
    print(f"Command: {command}")


if __name__ == "__main__":
    install()
