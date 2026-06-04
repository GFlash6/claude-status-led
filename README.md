# Claude Clawd Status

Claude Code status bridge for the Clawd Mochi Tank ESP32 display.

Full install, usage, Hub, transport, hook, and troubleshooting documentation lives in:

```text
SKILL.md
```

## Runtime Flow

```text
Claude Code hooks
  -> claude_clawd_hook.py
  -> Hook Hub at http://127.0.0.1:8765
  -> BLE / CH340 serial / HTTP
  -> ESP32
```

## Install

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\install_hooks.py
```

The installer writes Claude hooks and creates or updates the Windows Startup
shortcut `Clawd Hub App.lnk`, which starts `clawd_hub_app.py --minimized` at
login. Use `--no-startup` if you only want to refresh hook entries.

Then restart Claude Code so hook settings are reloaded.

## Daily Start

On Windows this is normally automatic after install because the Startup
shortcut launches the background UI controller at login.

Start the shared background UI controller:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "C:\Users\admin\.claude\skills\claude-clawd-status\scripts\clawd_hub_app.py",
    "--minimized"
  ) `
  -WindowStyle Hidden
```

The UI controller keeps Hub alive, provides restart buttons, and opens the
dashboard. If `pystray` is installed it lives in the system tray; otherwise it
uses a small Tkinter window.

Start the shared Hub:

```powershell
Start-Process -FilePath "C:\Python314\python.exe" `
  -ArgumentList @(
    "C:\Users\admin\.claude\skills\claude-clawd-status\scripts\clawd_status_hub.py",
    "--transport", "auto"
  ) `
  -WindowStyle Hidden
```

Open:

```text
http://127.0.0.1:8765
```

If Codex already started the Hub on port `8765`, reuse that same Hub.

## Client IDs

```text
claude-code     Claude Code hook events
manual          Hub dashboard buttons
```

When sharing the Hub with Codex, the same page may also show `codex-code`, `codex-vscode`, and `codex-desktop`.

## Test

```powershell
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --doctor
C:\Python314\python.exe C:\Users\admin\.claude\skills\claude-clawd-status\scripts\claude_clawd_hook.py --test thinking
Invoke-RestMethod http://127.0.0.1:8765/state
```

## Notes

- Default transport is `auto`: BLE, then auto-detected CH340/CH341 serial, then HTTP.
- Serial ports are not fixed; CH340/CH341 is detected from port metadata.
- Hub localhost requests bypass system proxy settings.
- Detailed behavior and troubleshooting are in `SKILL.md`.
