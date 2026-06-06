---
name: claude-clawd-status
description: Install and maintain Claude Code hooks that translate Claude session, tool, notification, compact, stop, failure, and subagent events into Hook Hub events and animation commands for a Clawd Mochi Tank ESP32 display. Use when setting up, debugging, or updating Claude-to-Clawd status detection, Hook Hub routing, transport fallback, and animation mapping.
---

# Claude Clawd Status

This skill connects Claude Code activity to the Clawd Mochi Tank ESP32 display.

---

## Architecture

```text
Claude Code (settings.json hooks)
  │  11 events: SessionStart, UserPromptSubmit, PreToolUse,
  │             PostToolUse, Notification, PreCompact,
  │             Stop, StopFailure, SessionEnd,
  │             SubagentStart, SubagentStop
  ▼
claude_clawd_hook.py          reads JSON payload from stdin,
                               maps event+tool → animation name
  │
  ▼ POST http://127.0.0.1:8765/hook
clawd_status_hub.py           Hook Hub — receives deliveries,
                               records state, drives transport
  │
  ├─── BLE Nordic UART ──────► ESP32 (Claude-Mochi-Tank)
  └─── CH340/ESP32 serial ───► ESP32 (COM11 or auto-detected)

clawd_hub_app.py              Background UI controller.
                               Keeps Hub alive, shows module
                               status, restartable from tray/window.
```

The Hub owns the ESP32 connection and provides a live dashboard at
`http://127.0.0.1:8765`. Multiple agents (Claude Code, Codex) can
share one Hub on port 8765.

---

## Is This Skill For You?

This skill targets **Claude Code** — the Anthropic CLI that stores
hook configuration in `~/.claude/settings.json`.

**You are the right agent if:**

- You are Claude Code (`claude` CLI / VSCode extension / desktop app).
- Your hook config file is `%USERPROFILE%\.claude\settings.json`.

**You are a different agent if:**

- You are OpenAI Codex CLI → use the `codex-clawd-status` skill instead.
- You are another LLM tool, CI runner, or custom agent → see
  [Adapt For Any Agent](#adapt-for-any-agent) at the end of this document.

---

## Requirements

- ESP32 is flashed with the Clawd Mochi Tank firmware.
- Python 3.10+ is available. On the current Windows setup this is typically `C:\Python314\python.exe`.
- Optional but recommended Python packages:

  ```powershell
  python -m pip install pyserial bleak
  ```

- `pyserial` enables ESP32 USB serial auto-detection, including CH340/CH341 adapters and native ESP32 USB CDC/JTAG ports.
- `bleak` enables BLE transport. If no Bluetooth adapter is available, `auto` transport falls back to serial.

---

## Files

Project copy:

```text
skills/claude-clawd-status/
```

Installed copy used by Claude:

```text
%USERPROFILE%\.claude\skills\claude-clawd-status\
```

Important scripts:

```text
scripts/install_hooks.py       writes ~/.claude/settings.json and Startup shortcut
scripts/clawd_hub_app.py       shared background UI controller
scripts/claude_clawd_hook.py   handles Claude Code hook payloads
scripts/clawd_status_hub.py    visual relay and transport owner
```

Runtime state and logs:

```text
%USERPROFILE%\.clawd-mochi\status-hook.log
%USERPROFILE%\.clawd-mochi\status-hub.log
%USERPROFILE%\.clawd-mochi\status-hub.pid
```

---

## Install Or Update

1. Copy or install this skill into:

   ```text
   %USERPROFILE%\.claude\skills\claude-clawd-status\
   ```

2. Install Claude hook entries and the Windows Startup shortcut:

   ```powershell
   C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\install_hooks.py
   ```

   From the project checkout:

   ```powershell
   python skills/claude-clawd-status/scripts/install_hooks.py
   ```

   To refresh hooks without touching the Startup shortcut or relaunching the UI:

   ```powershell
   python skills/claude-clawd-status/scripts/install_hooks.py --no-startup
   ```

3. The installer immediately launches `clawd_hub_app.py --minimized` in the
   background. The Hub and session watcher start automatically from there.

4. Restart active Claude Code sessions so hook settings are reloaded.

5. Verify `~/.claude/settings.json` contains commands pointing at:

   ```text
   %USERPROFILE%\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py
   ```

---

## Daily Start

After install, the Windows Startup shortcut (`Clawd Hub App.lnk`) launches
the UI controller automatically at login. To start manually:

Start the shared background UI controller:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "C:\Users\admin\.claude\skills\claude-clawd-status\scripts\clawd_hub_app.py",
    "--minimized"
  ) `
  -WindowStyle Hidden
```

