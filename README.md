# Claude Clawd Status - Hook Animation Mapping

This skill maps Claude Code hook events to Clawd Mochi Tank animations.

## Lifecycle

| Moment | Animation |
| --- | --- |
| `SessionStart` | `idle` |
| `UserPromptSubmit` | `thinking` |
| `PreToolUse` | tool-specific animation or `confused` when approval is needed |
| `PostToolUse` | tool-specific animation |
| `PreCompact` | `sweeping` |
| `Stop` | `happy` -> `idle` -> `sleeping` |
| `StopFailure` | `dizzy` |
| `SessionEnd` | `going_away` |
| `SubagentStart` | `conducting` |
| `SubagentStop` | `thinking` |

## Transport Configuration

Default priority is BLE GATT, then auto-detected CH340/CH341 USB serial, then HTTP `192.168.4.1`.

```powershell
$env:CLAWD_TANK_TRANSPORT = "ble"
$env:CLAWD_DEBUG = "1"
```

Use `CLAWD_TANK_TRANSPORT=serial` only when you want USB serial; the hook discovers the CH340 port from pyserial metadata. `CLAWD_TANK_SERIAL_PORT` is only an override.

```powershell
python scripts/claude_clawd_hook.py --doctor
python scripts/claude_clawd_hook.py --test typing --transport ble
python scripts/claude_clawd_hook.py --print-mapping
```
