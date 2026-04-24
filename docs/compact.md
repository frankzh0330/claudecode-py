# Context Compaction

[English](compact.md) | [简体中文](compact.zh-CN.md)

This document describes how `termpilot` reduces conversation size when the running transcript approaches the model context window.

## Overview

`compact.py` implements two levels of compaction:

- micro-compaction: local cleanup of older tool results
- full compaction: model-generated summary plus recent raw messages

The system is designed to preserve recent working context while preventing context overflow.

## Relevant Modules

```text
compact.py   → token estimation and compaction strategies
api.py       → calls auto_compact_if_needed()
config.py    → provides the context window size
```

## Trigger Logic

The current flow is:

1. Estimate transcript size with a local heuristic.
2. If the transcript is below the threshold, do nothing.
3. If large enough, try micro-compaction first.
4. If still too large, fall back to full compaction.

Important constants in the current implementation:

- default context window: `200_000`
- trigger threshold: `75%` of the context window
- target after full compaction: about `50%` of the context window
- token heuristic: about `3 characters ≈ 1 token`

The context window can also be overridden with `TERMPILOT_CONTEXT_WINDOW`.

## Micro-Compaction

Micro-compaction clears old `tool_result` payloads while keeping the surrounding conversation structure intact.

Two strategies are used:

- count-based cleanup of older compactable tool results
- time-based cleanup when the user has been idle long enough

This step does not call the model.

## Full Compaction

If the transcript is still too large after micro-compaction:

1. Keep a recent slice of raw messages.
2. Convert older messages to text.
3. Ask the model to summarize that older segment.
4. Replace the older segment with a compact summary message.

This preserves recency while maintaining a readable history.

## Token Estimation

The project currently uses a lightweight heuristic instead of a tokenizer dependency:

- mixed Chinese/English text is estimated at roughly `3 chars = 1 token`
- each message also adds a small fixed overhead

This is approximate but sufficient for deciding when to compact.

## What Compaction Tries to Preserve

- recent user and assistant turns
- the overall conversation shape
- actionable summaries of older work

## What It Discards First

- older large tool outputs
- redundant historical detail already represented in summaries

## Session Persistence and Compaction

Compaction operates on the in-memory message list. The session JSONL file (the append-only event log with parentUuid links) is not affected by compaction — it always retains the full history. If the user rewinds via `/rewind`, the original messages are still in the JSONL even though they were compacted in the active context.
