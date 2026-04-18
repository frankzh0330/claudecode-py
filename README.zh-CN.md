# TermPilot

[English](README.md) | [简体中文](README.zh-CN.md)

`TermPilot` 是一个用 Python 实现、原生运行在终端中的 AI 编程助手。它可以在命令行里读取和编辑文件、执行命令、搜索代码、调用外部工具，并管理长会话编码任务。

它已经可以用于日常开发工作，并会继续朝着更完整、更稳定的终端 agent 体验演进。

## 功能特性

- **多 API Provider 支持** — 兼容 Anthropic、OpenAI 及兼容接口（如智谱 GLM）
- **流式响应** — 实时输出模型回复，支持 Markdown 渲染
- **工具调用循环** — 模型可自主调用工具，循环执行直到完成任务
- **并发工具执行** — 安全的工具（读文件、搜索）并行执行，不安全的工具串行执行
- **权限系统** — 5 种模式、12 个安全工具白名单、32 个危险命令检测、路径安全验证、规则持久化
- **Hooks 系统** — 用户可配置 shell 命令钩子，在工具调用前后、用户输入等事件触发
- **CLAUDE.md 加载** — 自动搜索并注入项目级持久化指令
- **上下文压缩** — 长对话自动压缩（micro-compact + LLM 摘要），防止超出上下文窗口
- **会话持久化** — 对话历史以 JSONL 格式保存（parentUuid 链表结构），支持恢复历史会话
- **MCP 集成** — 通过 Model Context Protocol 连接外部工具服务器（stdio/sse），动态发现工具和资源
- **Skills 系统** — 可复用的 prompt 模板，支持 Markdown + YAML frontmatter 定义
- **Slash Commands** — `/help`、`/compact`、`/clear`、`/config`、`/skills`、`/mcp`、`/undo`、`/commit`、`/init` 等命令
- **Memory 系统** — 4 种长期记忆类型（user/feedback/project/reference），持久化到磁盘
- **Undo 回退** — 文件修改前自动快照，`/undo` 回退到修改前状态（磁盘持久化）
- **Token 精确计数 + 费用追踪** — 从 API usage 提取真实 token 数，按模型定价计算费用
- **对话标题** — AI 自动生成 3-7 词会话标题
- **子代理** — Agent 工具支持递归工具调用（Explore/Plan/general-purpose）
- **20+ 工具** — 6 核心 + Web 搜索/抓取 + 高级工具（Agent/Task/Plan/Notebook）+ MCP 动态工具

## 已实现的工具

| 工具 | 名称 | 说明 | 并发安全 | 需权限确认 |
|------|------|------|----------|------------|
| 读取文件 | `read_file` | 读取文件内容（带行号），支持 offset/limit | ✅ | ❌ 自动放行 |
| 写入文件 | `write_file` | 创建或覆盖文件，自动创建父目录 | ❌ | ✅ 需确认 |
| 编辑文件 | `edit_file` | 精确字符串替换，支持 replace_all | ❌ | ✅ 需确认 |
| 执行命令 | `bash` | 执行 shell 命令，支持超时设置 | ❌ | ✅ 需确认 |
| 文件搜索 | `glob` | Glob 模式匹配搜索文件 | ✅ | ❌ 自动放行 |
| 内容搜索 | `grep` | 正则表达式搜索文件内容 | ✅ | ❌ 自动放行 |
| 子代理 | `agent` | 启动子代理（Explore/Plan/general-purpose），支持递归工具调用 | ✅ | ❌ 自动放行 |
| 向用户提问 | `ask_user_question` | 向用户提问获取反馈 | ✅ | ❌ 自动放行 |
| 任务管理 | `task_create/update/list/get` | 任务列表 CRUD + 进度跟踪 | ✅ | ❌ 自动放行 |
| 规划模式 | `enter_plan_mode` / `exit_plan_mode` | 只读规划模式，不执行修改 | ✅ | ❌ 自动放行 |
| Notebook 编辑 | `notebook_edit` | Jupyter notebook 单元格编辑 | ❌ | ✅ 需确认 |
| Web 搜索 | `web_search` | DuckDuckGo 搜索，支持域名过滤 | ✅ | ❌ 自动放行 |
| Web 抓取 | `web_fetch` | httpx + html→markdown，SSRF 防护 + 缓存 | ✅ | ❌ 自动放行 |
| MCP 工具 | `mcp__*__*` | MCP server 暴露的动态工具 | ✅ | ❌ 自动放行 |
| MCP 资源列表 | `list_mcp_resources` | 列出 MCP server 的资源 | ✅ | ❌ 自动放行 |
| MCP 资源读取 | `read_mcp_resource` | 读取 MCP server 的资源 | ✅ | ❌ 自动放行 |
| Skill 调用 | `skill` | 调用预定义的 skill prompt | ✅ | ❌ 自动放行 |

