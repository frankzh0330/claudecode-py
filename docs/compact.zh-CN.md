# 上下文压缩

[English](compact.md) | [简体中文](compact.zh-CN.md)

本文档说明 `termpilot` 如何在对话接近上下文窗口上限时缩减历史消息。

## 概览

`compact.py` 实现了两级压缩：

- micro-compaction：本地清理旧工具结果
- full compaction：由模型生成摘要，并保留最近原始消息

目标是在不丢失近期工作上下文的前提下，防止上下文溢出。

## 涉及模块

```text
compact.py   → token 估算与压缩策略
api.py       → 调用 auto_compact_if_needed()
config.py    → 提供上下文窗口大小
```

## 触发逻辑

当前流程：

1. 用本地启发式估算 transcript 大小。
2. 如果低于阈值，则不压缩。
3. 如果超过阈值，先尝试 micro-compaction。
4. 如果仍然过大，再回退到 full compaction。

当前实现中的关键常量：

- 默认 context window：`200_000`
- 触发阈值：context window 的 `75%`
- full compaction 目标：约 `50%`
- token 估算：大约 `3 个字符 ≈ 1 个 token`

上下文窗口也可以通过 `TERMPILOT_CONTEXT_WINDOW` 环境变量覆盖。

## Micro-Compaction

micro-compaction 会清理旧的 `tool_result` 内容，但尽量保留消息结构本身。

当前有两类策略：

- 基于数量的旧工具结果清理
- 当用户空闲较久时的时间型清理

这一层不会调用模型。

## Full Compaction

如果 micro-compaction 后仍然过大，则：

1. 保留最近一段原始消息。
2. 将更早的消息转为纯文本。
3. 让模型为更早的历史生成摘要。
4. 用摘要消息替换旧历史。

这样既保留近期上下文，也保留可读的历史概览。

## Token 估算

当前项目没有引入额外 tokenizer，而是使用轻量启发式：

- 中英文混合文本大约按 `3 字符 = 1 token` 估算
- 每条消息还有一个小的固定开销

它不是精确计数，但足够用于判断何时需要压缩。

## 压缩优先保留什么

- 最近的 user / assistant 消息
- 对话整体结构
- 旧工作的重要摘要

## 最先丢弃什么

- 较早的超大工具输出
- 已被摘要覆盖的冗余历史细节

## 会话持久化与压缩

压缩操作作用于内存中的消息列表。session JSONL 文件（append-only 的事件日志，包含 parentUuid 链表）不受压缩影响——它始终保留完整历史。如果用户通过 `/rewind` 回退，即使原始消息在活跃上下文中已被压缩，它们仍然存在于 JSONL 中。
