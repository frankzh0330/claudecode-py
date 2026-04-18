# TermPilot

[English](README.md) | [简体中文](README.zh-CN.md)

## 概览

`TermPilot` 是一个用 Python 实现的终端 AI 编程助手。它可以读取和编辑文件、执行命令、搜索代码、调用外部工具，并在命令行中管理长时间运行的编码任务。

目前已可用于日常编码任务，并持续向更完善的终端 agent 体验演进。

## 功能特性

- 多 Provider API 支持：Anthropic、OpenAI、OpenAI 兼容接口，以及智谱 GLM 等兼容 Provider
- 流式响应，支持 Markdown 终端渲染
- 默认安静型终端 UI：阶段状态、紧凑工具卡片、按需展开完整结果
- 工具调用循环：模型可以反复调用工具，直到任务完成
- 并发工具执行：安全工具并行执行，不安全工具串行执行
- 权限系统：五种模式、持久化规则、路径验证、危险命令检测
- Hook 系统：围绕 prompt 和工具调用的 shell 命令钩子
- 自动加载 `CLAUDE.md` 项目级持久化指令
- 长对话上下文压缩
- 会话持久化：可恢复的 JSONL 历史记录和自动生成的对话标题
- MCP 集成：动态工具和资源
- Skills 和 Slash 命令
- 持久化记忆、撤销快照、Token/费用追踪、大型工具结果存储、附件、子代理

## 可用工具

| 工具 | 名称 | 说明 | 并发安全 | 需确认 |
|------|------|------|----------|--------|
| 目录摘要 | `list_dir` | 用摘要方式查看目录结构，避免直接输出整段 `ls` / `find` 结果 | ✅ | ❌ |
| 读取文件 | `read_file` | 读取文件内容，支持行号、`offset` 和 `limit` | ✅ | ❌ |
| 写入文件 | `write_file` | 创建或覆盖文件，自动创建父目录 | ❌ | ✅ |
| 编辑文件 | `edit_file` | 精确字符串替换，支持 `replace_all` | ❌ | ✅ |
| 执行命令 | `bash` | 执行 shell 命令，支持超时 | ❌ | ✅ |
| 文件搜索 | `glob` | 使用 glob 模式搜索文件 | ✅ | ❌ |
| 内容搜索 | `grep` | 使用正则表达式搜索文件内容 | ✅ | ❌ |
| 子代理 | `agent` | 启动递归子代理，用于探索、规划或实现 | ✅ | ❌ |
| 用户提问 | `ask_user_question` | 向用户提出一个聚焦的后续问题 | ✅ | ❌ |
| 任务管理 | `task_create`, `task_update`, `task_list`, `task_get` | 创建和管理当前会话的任务项 | ✅ | ❌ |
| 规划模式 | `enter_plan_mode`, `exit_plan_mode` | 进入或退出规划模式 | ✅ | ❌ |
| Notebook 编辑 | `notebook_edit` | 编辑 Jupyter notebook 单元格 | ❌ | ✅ |
| Web 搜索 | `web_search` | 搜索网页，支持域名过滤 | ✅ | ❌ |
| Web 抓取 | `web_fetch` | 抓取 URL，SSRF 防护，转换为 Markdown | ✅ | ❌ |
| MCP 工具 | `mcp__*__*` | MCP 服务器暴露的动态工具 | ✅ | ❌ |
| MCP 资源 | `list_mcp_resources`, `read_mcp_resource` | 列出和读取 MCP 资源 | ✅ | ❌ |
| Skill 工具 | `skill` | 调用可复用的 skill prompt | ✅ | ❌ |

## 快速开始

### 环境要求

- Python 3.10+
- `pip`

### 安装

```bash
pip install termpilot
```

后续升级：

```bash
pip install -U termpilot
```

### 配置

首次启动时，TermPilot 会引导你完成交互式配置：

```bash
termpilot
```

你会看到一个 Provider 选择器：

```
? Select your LLM provider:
  > OpenAI
    Anthropic (Claude)
    Zhipu GLM
    DeepSeek
    Qwen / DashScope
    Moonshot / Kimi
    SiliconFlow
    OpenRouter
    Groq
    Together
    Fireworks
    Ollama (local)
    vLLM (local)
    OpenAI-compatible (custom)
```

选择你的 Provider，输入 API Key，即可开始使用。

如需重新配置：

```bash
termpilot model
```

或手动编辑 `~/.termpilot/settings.json`：

```json
{
  "provider": "zhipu",
  "env": {
    "ZHIPU_API_KEY": "your-api-key",
    "ZHIPU_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
    "ZHIPU_MODEL": "glm-5.1"
  }
}
```