## 快速开始

### 环境要求

- Python >= 3.10
- `pip`

### 安装

```bash
pip install termpilot
```

后续升级可以直接执行：

```bash
pip install -U termpilot
```

### 配置

通过 `~/.termpilot/settings.json` 配置 API 密钥和 Provider：

```json
{
  "provider": "anthropic",
  "env": {
    "ANTHROPIC_API_KEY": "your-api-key-here"
  }
}
```

**使用 OpenAI 官方接口：**

```json
{
  "provider": "openai",
  "env": {
    "OPENAI_API_KEY": "your-openai-api-key",
    "OPENAI_MODEL": "gpt-4o"
  }
}
```

**切换为智谱 GLM：**

```json
{
  "provider": "zhipu",
  "env": {
    "ZHIPU_API_KEY": "your-zhipu-api-key",
    "ZHIPU_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
    "ZHIPU_MODEL": "glm-4-flash"
  }
}
```

**切换为通用 OpenAI 兼容接口：**

```json
{
  "provider": "openai_compatible",
  "env": {
    "OPENAI_API_KEY": "your-provider-key",
    "OPENAI_BASE_URL": "https://your-provider.example.com/v1",
    "OPENAI_MODEL": "your-model-name"
  }
}
```

支持的配置方式包括：

- `provider = "anthropic"` 搭配 `ANTHROPIC_*`
- `provider = "openai"` 搭配 `OPENAI_*`
- `provider = "openai_compatible"` 搭配 `OPENAI_*` 或 `TERMPILOT_*`
- `zhipu`、`deepseek`、`qwen`、`moonshot`、`siliconflow`、`openrouter`、`groq`、`together`、`fireworks`、`ollama`、`vllm` 等 provider 别名

> 环境变量优先级高于 settings.json。

### 运行

```bash
termpilot
termpilot -p "读取 main.py 的内容"
termpilot -m gpt-4o
termpilot --resume
termpilot -s <session-id>
```

**交互模式中的命令：**

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令 |
| `/compact` | 手动触发上下文压缩 |
| `/clear` | 清除对话历史 |
| `/config` | 显示当前配置 |
| `/skills` | 列出可用 skills |
| `/mcp` | 显示 MCP 服务器状态 |
| `/undo` | 回退最近一次文件修改 |
| `/commit` | AI 生成 git commit |
| `/init` | 为当前项目生成指令模板 |
| `/exit`、`/quit` | 退出程序 |
| `Ctrl+C` | 退出程序 |

## 项目结构

