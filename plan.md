# cc_python 开发计划

## 实现总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1. 工具调用 | 工具定义 + 工具调用循环 | ✅ 已完成 |
| 2. System Prompt 完善 | 补全完整系统指令 | ✅ 已完成 |
| 3. 权限系统 | 工具执行前询问用户确认 | 🔲 未实现 |
| 4. Hooks 系统 | 用户可配置的 shell 命令钩子 | 🔲 未实现 |
| 5. CLAUDE.md | 项目级持久化指令读取注入 | 🔲 未实现 |
| 6. 上下文压缩 | 对话过长时自动压缩历史消息 | 🔲 未实现 |
| 7. Message + Attachments 增强 | 消息规范化 + 附件处理 | 🔲 未实现 |
| 8. 高级工具 | Agent/Task/AskUserQuestion | 🔲 未实现 |
| 9. MCP + Skills + Commands | MCP 集成、Skill 系统、Slash 命令 | 🔲 未实现 |

---

## 阶段 1: 工具调用 ✅

### 1.1 工具定义框架
- `src/cc_python/tools/base.py`：Tool Protocol 定义
  - `name`, `description`, `input_schema`, `is_concurrency_safe`, `call()`
  - 对应 TS: `Tool.ts`

### 1.2 基础工具（6 个）
- `tools/read_file.py` — 文件读取（带行号、offset/limit）
- `tools/write_file.py` — 文件写入（自动创建父目录）
- `tools/edit_file.py` — 精确字符串替换
- `tools/bash.py` — Shell 命令执行（异步、超时）
- `tools/glob_tool.py` — 文件模式匹配
- `tools/grep_tool.py` — 正则内容搜索

### 1.3 工具注册
- `tools/__init__.py`：`get_all_tools()`, `get_tools_api_schemas()`, `find_tool_by_name()`
- 对应 TS: `tools.ts` 的 `getAllBaseTools()` 和 `getTools()`

### 1.4 工具调用循环
- `api.py`：`query_with_tools()` — 流式响应 → 检测 tool_use → 执行 → 回传 → 循环
- 并发控制：安全工具并行（Semaphore=10），不安全工具串行
- 双提供商支持（Anthropic + OpenAI）
- 对应 TS: `query.ts` 主循环 + `services/tools/toolOrchestration.ts`

### 1.5 消息格式
- `messages.py`：用户/助手消息创建
- 对应 TS: `utils/messages.ts`（简化版，5512 行 → ~60 行）

### TS 源码对应关系

| Python | TS 源码 | 功能 |
|--------|---------|------|
| `tools/base.py` | `Tool.ts` | 工具基类定义 |
| `tools/*.py` | `tools/*Tool/` | 具体工具实现 |
| `tools/__init__.py` | `tools.ts` | 工具注册和获取 |
| `api.py` | `query.ts` + `services/api/claude.ts` | 工具调用循环 |
| `messages.py` | `utils/messages.ts` | 消息格式（简化版） |

---

## 阶段 2: System Prompt 完善 ✅

### 2.1 静态 Section 补齐
- `_INTRO_SECTION`：身份声明 + CYBER_RISK 安全边界 + URL 约束
- `_SYSTEM_SECTION`：加入 hooks 说明、CommonMark 渲染说明
- `_DOING_TASKS_SECTION`：加入 user help 段（/help + issue 反馈）
- `_ACTIONS_SECTION`：补齐 CI/CD、上传警告、授权范围、merge conflict 处理
- `_TONE_STYLE_SECTION`：加入 GitHub issue 格式、工具调用句号规则

### 2.2 动态 Section 新增
- `_get_env_info_section(model)`：环境信息（模型名、Claude 模型 ID、渠道、Fast mode）
- `get_session_guidance_section(enabled_tools)`：Agent/Skill/AskUser 指导
- `get_language_section(language)`：用户语言偏好
- `get_mcp_instructions_section()`：MCP Server（预留接口）
- `get_summarize_tool_results_section()`：工具结果保存提醒
- `load_memory_prompt()`：读取 `~/.claude/projects/*/memory/MEMORY.md`

### 2.3 `build_system_prompt()` 增强
- 签名改为 `build_system_prompt(model, enabled_tools, language)`
- 13 个 section 按序拼接，对齐 TS `getSystemPrompt()`

### TS 源码对应关系

