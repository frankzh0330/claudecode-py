# 任务工具系统

[English](task-tool.md) | [简体中文](task-tool.zh-CN.md)

任务工具系统提供持久化任务管理和依赖图支持。模型可以将复杂工作拆解为可追踪的子任务，管理任务间的依赖关系，并在空闲时自动取下一个可执行任务。

## 工具列表

| 工具 | 名称 | 说明 |
|------|------|------|
| TaskCreate | `task_create` | 创建 todo 风格任务，用于复杂工作追踪 |
| TaskUpdate | `task_update` | 更新进度、状态、依赖关系、owner 或元数据 |
| TaskList | `task_list` | 查看当前 todo 计划，在长任务中恢复注意力 |
| TaskGet | `task_get` | 按 ID 获取单个任务完整详情 |

## 什么时候使用任务

任务工具适合那些不应该只依赖模型短期注意力维护的工作。模型会被引导在以下场景开始前创建任务：

- 包含三个或更多实质步骤的请求。
- 多文件实现、重构或 bug 修复。
- 用户给出多个独立目标。
- 长时间代码探索或排查。
- 完成前需要测试或验证的修改。

对于简单的一次性编辑或明确文件查找，模型通常应该直接使用文件/搜索工具，而不是创建任务。

## 进度纪律

推荐的任务流程是：

1. 使用 `task_create` 创建小型 todo 列表。
2. 使用 `task_update` 将且仅将一个任务标记为 `in_progress`。
3. 完成当前任务后先标记为 `completed`，再开始下一个任务。
4. 在长会话或上下文切换后，使用 `task_list` 恢复注意力。

这样可以让模型和用户都清楚当前工作处于哪个阶段。

## 数据模型

```python
@dataclass
class Task:
    id: str                     # 自增整数，字符串格式
    subject: str                # 简短标题（祈使语气）
    description: str = ""       # 详细需求描述
    status: str = "pending"     # pending | in_progress | completed | deleted
    owner: str = ""             # 认领该任务的 agent 名称
    active_form: str = ""       # 进行时态，用于 spinner 显示
    created_at: float           # Unix 时间戳
    updated_at: float           # Unix 时间戳
    blocks: list[str] = []      # 被本任务阻塞的任务 ID 列表
    blocked_by: list[str] = []  # 本任务等待的任务 ID 列表
    metadata: dict = {}         # 任意键值对
```

## 持久化

任务存储在 `~/.termpilot/projects/<cwd>/tasks.json`，使用单个 JSON 文件。同一项目目录的所有会话共享同一份任务数据。

- **懒加载**：首次访问时从磁盘加载。
- **即时保存**：每次写操作（创建、更新、删除）后立即持久化。
- **崩溃恢复**：变更同步写入，崩溃不会丢失数据。
- **计数器连续性**：任务 ID 计数器从已有最大 ID 初始化，确保 ID 不冲突。

## 依赖图

任务支持两个依赖字段：

- `blocks`：本任务完成后才能开始的任务列表。
- `blocked_by`：必须先完成才能开始本任务的列表。

通过 `task_update` 设置依赖时，系统自动维护双向链接：

```
TaskUpdate(taskId="1", addBlocks=["2"])
  → Task 1 获得 blocks: ["2"]
  → Task 2 获得 blocked_by: ["1"]
```

当任务被标记为 `completed` 时，被阻塞的下游任务会记录日志。

## TaskList 输出

`task_list` 工具以紧凑格式展示任务：

```
[ ] 1: 修复认证 bug (pending) [owner: main-agent]
[*] 2: 添加日志 (in_progress)
[ ] 3: 编写测试 (pending) [blocked by: 1]
    为认证模块添加单元测试
```

- `[ ]` = 待处理，`[*]` = 进行中，`[x]` = 已完成
- 被阻塞的任务显示活跃的阻塞源
- 描述截断为 100 个字符

### 过滤

`task_list` 支持可选过滤参数：

- `status`：只显示匹配状态的任务
- `owner`：只显示匹配 owner 的任务

## 自动取任务（TaskListWatcher）

在交互式 REPL 模式下，模型完成一次响应（返回文本而非工具调用）后，系统检查是否有可执行的任务：

1. 查找第一个 `pending`、无 owner 或 owner 匹配、且无活跃 `blocked_by` 的任务。
2. 如果找到，自动设置为 `in_progress`，并注入 prompt 让模型继续执行。

此机制仅在 REPL 模式生效，单次 `-p` 模式不触发。

## 元数据

任务支持通过 `metadata` 字段存储任意元数据：

- 创建时设置，更新时合并。
- 将某个 key 设为 `null` 可删除该字段。

典型用途：关联外部 issue、存储优先级、记录上下文。

## 对应的 TypeScript 实现

Python 版本对应 TS 版以下模块：

- `tools/TaskCreateTool/` + `TaskUpdateTool/` + `TaskListTool/` + `TaskGetTool/`
- `hooks/useTaskListWatcher.ts`（自动取任务逻辑）
- `utils/tasks.ts`（持久化 + 依赖图）