```text
termpilot/
├── pyproject.toml              # 项目配置与依赖
├── docs/
│   ├── system_prompt_sections.md  # System Prompt 详解
│   ├── hooks.md                   # Hooks 系统详解
│   ├── claudemd.md                # CLAUDE.md 加载详解
│   ├── compact.md                 # 上下文压缩详解
│   └── mcp.md                     # MCP/Skills/Commands 详解
└── src/termpilot/
    ├── __init__.py
    ├── __main__.py             # python -m 入口
    ├── cli.py                  # CLI 主界面（Click + Rich）
    ├── api.py                  # API 调用 + 工具调用循环
    ├── config.py               # 配置管理（settings.json / 环境变量）
    ├── context.py              # 系统上下文（OS 信息、Git 状态、System Prompt + Memory）
    ├── hooks.py                # Hooks 系统（事件钩子 + shell 命令执行）
    ├── compact.py              # 上下文压缩（micro-compact + LLM 摘要）
    ├── messages.py             # 消息格式化
    ├── permissions.py          # 权限系统（规则引擎 + 路径验证 + Bash 分类 + 持久化）
    ├── session.py              # 会话持久化（JSONL + parentUuid 链 + 标题生成）
    ├── undo.py                 # Undo 回退（磁盘持久化快照）
    ├── token_tracker.py        # Token 精确计数 + 费用追踪
    ├── skills.py               # Skills 系统（加载 + 注册 + 调用）
    ├── commands.py             # Slash Commands（解析 + 分派 + 内置命令）
    ├── claudemd.py             # CLAUDE.md 加载（项目级指令）
    ├── tool_result_storage.py  # 大型工具结果磁盘存储
    ├── attachments.py          # 文件附件处理
    ├── mcp/                    # MCP 子包
    │   ├── __init__.py         # MCPManager（连接管理）
    │   ├── client.py           # MCP 客户端（JSON-RPC）
    │   ├── transport.py        # 传输层（stdio/sse）
    │   └── config.py           # MCP 配置读取
    └── tools/
        ├── __init__.py          # 工具注册
        ├── base.py             # Tool 协议定义
        ├── read_file.py        # 文件读取工具
        ├── write_file.py       # 文件写入工具
        ├── edit_file.py        # 文件编辑工具
        ├── bash.py             # 命令执行工具
        ├── glob_tool.py        # 文件搜索工具
        ├── grep_tool.py        # 内容搜索工具
        ├── agent.py            # 子代理工具（递归工具调用）
        ├── ask_user.py         # 向用户提问工具
        ├── task.py             # 任务管理工具
        ├── enter_plan.py       # 进入规划模式
        ├── exit_plan.py        # 退出规划模式
        ├── notebook_edit.py    # Jupyter notebook 编辑
        ├── web_search.py       # Web 搜索工具
        ├── web_fetch.py        # Web 抓取工具
        ├── mcp_tool.py         # MCP 工具适配器
        ├── list_mcp_resources.py  # MCP 资源列表
        ├── read_mcp_resource.py   # MCP 资源读取
        └── skill_tool.py       # Skill 工具
```

## 架构概览

```text
用户输入
  │
  ▼
┌──────────┐  UserPromptSubmit Hook   ┌──────────────┐    流式请求
│  cli.py  │ ────────────────────────► │   api.py     │ ───────────► API
│  (REPL)  │                          │ (工具循环)    │
└──────────┘                          └──────┬───────┘
                                             │
                                       ┌─────▼──────┐
                                       │  API 响应   │
                                       │ (流式事件)  │
                                       └─────┬──────┘
                                             │
                                  ┌──────────┼──────────┐
                                  │ 纯文本    │          │ tool_use
                                  ▼          │          ▼
                             渲染输出        │   ┌────────────────┐
                                            │   │ PreToolUse Hook│
                                            │   │ (dispatch_hooks)│
                                            │   └───────┬────────┘
                                            │           │
                                            │     ALLOW ▼ BLOCKED
                                            │   ┌────────────────┐
                                            │   │   权限检查      │
                                            │   │ (check_permission)
                                            │   └───────┬────────┘
                                            │           │
                                            │     ALLOW ▼ ASK/DENY
                                            │   ┌────────────────┐
                                            │   │ 并发执行工具    │
                                            │   │ (safe=并行,    │
                                            │   │  unsafe=串行)  │
                                            │   └───────┬────────┘
                                            │           │
                                            │     ┌─────▼──────────┐
                                            │     │ PostToolUse Hook│
                                            │     └─────┬──────────┘
                                            │           │
                                            │     ┌─────▼─────┐
                                            │     │ tool_result │
                                            │     │ 回传 API    │
                                            │     └─────┬─────┘
                                            │           │
                                            └───────────┘
                                              (循环直到无 tool_use)
                                                  │
                                                  ▼
                                           Stop Hook
```

## 核心流程