The UI controller keeps Hub alive, shows module status, opens the dashboard,
and can restart Hub or BLE. If `pystray` is installed it stays in the Windows
system tray; without `pystray` it falls back to Tkinter minimize behavior.

Start the shared Hub:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "C:\Users\admin\.claude\skills\claude-clawd-status\scripts\clawd_status_hub.py",
    "--transport", "auto"
  ) `
  -WindowStyle Hidden
```

Open the dashboard:

```text
http://127.0.0.1:8765
```

If Codex and Claude share the Hub, only one `clawd_status_hub.py` process should
own port `8765`. It does not matter which skill directory the Hub was started from;
both hook scripts post to the same URL.

---

## Trigger Flow

Claude hook flow:

```text
Claude Code event
  -> ~/.claude/settings.json command
  -> claude_clawd_hook.py reads JSON from stdin
  -> payload_to_anim()
  -> deliver_anim()
  -> POST http://127.0.0.1:8765/hook
  -> Hub forwards to device
```

Dashboard identity:

```text
client_id = claude-code
```

Override:

```powershell
$env:CLAWD_TANK_CLIENT_ID = "my-claude"
```

---

## Hook Hub

Default Hub URL:

```text
http://127.0.0.1:8765
```

Endpoints:

```text
/        dashboard
/hook    hook/event intake  (POST JSON — see below)
/send    manual animation command
/state   current state JSON
/events  recent event history JSON
/health  liveness check
```

The Hub records:

- client connection and work status
- per-hook status
- current animation
- transport result
- recent event history

Hub localhost calls bypass system HTTP proxy settings so `HTTP_PROXY` and
`HTTPS_PROXY` do not break `127.0.0.1:8765`.

### POST /hook payload

```json
{
  "anim":        "thinking",
  "client_id":   "claude-code",
  "client_kind": "claude",
  "event":       "PreToolUse",
  "tool":        "Bash"
}
```

Only `anim` is required. All other fields are optional metadata shown on
the dashboard. `anim` must be one of:
`idle` `thinking` `typing` `building` `debugger` `wizard`
`conducting` `juggling` `confused` `sweeping` `happy`
`sleeping` `beacon` `alert` `dizzy`

---

## Transport

Default:

```text
auto = BLE -> ESP32 serial
```

Supported values:

```text
auto         BLE, then ESP32 serial
parallel     send by BLE and ESP32 serial simultaneously
bluetooth    alias of ble
ble          BLE Nordic UART only
serial       ESP32 USB serial only
ble,serial   custom ordered fallback list
```

Set transport:

```powershell
$env:CLAWD_TANK_TRANSPORT = "auto"
```

Use serial only:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --test typing --transport serial
```

Serial detection:

- The script scans pyserial port metadata.
- It prefers CH340/CH341, Espressif VID `303A`, and fields containing `ESP32`, `ESPRESSIF`, `USB JTAG`, `USB CDC`, `USB SERIAL`, `USB-SERIAL`, or `CP210`.
- Do not hard-code COM ports in normal use.
- Use `CLAWD_TANK_SERIAL_PORT` only as a deliberate override.

BLE details:

```text
Device name: Claude-Mochi-Tank
Service UUID: 6e400001-b5a3-f393-e0a9-e50e24dcca9e
RX UUID:      6e400002-b5a3-f393-e0a9-e50e24dcca9e
TX UUID:      6e400003-b5a3-f393-e0a9-e50e24dcca9e
```

BLE payloads are newline-terminated JSON commands.

---

## Event Mapping

Default mapping:

| Claude Code event | Animation |
| --- | --- |
| `SessionStart` | `idle` |
| `UserPromptSubmit` | `thinking` |
| `PreToolUse` shell/code execution | `building` |
| `PreToolUse` edit/write tools | `typing` |
| `PreToolUse` read/search/inspect tools | `debugger` |
| `PreToolUse` web tools | `wizard` |
| `PreToolUse` task/subagent tools | `conducting` |
| `PreToolUse` management tools | `juggling` |
| permission/waiting/notification events | `alert` or `confused` depending on payload |
| `PostToolUse` | tool-specific animation or `thinking` depending on payload |
| `PreCompact` | `sweeping` |
| `Stop` | `happy` |
| `StopFailure` | `dizzy` |
| `SessionEnd` | `going_away` |
| `SubagentStart` | `conducting` |
| `SubagentStop` | `thinking` |
| MCP/LSP-like calls | `beacon` |
| unknown tool | `typing` |

Lifecycle after completion:

```text
happy -> idle -> sleeping
```

---

## Test

Check device discovery:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --doctor
```

