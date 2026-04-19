# `TERMPILOT.md` 加载

[English](termpilotmd.md) | [简体中文](termpilotmd.zh-CN.md)

本文档说明 `termpilot` 如何发现并注入 `TERMPILOT.md` 风格的项目级指令文件。

## 概览

`termpilotmd.py` 会搜索分层的指令文件，并将它们格式化后注入 system prompt。这样用户就能在不改代码的前提下，为项目提供持久化指导。实现位于 `src/termpilot/termpilotmd.py`。

## 涉及模块

```text
src/termpilot/termpilotmd.py  → 文件发现与格式化
src/termpilot/context.py   → 在 build_system_prompt() 中注入结果
文件系统                      → 用户级、项目级、本地级与 rules 目录
```

## 搜索顺序

文件按“低优先级到高优先级”的顺序加载，因此越靠近当前项目的文件越具体。

1. `~/.termpilot/TERMPILOT.md` — 用户全局指令
2. `~/.termpilot/rules/*.md` — 用户全局规则
3. 从文件系统根目录到当前工作目录链上的 `TERMPILOT.md`
4. 同一目录链上的 `.termpilot/TERMPILOT.md`
5. 同一目录链上的 `TERMPILOT.local.md`
6. 同一目录链上的 `.termpilot/rules/*.md`

说明：

- 越靠近项目的文件会在拼接结果中越靠后，因此具备更高上下文优先级。
- `rules` 目录按文件名排序加载。
- `TERMPILOT.local.md` 用于本地私有指令。

## 文件类别

| 类别 | 示例 | 用途 |
|------|------|------|
| 用户全局 | `~/.termpilot/TERMPILOT.md` | 跨项目个人偏好 |
| 用户规则 | `~/.termpilot/rules/*.md` | 可复用规则片段 |
| 项目级 | `<repo>/TERMPILOT.md`、`<repo>/.termpilot/TERMPILOT.md` | 团队共享项目规则 |
| 本地项目级 | `<repo>/TERMPILOT.local.md` | 私有本地覆盖 |
| 项目规则 | `<repo>/.termpilot/rules/*.md` | 模块化项目规则 |

## 注入格式

被发现的文件会先被包装成带标签的 prompt 片段，再追加到 system prompt 中。每个文件使用其类别（`user`、`project` 或 `local`）作为 XML 标签：

```text
<user>/home/user/.termpilot/TERMPILOT.md</user>
... 文件内容 ...
</user>

<project>/home/user/project/TERMPILOT.md</project>
... 文件内容 ...
</project>

<local>/home/user/project/TERMPILOT.local.md</local>
... 文件内容 ...
</local>
```

具体包装逻辑由 `termpilotmd.py` 处理，`context.py` 只消费聚合后的字符串。

## 父目录链行为

例如当前工作目录是：

```text
/Users/frank/work/termpilot
```

加载器会依次检查父目录链上的候选文件，从而让更上层目录的规则被下层项目继承。

## 设计约束

- 整个加载过程只读。
- 缺失文件会被忽略。
- 加载器保证顺序确定性，不负责复杂的规则冲突解析。
- 该机制与 memory、hooks 互补，但不替代它们。
