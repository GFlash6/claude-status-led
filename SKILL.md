---
name: claude-clawd-status
description: Install and maintain Claude Code hooks that translate Claude session, tool, notification, compact, stop, failure, and subagent events into HTTP animation commands for a Clawd Mochi Tank ESP32 display. Use when setting up, debugging, or updating Claude-to-Clawd status detection and animation mapping.
---

# Claude Clawd Status

Use this skill to wire Claude Code hook events to the ESP32 Clawd Mochi Tank firmware.

## Quick Start

1. Ensure the ESP32 is running the Clawd Mochi Tank firmware and is reachable by HTTP, USB serial, or BLE UART.
2. Run `scripts/install_hooks.py` to install Claude Code hooks into `~/.claude/settings.json`.
3. Restart active Claude Code sessions so hooks are loaded.
4. Test manually:
   ```powershell
   python skills/claude-clawd-status/scripts/claude_clawd_hook.py --test typing
   ```

Device-side behavior:

```text
startup / no Bluetooth client  -> beacon
Bluetooth serial client opens  -> idle
Claude hook command received   -> mapped tank animation
Bluetooth serial disconnects   -> beacon
```

The ESP32 returns a JSON state line after serial/Bluetooth serial commands; the hook records it in `~/.clawd-mochi/status-hook.log`.

Check whether the skill can find the device by itself:

```powershell
python skills/claude-clawd-status/scripts/claude_clawd_hook.py --doctor
```

Default transport is fallback mode: serial/Bluetooth COM first, then HTTP. Set `CLAWD_TANK_URL` to override the default HTTP device URL:

```powershell
$env:CLAWD_TANK_URL="http://192.168.4.1"
```

Use serial only:

```powershell
$env:CLAWD_TANK_TRANSPORT="serial"
$env:CLAWD_TANK_SERIAL_PORT="COM5"
python skills/claude-clawd-status/scripts/claude_clawd_hook.py --test typing
```

Use Bluetooth serial instead:

```powershell
$env:CLAWD_TANK_TRANSPORT="bluetooth"
$env:CLAWD_TANK_SERIAL_PORT="COM5"
python skills/claude-clawd-status/scripts/claude_clawd_hook.py --test typing
```

The current ESP32 firmware advertises as Windows-visible classic Bluetooth SPP. Pair `Claude-Mochi-Tank` in Windows Bluetooth settings first, then use the outgoing COM port shown in Windows. Serial/Bluetooth serial requires `pyserial`.

If `CLAWD_TANK_SERIAL_PORT` is not set, the hook tries to auto-detect a Windows COM port whose device name contains `Claude-Mochi-Tank`.

Send through every channel instead of stopping after the first success:

```powershell
$env:CLAWD_TANK_TRANSPORT="parallel"
```

Accepted transport values:

```text
auto      serial/Bluetooth COM -> HTTP fallback, this is the default
parallel  send by serial, BLE, and HTTP
bluetooth Bluetooth serial only, alias of serial
serial    serial only
ble       BLE GATT only, for firmware that uses Nordic UART Service
http      HTTP only
serial,http  custom ordered fallback list
```

## Event Mapping

Default mapping:

| Claude Code event | Display animation |
| --- | --- |
| `SessionStart` | `walking` |
| `PreToolUse` with `Bash` | `building` |
| `PreToolUse` with `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | `typing` |
| `PreToolUse` with `Read`, `Grep`, `Glob`, `LS` | `debugger` |
| `PreToolUse` with `WebFetch`, `WebSearch` | `wizard` |
| `PreToolUse` with MCP/LSP-like names | `beacon` |
| `PreCompact` | `sweeping` |
| `Notification` / permission / waiting events | `alert` |
| `Stop` | `alert` |
| `StopFailure` | `dizzy` |
| `SubagentStart` | `conducting` |
| `UserPromptSubmit` | `thinking` |
| `SessionEnd` | `going_away` |

After a tool finishes, the hook chooses `thinking` rather than immediately returning to idle, because Claude often performs several tools in sequence.

## Scripts

- `scripts/claude_clawd_hook.py`: stdin hook handler. Reads Claude Code hook JSON, maps it to an animation, sends it to the ESP32 by HTTP, serial, or BLE, and exits 0 on best-effort failures.
- `scripts/install_hooks.py`: idempotently writes hook entries to `~/.claude/settings.json`.

## Debugging

Logs are written to:

```text
~/.clawd-mochi/status-hook.log
```

If the display does not change:

1. Open `http://192.168.4.1/state` while connected to the ESP32 AP.
2. Run the hook script with `--test idle`, `--test typing`, or `--test alert`.
3. Check that `~/.claude/settings.json` contains hook entries pointing at `claude_clawd_hook.py`.
4. Restart Claude Code after changing hook settings.

For the full hook payload assumptions and tool mapping details, read `references/hook-mapping.md`.