Send a test animation through Hub:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --test thinking
```

Print mapping:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --print-mapping
```

Check Hub:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/state
Invoke-RestMethod http://127.0.0.1:8765/events
```

---

## Troubleshooting

Hub page has no Claude events:

1. Check Hub is running:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8765/health
   ```

2. Check `~/.claude/settings.json` points to the installed `claude_clawd_hook.py`.
3. Restart Claude Code after changing hook settings.
4. Read `~/.clawd-mochi/status-hook.log`.

Hub has events but ESP32 does not change:

1. Open the dashboard and inspect `transport_message`.
2. If BLE fails but serial succeeds, this is acceptable fallback behavior.
3. If serial fails, close PlatformIO Serial Monitor or any app holding the COM port.
4. Replug the ESP32 USB cable and rerun `--doctor`.

Disable Hub for direct transport debugging:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --test typing --no-hub --transport serial
```

---

## Adapt For Any Agent

If you are not Claude Code but want your agent's activity to appear on
the Clawd display, you only need to POST to the Hub. No hook scripts or
`install_hooks.py` are required.

### Step 1 — Ensure the Hub is running

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
# expects: {"ok": true, "pid": ...}
```

If it is not running:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "C:\Users\admin\.claude\skills\claude-clawd-status\scripts\clawd_status_hub.py",
    "--transport", "auto"
  ) -WindowStyle Hidden
```

### Step 2 — POST an animation to the Hub

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/hook -Method Post `
  -ContentType "application/json" `
  -Body '{"anim":"thinking","client_id":"my-agent","event":"WorkStarted"}'
```

Python:

```python
import json, urllib.request
body = json.dumps({"anim": "thinking", "client_id": "my-agent", "event": "WorkStarted"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8765/hook", data=body,
    headers={"Content-Type": "application/json"},
)
urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3)
```

### Step 3 — Write a hook shim for your platform

```python
#!/usr/bin/env python3
"""Minimal hook shim — adapt event names and client_id for your agent."""
import json, sys, urllib.request

EVENT_TO_ANIM = {
    "work_start": "thinking",
    "tool_use":   "building",
    "edit_file":  "typing",
    "read_file":  "debugger",
    "web_fetch":  "wizard",
    "task_done":  "happy",
    "error":      "dizzy",
}

def post(anim: str, event: str = "", tool: str = "") -> None:
    body = json.dumps({"anim": anim, "client_id": "my-agent",
                       "event": event, "tool": tool}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8765/hook", data=body,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(req, timeout=3)
    except Exception:
        pass  # never block the agent

try:
    payload = json.loads(sys.stdin.read())
    anim = EVENT_TO_ANIM.get(payload.get("event", ""), "thinking")
    post(anim, event=payload.get("event", ""), tool=payload.get("tool", ""))
except Exception:
    pass
```

The key contract: always exit 0, never let display failures block the agent.

### Step 4 — Verify on the dashboard

Open `http://127.0.0.1:8765` and confirm your `client_id` appears in
the Clients table and animations change when your agent is active.

---

## Maintenance Notes

- Prefer editing the project copy (`skills/claude-clawd-status/`), then sync to `%USERPROFILE%\.claude\skills\claude-clawd-status`.
- Keep Claude and Codex Hub behavior aligned when they share port `8765`.
- If the hook command changes, rerun `scripts/install_hooks.py` and restart Claude Code.
- Do not make the serial port fixed by default; ESP32 serial auto-detection is intentional.
- Keep Hub as the normal path so dashboard state remains accurate.

For lower-level payload assumptions, read `references/hook-mapping.md`.
