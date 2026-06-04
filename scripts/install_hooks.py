#!/usr/bin/env python3
"""Install Claude Code hooks for Clawd Mochi Tank status animation."""

from __future__ import annotations

import json
import os
import shlex
import sys
import argparse
from pathlib import Path


SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
STARTUP_SHORTCUT_NAME = "Clawd Hub App.lnk"
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


def pythonw_path() -> Path:
    python = Path(sys.executable)
    if os.name == "nt":
        candidate = python.with_name("pythonw.exe")
        if candidate.exists():
            return candidate
    return python


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def install_startup_shortcut(app_script: Path) -> Path | None:
    if os.name != "nt":
        print("Startup shortcut skipped: only Windows startup links are supported by this installer.")
        return None
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    shortcut = startup / STARTUP_SHORTCUT_NAME
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut({ps_literal(shortcut)})
$sc.TargetPath = {ps_literal(pythonw_path())}
$sc.Arguments = '"' + {ps_literal(app_script)} + '" --minimized'
$sc.WorkingDirectory = {ps_literal(app_script.parent)}
$sc.WindowStyle = 7
$sc.Description = 'Start Clawd Hook Hub background UI'
$sc.Save()
"""
    import subprocess
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return shortcut


def hook_entry(command: str, event: str) -> dict:
    return {"hooks": [{"type": "command", "command": command}]}


def remove_old_clawd_hooks(entries: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for entry in entries:
        hooks = [
            hook
            for hook in entry.get("hooks", [])
            if "claude_clawd_hook.py" not in str(hook.get("command", ""))
        ]
        if hooks:
            next_entry = dict(entry)
            next_entry["hooks"] = hooks
            cleaned.append(next_entry)
    return cleaned


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


def install(install_startup: bool = True) -> None:
    script = Path(__file__).with_name("claude_clawd_hook.py").resolve()
    app_script = Path(__file__).with_name("clawd_hub_app.py").resolve()
    command = command_for(script)

    settings = load_settings()
    hooks = settings.setdefault("hooks", {})

    for event in HOOK_EVENTS:
        entries = remove_old_clawd_hooks(hooks.setdefault(event, []))
        entries.append(hook_entry(command, event))
        hooks[event] = entries

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Installed Clawd hooks in {SETTINGS_PATH}")
    print(f"Command: {command}")
    if install_startup:
        shortcut = install_startup_shortcut(app_script)
        if shortcut:
            print(f"Installed Clawd Hub startup shortcut: {shortcut}")
    else:
        print("Skipped Clawd Hub startup shortcut.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-startup", action="store_true", help="do not create Windows Startup shortcut")
    args = parser.parse_args()
    install(install_startup=not args.no_startup)