1. **用户输入** → UserPromptSubmit Hook → 构造消息 + System Prompt
2. **调用 API** → 流式接收响应（文本 + tool_use blocks）
3. **PreToolUse Hook** → 用户配置的 shell 命令钩子，可阻断或修改工具调用
4. **权限检查** → 检查工具是否有权限执行（白名单/规则/用户确认）
5. **工具执行** → 按并发安全性分组，并行或串行执行
6. **PostToolUse Hook** → 工具执行后的审计/后处理钩子
7. **结果回传** → tool_result 追加到消息，再次调用 API
8. **循环** → 重复步骤 2-7，直到模型返回纯文本（不再调用工具）
9. **渲染输出** → Markdown 格式显示最终回复
10. **Stop Hook** → 响应结束后的清理/审计

---

## 权限系统

对应 TS: `utils/permissions/`（24 文件，~8000 行），Python 简化版保留核心流程。

### 涉及文件与职责

```text
permissions.py          ← 权限核心（类型定义 + 规则引擎 + 持久化）
  ↓
api.py                  ← 在工具执行循环中调用权限检查
  ↓
cli.py                  ← 用户确认 UI 回调 + 构建 PermissionContext
  ↓
~/.termpilot/settings.json ← 规则持久化存储
```

### 数据模型

```text
PermissionMode (5 种模式)
├── DEFAULT       标准模式，有副作用的工具需确认
├── ACCEPT_EDITS  自动放行工作目录内的文件编辑
├── BYPASS        跳过所有权限检查（子代理工具除外）
├── DONT_ASK      需要确认时自动拒绝
└── PLAN          规划模式，只允许只读工具

PermissionBehavior (3 种结果)
├── ALLOW  放行
├── DENY   拒绝
└── ASK    需要询问用户

PermissionRule (一条规则)
├── tool_name  "bash" / "write_file" / "edit_file"
├── behavior   ALLOW / DENY / ASK
├── pattern    "*" / "git push:*" / "/tmp/*"（支持通配符 *）
└── source     "cli" / "session" / "local" / "project" / "user" / "policy"

PermissionContext (一次会话的权限环境)
├── mode              当前权限模式
├── allow_rules       放行规则列表
├── deny_rules        拒绝规则列表
├── ask_rules         强制询问规则列表
├── working_directory 当前工作目录
└── disallowed_tools  CLI 参数禁止的工具集合

PermissionResult (一次检查的结果)
├── behavior     ALLOW / DENY / ASK
├── message      原因描述
└── rule_updates 用户选择的持久化规则（可选）
```

### 工具安全分类

```text
SAFE_TOOLS (自动放行，永不弹确认)
├── read_file    只读文件
├── glob         文件搜索
├── grep         内容搜索
├── task_create / task_update / task_list / task_get
├── ask_user_question
├── enter_plan_mode / exit_plan_mode
└── list_mcp_resources / read_mcp_resource

UNSAFE_TOOLS (默认需要确认)
├── write_file   创建/覆盖文件
├── edit_file    修改文件
├── bash         执行命令
└── notebook_edit Jupyter notebook 编辑

AGENT_TOOLS (默认不自动放行)
└── agent        子代理工具
```

### `check_permission()` 决策流程（11 步瀑布）

```text
输入: tool_name, tool_input, context
  │
  ├─ Step 1: tool_name in disallowed_tools (CLI --disallowed-tools)?
  │   YES → DENY
  │
  ├─ Step 2: tool_name in SAFE_TOOLS?
  │   YES → ALLOW（12 个安全工具永远放行）
  │
  ├─ Step 3: deny_rules 有匹配?
  │   YES → DENY + "被规则拒绝: bash(rm *)"
  │
  ├─ Step 4: mode == BYPASS 且不是 AGENT_TOOLS?
  │   YES → ALLOW
  │
  ├─ Step 5: mode == PLAN 且不是 SAFE_TOOLS?
  │   YES → DENY
  │
  ├─ Step 6: allow_rules 有匹配?
  │   YES → ALLOW
  │
  ├─ Step 7: ask_rules 有匹配?
  │   YES → ASK（强制询问）
  │
  ├─ Step 8: mode == ACCEPT_EDITS 且工具是 write_file/edit_file
  │          且路径在工作目录内（路径安全检查通过）?
  │   YES → ALLOW
  │
  ├─ Step 9: write_file/edit_file 且路径安全检查失败?
  │   YES → DENY（受保护文件/目录/路径穿越/shell展开）
  │
  ├─ Step 10: bash 且命令匹配高危模式?
  │   YES → ASK + "危险操作: ..."
  │
  └─ Step 11: tool_name in UNSAFE_TOOLS?
      YES → mode == DONT_ASK ? DENY : ASK
      NO  → ALLOW（未知工具默认放行）
```

