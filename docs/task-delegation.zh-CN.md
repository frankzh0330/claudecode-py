# 任务委派与子代理路由

[English](task-delegation.md) | [简体中文](task-delegation.zh-CN.md)

本文说明 TermPilot 这次任务委派与子代理路由升级的背景、当前流程、已知问题、优化方案和实现步骤。

这次改造的目标是：当用户选择的 LLM 不太擅长主动规划、拆解任务或委派探索时，TermPilot 仍然能更稳定地处理复杂编码任务。第一阶段重点优化工具语义、system guidance、批量委派、任务追踪和 Quiet UI 展示；暂不引入真正的并行子代理执行。

## 背景

TermPilot 已经具备 terminal coding agent 的核心模块：

- 主 agent loop：可以调用工具、读文件、改代码、跑命令，并基于工具结果继续推理。
- 持久化会话和上下文压缩。
- 文件和 shell 操作权限系统。
- 基于 `~/.termpilot/projects/<cwd>/tasks.json` 的任务系统。
- 内置子代理：`Explore`、`Plan`、`Verification`、`general-purpose`。
- 从 `~/.termpilot/agents/*.md` 加载的自定义代理。

之前的版本直接将claude code设计和逻辑迁移过来，但在接入更多LLM后问题开始变得明显：在 Claude 模型上会做task拆分/spawn子agent的逻辑，部分模型却仍然倾向于在主循环里继续搜索和编辑；另一些模型能稳定使用直接文件/搜索工具，但不一定能持续判断什么时候应该拆任务、先规划或委派验证。这说明问题不只是实现缺口，而是产品体验问题：委派不能只是“可用”，还必须足够容易被模型选择。

但仅仅暴露一个泛化的 `agent` 工具并不够。强规划模型可能能自己判断什么时候应该委派；但 GLM-5.1 这类模型更容易一路使用 `glob`、`grep`、`read_file`、`bash` 在主循环里做到底。直接工具的语义很明确，而“启动 agent”需要模型先意识到当前任务值得委派。

任务拆解和委派应该是一等运行时能力，而不是完全依赖模型临场自觉。

## 之前的流程

改造前大致流程是：

1. 用户发送请求。
2. 主 agent 收到 system prompt、用户消息、可用工具和会话上下文。
3. 模型决定直接回答还是调用工具。
4. 对代码库探索类任务，模型通常调用 `glob`、`grep`、`read_file` 或 `bash`。
5. 如果模型选择 `agent`，TermPilot 使用 `subagent_type`、`description`、`prompt` 启动一个子代理。
6. 子代理在独立上下文中运行，并将最终结果返回给主 agent。
7. 主 agent 总结结果或继续执行。

这个流程在技术上可用，但太依赖模型主动性。工具存在，不代表模型总能理解何时应该使用它。

## 存在的问题

### 1. 委派语义过于抽象

旧版 `agent` 描述偏向“launch an agent”。这个说法没错，但操作性不强。模型需要自己判断：

- 当前任务是否足够复杂。
- 应该使用哪个子代理类型。
- 探索应留在主循环，还是委派给子代理。
- 实现前是否应该先委派规划。

对于自我评估和规划能力较弱的模型，这通常会导致主 agent 一路做到底。

### 2. 规划意图不够强

例如“规划一下添加 `/redo` 命令”这类请求，应该自然触发 `Plan`。但模型有时会把它当作代码搜索任务，先用 `grep` 或 `glob`。

问题不是 `Plan` 不存在，而是 runtime guidance 对路由规则说得不够硬。

### 3. 多方向探索缺少批量接口

当用户请求包含多个独立方向时，主 agent 过去只有两个不理想选择：

- 自己跑很多搜索，把主上下文塞满。
- 手动多次调用 `agent`。

缺少一个低摩擦方式表达：“把这三个独立方向委派出去，然后给我合并摘要。”

### 4. 任务追踪描述不够具体

`task_create`、`task_update`、`task_list` 已经支持持久化任务状态，但工具描述比较泛。它没有明确告诉模型：

- 3+ 步骤工作应该创建任务。
- 多文件修改应该创建任务。
- 同一时间只保留一个任务为 `in_progress`。
- 每完成一个阶段就标记为 `completed`。
- 长会话中用 `task_list` 恢复注意力。

所以任务系统“可用”，但不一定“会被稳定触发”。

### 5. Quiet UI 没有区分批量委派

Quiet UI 已经能显示单个 agent 卡片，但批量委派需要更清楚的展示：

- `Running 3 delegated agents...`
- 每个子任务一行摘要。
- 完整结果通过 `/details` 查看。

否则批量委派要么很吵，要么太不透明。

## 设计目标

这次升级遵循几个约束：

