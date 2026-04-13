# 架构概览

本文档描述 cc_python 的分层架构、模块职责和依赖方向。

## 分层架构

```
┌──────────────────────────────────────────────────────┐
│                    CLI Layer                          │
│                    cli.py                             │
│  用户输入 · 权限确认 UI · UserPromptSubmit/Stop/      │
│  SessionStart hook dispatch · REPL 循环              │
├──────────────────────────────────────────────────────┤
│                    API Layer                          │
│                    api.py                             │
│  工具调用循环 · 流式响应 · PreToolUse/PostToolUse      │
│  hook dispatch · 权限检查 · 并发/串行工具执行          │
├──────────────────────────────────────────────────────┤
│                 Service Layer                         │
│  permissions.py ── 权限检查（8 步瀑布）               │
│  hooks.py      ── 事件钩子（5 种事件）                │
│  undo.py       ── 文件快照与回退                      │
├──────────────────────────────────────────────────────┤
│                 Context Layer                         │
│  context.py  ── System Prompt（13 sections）          │
│  config.py   ── 配置管理（settings.json）             │
│  messages.py ── 消息格式化                            │
│  session.py  ── 会话持久化（JSONL）+ 标题生成         │
├──────────────────────────────────────────────────────┤
│                 Tool Layer                            │
│  tools/*.py  ── 6 个核心工具 + 4 个高级工具            │
│  read_file · write_file · edit_file · bash ·          │
│  glob_tool · grep_tool · web_fetch · web_search        │
│  + Agent/Task/Plan/NotebookEdit/MCP 工具               │
└──────────────────────────────────────────────────────┘
```

## 依赖方向

**核心规则**：依赖只能向下，不能向上。

```
cli.py → api.py → permissions.py / hooks.py → tools/*.py
                 → context.py / config.py / messages.py / session.py
```

- CLI Layer 只依赖 API Layer
- API Layer 依赖 Service Layer 和 Context Layer
- Service Layer 不依赖 CLI Layer（权限/hooks 通过回调解耦）
- Tool Layer 不依赖任何上层

**横切关注点**：`config.py` 和 `messages.py` 被多层使用，不构成层级关系。

## 模块职责

### cli.py（CLI Layer）

入口和用户交互。负责：
- 命令行参数解析（click）
- REPL 循环（交互模式 / 单次模式）
- 权限确认 UI（`_permission_prompt`）
- Hook 事件分发：UserPromptSubmit、Stop、SessionStart
- 渲染输出（Markdown via rich）

对应 TS：`main.tsx` + `entrypoints/cli.tsx`

### api.py（API Layer）

工具调用核心循环。负责：
- 创建 API 客户端（Anthropic / OpenAI 格式）
- 流式响应处理（async generator）
- 工具调用编排：PreToolUse Hook → 权限检查 → 执行 → PostToolUse Hook
- 并发控制：安全工具并行、不安全工具串行
- 消息构造（tool_use / tool_result 格式）

对应 TS：`query.ts` + `services/api/claude.ts` + `services/tools/toolOrchestration.ts`

### permissions.py（Service Layer）

权限检查引擎。负责：
- 4 种权限模式（DEFAULT / ACCEPT_EDITS / BYPASS / DONT_ASK）
- 8 步瀑布式检查流程
- Bash 危险命令检测（13 种模式）
- 规则匹配（`*` / 命令前缀 / 路径前缀）
- 规则持久化到 `~/.claude/settings.json`

对应 TS：`utils/permissions/`（24 文件，~8000 行）

### hooks.py（Service Layer）

事件钩子系统。负责：
- 5 种 Hook 事件
- 从 settings.json 加载 hook 配置
- 异步子进程执行 command hook（stdin JSON → stdout JSON）
- Hook 结果处理（阻断 / 放行 / 修改输入）

对应 TS：`services/hooks/`

### undo.py（Service Layer）