| Python | TS 源码 |
|--------|---------|
| `context.py` | `constants/prompts.ts` + `constants/cyberRiskInstruction.ts` |
| `get_system_context()` | `context.ts` getSystemContext() |
| `get_git_status()` | `context.ts` getGitStatus() |
| `_INTRO_SECTION` | `getSimpleIntroSection()` |
| `_SYSTEM_SECTION` | `getSimpleSystemSection()` + `getHooksSection()` |
| `_DOING_TASKS_SECTION` | `getSimpleDoingTasksSection()` |
| `_ACTIONS_SECTION` | `getActionsSection()` |
| `_TOOL_USAGE_SECTION` | `getUsingYourToolsSection()` |
| `_TONE_STYLE_SECTION` | `getSimpleToneAndStyleSection()` |
| `_OUTPUT_EFFICIENCY_SECTION` | `getOutputEfficiencySection()` |
| `_get_env_info_section()` | `computeSimpleEnvInfo()` |
| `get_session_guidance_section()` | `getSessionSpecificGuidanceSection()` |
| `get_language_section()` | `getLanguageSection()` |
| `get_mcp_instructions_section()` | `getMcpInstructionsSection()` |
| `load_memory_prompt()` | `memdir/memdir.ts` loadMemoryPrompt() |

---

## 阶段 3: 权限系统 🔲

对应 TS: `utils/permissions/`（24 文件，~8000 行）+ `hooks/useCanUseTool.tsx`

### 3.1 权限模式定义
- 新建 `src/cc_python/permissions.py`
- 定义权限模式枚举：`default`, `plan`, `accept_edits`, `bypass`, `dont_ask`
- 对应 TS: `PermissionMode.ts`

### 3.2 权限规则引擎
- 规则类型：allow / deny / ask
- 规则来源优先级：cli_arg > session > local_settings > project_settings > user_settings > policy_settings
- 规则格式：`ToolName(pattern)` 如 `Bash(git push:*)`, `Read(*)`, `Edit(*)`
- 对应 TS: `PermissionRule.ts`, `permissionRuleParser.ts`, `permissionsLoader.ts`

### 3.3 工具权限检查流程
```
has_permission_to_use_tool(tool, input):
  1. 检查 deny 规则 → 命中则拒绝
  2. 检查 ask 规则 → 命中则询问用户
  3. 调用 tool.check_permissions(input) → 工具自身逻辑
  4. 应用模式变换（dont_ask → deny, bypass → allow）
  5. 检查 allow 规则 → 命中则放行
  6. 默认 → 询问用户
```
- 对应 TS: `permissions.ts` hasPermissionsToUseToolInner()

### 3.4 用户交互
- 在 `cli.py` 中实现权限提示 UI
- 用户选项：Allow once / Always allow / Deny / Always deny
- Always allow/deny 的规则持久化到 settings
- 对应 TS: `hooks/useCanUseTool.tsx`

### 3.5 安全检查
- 路径验证：防止访问 `.git/`, `.claude/`, shell 配置文件
- Bash 命令分类：危险命令（rm -rf, force-push）需额外确认
- 对应 TS: `permissions/pathValidation.ts`, `permissions/bashClassifier.ts`

### 3.6 集成到工具调用循环
- 修改 `api.py` 的 `_execute_tools_concurrent()`
- 执行前调用权限检查，被拒绝则返回错误结果给模型

### TS 源码对应关系

| Python (待实现) | TS 源码 | 功能 |
|----------------|---------|------|
| `permissions.py` | `utils/permissions/permissions.ts` | 权限检查主逻辑 |
| `PermissionMode` 枚举 | `PermissionMode.ts` | 权限模式定义 |
| `PermissionRule` 类 | `PermissionRule.ts` + `permissionRuleParser.ts` | 规则解析 |
| `check_path_safety()` | `permissions/pathValidation.ts` | 路径安全检查 |
| `classify_bash_command()` | `permissions/bashClassifier.ts` | Bash 命令分类 |
| 规则加载 | `permissionsLoader.ts` | 规则加载器 |

---

## 阶段 4: Hooks 系统 🔲

对应 TS: `utils/hooks.ts`（5022 行）

### 4.1 Hook 配置
- 从 `settings.json` 读取 hooks 配置
- 支持的事件：`PreToolUse`, `PostToolUse`, `Notification`, `Stop`
- 对应 TS: `utils/hooks.ts` 的 HookConfig 类型

