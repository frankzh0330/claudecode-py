# TermPilot

一个运行在终端的 AI 编程助手，支持工具调用、权限系统和事件钩子。

> 本项目是终端 AI coding agent 的 Python 重写实现，逐阶段开发中。

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

## 编码规则

- 工具实现 `Tool` 协议（`tools/base.py`），不用深层继承
- 权限策略只在 `permissions.py` + `api.py`，工具内部不做权限判断
- Hook 逻辑只在 `hooks.py`，调用方消费结果而非重新实现
- System prompt 各 section 保持模块化，不写单体大函数
- 工具调用主循环集中在 `api.py`，不分散到多层
- 能用本地函数解决的（截断、验证、路径检查）不要加模型调用
- 持久化走现有子系统：session → `session.py`，undo → `undo.py`，大结果 → `tool_result_storage.py`
- 参照 TS 版逻辑做精简重写，保留行为和清晰度，不逐行搬运复杂度

## 编码约定

- 模块/函数: `snake_case`，类: `PascalCase`，常量: `UPPER_SNAKE_CASE`，私有: `_` 前缀
- import 顺序: `from __future__ import annotations` → 标准库 → 第三方 → 项目内
- 一个概念一个模块，工具在 `tools/`，MCP 在 `mcp/`，文档在 `docs/`
- match 当前代码风格，不做无关重构

## 关键文档

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块分层、依赖方向、数据流 |
| [docs/golden-rules.md](docs/golden-rules.md) | 编码规则完整版 |
| [docs/conventions.md](docs/conventions.md) | 命名和组织约定完整版 |
| [docs/hooks.md](docs/hooks.md) | Hooks 系统详解 |
| [docs/compact.md](docs/compact.md) | 上下文压缩系统详解 |
| [docs/mcp_skills.md](docs/mcp_skills.md) | MCP/Skills/Commands 详解 |
| [docs/task-tool.md](docs/task-tool.md) | 任务管理、持久化和依赖图 |
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
| 10. 对齐 TS 版缺失模块（Web 工具、Undo、Token 追踪、对话标题） | ✅ |
| 11. P0 核心能力补齐（子代理递归、链回溯、Undo 持久化、权限完善） | ✅ |

## TS 源码位置

每个 Python 模块的 docstring 都标注了对应的 TypeScript 参考实现文件。开发时参照 TS 版逻辑，Python 版做精简重写。

## 运行

```bash
python -m termpilot               # 交互模式
python -m termpilot -p "问题"      # 单次模式
python -m termpilot --resume       # 恢复会话
python -m termpilot model          # 重新配置 provider / API key
python3 scripts/check.py           # 质量检查
```

## 配置

`~/.termpilot/settings.json`，支持 Anthropic / OpenAI / 智谱 GLM 等接口。首次启动会引导交互式配置。