- 在不依赖更强模型的前提下，提高委派触发率和分发质量。
- 保留公开工具名 `agent`，避免破坏兼容性。
- 将工具语义重塑为 `delegate_task`。
- 保持保守的权限边界。
- 第一阶段不做真正并行。
- 子代理上下文和主上下文隔离。
- 禁止递归 agent spawning。
- 让 task 工具更像 todo 系统。
- 默认 UI 保持安静和紧凑。

## 新流程

改造后的目标流程是：

1. 用户发送请求。
2. Session guidance 告诉模型何时使用 `Plan`、`Explore`、`Verification`、task 工具或批量委派。
3. 如果是复杂实现任务，模型先创建 task list。
4. 如果是规划/设计请求，模型委派给 `Plan`。
5. 如果是大范围代码理解、架构分析或设计模式识别，模型委派给 `Explore`。
6. 如果是 review、测试或正确性检查，模型委派给 `Verification`。
7. 如果有多个独立探索方向，模型用 `agent.tasks` 一次委派多个子任务。
8. 第一阶段子代理串行运行，并返回结构化摘要。
9. Quiet UI 渲染一张紧凑的 delegation 卡片。
10. 主 agent 基于子代理结果继续执行或输出最终回答。

## 工具语义

### 单任务委派

旧调用方式仍然兼容：

```json
{
  "subagent_type": "Plan",
  "description": "Plan redo command",
  "prompt": "Design an implementation plan for adding a /redo command to TermPilot."
}
```

模型仍然可以像以前一样调用 `agent`。不同点在于工具描述现在强调“任务委派”，而不是泛泛的“启动 agent”。

### 批量委派

新的 `tasks` 字段支持一次最多三个独立子任务：

```json
{
  "tasks": [
    {
      "subagent_type": "Explore",
      "description": "Inspect commands",
      "prompt": "Find how slash commands are registered and executed."
    },
    {
      "subagent_type": "Explore",
      "description": "Inspect session rewind",
      "prompt": "Find how session rewind and parentUuid traversal work."
    },
    {
      "subagent_type": "Verification",
      "description": "Review tests",
      "prompt": "Check which tests cover command execution and session persistence."
    }
  ]
}
```

第一阶段行为刻意保持串行：

- `tasks` 存在时，忽略顶层 `subagent_type`、`description`、`prompt`。
- 每个任务都有独立结果。
- 单个子任务失败不会阻止其他任务继续执行。
- 工具返回包含 `delegated_tasks` 和 `summary` 的 JSON 结果。
- 超过三个任务会被拒绝，避免失控委派。

## 内置代理角色

### Explore

用于大范围只读代码探索：

- 项目架构。
- 设计模式。
- 命令系统结构。
- 文件关系。
- 主 agent 可能需要多次 `glob` / `grep` 的大型搜索。

允许工具：

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### Plan

用于用户请求规划、设计、实现策略或“应该怎么做”的场景。

规划意图优先于探索意图。即使一个 plan 需要先读代码，也应该使用 `Plan`。

允许工具：

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### Verification

用于实现后检查，或用户明确要求验证正确性：

- 检查 diff。
- 跑定向测试。
- 找回归。
- 识别缺失覆盖。

允许工具：

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### general-purpose

用于不是纯规划、探索或验证的复杂自主任务。

允许工具：

- 除 `agent` 以外的所有常规工具，避免递归 spawning。

### 自定义代理

自定义代理定义在：

```text
~/.termpilot/agents/*.md
```

frontmatter 可以声明名称、描述和允许工具。这些代理会动态加入 `agent` schema。

## Task 工具变化

任务系统现在更像复杂编码任务的 todo list。

### 何时创建任务

模型会被引导在以下场景创建任务：

- 三个或更多步骤。
- 多文件修改。
- 多个用户目标。
- 长时间探索。
- 完成前需要测试或验证。

### 任务状态纪律

模型会被引导：

- 同一时间只保留一个任务为 `in_progress`。
- 当前阶段完成后立即标记为 `completed`。
- 长会话中使用 `task_list` 恢复注意力。

这让任务系统既能帮助模型规划，也能帮助用户观察进度。

## System Prompt Guidance

会话 guidance 现在包含更强的路由规则：

- 规划/设计请求使用 `Agent` + `subagent_type=Plan`。
- 整体项目阅读、架构分析或设计模式识别使用 `Explore`。
- 正确性检查、测试、review、回归搜索使用 `Verification`。
- 超过三次查询的大范围搜索应委派给 `Explore`。
- 多个独立方向使用 `agent.tasks`。
- 复杂实现工作先创建 task list。
- `task_update` 同一时间只保持一个活跃任务。

这些规则刻意更直接，目标是帮助不太主动拆解任务的模型。

## Quiet UI 变化

