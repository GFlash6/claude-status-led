---
name: claude-clawd-status
description: Install and maintain Claude Code hooks that translate Claude session, tool, notification, compact, stop, failure, and subagent events into Hook Hub events and animation commands for a Clawd Mochi Tank ESP32 display. Use when setting up, debugging, or updating Claude-to-Clawd status detection, Hook Hub routing, transport fallback, and animation mapping.
---

# Claude Clawd Status

This skill connects Claude Code activity to the Clawd Mochi Tank ESP32 display.

Runtime flow:

```text
Claude Code hooks
  -> claude_clawd_hook.py
  -> local Hook Hub at http://127.0.0.1:8765
  -> BLE Nordic UART / auto-detected CH340 serial / HTTP
  -> ESP32 firmware
```

The Hub can be shared with Codex. Claude events appear as `claude-code` in the dashboard.

## Requirements

- ESP32 is flashed with the Clawd Mochi Tank firmware.
- Python 3.10+ is available. On the current Windows setup this is typically `C:\Python314\python.exe`.
- Optional but recommended Python packages:
  ```powershell
  python -m pip install pyserial bleak
  ```
- `pyserial` enables CH340/CH341 USB serial auto-detection.
- `bleak` enables BLE transport. If no Bluetooth adapter is available, `auto` transport falls back to serial and then HTTP.

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
scripts/install_hooks.py       writes ~/.claude/settings.json
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

## Install Or Update

1. Copy or install this skill into:

   ```text
   %USERPROFILE%\.claude\skills\claude-clawd-status\
   ```

2. Install Claude hook entries:

   ```powershell
   C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\install_hooks.py
   ```

   From the project checkout, use:

   ```powershell
   python skills/claude-clawd-status/scripts/install_hooks.py
   ```

   The installer also creates or updates this Windows Startup shortcut:

   ```text
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Clawd Hub App.lnk
   ```

   The shortcut starts `clawd_hub_app.py --minimized` at login, so the shared
   Hub UI can stay available in the background. To install hooks without
   changing Startup entries, run:

   ```powershell
   python scripts/install_hooks.py --no-startup
   ```

3. Restart active Claude Code sessions so hook settings are reloaded.

4. Verify `~/.claude/settings.json` contains commands pointing at:

   ```text
   %USERPROFILE%\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py
   ```

## Daily Start

After installation on Windows, the Hub UI controller is started automatically
at login from the `Clawd Hub App.lnk` Startup shortcut.

Claude hooks can auto-start the Hub, but the most predictable daily setup is to keep Hub running before opening Claude Code.

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
and can restart Hub, the Codex watcher, or BLE. If `pystray` is installed it
can stay in the Windows system tray; without `pystray` it falls back to Tkinter
minimize behavior.

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

If Codex and Claude share the Hub, only one `clawd_status_hub.py` process should own port `8765`. It does not matter whether the Hub was started from the Codex skill directory or the Claude skill directory; both hook scripts post to the same URL.

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

## Hook Hub

Default Hub URL:

```text
http://127.0.0.1:8765
```

Endpoints:

```text
/        dashboard
/hook    hook/event intake
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

Hub localhost calls bypass system HTTP proxy settings so `HTTP_PROXY` and `HTTPS_PROXY` do not break `127.0.0.1:8765`.

## Transport

Default:

```text
auto = BLE -> CH340 serial -> HTTP
```

Supported values:

```text
auto         BLE, then CH340 serial, then HTTP
parallel     send by BLE, CH340 serial, and HTTP
bluetooth    alias of ble
ble          BLE Nordic UART only
serial       CH340/CH341 USB serial only
http         HTTP only
serial,http  custom ordered fallback list
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
- It matches CH340/CH341 by VID `1A86` or fields containing `CH340`, `CH341`, `USB-SERIAL`, etc.
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

HTTP fallback default:

```text
http://192.168.4.1
```

Override:

```powershell
$env:CLAWD_TANK_URL = "http://192.168.4.1"
```

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
4. Replug CH340 USB and rerun `--doctor`.
5. If using HTTP fallback, connect to the ESP32 AP and check `http://192.168.4.1/state`.

Disable Hub for direct transport debugging:

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --test typing --no-hub --transport serial
```

## Maintenance Notes For Codex

- Prefer editing the project copy, then sync to `%USERPROFILE%\.claude\skills\claude-clawd-status`.
- Keep Claude and Codex Hub behavior aligned when they share port `8765`.
- If the hook command changes, rerun `scripts/install_hooks.py` and restart Claude Code.
- Do not make the serial port fixed by default; CH340 auto-detection is intentional.
- Keep Hub as the normal path so dashboard state remains accurate.

For lower-level payload assumptions, read `references/hook-mapping.md`.