### 4.2 Hook 执行器
- 新建 `src/cc_python/hooks.py`
- 执行用户配置的 shell 命令
- 捕获 stdout/stderr
- 超时控制
- 对应 TS: `utils/hooks.ts` executeHook()

### 4.3 Hook 集成
- PreToolUse hook：在工具执行前运行，可拦截（返回 deny/allow）
- PostToolUse hook：在工具执行后运行
- 将 hook 反馈注入上下文，模型视为用户指令
- 对应 TS: `services/tools/toolHooks.ts`

### 4.4 权限 Hook
- `PermissionRequest` 类型 hook 可在权限提示前拦截
- hook 返回 allow/deny 可跳过用户提示
- 对应 TS: `hooks/toolPermission/`

### TS 源码对应关系

| Python (待实现) | TS 源码 | 功能 |
|----------------|---------|------|
| `hooks.py` | `utils/hooks.ts` | Hook 配置和执行 |
| PreToolUse 集成 | `services/tools/toolHooks.ts` | 工具调用前后 hook |

---

## 阶段 5: CLAUDE.md 🔲

对应 TS: `utils/claudemd.ts`（1479 行）

### 5.1 CLAUDE.md 读取
- 新建 `src/cc_python/claudemd.py`
- 搜索路径：项目根目录 → 父目录 → `~/.claude/CLAUDE.md`
- 支持 `.claude/CLAUDE.md` 子目录位置
- 对应 TS: `utils/claudemd.ts` getClaudeMdContents()

### 5.2 注入上下文
- 将 CLAUDE.md 内容作为 system prompt 的一部分
- 文件变更时自动重新加载
- 对应 TS: `utils/claudemd.ts` 中通过 attachments 机制注入

---

## 阶段 6: 上下文压缩 🔲

对应 TS: `services/compact/`（9 文件，~3000 行）

### 6.1 自动压缩触发
- 新建 `src/cc_python/compact.py`
- 当消息接近上下文窗口限制时自动触发
- 对应 TS: `services/compact/autoCompact.ts`

### 6.2 压缩策略
- 保留最近 N 轮对话完整
- 早期对话生成摘要替换
- 工具结果压缩（保留关键信息，去除冗余输出）
- 对应 TS: `services/compact/compact.ts`

### 6.3 Micro Compact
- 单轮工具结果的精简
- 去除重复/冗长的工具输出
- 对应 TS: `services/compact/microCompact.ts`

### 6.4 压缩提示词
- 生成摘要用的 prompt 模板
- 对应 TS: `services/compact/prompt.ts`

### TS 源码对应关系

| Python (待实现) | TS 源码 | 功能 |
|----------------|---------|------|
| `compact.py` | `services/compact/compact.ts` | 压缩主逻辑 |
| 触发条件 | `services/compact/autoCompact.ts` | 自动触发 |
| micro compact | `services/compact/microCompact.ts` | 单轮精简 |
| 摘要 prompt | `services/compact/prompt.ts` | 摘要提示词 |

---

## 阶段 7: Message + Attachments 增强 🔲

### 7.1 Message 规范化增强
- 增强 `messages.py`
- 完整支持 tool_use / tool_result 消息构造
- 消息格式转换（Anthropic ↔ OpenAI）
- 对应 TS: `utils/messages.ts`（5512 行）

### 7.2 Tool Result Storage
- 新建 `src/cc_python/tool_result_storage.py`
- 大型工具结果独立存储到磁盘
- 上下文中只保留引用
- 对应 TS: `utils/toolResultStorage.ts`（1040 行）

### 7.3 Attachments
- 新建 `src/cc_python/attachments.py`
- 图片/PDF/文件附件处理
- MCP 指令 delta 注入
- 对应 TS: `utils/attachments.ts`（3997 行）

### 7.4 Session Storage 增强
- 增强 `session.py`
- 消息搜索、规范化存储
- 对应 TS: `utils/sessionStorage.ts`（5105 行）

---

## 阶段 8: 高级工具 🔲

### 8.1 Agent 工具
- 新建 `src/cc_python/tools/agent.py`
- 子代理系统：Explore / Plan / general-purpose
- 支持 worktree 隔离执行
- 对应 TS: `tools/AgentTool/`（~800 行）

