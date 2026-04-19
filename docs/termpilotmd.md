# `TERMPILOT.md` Loading

[English](termpilotmd.md) | [简体中文](termpilotmd.zh-CN.md)

This document describes how `termpilot` discovers and injects project instructions from `TERMPILOT.md`-style files. The implementation lives in `src/termpilot/termpilotmd.py`.

## Overview

`termpilotmd.py` searches layered instruction files and formats them for inclusion in the system prompt. The goal is to let users define persistent project guidance without editing the codebase.

## Relevant Modules

```text
src/termpilot/termpilotmd.py  → file discovery and formatting
src/termpilot/context.py   → injects discovered content into build_system_prompt()
filesystem                  → user, project, local, and rules-based instruction files
```

## Search Order

Files are loaded from lower priority to higher priority so that later files have more specific context.

1. `~/.termpilot/TERMPILOT.md` — user global instructions
2. `~/.termpilot/rules/*.md` — user global rules
3. `TERMPILOT.md` along the parent chain from filesystem root to current working directory
4. `.termpilot/TERMPILOT.md` along the same parent chain
5. `TERMPILOT.local.md` along the same parent chain
6. `.termpilot/rules/*.md` along the same parent chain

Notes:

- Project-near files override broader files simply by appearing later in the merged prompt payload.
- Rules directories are loaded in filename order.
- `TERMPILOT.local.md` is intended for local, uncommitted instructions.

## File Categories

| Category | Example | Typical use |
|----------|---------|-------------|
| User-global | `~/.termpilot/TERMPILOT.md` | personal preferences across projects |
| User rules | `~/.termpilot/rules/*.md` | reusable instruction snippets |
| Project | `<repo>/TERMPILOT.md`, `<repo>/.termpilot/TERMPILOT.md` | team-shared project instructions |
| Local project | `<repo>/TERMPILOT.local.md` | private local overrides |
| Project rules | `<repo>/.termpilot/rules/*.md` | modular project rules |

## Injection Format

Discovered files are wrapped as tagged prompt fragments before being appended to the system prompt. Each file uses its category (`user`, `project`, or `local`) as the XML tag:

```text
<user>/home/user/.termpilot/TERMPILOT.md</user>
... file contents ...
</user>

<project>/home/user/project/TERMPILOT.md</project>
... file contents ...
</project>

<local>/home/user/project/TERMPILOT.local.md</local>
... file contents ...
</local>
```

The exact wrapper text is handled by `termpilotmd.py`; `context.py` only consumes the formatted aggregate.

## Parent Chain Behavior

For a current working directory like:

```text
/Users/frank/work/termpilot
```

the loader checks parent directories in order, allowing instructions at broader directories to be inherited by deeper projects.

## Design Constraints

- File loading is read-only.
- Missing files are ignored.
- The loader focuses on deterministic ordering, not rule conflict resolution.
- This mechanism complements memory and hooks; it does not replace them.