优先级：**disallowed_tools > SAFE_TOOLS > deny 规则 > BYPASS > PLAN > allow 规则 > ask 规则 > ACCEPT_EDITS > 路径安全 > Bash 分类 > 默认策略**

### Bash 危险命令检测

| 模式 | 警告信息 |
|------|---------|
| `rm -rf` / `rm -f` | 递归强制删除 |
| `git push --force` | 强制推送 |
| `git reset --hard` | 硬重置 |
| `git push origin --delete` | 删除远程分支 |
| `git branch -D` | 强制删除分支 |
| `drop database` | 删除数据库 |
| `truncate table` | 清空表 |
| `kill -9` | 强制终止进程 |
| `dd if=` | dd 磁盘操作 |
| `sudo rm` | sudo 删除 |
| `chmod 777` | 递归设置 777 |
| `curl/wget ... \| sh` | 管道执行远程脚本 |

### 规则匹配

`_match_rule()` 三种匹配方式：

| pattern | 匹配逻辑 | 示例 |
|---------|---------|------|
| `*` | 匹配该工具的所有调用 | `Bash(*)` → 所有 bash 命令 |
| `git push:*` | Bash 专用，匹配命令前缀 | 匹配 `git push origin main` |
| `/tmp/*` | 文件工具专用，匹配路径前缀 | 匹配 `/tmp/a.txt`, `/tmp/dir/b.py` |
| `/tmp/a.py` | 精确匹配路径 | 只匹配 `/tmp/a.py` |

### 用户确认 UI

当权限检查结果为 ASK 时，弹出确认提示：

```text
────────────────── 权限请求 ──────────────────
write_file — 工具 write_file 需要用户确认
  文件: /tmp/test.txt

选择:
  [1] Allow once    (本次允许)
  [2] Always allow  (始终允许同类操作)
  [3] Deny          (拒绝)
  [4] Always deny   (始终拒绝同类操作)

选择 [1-4]:
```

- 选 `[2] Always allow` 或 `[4] Always deny` → 规则自动写入 `~/.termpilot/settings.json`
- 下次同类操作自动匹配规则，不再弹确认

### 权限配置

在 `~/.termpilot/settings.json` 中配置：

```json
{
  "permissions": {
    "mode": "default",
    "rules": [
      {"tool_name": "bash", "pattern": "git *", "behavior": "allow"},
      {"tool_name": "bash", "pattern": "rm *", "behavior": "deny"},
      {"tool_name": "write_file", "pattern": "*", "behavior": "allow"}
    ]
  }
}
```

效果：
- `git status` / `git push` 等命令自动放行（不弹确认）
- `rm` 命令直接拒绝（不弹确认）
- 文件写入自动放行
- 其他有副作用操作仍需确认

### 端到端调用链示例

```text
用户输入: "在 /tmp 创建一个文件"
  │
  ▼
cli.py: build_permission_context()
  ├─ 读取 ~/.termpilot/settings.json
  ├─ 解析 permissions.mode → PermissionMode
  ├─ load_permission_rules() → 按 behavior 分组
  └─ → PermissionContext(mode=DEFAULT)
  │
  ▼
api.py: _execute_tools_concurrent()
  ├─ 模型返回 tool_use(name="write_file", input={"file_path": "/tmp/test.txt"})
  │
  ├─ check_permission("write_file", input, ctx)
  │   Step 1: disallowed_tools? NO
  │   Step 2: SAFE_TOOLS? NO
  │   Step 3: deny_rules? NO
  │   Step 4: BYPASS? NO
  │   Step 5: PLAN? NO
  │   Step 6: allow_rules? NO
  │   Step 7: ask_rules? NO
  │   Step 9: path safety? OK
  │   Step 11: UNSAFE_TOOLS? YES → ASK
  │
  ├─ ASK → on_permission_ask() → 弹出确认 UI
  │   用户选 [1] Allow once
  │   → PermissionResult(ALLOW)
  │
  ├─ ALLOW → 执行 write_file
  └─ 返回 tool_result 给模型
```

