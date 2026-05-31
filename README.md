# Claude Clawd Status — Hook 动画映射说明

## 会话生命周期

| 时机 | 动画 | 含义 |
|------|------|------|
| 会话开始 `SessionStart` | **idle** | Claude 已注册，等待第一条消息 |
| 用户发送消息 `UserPromptSubmit` | **thinking** | Claude 开始推理，尚未调用工具 |
| 任务完成 `Stop` | **happy** → 10s → **idle** → 30s → **sleeping** | 完成回应；10s 无操作切到待机；再 30s 无操作进入休眠 |
| 错误中止 `StopFailure` | **dizzy** | API 错误或异常终止 |
| 会话结束 `SessionEnd` | **going_away** | 会话关闭，Claude 离场 |
| Context 压缩 `PreCompact` | **sweeping** | 正在整理/压缩上下文 |

## 工具调用（需要确认时）

| 时机 | 动画 | 含义 |
|------|------|------|
| `PreToolUse`（非 bypassPermissions） | **confused** | 等待你点 Allow/Deny |
| `PostToolUse` | 工具对应动画（见下表） | 确认执行完毕，反映刚才做了什么 |
| `PreToolUse`（bypassPermissions 模式） | 工具对应动画 | 全自动批准，直接显示执行内容 |

## 工具类型 → 动画

| 工具 | 动画 | 含义 |
|------|------|------|
| `Edit` `Write` `MultiEdit` `NotebookEdit` | **typing** | 正在写代码/编辑文件 |
| `Read` `Grep` `Glob` `LS` | **debugger** | 正在查找/检索文件 |
| `Bash` `Shell` `PowerShell` | **building** | 正在执行命令 |
| `WebFetch` `WebSearch` | **wizard** | 正在联网搜索 |
| `Task` `Agent` `Subagent` | **conducting** | 正在调度子 agent |
| `TodoWrite` `TodoRead` | **juggling** | 正在管理任务列表 |
| `AskUserQuestion` `AskFollowup` | **confused** | Claude 在问你问题 |
| `mcp__*` / `lsp` / `language` / `context` 类 | **beacon** | 正在调用外部服务/MCP |
| 其他未知工具 | **typing** | 默认兜底 |

## 子 Agent

| 时机 | 动画 | 含义 |
|------|------|------|
| `SubagentStart` | **conducting** | 派发子 agent，Claude 在指挥 |
| `SubagentStop` | **thinking** | 子 agent 返回，Claude 在处理结果 |

## 通知 / 等待确认

| 时机 | 动画 | 含义 |
|------|------|------|
| `Notification`（任意类型） | **confused** | 需要你的输入或确认，立即触发 |

## 未从 hook 触发的动画

| 动画 | 原因 |
|------|------|
| **alert** | 已替换为 happy（任务完成通知） |
| **walking** | 占位符，实际渲染 idle 精灵，不使用 |
| **disconnected** | 固件在蓝牙断开时自动触发，hook 不干预 |

## 自动定时切换（Stop 之后）

```
Stop → happy（10s）→ idle（30s 无活动）→ sleeping
              ↑ 任何新事件触发后自动取消定时器
```

## 传输配置

默认优先级：串口 CH340（USB VID `0x1A86` 自动检测）→ HTTP `192.168.4.1`

```powershell
# 指定串口
$env:CLAWD_TANK_SERIAL_PORT = "COM3"

# 仅使用串口
$env:CLAWD_TANK_TRANSPORT = "serial"

# 调试模式（记录完整 payload）
$env:CLAWD_DEBUG = "1"
```

```powershell
# 诊断
python scripts/claude_clawd_hook.py --doctor

# 手动测试单个动画
python scripts/claude_clawd_hook.py --test typing --transport serial

# 查看工具映射表
python scripts/claude_clawd_hook.py --print-mapping
```
