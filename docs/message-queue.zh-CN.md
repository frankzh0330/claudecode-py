# 消息队列与交互式 Drain Loop

[English](message-queue.md) | [简体中文](message-queue.zh-CN.md)

本文说明 TermPilot 的交互式消息队列、drain loop、中断行为和终端 prompt 处理逻辑。

## 涉及模块

```text
cli.py                 → REPL 输入收集、drain loop、slash command、UI 协调
queue.py               → 优先级命令队列和后台 agent 跟踪
api.py                 → 工具循环、权限更新、OpenAI-compatible usage 提取
ui.py                  → 安静状态行和紧凑工具卡片
tools/agent.py         → 子代理进度事件和后台通知
tools/task.py          → 任务持久化和中断任务清理
tools/ask_user.py      → 稳定的数字选择式用户提问
prompt_utils.py        → 共享的 prompt-toolkit/questionary 取消辅助
routing.py             → 轻量委派路由提醒
```

## 为什么需要这一层

交互式 coding agent 里会同时发生两件事：

- 用户可能在模型/工具 turn 还没结束时继续输入
- 当前 turn 可能正在读文件、请求权限、派生子代理或等待用户澄清

如果 slash command 直接修改 `messages`，`/clear`、`/compact`、`/rewind` 这类命令就可能和正在运行的模型 turn 竞争。结果可能是旧 assistant/tool 结果被重新写回 session、上下文过早清空，或者用户中断后旧 task 又被重新执行。

TermPilot 现在将输入收集和命令执行拆开：

- 输入收集器只负责入队
- drain loop 在安全点串行执行命令

## 队列模型

主队列存储 `QueuedCommand`：

- `mode`：`prompt`、`slash_command`、`task_notification` 或 `system`
- `priority`：`NOW`、`NEXT` 或 `LATER`
- `origin`：`user`、`agent`、`system` 或 `task-watcher`
- `agent_id`：空字符串表示主循环，非空表示特定 agent 作用域

主 REPL 只消费发给主循环的命令。这样可以避免未来多个子 agent 互相偷取主线程通知或彼此的消息。

队列还支持：

- 带 filter 的出队，不移除无关命令
- 不改变 FIFO 顺序的 `peek()`
- 用 `discard()` 在 `/clear` 或 interrupt 后删除过期队列项
- 后台 agent 注册和取消

## Drain Loop 流程

交互模式下：

1. `_input_collector()` 等待终端输入。
2. 它会解析 slash command，但不会直接执行。
3. 它将输入入队为 `prompt` 或 `slash_command`。
4. `_drain_loop()` 每次只取一个主线程命令。
5. 它执行 `_handle_prompt()` 或 `_handle_slash_command()`。
6. 只有当前命令完成后，才考虑 task-watcher 的后续任务。

因此这些状态修改会串行发生：

- `messages`
- session storage
- permission context
- task watcher 状态
- `/clear`、`/compact`、`/rewind`

## Slash Command 安全点

Slash command 会先入队，不会立即执行。

当前状态修改类命令包括：

- `/clear`
- `/compact`
- `/rewind`

如果这些命令是在模型 turn 运行中输入的，TermPilot 会标记它们是 active turn 期间入队的。若 assistant 最终输出看起来仍在等待用户确认或选择，该命令会延后到下一个安全点。

示例：

```text
> 修改 hello.py
assistant: 文件当前内容是 ... 你想改成什么？
> /clear
> 算了不改了
assistant: 好的，保持原样。还有其他需要吗？
> 没了
assistant: 好的，有事随时找我。
/clear 在这里执行
```

当前“等待用户”的判断是轻量启发式：它会检查问题句、`confirm`、`choose`、`which`、`确认`、`选择`、`是否` 等确认/选择类表达。它不是完整的语义级任务完成检测。

## 中断行为

在 active turn 中按 `ESC` 会让当前 turn generation 失效，并执行清理：

- 取消当前处理任务
- 清除可见状态行
- 丢弃队列中的 `agent` 和 `task-watcher` 通知
- 取消已注册的后台 agent task
- 删除 pending 或 in-progress 的 task list 条目
- 忽略被取消 turn 后续迟到的 UI event

这样可以避免中断后的旧委派任务再次显示为 `Running delegated agents...`，也避免 task watcher 继续拾取旧任务。

## 交互式提示

TermPilot 在主 REPL 的高频交互里避免嵌套 prompt-toolkit 菜单。

以下提示使用稳定的数字输入：

- 权限确认
- `ask_user_question`
- `/rewind`

这样可以避免部分终端里出现 CPR warning 和原始方向键 escape 序列。

对于仍使用 questionary 的配置类提示，`prompt_utils.ask_with_esc()` 提供共享的 ESC 取消和更安全的 prompt-toolkit shutdown 处理。

## Prompt 渲染

交互式 prompt 使用 `prompt_toolkit.patch_stdout()`，让 Rich 输出和 prompt-toolkit 输入可以共存。模型输出、工具卡片和状态行结束后，输入提示可以重新绘制。

TermPilot 不再在每轮结束后打印 `console.rule()` 分割线。默认 UI 更接近安静型终端助手：

- `: Coalescing...` 这类紧凑状态行
- 紧凑工具卡片
- 每轮结束后不强制打印绿色分割线
- prompt 前缀显示当前模式，例如 `plan >`

## Token Usage

对于 OpenAI-compatible streaming API，TermPilot 会请求：

```python
stream_options={"include_usage": True}
```

如果 provider 不支持该参数，TermPilot 会自动回退到普通 streaming 请求。

Token 展示基于实际记录到的 token usage，而不是是否有已知定价。对于没有内置定价表的 provider，cost 可能是 `$0.0000`，但只要 provider 返回 usage，就仍然应该展示 token 数。

## 当前限制

- 等待用户判断仍是文本启发式。
- `/clear` 延后并不证明用户任务语义上已经完成，只是等到 assistant 不再明显要求用户做决定。
- 当前没有实现全屏终端 UI；现阶段保持 Rich 和 prompt-toolkit 的轻量组合。
- 后台子代理取消依赖已注册 task。同步 provider 调用在 SDK/网络层可能仍需要一点时间才能停止。

## 测试重点

当前测试覆盖重点包括：

- slash command 队列安全
- 延后的 `/clear`
- 稳定数字式权限和 rewind 选择
- `ask_user_question` 数字输入
- 队列 filter、discard 和 FIFO 行为
- OpenAI-compatible streaming usage 提取
- 中断后的任务清理
