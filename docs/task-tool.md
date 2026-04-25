# Task Tool System

[English](task-tool.md) | [简体中文](task-tool.zh-CN.md)

The task tool system provides persistent task management with dependency graph support. It enables the model to break complex work into tracked subtasks, manage dependencies between them, and auto-pick the next available task when idle.

## Tools

| Tool | Name | Description |
|------|------|-------------|
| TaskCreate | `task_create` | Create a new task with subject, description, and optional metadata |
| TaskUpdate | `task_update` | Update status, subject, description, dependencies, owner, or metadata |
| TaskList | `task_list` | List tasks with optional status/owner filters, shows dependency info |
| TaskGet | `task_get` | Get full details of a specific task by ID |

## Data Model

```python
@dataclass
class Task:
    id: str                     # Auto-incrementing integer as string
    subject: str                # Brief title (imperative form)
    description: str = ""       # Detailed requirements
    status: str = "pending"     # pending | in_progress | completed | deleted
    owner: str = ""             # Agent name that claimed the task
    active_form: str = ""       # Present continuous form for spinner
    created_at: float           # Unix timestamp
    updated_at: float           # Unix timestamp
    blocks: list[str] = []      # Task IDs blocked by this task
    blocked_by: list[str] = []  # Task IDs that must complete first
    metadata: dict = {}         # Arbitrary key-value pairs
```

## Persistence

Tasks are stored in `~/.termpilot/projects/<cwd>/tasks.json` as a single JSON file. The storage is shared across all sessions for the same project directory.

- **Lazy loading**: tasks are loaded from disk on first access.
- **Auto-save**: every write operation (create, update, delete) persists immediately.
- **Crash recovery**: since changes are written synchronously, no data is lost on crash.
- **Counter continuity**: the task ID counter is initialized from the highest existing ID, ensuring IDs never collide.

## Dependency Graph

Tasks support two dependency fields:

- `blocks`: list of task IDs that cannot start until this task completes.
- `blocked_by`: list of task IDs that must complete before this task can start.

When setting dependencies via `task_update`, the system maintains bidirectional links automatically:

```
TaskUpdate(taskId="1", addBlocks=["2"])
  → Task 1 gains blocks: ["2"]
  → Task 2 gains blocked_by: ["1"]
```

When a task is marked `completed`, blocked downstream tasks are logged for visibility.

## TaskList Output

The `task_list` tool displays tasks in a compact format:

```
[ ] 1: Fix authentication bug (pending) [owner: main-agent]
[*] 2: Add logging (in_progress)
[ ] 3: Write tests (pending) [blocked by: 1]
    Add unit tests for the auth module
```

- `[ ]` = pending, `[*]` = in progress, `[x]` = completed
- Blocked tasks show active blockers
- Description is shown truncated to 100 characters

### Filters

`task_list` accepts optional filters:

- `status`: only show tasks with matching status
- `owner`: only show tasks with matching owner

## Auto-pick (TaskListWatcher)

In interactive REPL mode, after the model finishes a response (returns text without tool calls), the system checks for the next available task:

1. Find the first task that is `pending`, has no `owner` or matches the current owner, and has no active `blocked_by` entries.
2. If found, automatically set it to `in_progress` and inject a prompt for the model to continue.

This mechanism only triggers in REPL mode, not in one-shot `-p` mode.

## Metadata

Tasks support arbitrary metadata via the `metadata` field on create and update:

- On update, metadata keys are merged into the existing dict.
- Setting a key to `null` removes it.

Use cases: linking to external issues, storing priority, recording context.

## Corresponding TypeScript Implementation

The Python implementation corresponds to:

- `tools/TaskCreateTool/` + `TaskUpdateTool/` + `TaskListTool/` + `TaskGetTool/`
- `hooks/useTaskListWatcher.ts` (auto-pick logic)
- `utils/tasks.ts` (persistence + dependency graph)
