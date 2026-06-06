# Claude Clawd Status

Claude Code status bridge for the Clawd Mochi Tank ESP32 display.

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
  [Adapt For Any Agent](#adapt-for-any-agent) below.

---

## Requirements

- ESP32 flashed with Clawd Mochi Tank firmware.
- Python 3.10+. On this machine: `C:\Python314\python.exe`.
- Optional Python packages (recommended):

  ```powershell
  python -m pip install pyserial bleak
  ```

  `pyserial` — USB serial auto-detection (CH340/CH341, ESP32 native CDC).  
  `bleak` — BLE Nordic UART transport. Falls back to serial if unavailable.

---

## Install (Claude Code)

**Step 1 — copy the skill** into the Claude skills directory:

```powershell
# From this repo:
Copy-Item -Recurse -Force `
  skills\claude-clawd-status `
  "$env:USERPROFILE\.claude\skills\claude-clawd-status"
```

**Step 2 — write hooks** into `~/.claude/settings.json`:

```powershell
C:\Python314\python.exe `
  "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\install_hooks.py"
```

The installer also creates a Windows Startup shortcut (`Clawd Hub App.lnk`)
so the Hub UI starts automatically at login. Use `--no-startup` to skip:

```powershell
... install_hooks.py --no-startup
```

**Step 3** — The installer immediately launches `clawd_hub_app.py --minimized`
in the background. The Hub starts automatically from there.

**Step 4 — restart Claude Code** so the new hook settings are loaded.

**Step 5 — verify** `~/.claude/settings.json` contains entries pointing at
`claude_clawd_hook.py` for each of the 11 hook events.

---

## Daily Start

After install, the Windows Startup shortcut handles Hub launch at login.
To start manually:

**UI controller** (keeps Hub alive, shows module status, system tray):

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\clawd_hub_app.py",
    "--minimized"
  ) -WindowStyle Hidden
```

**Hub** (animation router and ESP32 transport owner):

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\clawd_status_hub.py",
    "--transport", "auto"
  ) -WindowStyle Hidden
```

Dashboard: `http://127.0.0.1:8765`

If Codex is already running a Hub on port 8765, reuse it — do not
start a second Hub.

---

## Test

```powershell
# Device and transport discovery:
C:\Python314\python.exe `
  "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py" `
  --doctor

# Send a test animation through Hub:
C:\Python314\python.exe `
  "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py" `
  --test thinking

# Check Hub state:
Invoke-RestMethod http://127.0.0.1:8765/state
```

---

## Event → Animation Mapping

| Claude Code event | Animation |
| --- | --- |
| `SessionStart` | `idle` |
| `UserPromptSubmit` | `thinking` |
| `PreToolUse` — shell/Bash/PowerShell | `building` |
| `PreToolUse` — Edit/Write/MultiEdit/NotebookEdit | `typing` |
| `PreToolUse` — Read/Grep/Glob/LS | `debugger` |
| `PreToolUse` — WebFetch/WebSearch | `wizard` |
| `PreToolUse` — Task/Agent/Subagent | `conducting` |
| `PreToolUse` — TodoWrite/TodoRead | `juggling` |
| `PreToolUse` — AskUserQuestion | `confused` |
| `Notification` (permission/wait) | `alert` or `confused` |
| `PostToolUse` | tool-specific or `thinking` |
| `PreCompact` | `sweeping` |
| `Stop` | `happy` → `idle` → `sleeping` |
| `StopFailure` | `dizzy` |
| `SessionEnd` | `going_away` |
| `SubagentStart` | `conducting` |
| `SubagentStop` | `thinking` |

---

## Transport

Default: `auto` — tries BLE first, falls back to auto-detected serial.

```powershell
# Override transport:
$env:CLAWD_TANK_TRANSPORT = "serial"   # serial only
$env:CLAWD_TANK_TRANSPORT = "ble"      # BLE only
$env:CLAWD_TANK_TRANSPORT = "auto"     # BLE → serial (default)
```

Serial auto-detection scans port metadata for CH340/CH341 VID, Espressif
VID `303A`, and keywords ESP32/ESPRESSIF/USB JTAG/USB CDC. Do not
hard-code a COM port — use `CLAWD_TANK_SERIAL_PORT` only as a deliberate
override.

BLE device: `Claude-Mochi-Tank`  
Service UUID: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`

---

## Troubleshooting

**Hub page shows no Claude events:**

1. `Invoke-RestMethod http://127.0.0.1:8765/health` — is Hub running?
2. Check `~/.claude/settings.json` has `claude_clawd_hook.py` entries.
3. Restart Claude Code after changing hook settings.
4. Read `~/.clawd-mochi/status-hook.log`.