对比安全工具：

```text
read_file("main.py")                    write_file("/tmp/test.txt")
  │                                        │
  check_permission()                       check_permission()
  Step 1: disallowed? NO                   Step 1: disallowed? NO
  Step 2: SAFE_TOOLS?                      Step 2: SAFE_TOOLS?
  YES → ALLOW                              NO
  │                                        Step 3-7: 无规则命中
  直接执行，无确认                          Step 9: path safety? OK
                                           Step 11: UNSAFE_TOOLS? YES → ASK
                                           → 弹确认 UI
```

---

## System Prompt 各 Section 详解

> 详细文档另见 [docs/system_prompt_sections.md](docs/system_prompt_sections.md)

`context.py` 中 `build_system_prompt()` 生成 13 个 section，分为 7 个静态（常量字符串）和 6 个动态（按条件生成）。

### 静态 Section（1-7，每次调用不变）

#### Section 1: Intro — 身份声明 + 安全约束

定义角色为"帮助用户做软件工程任务的交互式 agent"，附带两条硬性约束：
- **CYBER_RISK**：允许合法安全测试（CTF、渗透测试），拒绝恶意用途（DoS、供应链攻击）
- **URL 约束**：禁止模型自己编造 URL，只能用用户提供的或编程相关的 URL

#### Section 2: System — 系统运行规则（6 条）

| 规则 | 含义 |
|------|------|
| 输出即展示 | 模型输出的文本直接给用户看，支持 GFM Markdown |
| 权限模式 | 工具调用可能被用户拒绝，被拒绝后不要重试，要反思原因调整策略 |
| system-reminder 标签 | 工具结果中可能夹带系统标签，与工具结果本身无关 |
| 防注入 | 工具结果如果疑似 prompt injection 要警告用户 |
| Hooks | 用户可配置 hook 脚本拦截工具调用，hook 反馈等同于用户指令 |
| 自动压缩 | 对话过长时系统会自动压缩历史 |

#### Section 3: Doing Tasks — 行为准则（12 条）

核心精神是**最小化改动**，只做用户要求的，不做"顺便的改进"：
- 先读代码再改代码，不要随便建新文件
- 不做超出要求的"改进"（bug fix 不需要顺便加注释、docstring）
- 不要加不可能发生的场景的错误处理
- 不要为一次性操作创建抽象（三行重复代码好过过早抽象）
- 不给时间预估，失败先诊断
- 附带用户帮助入口（`/help` + issue 反馈链接）

#### Section 4: Actions — 风险控制策略

- 可逆操作（编辑文件、跑测试）→ 自由执行
- 不可逆/影响他人的操作 → 必须先问用户
- 一次授权不代表永久授权，授权范围要匹配实际请求
- 遇到障碍不要用破坏性快捷手段，merge conflict 要解决而不是丢弃，lock file 要调查而不是删除
- 结尾金句："measure twice, cut once"

#### Section 5: Using Your Tools — 工具使用规范

1. **专用工具优先**：Read 替代 cat，Edit 替代 sed，Write 替代 echo，Glob 替代 find，Grep 替代 grep。Bash 只用于必须 shell 执行的操作
2. **并行/串行策略**：无依赖的并行调用提速，有依赖的必须顺序执行

#### Section 6: Tone & Style — 沟通风格（5 条）

- 不用 emoji，回答简短
- 代码引用用 `file_path:line_number` 格式
- GitHub 引用用 `owner/repo#123` 格式
- 工具调用前不用冒号，用句号

