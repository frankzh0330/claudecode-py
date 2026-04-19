# Hooks

[English](hooks.md) | [简体中文](hooks.zh-CN.md)

本文档说明 `hooks.py` 中实现的 hook 系统。

## 概览

Hooks 是用户配置的 shell 命令，会在关键生命周期事件前后自动执行。它允许用户在不修改主代码的前提下，实现策略校验、日志审计、prompt 增强和工具调用拦截。

## 涉及模块

```text
hooks.py    → hook 配置加载、匹配、执行、解析
api.py      → PreToolUse / PostToolUse 分发
cli.py      → SessionStart / UserPromptSubmit / Stop 分发
```

## 支持的事件

当前支持：

- `PreToolUse`
- `PostToolUse`
- `UserPromptSubmit`
- `Stop`
- `SessionStart`

## 配置格式

Hooks 从 `~/.termpilot/settings.json` 读取。

概念上类似：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "/path/to/hook.sh", "timeout": 5}
        ]
      }
    ]
  }
}
```

## 匹配模型

每个事件下可以有多个 matcher。

- `matcher` 可以匹配某个工具族或具体工具名
- 缺省 matcher 或 `*` 视为通配
- 只有工具事件需要 matcher；prompt/session 事件直接执行

## Hook 命令的输入

hook 命令会通过 stdin 收到 JSON，包含事件相关字段，例如：

- session id
- 当前工作目录
- event name
- 工具事件中的 tool name / tool input
- prompt 提交事件中的 prompt 文本

## Hook 返回结果

每次执行都会产出一个 `HookResult`，其中包含：

- 进程退出码
- stdout
- stderr
- 解析后的 `allow` / `deny`
- 可选的更新后工具输入

## 阻断行为

- `exit code == 2` 会被视为阻断
- 解析出的 `deny` 也可以阻断工具执行
- 非阻断失败会被记录为日志/警告，而不是致命错误

## 典型生命周期

```text
PreToolUse hook
  → 权限检查
  → 工具执行
  → PostToolUse hook
```

其他事件：

- `SessionStart`：session 初始化后触发
- `UserPromptSubmit`：prompt 发送给模型前触发
- `Stop`：模型回复结束后触发