**Events in Hub but ESP32 does not change:**

1. Open dashboard, read `transport_message`.
2. BLE failing with "No Bluetooth adapter" and serial succeeding is normal fallback.
3. If serial also fails, close PlatformIO Serial Monitor or other apps holding the port.
4. Replug ESP32 and rerun `--doctor`.

**Bypass Hub for direct transport debugging:**

```powershell
... claude_clawd_hook.py --test typing --no-hub --transport serial
```

---

## Adapt For Any Agent

If you are **not** Claude Code but you want your agent's activity to
appear on the Clawd display, you only need to POST to the Hub. The
hook scripts and `install_hooks.py` are not required.

### Step 1 — Ensure the Hub is running

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
# expects: {"ok": true, "pid": ...}
```

If it is not running, start it:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "$env:USERPROFILE\.claude\skills\claude-clawd-status\scripts\clawd_status_hub.py",
    "--transport", "auto"
  ) -WindowStyle Hidden
```

### Step 2 — POST an animation to the Hub

The Hub accepts `POST http://127.0.0.1:8765/hook` with JSON:

```json
{
  "anim":        "thinking",
  "client_id":   "my-agent",
  "client_kind": "my-agent",
  "event":       "WorkStarted",
  "tool":        "some_tool"
}
```

| Field | Required | Description |
| --- | --- | --- |
| `anim` | **yes** | Animation name (see list below) |
| `client_id` | no | Identifier shown on dashboard (e.g. `"my-agent"`) |
| `client_kind` | no | Kind label (can match `client_id`) |
| `event` | no | Event name for dashboard display |
| `tool` | no | Tool name for dashboard display |

Available animations:
`idle` `thinking` `typing` `building` `debugger` `wizard`
`conducting` `juggling` `confused` `sweeping` `happy`
`sleeping` `beacon` `alert` `dizzy`

**PowerShell example:**

```powershell
Invoke-RestMethod http://127.0.0.1:8765/hook -Method Post `
  -ContentType "application/json" `
  -Body '{"anim":"thinking","client_id":"my-agent","event":"WorkStarted"}'
```

**Python example:**

```python
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8765/hook",
    data=json.dumps({"anim": "thinking", "client_id": "my-agent"}).encode(),
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(req, timeout=5)
```

### Step 3 — Wire into your platform's hook system

Most agent platforms let you run a shell command when events fire.
The simplest approach is a small wrapper script that receives the
event context and calls the Hub:

```python
#!/usr/bin/env python3
"""Minimal hook shim for any agent platform.

Receive event JSON on stdin (or as argv), map to an animation,
POST to Hub. Exit 0 always so the agent is never blocked.
"""
import json, sys, urllib.request

EVENT_TO_ANIM = {
    "work_start": "thinking",
    "tool_use":   "building",
    "edit_file":  "typing",
    "read_file":  "debugger",
    "web_fetch":  "wizard",
    "task_done":  "happy",
    "error":      "dizzy",
    "idle":       "idle",
}

def post(anim: str, event: str = "", tool: str = "") -> None:
    body = json.dumps({
        "anim": anim,
        "client_id": "my-agent",
        "event": event,
        "tool": tool,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8765/hook",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(req, timeout=3)
    except Exception:
        pass  # never block the agent

try:
    payload = json.loads(sys.stdin.read())
    event_name = payload.get("event", "")
    tool_name  = payload.get("tool", "")
    anim = EVENT_TO_ANIM.get(event_name, "thinking")
    post(anim, event=event_name, tool=tool_name)
except Exception:
    pass
```

Save this as `my_agent_hook.py`, then register it in your platform
however hooks are configured. The key contract:

- The script reads event context from stdin (or any other source).
- It calls `POST /hook` with `anim` + metadata.
- It always exits 0.

### Step 4 — Verify on the dashboard

Open `http://127.0.0.1:8765` and confirm your `client_id` appears
in the Clients table and the animation changes when your agent is active.

---

## Files

```text
scripts/
  install_hooks.py        writes ~/.claude/settings.json hooks
  claude_clawd_hook.py    Claude Code hook payload → animation → Hub
  clawd_status_hub.py     Hub: HTTP server, transport owner, dashboard
  clawd_hub_app.py        background UI controller (tray / Tkinter)
references/
  hook-mapping.md         low-level payload field assumptions
SKILL.md                  full reference for Claude Code agents
```

Logs and runtime state: `%USERPROFILE%\.clawd-mochi\`