所有 Provider 均使用 OpenAI 兼容 API 格式。支持的 Provider：OpenAI、Anthropic、智谱 GLM、DeepSeek、Qwen/DashScope、Moonshot/Kimi、SiliconFlow、OpenRouter、Groq、Together、Fireworks、Ollama、vLLM，以及任何自定义 OpenAI 兼容接口。

环境变量优先级高于 `settings.json`。

### 运行

```bash
termpilot
termpilot -p "读取 main.py"
termpilot -m gpt-4o
termpilot --resume
termpilot -s <session-id>
```

### 常用 Slash 命令

| 命令 | 说明 |
|------|------|
| `/details last`、`/details <n>` | 查看最近一次或指定某次工具调用的完整输出 |
| `/help` | 显示可用命令 |
| `/compact` | 手动触发上下文压缩 |
| `/clear` | 清除对话历史 |
| `/config` | 显示当前配置 |
| `/skills` | 列出可用 skills |
| `/mcp` | 显示 MCP 服务器状态 |
| `/undo` | 恢复上一次文件快照 |
| `/commit` | AI 生成 git commit |
| `/init` | 为当前项目生成指令模板 |
| `/exit`、`/quit` | 退出程序 |

## 项目结构

```text
src/termpilot/
├── cli.py            # CLI 入口，REPL，权限 UI，slash commands
├── api.py            # 工具循环，流式响应，hooks，编排
├── context.py        # System prompt 构建器
├── config.py         # 配置和环境变量解析
├── hooks.py          # Hook 系统
├── permissions.py    # 权限引擎
├── messages.py       # 消息规范化和辅助工具
├── session.py        # 会话持久化
├── compact.py        # 上下文压缩
├── token_tracker.py  # Token 计数和费用追踪
├── skills.py         # Skill 加载和注册
├── commands.py       # Slash 命令
├── claudemd.py       # CLAUDE.md 加载
├── mcp/              # MCP 客户端、传输和配置
└── tools/            # 核心工具、Web 工具、高级工具、MCP 适配器
```

## 架构概览

主要运行流程：

1. `cli.py` 收集用户输入并渲染流式输出。
2. `context.py` 从环境、配置、记忆和项目指令构建 system prompt。
3. `api.py` 调用模型，收集 `tool_calls`，并编排工具执行。
4. `hooks.py` 运行 pre/post hooks 和 prompt/session hooks。
5. `permissions.py` 决定每个工具调用是被允许、拒绝还是需要确认。
6. 工具结果返回给模型，直到模型停止请求工具。

更详细的模块分解见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 文档导航

- [ARCHITECTURE.md](ARCHITECTURE.md)：分层、数据流和模块职责
- [docs/golden-rules.md](docs/golden-rules.md)：机械化编码规则
- [docs/conventions.md](docs/conventions.md)：命名和组织约定
- [docs/hooks.md](docs/hooks.md)：Hook 设计和行为
- [docs/claudemd.md](docs/claudemd.md)：项目指令加载
- [docs/compact.md](docs/compact.md)：压缩策略
- [docs/mcp.md](docs/mcp.md)：MCP、Skills 和命令
- [docs/system_prompt_sections.md](docs/system_prompt_sections.md)：System Prompt 各 Section

## 开发状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | 工具框架和核心工具 | ✅ |
| 2 | System Prompt 各 Section | ✅ |
| 3 | 权限系统 | ✅ |
| 4 | Hooks 系统 | ✅ |
| 5 | `CLAUDE.md` 加载 | ✅ |
| 6 | 上下文压缩 | ✅ |
| 7 | 消息和附件 | ✅ |
| 8 | 高级工具：agent、task、ask-user、plan | ✅ |
| 9 | MCP、Skills 和 Slash 命令 | ✅ |
| 10 | 剩余 TypeScript 对齐工作 | 🚧 进行中 |

## 参考实现

本项目仍在参考上游 TypeScript 实现进行优化，但 `TermPilot` 保持独立的产品标识、打包和文档。

## 交互说明

- 默认 CLI 体验是安静型的：长工具输出会被折叠成紧凑卡片，而不是整段直接打印。
- 当模型仍在理解上下文时，CLI 会显示短暂阶段状态，例如 `Coalescing…`、项目结构检查、结论整理。
- 权限确认使用可用方向键操作的菜单（`↑` / `↓` + Enter），不再要求输入数字。

## 开发

如果你想在本地开发 TermPilot：

```bash
git clone https://github.com/frankzh0330/termpilot.git
cd termpilot

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

本地质量检查：

```bash
python3 scripts/check.py
```

## 许可证

MIT