单 agent 调用继续显示 agent 卡片：

```text
Running Explore agent: Inspect command system...
```

批量委派显示为紧凑分组卡片：

```text
Running 3 delegated agents...

1. Explore - Inspect commands (completed)
2. Explore - Inspect session rewind (completed)
3. Verification - Review tests (failed)
Summary: 2/3 succeeded
```

完整输出仍可通过以下命令查看：

```text
/details <n>
```

这样主终端保持安静，同时不丢失完整子代理结果。

## 实现步骤

### 1. 更新 Agent 工具描述

`agent` 工具描述被改写，强调：

- 委派。
- 独立子代理上下文。
- 只返回最终摘要。
- 何时使用各内置代理。
- 何时不应委派。
- 自定义代理支持。

### 2. 给 Agent Schema 增加 `tasks`

schema 现在支持：

- 旧单任务字段：`subagent_type`、`description`、`prompt`。
- 新批量字段：`tasks`。

顶层 required 字段被移除，使 batch 调用合法；具体校验在 `AgentTool.call()` 内完成。

### 3. 实现串行批量执行

`AgentTool.call()` 会检查 `tasks`：

1. 校验 batch 大小。
2. 遍历每个子任务。
3. 校验 `prompt` 和 `subagent_type`。
4. 使用 `_run_agent()` 执行子代理。
5. 逐项记录成功或失败。
6. 返回 JSON 汇总。

### 4. 给只读代理增加 `list_dir`

`Explore`、`Plan`、`Verification` 现在允许使用 `list_dir`，用于结构化目录摘要，减少 raw `ls` / `find` 输出。

### 5. 强化 Task 工具描述

`task_create`、`task_update`、`task_list` 的描述现在明确说明：

- todo 风格用法。
- 复杂工作触发场景。
- 单活跃任务纪律。
- 注意力恢复。

任务数据模型不需要改变。

### 6. 扩展 Session Guidance

`get_session_guidance_section()` 现在提供 Plan、Explore、Verification、批量委派和 task list 使用规则。

### 7. 更新 Quiet UI

UI 现在会识别带 `tasks` 数组的 `agent` 调用，并显示为 `Delegation` 卡片，同时解析 JSON 结果，为每个子任务展示一行摘要。

## 测试

新增或更新的测试覆盖：

- `AgentTool.input_schema` 包含 `tasks`。
- 单 agent 调用保持兼容。
- 批量委派能执行多个任务。
- 未知子代理类型只让单项失败，不影响其他项。
- batch 大小限制。
- Task 工具描述包含 todo 风格 guidance。
- Session guidance 包含路由规则。
- Quiet UI 能总结批量委派。

定向测试命令：

```bash
PYTHONPATH=src uv run pytest \
  tests/tools/test_agent_tool.py \
  tests/tools/test_task.py \
  tests/test_context.py::TestSessionGuidance \
  tests/test_ui_delegation.py \
  -q
```

当前定向结果：

```text
23 passed, 1 skipped
```

跳过项依赖测试环境中是否安装可选 UI 依赖 `rich`。

## 当前限制

### 暂无真正并行

批量委派第一阶段是串行的。真正并行需要额外处理：

- UI 事件交错。
- 权限提示。
- 工具结果顺序。
- 共享 workspace 写入。
- 取消和超时。

### 子代理边界保守

只读代理保持只读。`general-purpose` 可以使用更广工具，但禁止递归调用 `agent`。

### 仍依赖模型遵循

runtime 已经给出更清晰的指令和 schema，但 LLM 仍然负责决定是否调用工具。这次改造提高正确委派概率，但还不是确定性的前置路由器。

### 暂无写文件 Worker 隔离

这一阶段没有引入带独立权限边界的写文件 worker agent。后续如果要支持并行实现型 worker，需要单独设计。

## 后续方向

可以继续优化：

- 在 UI 和权限边界稳定后引入真正并行 batch。
- 为明显意图增加确定性 pre-router。
- 用 eval prompts 统计不同模型的委派触发率。
- 增加带写入范围约束的 worker 子代理。
- 支持长运行子代理取消。
- 在 compaction summary 中保留活跃任务状态。
- 为嵌套 delegation 提供更丰富的 `/details` 视图。

## 总结

这次升级把 TermPilot 的委派系统从“模型可能会用的泛化 agent 工具”，推进成更清晰的任务分发接口：

- 用 task 工具追踪多步骤工作。
- 用 `Plan` 处理实现策略。
- 用 `Explore` 处理大范围代码理解。
- 用 `Verification` 处理正确性检查。
- 多个独立方向用 `agent.tasks` 一次委派。

这样 TermPilot 对模型自发规划能力的依赖更低，也给规划能力较弱的模型提供了更清楚的结构化执行路径。