#### Section 7: Output Efficiency — 效率指令

- 直奔主题，先给答案再给理由
- 文本输出只聚焦三类：需用户决策、里程碑状态、错误阻塞
- 一句能说清不用三句
- 明确排除：**不适用于代码和工具调用**

### 动态 Section（8-13，按条件生成或跳过）

#### Section 8: Session-specific Guidance

根据 `enabled_tools` 集合条件性生成，指导模型如何使用当前启用的工具：
- Agent 工具 → 子代理使用规范
- AskUserQuestion → 工具被拒后如何与用户沟通
- `!command` → 让用户自己执行 shell 命令的方式
- Skill → `/commit` 等 skill 调用机制

#### Section 9: Memory

读取 `~/.termpilot/projects/*/memory/MEMORY.md`。如果存在，将记忆内容注入 system prompt，告诉模型有持久化记忆系统可用。不存在则跳过。

#### Section 10: Environment Info

始终生成，包含：工作目录、平台、Shell、OS 版本、模型名、模型 ID 列表、TermPilot 渠道信息、Fast mode 说明、Git 状态、当前日期。

#### Section 11: Language

根据 `language` 参数控制输出语言。未设置时跳过。设置后用指定语言回复，技术术语和代码标识符保持原样。

#### Section 12: MCP Instructions

预留接口。MCP Server 实现后，会将 Server 自带的 usage instructions 注入 system prompt。当前返回 None 跳过。

#### Section 13: Summarize Tool Results

始终追加。提醒模型"工具结果可能会被自动清理，必须在回答中记下关键信息"。

### 静态 vs 动态总结

| 类型 | Section | 数据来源 |
|------|---------|---------|
| 静态 | 1-7 | 硬编码常量字符串 |
| 动态 | 8 Session Guidance | 依赖 `enabled_tools` 参数 |
| 动态 | 9 Memory | 依赖 `MEMORY.md` 是否存在 |
| 动态 | 10 Environment | 依赖 `get_system_context()` + `get_git_status()` |
| 动态 | 11 Language | 依赖 `language` 参数 |
| 动态 | 12 MCP Instructions | 依赖 MCP Server 连接（预留） |
| 动态 | 13 Summarize | 始终追加 |

---

## Hooks 系统

对应 TS: `services/hooks/`（~2000 行），Python 简化版保留核心流程。详细文档另见 [docs/hooks.md](docs/hooks.md)。

### 支持的事件

| 事件 | 触发时机 | 作用 |
|------|---------|------|
| `PreToolUse` | 工具执行前（权限检查前） | 可阻断/修改工具调用 |
| `PostToolUse` | 工具执行后 | 审计、后处理 |
| `UserPromptSubmit` | 用户提交 prompt 后 | 输入验证/增强 |
| `Stop` | 模型响应结束后 | 输出验证、清理 |
| `SessionStart` | 会话开始时 | 环境初始化 |

### 配置

在 `~/.termpilot/settings.json` 中配置（格式与 TS 版一致）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "/path/to/validate.sh", "timeout": 5}
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {"type": "command", "command": "/path/to/audit.sh"}
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {"type": "command", "command": "/path/to/check.sh"}
        ]
      }
    ]
  }
}
```

### Exit Code 语义

| Exit Code | 含义 |
|-----------|------|
| 0 | 成功，stdout 可包含 JSON 响应（如 `{"decision": "deny", "reason": "..."}`) |
| 2 | 阻塞错误，阻断工具调用或用户输入 |
| 其他 | 非阻塞错误，记录警告并继续 |

### Hook 执行流程

```text
PreToolUse Hook → 权限检查 → 工具执行 → PostToolUse Hook
     │                │                       │
   可阻断           可拒绝                  审计/警告
  (exit=2)        (DENY)               (exit=2 追加警告)
