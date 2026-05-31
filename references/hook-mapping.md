# Hook Mapping Reference

The hook script accepts JSON on stdin. It expects Claude Code-style fields, but uses defensive fallbacks so small schema changes do not break status reporting.

Common fields:

- `hook_event_name`: event name such as `PreToolUse`, `Stop`, `Notification`
- `session_id`: Claude Code session identifier
- `tool_name` or `toolName`: tool name for `PreToolUse` / `PostToolUse`
- `notification_type`: commonly `idle_prompt`
- `cwd`: current project path
- `agent_id`: subagent identifier for subagent hooks

Animation endpoint:

```text
GET http://192.168.4.1/anim?id=<animation>
```

Supported firmware animations:

```text
idle, typing, thinking, building, juggling, conducting, debugger, wizard,
beacon, confused, sweeping, walking, going_away, alert, happy, sleeping,
dizzy, disconnected
```

## Event → Animation Mapping

Aligned with clawd-tank-master authoritative spec (docs/superpowers/specs/, host/clawd_tank_daemon/).

| Claude Code event | Animation | Notes |
| --- | --- | --- |
| `SessionStart` | `idle` | Session registered, not yet active |
| `UserPromptSubmit` | `thinking` | Claude starts reasoning before tools |
| `PreToolUse` (permission_mode = bypassPermissions) | tool-specific | All tools auto-approved; show what's running |
| `PreToolUse` (any other permission_mode) | `confused` | Tool may need user approval before running |
| `PostToolUse` | `thinking` | Tool ran; Claude processes result |
| `PreCompact` | `sweeping` | Context compaction (oneshot in full impl) |
| `Notification` (any type) | `confused` | Waiting for user; idle_prompt = 60s+ idle |
| `SubagentStart` | `conducting` | Subagent spawned; overrides current tool |
| `SubagentStop` | `thinking` | Subagent done, back to processing |
| `Stop` | `alert` | Claude finished turn; oneshot in full impl |
| `StopFailure` | `dizzy` | API or auth error |
| `SessionEnd` | `going_away` | Session closing; oneshot in full impl |

## Tool → Animation Mapping

Based on `TOOL_ANIMATION_MAP` in clawd_tank_daemon/daemon.py, extended with additional tools.

| Tool category | Tools | Animation |
| --- | --- | --- |
| Edit | `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | `typing` |
| Debug / search | `Read`, `Grep`, `Glob`, `LS` | `debugger` |
| Build / shell | `Bash`, `Shell`, `PowerShell` | `building` |
| Web | `WebFetch`, `WebSearch` | `wizard` |
| Agent | `Task`, `Agent`, `Subagent` | `conducting` |
| Task management | `TodoWrite`, `TodoRead` | `juggling` |
| Ask user | `AskUserQuestion`, `AskFollowup` | `confused` |
| MCP / LSP (name hint) | tools containing `mcp`, `lsp`, `language`, `context` | `beacon` |
| Unknown | anything else | `typing` (spec fallback) |

## Unused animations (hook script)

| Animation | Reason |
| --- | --- |
| `happy` | Oneshot for notification dismissal; requires state tracking not available in single-hook model. Appears in device auto-cycle. |
| `sleeping` | Triggered by daemon staleness timeout (no sessions for >10 min); not a hook event. |
| `walking` | Placeholder sprite (renders idle sprite); reserved for future multi-session transitions. |
| `disconnected` | Firmware-autonomous on BLE drop; not sent by hook. |

## Oneshot behavior note

In the full clawd-tank-master implementation, `alert`, `happy`, `sweeping`, and `going_away` are oneshot animations that play once then return to a fallback state. In this simplified direct-send bridge, they stay displayed until the next hook event overwrites them. The effect is similar since hook events follow quickly in normal usage.

The script turns off ESP auto-cycle (`/auto?on=0`) before sending explicit status so manual state is not overridden by the firmware's own cycling.