### 8.2 Task 工具组
- 新建 `src/cc_python/tools/task_create.py` 等
- 任务列表 CRUD + 进度跟踪
- 对应 TS: `tools/TaskCreateTool/` 等 6 个工具（~1200 行）

### 8.3 AskUserQuestion 工具
- 新建 `src/cc_python/tools/ask_user.py`
- 向用户提问，获取反馈
- 对应 TS: `tools/AskUserQuestionTool/`

### 8.4 Plan Mode 工具
- 新建 `src/cc_python/tools/enter_plan.py` + `exit_plan.py`
- 规划模式：只读探索，不执行修改
- 对应 TS: `tools/EnterPlanModeTool/` + `tools/ExitPlanModeTool/`

### 8.5 NotebookEdit 工具
- 新建 `src/cc_python/tools/notebook_edit.py`
- Jupyter notebook 单元格编辑
- 对应 TS: `tools/NotebookEditTool/`

---

## 阶段 9: MCP + Skills + Commands 🔲

### 9.1 MCP 集成
- 新建 `src/cc_python/mcp/` 目录
- MCP Server 连接管理（stdio/SSE transport）
- MCP 工具发现和调用
- MCP 资源读取
- 对应 TS: `tools/MCPTool/` + `tools/ListMcpResourcesTool/` + `tools/McpAuthTool/`

### 9.2 Skills 系统
- 新建 `src/cc_python/skills.py`
- 从目录加载 skill 定义
- Skill 注册和调用
- 对应 TS: `skills/`（3 文件，~300 行）

### 9.3 Slash Commands
- 新建 `src/cc_python/commands/` 目录
- 实现 `/commit`, `/review`, `/init` 等命令
- 命令注册和分发
- 对应 TS: `commands/`（12 文件，~1500 行）

---

## TS 源码目录 → Python 模块映射总表

| TS 目录 | 行数(估) | Python 模块 | 阶段 |
|---------|---------|-------------|------|
| `tools/FileReadTool/` | ~300 | `tools/read_file.py` | 1 ✅ |
| `tools/FileWriteTool/` | ~200 | `tools/write_file.py` | 1 ✅ |
| `tools/FileEditTool/` | ~300 | `tools/edit_file.py` | 1 ✅ |
| `tools/BashTool/` | ~500 | `tools/bash.py` | 1 ✅ |
| `tools/GlobTool/` | ~200 | `tools/glob_tool.py` | 1 ✅ |
| `tools/GrepTool/` | ~300 | `tools/grep_tool.py` | 1 ✅ |
| `services/tools/toolOrchestration.ts` | ~188 | `api.py` 并发部分 | 1 ✅ |
| `constants/prompts.ts` | ~800 | `context.py` | 2 ✅ |
| `utils/permissions/` | ~8000 | `permissions.py` | 3 🔲 |
| `hooks/useCanUseTool.tsx` | ~203 | `permissions.py` | 3 🔲 |
| `utils/hooks.ts` | ~5022 | `hooks.py` | 4 🔲 |
| `services/tools/toolHooks.ts` | ~300 | `hooks.py` | 4 🔲 |
| `utils/claudemd.ts` | ~1479 | `claudemd.py` | 5 🔲 |
| `services/compact/` | ~3000 | `compact.py` | 6 🔲 |
| `utils/messages.ts` | ~5512 | `messages.py` 增强 | 7 🔲 |
| `utils/attachments.ts` | ~3997 | `attachments.py` | 7 🔲 |
| `utils/sessionStorage.ts` | ~5105 | `session.py` 增强 | 7 🔲 |
| `utils/toolResultStorage.ts` | ~1040 | `tool_result_storage.py` | 7 🔲 |
| `tools/AgentTool/` | ~800 | `tools/agent.py` | 8 🔲 |
| `tools/TaskCreateTool/` 等 | ~1200 | `tools/task_*.py` | 8 🔲 |
| `tools/AskUserQuestionTool/` | ~200 | `tools/ask_user.py` | 8 🔲 |
| `tools/EnterPlanModeTool/` 等 | ~300 | `tools/enter_plan.py` | 8 🔲 |
| `tools/MCPTool/` 等 | ~600 | `mcp/` | 9 🔲 |
| `skills/` | ~300 | `skills.py` | 9 🔲 |
| `commands/` | ~1500 | `commands/` | 9 🔲 |
| `memdir/` | ~1500 | `context.py` load_memory | 2 ✅(基础) |