文件修改快照与回退。负责：
- 内存栈式快照（上限 50 个）
- write_file/edit_file 修改前自动保存
- `/undo` 命令弹出快照恢复文件

对应 TS：`utils/diff.ts`（~5K 行，TS 版有完整 patch/diff 系统）

### context.py（Context Layer）

System Prompt 构建。负责：
- 13 个 section（7 静态 + 6 动态）
- 环境信息收集（OS、Git 状态、模型名）
- Memory 系统加载
- MCP Instructions（预留）

对应 TS：`utils/systemPrompt.ts` + `constants/prompts.ts`

### config.py（Context Layer）

配置管理。负责：
- 读取 `~/.claude/settings.json`
- API Key / Base URL / Model 解析（环境变量 + settings.json）
- 环境变量注入

对应 TS：`utils/config.ts` + `utils/managedEnv.ts`

### session.py（Context Layer）

会话持久化。负责：
- JSONL 格式存储对话历史
- 会话创建、恢复、列表
- 记录 user/assistant/tool 消息

对应 TS：`utils/conversation.ts`

## 数据流

### 工具调用完整流程

```
用户输入
  │
  ▼
cli.py: UserPromptSubmit Hook
  │
  ▼
cli.py → api.py: query_with_tools()
  │
  ├─ api.py: 流式调用 API
  │   ├─ 文本 → on_text() 回调 → cli.py 渲染
  │   └─ tool_use → 收集到 tool_use_blocks
  │
  ▼
api.py: _execute_tools_concurrent()
  │
  ├─ for each tool_use:
  │   ├─ PreToolUse Hook（dispatch_hooks）
  │   │   └─ 阻断？→ 返回拒绝的 tool_result
  │   ├─ 权限检查（check_permission）
  │   │   └─ DENY → 返回拒绝的 tool_result
  │   │   └─ ASK → on_permission_ask() → 用户确认
  │   ├─ 工具执行（tool.call()）
  │   │   └─ safe → 并行 · unsafe → 串行
  │   └─ PostToolUse Hook（dispatch_hooks）
  │
  ▼
tool_result 追加到消息 → 再次调用 API
  │
  ▼
循环直到无 tool_use → 返回最终文本
  │
  ▼
cli.py: Stop Hook
```

### 配置加载流程

```
~/.claude/settings.json
  │
  ├─ config.py: get_settings()
  │   ├─ get_settings_env() → API Key / Base URL / Model
  │   └─ apply_settings_env() → 注入 os.environ
  │
  ├─ permissions.py: build_permission_context()
  │   ├─ load_permission_rules() → allow/deny/ask 规则
  │   └─ PermissionMode 解析
  │
  └─ hooks.py: load_hooks_config()
      └─ 按 HookEvent 分组的 HookMatcher 列表
```

## 与 TS 版的对应关系

| Python 模块 | TS 源码 | TS 行数(估) |
|------------|---------|------------|
| `cli.py` | `main.tsx` + `entrypoints/cli.tsx` | ~3000 |
| `api.py` | `query.ts` + `services/api/claude.ts` + `toolOrchestration.ts` | ~3000 |
| `permissions.py` | `utils/permissions/`（24 文件） | ~8000 |
| `hooks.py` | `services/hooks/` | ~2000 |
| `context.py` | `utils/systemPrompt.ts` + `constants/prompts.ts` | ~1500 |
| `config.py` | `utils/config.ts` + `utils/managedEnv.ts` | ~500 |
| `session.py` | `utils/conversation.ts` + `utils/sessionTitle.ts` | ~2500 |
| `messages.py` | `utils/messages.ts` | ~5500 |
| `tools/*.py` | `tools/*Tool/` | ~2500 |
| `compact.py` | `services/compact/` | ~3000 |
| `token_tracker.py` | `utils/tokens.ts` + `utils/cost-tracker.ts` | ~2000 |
| `undo.py` | `utils/diff.ts` | ~5000 |
