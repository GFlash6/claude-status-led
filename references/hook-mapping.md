# Hook Mapping Reference

The hook script accepts one JSON object on stdin. It expects Claude Code hook fields and keeps defensive fallbacks for nearby Claude-style names where harmless.

Common Claude Code fields:

- `hook_event_name`: event name such as `PreToolUse`, `PermissionRequest`, `PostToolUse`, or `Stop`
- `session_id`: current Claude Code session identifier
- `turn_id`: current turn identifier when supplied by the host
- `tool_name`: canonical tool name for tool events
- `tool_input`: tool-specific input, such as a Bash command or `apply_patch` command
- `cwd`: current project path
- `model`: active model slug
- `permission_mode`: `default`, `acceptEdits`, `plan`, `dontAsk`, or `bypassPermissions`
- `agent_id` / `agent_type`: subagent identifiers for subagent hooks

Device command:

```text
{"auto":false,"anim":"<animation>"}
```

The command is sent as newline-terminated JSON over BLE Nordic UART or serial.

Supported firmware animations:

```text
idle, typing, thinking, building, juggling, conducting, debugger, wizard,
beacon, confused, sweeping, walking, going_away, alert, happy, sleeping,
dizzy, disconnected
```

## Event to Animation Mapping

| Claude Code event | Animation | Notes |
| --- | --- | --- |
| `SessionStart` | `idle` | Session registered and waiting for work; configurable with `CLAWD_TANK_IDLE_ANIM` |
| `UserPromptSubmit` | `thinking` | Claude starts reasoning before tools |
| `PreToolUse` | tool-specific | Show the supported tool that is about to run |
| `PermissionRequest` | `confused` | Claude is waiting for approval |
| `PostToolUse` | `thinking` | Tool finished; Claude is reading results and deciding the next step |
| `PreCompact` | `sweeping` | Context compaction is about to start |
| `PostCompact` | `thinking` | Compaction finished; Claude resumes processing |
| `SubagentStart` | `conducting` | Subagent spawned; overrides current tool |
| `SubagentStop` | `thinking` | Subagent done, back to processing |
| `Stop` | `happy` | Claude finished the turn; configurable with `CLAWD_TANK_COMPLETE_ANIM`, then timed `idle` and `sleeping` |
| `StopFailure` | `dizzy` | Claude stopped because of a failure |
| `Notification` | `confused` | Claude requires attention |
| `SessionEnd` | `going_away` | Claude session ended |

## Tool to Animation Mapping

The animation semantics follow the Claude mapping, but Codex tool names are often
namespaced. The hook checks both the full Codex name and the final leaf name, so
`functions.shell_command` and `shell_command` resolve the same way.

| Tool category | Tools | Animation |
| --- | --- | --- |
| Edit | Claude: `Edit`, `Write`, `MultiEdit`, `NotebookEdit`; Codex: `apply_patch`, `functions.apply_patch` | `typing` |
| Debug / read / inspect | Claude: `Read`, `Grep`, `Glob`, `LS`; Codex: `view_image`, `functions.view_image`, `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` | `debugger` |
| Build / shell / execution | Claude: `Bash`, `Shell`, `PowerShell`; Codex: `shell_command`, `functions.shell_command`, `js`, `mcp__node_repl.js` | `building` |
| Web / generated media | Claude: `WebFetch`, `WebSearch`; Codex: `web.run`, `imagegen`, `image_gen.imagegen` | `wizard` |
| Agent | `Task`, `Agent`, `Subagent` | `conducting` |
| Task / goal management | Claude: `TodoWrite`, `TodoRead`; Codex: `update_plan`, `get_goal`, `create_goal`, `update_goal` | `juggling` |
| Ask user | Claude: `AskUserQuestion`, `AskFollowup`; Codex: `request_user_input` | `confused` |
| MCP / LSP name hint | names containing `mcp`, `lsp`, `language`, or `context` | `beacon` |
| Parallel tool wrapper | `multi_tool_use.parallel` | nested tools if unambiguous, otherwise `juggling` |
| Unknown | anything else | `typing` |

Codex tool aliases are retained so the Claude and Codex bridges classify shared and namespaced tools consistently.

## Unused Animations

| Animation | Reason |
| --- | --- |
| `alert` | Available through `CLAWD_TANK_COMPLETE_ANIM=alert`, but default completion is `happy` |
| `walking` | Placeholder sprite; reserved for future multi-session transitions |
| `disconnected` | Firmware-autonomous on BLE drop; not sent by hook |

## Timed Stop Behavior

```text
Stop -> happy (10s) -> idle (30s) -> sleeping
             -> any new event cancels the pending timer
```

Lifecycle defaults can be customized before Claude Code starts:

```powershell
$env:CLAWD_TANK_COMPLETE_ANIM = "happy"
$env:CLAWD_TANK_IDLE_ANIM = "idle"
$env:CLAWD_TANK_SLEEP_ANIM = "sleeping"
$env:CLAWD_TANK_COMPLETE_SECONDS = "10"
$env:CLAWD_TANK_IDLE_SECONDS = "30"
```

The script turns off ESP auto-cycle (`/auto?on=0`) before sending explicit status so manual state is not overridden by the firmware's own cycling.

## VS Code Session Watcher

If a session has `originator: codex_vscode` or `originator: Codex Desktop`, it
may record `response_item` tool events in `~/.codex/sessions/**/*.jsonl`
without invoking `~/.codex/hooks.json`. `scripts/codex_session_watch.py` tails
the newest JSONL stream, switches as newer sessions appear, and maps:

| Session JSONL event | Animation |
| --- | --- |
| `event_msg` `user_message` | `thinking` |
| `response_item` `function_call` / `custom_tool_call` | tool-specific |
| `response_item` `function_call_output` / `custom_tool_call_output` | `thinking` |
| `event_msg` `task_complete` | completion animation, then timed idle/sleeping |

`clawd_hub_app.py` keeps this optional watcher running alongside the Hub. It can
also be launched directly with `codex_session_watch.py --follow-latest`.
