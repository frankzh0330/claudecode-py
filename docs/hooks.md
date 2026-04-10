# Hooks 系统

本文档详细描述 `hooks.py` 的架构、配置格式、事件类型和执行流程。

---

## 概览

Hooks 是用户可配置的 shell 命令钩子，在特定事件触发时自动执行。允许用户在不修改代码的情况下扩展和定制 Claude Code 的行为——如拦截工具调用、验证用户输入、审计操作日志等。

对应 TS 版：`services/hooks/` 目录（~2000 行），Python 简化版 ~300 行，保留核心功能。

---

## 涉及文件与职责

```
hooks.py              ← Hooks 核心（数据类型 + 配置加载 + 子进程执行 + 事件分发）
  ↓
api.py                ← PreToolUse / PostToolUse hook 分发（工具执行前后）
  ↓
cli.py                ← UserPromptSubmit / Stop / SessionStart hook 分发
  ↓
~/.claude/settings.json ← Hook 配置存储
```

---

## 数据模型

```
HookEvent (5 种事件，str Enum)
├── PRE_TOOL_USE        工具执行前（权限检查前）
├── POST_TOOL_USE       工具执行后
├── USER_PROMPT_SUBMIT  用户提交 prompt 时
├── STOP                模型响应结束后
└── SESSION_START       会话开始时

HookConfig (一条 hook 命令配置)
├── type       "command"（目前仅支持 command 类型）
├── command    Shell 命令字符串
├── timeout    超时秒数（默认 30）
└── is_async   是否异步执行（默认 False）

HookMatcher (一个匹配器)
├── matcher    工具名匹配模式（如 "Bash"），None/"*" 匹配所有
└── hooks      HookConfig 列表

HookResult (一次 hook 执行的结果)
├── exit_code      进程退出码
├── stdout         标准输出
├── stderr         标准错误
├── decision       从 stdout JSON 解析: "allow" / "deny"
├── reason         从 stdout JSON 解析: 原因描述
└── updated_input  从 stdout JSON 解析: 修改后的工具输入
```

---

## 支持的事件

### PreToolUse

**触发时机**：工具执行前，权限检查前。

**作用**：
- 阻止工具调用（exit_code == 2 或 decision == "deny"）
- 修改工具输入（通过 updatedInput 字段）
- 审计日志（记录工具调用信息）

**匹配器**：`matcher` 字段匹配工具名（如 `"Bash"` 匹配 bash 工具调用）。

**stdin JSON**：
```json
{
  "session_id": "...",
  "cwd": "/current/working/directory",
  "hook_event_name": "PreToolUse",
  "tool_name": "bash",
  "tool_input": {"command": "rm -rf /tmp/test"},
  "tool_use_id": "toolu_xxx"
}
```

**stdout JSON 响应**（可选）：
```json
{
  "decision": "deny",
  "reason": "不允许执行 rm -rf"
}
```
或：
```json
{
  "decision": "allow",
  "updatedInput": {"command": "rm -r /tmp/test"}
}
```

### PostToolUse

**触发时机**：工具执行完成后。

**作用**：
- 审计日志
- 结果后处理
- exit_code == 2 时，stderr 追加到 tool_result 中作为警告

**额外 stdin 字段**：`tool_response` 包含工具执行结果。

### UserPromptSubmit

**触发时机**：用户提交 prompt 后，发送 API 前。

**作用**：
- 输入验证（exit_code == 2 阻断 prompt）
- 输入增强（stdout 追加到用户消息中）

**额外 stdin 字段**：`prompt` 包含用户输入。

**Hook 反馈注入**：exit_code == 0 且有 stdout 时，输出被包裹在 `<user-prompt-submit-hook>` 标签中注入到用户消息：
```
用户原始输入

<user-prompt-submit-hook>
hook 的 stdout 输出
</user-prompt-submit-hook>
```

System Prompt 中已声明：模型应将 hook 反馈视为来自用户的指令。

### Stop

**触发时机**：模型响应结束后（每个对话轮次结束）。

**作用**：输出验证、清理工作、审计。

### SessionStart

**触发时机**：会话开始时（交互模式启动或单次模式执行前）。