```

UserPromptSubmit Hook 的反馈通过 `<user-prompt-submit-hook>` 标签注入到用户消息中，模型将其视为用户指令。

---

## CLAUDE.md 加载系统

对应 TS: `utils/claudemd.ts`（~1500 行），Python 简化版保留核心功能。详细文档另见 [docs/claudemd.md](docs/claudemd.md)。

### 搜索路径

按优先级从低到高（后加载的覆盖先加载的）：

| 位置 | 类型 | 提交 git |
|------|------|---------|
| `~/.termpilot/CLAUDE.md` | 用户全局 | N/A |
| `~/.termpilot/rules/*.md` | 用户全局规则 | N/A |
| `CLAUDE.md`（项目各级目录） | 项目 | 是 |
| `.claude/CLAUDE.md`（项目各级目录） | 项目 | 是 |
| `CLAUDE.local.md`（项目各级目录） | 本地私有 | 否 |
| `.claude/rules/*.md`（项目各级目录） | 项目规则 | 是 |

### 注入方式

内容用 XML 标签包裹，作为 System Prompt Section 8.5 注入（Memory 之前）：

```text
<project>/path/to/CLAUDE.md</project>
项目级指令内容
</project>
```

---

## 与 TypeScript 版的对应关系

| Python | TypeScript | 功能 |
|--------|-----------|------|
| `cli.py` | `main.tsx` + `entrypoints/cli.tsx` | CLI 入口和 REPL |
| `api.py` | `query.ts` + `services/api/claude.ts` | API 调用和工具循环 |
| `config.py` | `utils/config.ts` + `utils/managedEnv.ts` | 配置管理 |
| `context.py` | `utils/systemPrompt.ts` + `constants/prompts.ts` | 系统上下文和 Prompt |
| `hooks.py` | `services/hooks/` | Hooks 系统（事件钩子） |
| `claudemd.py` | `utils/claudemd.ts` | CLAUDE.md 加载系统 |
| `compact.py` | `services/compact/`（9 文件） | 上下文压缩 |
| `skills.py` | `skills/`（3 文件） | Skills 系统 |
| `commands.py` | `utils/slashCommandParsing.ts` + `processSlashCommand.tsx` | Slash 命令 |
| `mcp/` | `services/mcp/`（~15 文件） | MCP 集成 |
| `tools/mcp_tool.py` | `tools/MCPTool/` | MCP 工具适配器 |
| `tools/skill_tool.py` | `tools/SkillTool/` | Skill 工具 |
| `permissions.py` | `utils/permissions/`（24 文件） | 权限系统 |
| `session.py` | `utils/conversation.ts` | 会话持久化 |
| `messages.py` | `utils/messages.ts` | 消息创建与规范化 |
| `tool_result_storage.py` | `utils/toolResultStorage.ts` | 大型工具结果磁盘存储 |
| `attachments.py` | `utils/attachments.ts` | 文件附件处理 |
| `tools/base.py` | `Tool.ts` | 工具基类定义 |
| `tools/*.py` | `tools/*Tool/` | 具体工具实现 |
| `tools/__init__.py` | `tools.ts` | 工具注册 |

## 开发计划

当前状态以阶段为准，旧的逐项 checklist 已不再精确反映实现进度：

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | 工具调用框架 + 6 个核心工具 | ✅ 已完成 |
| 2 | System Prompt（13 sections） | ✅ 已完成 |
| 3 | 权限系统 | ✅ 已完成 |
| 4 | Hooks 系统 | ✅ 已完成 |
| 5 | CLAUDE.md / AGENTS 指令加载 | ✅ 已完成 |
| 6 | 上下文压缩 | ✅ 已完成 |
| 7 | Message + Attachments + Tool Result Storage | ✅ 已完成 |
| 8 | 高级工具（Agent / Task / AskUser / Plan / Notebook） | ✅ 已完成 |
| 9 | MCP + Skills + Commands | ✅ 已完成 |
| 10 | 与 TS 版缺失模块对齐、持续补全细节 | 🚧 进行中 |

## 依赖

- [click](https://click.palletsprojects.com/) — CLI 框架
- [rich](https://rich.readthedocs.io/) — 终端渲染（Markdown、表格、面板）
- [openai](https://github.com/openai/openai-python) — OpenAI API SDK（所有 provider 统一使用 OpenAI 兼容格式）

## 许可证

MIT
