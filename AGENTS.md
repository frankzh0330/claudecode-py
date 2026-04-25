# TermPilot

一个运行在终端的 AI 编程助手，支持工具调用、权限系统和事件钩子。

> 本项目是 [TermPilot](https://github.com/frankzh0330/termpilot) 的 Python 实现，逐阶段开发中。

## 技术栈

- Python 3.10+ / asyncio
- click (CLI) + rich (终端渲染)
- questionary（Provider 配置向导 + 权限选择菜单）
- anthropic / openai SDK（API 调用，按需安装）

## 项目结构

```
src/termpilot/
├── cli.py            # CLI 入口 + quiet UI + 权限菜单 + slash commands
├── api.py            # 工具调用循环 + 流式响应 + UI 事件 + PreToolUse/PostToolUse hooks
├── context.py        # System Prompt 构建（13 个 section）
├── config.py         # 配置管理（settings.json + 环境变量）
├── hooks.py          # Hooks 系统（5 个事件，command 类型）
├── permissions.py    # 权限系统（4 种模式，8 步检查）
├── messages.py       # 消息格式化
├── session.py        # 会话持久化（JSONL）
├── compact.py        # 上下文压缩（micro-compact + full-compact）
├── token_tracker.py  # Token 精确计数 + 费用追踪
├── skills.py         # Skills 系统（磁盘加载 + frontmatter 解析）
├── commands.py       # Slash Commands（解析 + 分派 + skill 回退）
├── termpilotmd.py    # TERMPILOT.md 加载
├── mcp/              # MCP 子包
│   ├── __init__.py   # MCPManager（连接管理 + 工具收集）
│   ├── client.py     # MCP 客户端（JSON-RPC 通信）
│   ├── transport.py  # 传输层（stdio/sse）
│   └── config.py     # MCP 配置读取（settings.json + .mcp.json）
└── tools/            # 工具（含 list_dir + 核心工具 + Web + MCP 动态 + Skill）
```

## 关键文档

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块分层、依赖方向、数据流 |
| [docs/golden-rules.md](docs/golden-rules.md) | 机械化的编码规则 |
| [docs/conventions.md](docs/conventions.md) | 命名、模式、文件组织规范 |
| [docs/hooks.md](docs/hooks.md) | Hooks 系统详解 |
| [docs/termpilotmd.md](docs/termpilotmd.md) | TERMPILOT.md 加载系统详解 |
| [docs/compact.md](docs/compact.md) | 上下文压缩系统详解 |
| [docs/mcp.md](docs/mcp.md) | MCP/Skills/Commands 详解 |
| [docs/task-tool.md](docs/task-tool.md) | 任务管理、持久化和依赖图详解 |
| [docs/system_prompt_sections.md](docs/system_prompt_sections.md) | System Prompt 13 个 section 详解 |

## 开发状态

| 阶段 | 状态 |
|------|------|
| 1. 工具调用框架 + 6 个工具 | ✅ |
| 2. System Prompt（13 sections） | ✅ |
| 3. 权限系统 | ✅ |
| 4. Hooks 系统 | ✅ |
| 5. TERMPILOT.md 读取注入 | ✅ |
| 6. 上下文压缩 | ✅ |
| 7. Message + Attachments | ✅ |
| 8. 高级工具（Agent/Task/Plan） | ✅ |
| 9. MCP + Skills + Commands | ✅ |
| 10. 对齐 TS 版缺失模块（进行中） | 🔲 |

## TS 源码位置

每个 Python 模块的 docstring 都标注了对应的 TypeScript 参考实现文件。开发时参照 TS 版逻辑，Python 版做精简重写。

## 运行

```bash
python -m termpilot               # 交互模式
python -m termpilot -p "问题"      # 单次模式
python -m termpilot --resume       # 恢复会话
python -m termpilot model          # 重新配置 provider / API key
python3 scripts/check.py          # 质量检查
```

## 配置

`~/.termpilot/settings.json`，支持 Anthropic / OpenAI / 智谱 GLM 等接口。首次启动会引导交互式配置。

## CLI 交互约定

- 默认是安静型终端体验：优先显示阶段状态、紧凑工具卡片和最终结论，而不是整段原始工具输出。
- 目录/项目结构探索优先使用 `list_dir`、`glob`、`grep`、`read_file`，不要默认退回 `bash ls` / `find`。
- 权限确认使用方向键菜单，不再依赖数字输入。
- 如需查看完整工具输出，可使用 `/details last` 或 `/details <n>`。