**作用**：环境初始化、加载外部上下文。

---

## 配置格式

在 `~/.claude/settings.json` 中配置（与 TS 版格式一致）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/validate.sh",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "write_file",
        "hooks": [
          {
            "type": "command",
            "command": "echo '{\"decision\":\"allow\"}'",
            "timeout": 3
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/audit.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/check-prompt.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/cleanup.sh",
            "async": true
          }
        ]
      }
    ]
  }
}
```

### 匹配器规则

| matcher 值 | 匹配逻辑 |
|-----------|---------|
| 未设置 / `null` / `""` / `"*"` | 匹配所有工具 |
| `"Bash"` / `"bash"` | 匹配指定工具名（不区分大小写） |

### Hook 配置选项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | string | `"command"` | Hook 类型（目前仅 command） |
| `command` | string | 必填 | Shell 命令 |
| `timeout` | int | `30` | 超时秒数 |
| `async` | bool | `false` | 是否异步执行（不阻塞主流程） |

---

## 核心函数

### `load_hooks_config() → dict[HookEvent, list[HookMatcher]]`

从 `settings.json` 读取 `hooks` 配置，解析为 `HookEvent → HookMatcher` 映射。忽略不支持的事件和格式错误的条目。

### `_get_matching_hooks(event, tool_name) → list[HookConfig]`

根据事件类型和工具名，从配置中筛选匹配的 hook 列表。

### `_execute_command_hook(command, input_json, timeout) → HookResult`

执行单个 command hook：
1. `asyncio.create_subprocess_shell()` 启动子进程
2. 通过 stdin 写入 JSON
3. `asyncio.wait_for()` 带超时等待
4. 解析 stdout 中的 JSON 响应（扫描第一行 `{` 开头的行）
5. 返回 `HookResult`

### `dispatch_hooks(event, ...) → list[HookResult]`

Hooks 分发主入口：
1. 加载配置，获取匹配的 hooks
2. 构建 stdin JSON
3. 异步 hooks：`asyncio.create_task()` spawn 但不等待
4. 同步 hooks：按顺序执行，遇到阻塞结果（exit_code == 2 或 decision == "deny"）短路返回
5. 返回所有同步 hook 的结果

---

## 执行流程

### 工具调用完整流程（含 Hooks）

```
模型返回 tool_use (name="bash", input={"command": "rm -rf /tmp/test"})
  │
  ▼
api.py: _execute_tools_concurrent()
  │
  ├─ find_tool_by_name("bash") → BashTool
  │
  ├─ dispatch_hooks(PreToolUse, tool_name="bash", tool_input={...})
  │   ├─ 匹配 hooks（如 matcher="Bash" 匹配）
  │   ├─ 执行 command hook，传入 stdin JSON
  │   └─ 收集 HookResult 列表
  │
  ├─ Hook 阻断检查
  │   ├─ exit_code == 2 或 decision == "deny"
  │   │   → 生成拒绝的 tool_result，跳过执行
  │   └─ updated_input 存在
  │       → 修改工具输入
  │
  ├─ [未被阻断] check_permission() 权限检查
  │   ├─ DENY → 拒绝
  │   ├─ ASK → 弹出用户确认
  │   └─ ALLOW → 继续
  │
  ├─ [权限通过] 执行 tool.call(command="rm -rf /tmp/test")
  │
  ├─ dispatch_hooks(PostToolUse, tool_response="执行结果")
  │   └─ exit_code == 2 → stderr 追加到结果中
  │
  └─ 返回 tool_result
```

### 用户输入流程（含 Hooks）

```
用户输入: "创建一个测试文件"
  │
  ▼
cli.py: _async_interactive()
  │
  ├─ dispatch_hooks(UserPromptSubmit, prompt="创建一个测试文件")
  │   ├─ exit_code == 2 → 阻断，跳过此输入
  │   └─ exit_code == 0 且有 stdout → 注入 <user-prompt-submit-hook>
  │
  ├─ [未被阻断] 构造 user message
  │   "创建一个测试文件"
  │   <user-prompt-submit-hook>
  │   hook 的输出内容
  │   </user-prompt-submit-hook>
  │
  ├─ 调用 query_with_tools()
  │
  ├─ 模型返回响应
  │
  └─ dispatch_hooks(Stop)
      └─ 审计/清理
```

### 会话生命周期

```
启动会话
  │
  ├─ storage.start_session()
  ├─ dispatch_hooks(SessionStart)
  │
  ├─ [交互循环]
  │   ├─ 用户输入 → dispatch_hooks(UserPromptSubmit)
  │   ├─ API 调用 → 工具执行
  │   │   ├─ dispatch_hooks(PreToolUse)
  │   │   ├─ 权限检查
  │   │   ├─ 执行工具
  │   │   └─ dispatch_hooks(PostToolUse)
  │   └─ dispatch_hooks(Stop)
  │
  └─ 退出
```

---

## Exit Code 语义

与 TS 版一致的退出码约定：

| Exit Code | PreToolUse | PostToolUse | UserPromptSubmit | Stop |
|-----------|-----------|------------|-----------------|------|
| **0** | 允许（可选修改输入） | 成功，结果不变 | 成功，stdout 注入消息 | 成功 |
| **2** | 阻断工具调用 | stderr 追加到结果 | 阻断 prompt | stderr 显示给用户 |
| **其他** | 记录警告，继续 | 记录警告，继续 | 记录警告，继续 | 记录警告 |

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| Hook 命令不存在 | 记录警告，跳过（非阻塞） |
| Hook 超时 | kill 进程，返回 exit_code=-1（非阻塞） |
| Hook 崩溃（exit != 0, != 2） | 记录 warning 日志，继续执行 |
| Hook 返回非法 JSON stdout | 忽略 stdout，继续 |
| Hook 返回 exit_code == 2 | 阻塞：拒绝工具/阻止 prompt |
| settings.json hooks 格式错误 | 跳过格式错误条目，解析正确的 |

---

## Hook 反馈到 LLM 的渠道

| 渠道 | 场景 | 格式 |
|------|------|------|
| tool_result 修改 | PreToolUse hook 拒绝工具调用 | `"Hook blocked: <reason>"` 作为 tool_result |
| user message 增强 | UserPromptSubmit hook 返回内容 | `<user-prompt-submit-hook>` 标签包裹 |
| tool_result 追加 | PostToolUse hook 报告问题 | `[Hook warning: <stderr>]` 追加到结果 |

System Prompt 中已声明（Section 2 System）：
> Treat feedback from hooks, including `<user-prompt-submit-hook>`, as coming from the user.

---

## 与 TS 版的对应关系

| Python | TypeScript | 功能 |
|--------|-----------|------|
| `hooks.py` | `services/hooks/index.ts` | Hooks 系统入口 |
| `HookEvent` | `HookEventName` | 事件类型枚举 |
| `HookConfig` | `CommandHookConfig` | Hook 命令配置 |
| `HookMatcher` | `HookMatcher` | 匹配器 |
| `HookResult` | `HookResult` | 执行结果 |
| `load_hooks_config()` | `getHooks()` | 配置加载 |
| `_get_matching_hooks()` | `resolveHookConfig()` | 匹配器解析 |
| `_execute_command_hook()` | `runCommandHook()` | 子进程执行 |
| `dispatch_hooks()` | `dispatchHooks()` | 事件分发 |

**TS 版支持但 Python 版暂不支持**：
- 更多事件（33 个 vs 5 个）：PostToolUseFailure、PermissionRequest、SubagentStart/Stop、ConfigChange、FileChanged 等
- 更多 hook 类型：prompt（LLM 评估）、http（HTTP 请求）、agent（代理验证）、function/callback
- 高级特性：asyncRewake（异步唤醒）、once（一次性 hook）、statusMessage（自定义状态消息）

---

## 配置示例

### 阻止危险 Bash 命令

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "echo \"$INPUT\" | python3 -c \"import sys,json; d=json.load(sys.stdin); cmd=d.get('tool_input',{}).get('command',''); sys.exit(2 if 'rm -rf' in cmd else 0)\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### 审计所有工具调用

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "echo \"$INPUT\" >> /tmp/claude_audit.log",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

### 用户输入增强

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Please respond in Chinese.'",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```
