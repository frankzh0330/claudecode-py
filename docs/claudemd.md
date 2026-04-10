# CLAUDE.md 加载系统

本文档详细描述 `claudemd.py` 的架构、搜索逻辑、文件格式和注入机制。

---

## 概览

CLAUDE.md 是项目级持久化指令文件，允许用户为项目定制 AI 助手的行为。cc_python 启动时自动搜索多个位置的 CLAUDE.md 文件，将内容注入 System Prompt，使模型能感知项目特定规则。

对应 TS 版：`utils/claudemd.ts`（~1500 行），Python 简化版 ~160 行，保留核心搜索+加载+注入。

---

## 涉及文件与职责

```
claudemd.py             ← CLAUDE.md 核心（搜索 + 加载 + 格式化）
  ↓
context.py              ← build_system_prompt() 中调用 load_claude_md()，注入为 Section 8.5
  ↓
文件系统                 ← 多个位置的 CLAUDE.md / CLAUDE.local.md / rules/*.md
```

---

## 数据模型

```
MemoryFileInfo (一个 CLAUDE.md 文件的信息)
├── path       文件绝对路径
├── content    文件内容（Markdown 文本）
└── file_type  来源类型: "user" / "project" / "local"
```

| file_type | 含义 | 示例路径 |
|-----------|------|---------|
| `user` | 用户全局指令 | `~/.claude/CLAUDE.md` |
| `project` | 项目指令（可提交 git） | `/project/CLAUDE.md`、`/project/.claude/CLAUDE.md` |
| `local` | 本地私有指令（不提交 git） | `/project/CLAUDE.local.md` |

---

## 搜索路径

按优先级从低到高，后加载的文件优先级更高（内容覆盖前面的）：

```
优先级低
  │
  ├─ 1. ~/.claude/CLAUDE.md              用户全局指令
  ├─ 2. ~/.claude/rules/*.md             用户全局规则文件（按文件名排序）
  ├─ 3. /.../CLAUDE.md                   从根到 CWD 每个目录
  ├─ 4. /.../.claude/CLAUDE.md           从根到 CWD 每个目录
  ├─ 5. /.../CLAUDE.local.md             从根到 CWD 每个目录（私有）
  └─ 6. /.../.claude/rules/*.md          从根到 CWD 每个目录（按文件名排序）
  │
优先级高
```

### 搜索算法

```python
def _parent_chain(cwd):
    """从根到 CWD 的目录链。

    cwd="/Users/frank/project" →
    ["/Users", "/Users/frank", "/Users/frank/project"]
    """
```

对目录链中的每个目录，依次搜索 `CLAUDE.md` → `.claude/CLAUDE.md` → `CLAUDE.local.md` → `.claude/rules/*.md`。越接近 CWD 的文件越后加载，优先级越高。

### 文件类型分类

| 位置 | file_type | 是否应提交 git |
|------|-----------|--------------|
| `~/.claude/CLAUDE.md` | `user` | N/A（用户主目录） |
| `~/.claude/rules/*.md` | `user` | N/A |
| `{project}/CLAUDE.md` | `project` | 是（团队共享） |
| `{project}/.claude/CLAUDE.md` | `project` | 是 |
| `{project}/.claude/rules/*.md` | `project` | 是 |
| `{project}/CLAUDE.local.md` | `local` | 否（加入 .gitignore） |

---

## 核心函数

### `find_claude_md_files(cwd) → list[MemoryFileInfo]`

搜索所有 CLAUDE.md 文件，返回按优先级排序的列表。

对应 TS `getMemoryFiles()` + `getClaudeMds()`。

### `load_claude_md(cwd) → str | None`

加载所有文件并格式化为 System Prompt section。无文件时返回 `None`。

对应 TS `getClaudeMds()` 的格式化部分。

### `_read_file(path) → str | None`

读取单个文件，失败或空文件返回 `None`。处理编码异常。

### `_read_rules_dir(rules_dir, file_type) → list[MemoryFileInfo]`

读取 `rules/` 目录下所有 `.md` 文件，按文件名排序。

### `_parent_chain(cwd) → list[str]`

生成从根到 CWD 的目录路径链。

---

## 注入格式

CLAUDE.md 内容作为 System Prompt 的 Section 8.5 注入（在 Memory section 之前）。

每个文件内容用 XML 标签包裹，标注来源类型和路径：

```
# Project & User Instructions

Codebase and user instructions are shown below. Be sure to adhere to these
instructions. IMPORTANT: These instructions OVERRIDE any default behavior.

<user>/Users/frank/.claude/CLAUDE.md</user>
用户全局指令内容
</user>

<project>/Users/frank/my-project/CLAUDE.md</project>
项目级指令内容
</project>

<local>/Users/frank/my-project/CLAUDE.local.md</local>
本地私有指令内容
</local>
```

### XML 标签含义

| 标签 | 含义 |
|------|------|
| `<user>` | 用户全局指令，对所有项目生效 |
| `<project>` | 项目指令，团队共享（提交到 git） |
| `<local>` | 本地私有指令，仅本机生效（不提交 git） |

---

## 注入位置

在 `build_system_prompt()` 中的位置：

```
Section 7: Output Efficiency (静态)
Section 8: Session-specific Guidance (动态)
Section 8.5: CLAUDE.md 项目指令 (动态) ← 这里
Section 9: Memory (动态)
Section 10: Environment Info (动态)
```

---

## 搜索流程图

```
load_claude_md(cwd="/Users/frank/project")
  │
  ▼
find_claude_md_files(cwd)
  │
  ├─ Step 1: ~/.claude/CLAUDE.md
  │   └─ 存在 → MemoryFileInfo(type="user")
  │
  ├─ Step 2: ~/.claude/rules/*.md
  │   └─ 每找到一个 → MemoryFileInfo(type="user")
  │
  ├─ Step 3: _parent_chain() → ["/Users", "/Users/frank", "/Users/frank/project"]
  │   │
  │   ├─ /Users/CLAUDE.md → 不存在，跳过
  │   ├─ /Users/frank/CLAUDE.md → 不存在，跳过
  │   └─ /Users/frank/project/CLAUDE.md → 存在 → MemoryFileInfo(type="project")
  │
  ├─ Step 4: 同链中每个目录的 .claude/CLAUDE.md
  │
  ├─ Step 5: 同链中每个目录的 CLAUDE.local.md
  │   └─ /Users/frank/project/CLAUDE.local.md → 存在 → MemoryFileInfo(type="local")
  │
  └─ Step 6: 同链中每个目录的 .claude/rules/*.md
      └─ 每找到一个 → MemoryFileInfo(type="project")
  │
  ▼
格式化为 System Prompt section
  │
  ▼
"# Project & User Instructions\n\n"
"<user>~/.claude/CLAUDE.md</user>\n"
"全局指令\n"
"</user>\n\n"
"<project>/Users/frank/project/CLAUDE.md</project>\n"
"项目指令\n"
"</project>\n\n"
...
```

---

## 使用场景

### 场景 1：团队共享项目规范

```bash
# 项目根目录创建 CLAUDE.md（提交到 git）
echo "本项目使用 Python 3.12，所有函数必须有类型注解。" > CLAUDE.md
echo "测试使用 pytest，放在 tests/ 目录。" >> CLAUDE.md
```

所有克隆该仓库的开发者，cc_python 都会自动加载这些规则。

### 场景 2：个人私有指令

```bash
# 项目根目录创建 CLAUDE.local.md（不提交 git）
echo "回复使用中文。" > CLAUDE.local.md
echo "我偏好详细的代码解释。" >> CLAUDE.local.md

# 确保 .gitignore 包含
echo "CLAUDE.local.md" >> .gitignore
```

### 场景 3：全局指令

```bash
# 对所有项目生效
mkdir -p ~/.claude
echo "不要使用 emoji。" > ~/.claude/CLAUDE.md
```

### 场景 4：规则文件目录

```bash
# 多个规则文件，按文件名排序加载
mkdir -p .claude/rules
echo "命名规范：snake_case" > .claude/rules/naming.md
echo "错误处理：边界验证" > .claude/rules/error-handling.md
```

---

## 与 TS 版的差异

| 特性 | TS 版 | Python 版 |
|------|-------|----------|
| 搜索 CLAUDE.md | ✅ | ✅ |
| 搜索 .claude/CLAUDE.md | ✅ | ✅ |
| 搜索 CLAUDE.local.md | ✅ | ✅ |
| 搜索 rules/*.md | ✅ | ✅ |
| @include 指令 | ✅ | ❌ |
| Frontmatter 条件规则（globs） | ✅ | ❌ |
| 团队记忆 | ✅ | ❌ |
| Auto-memory | ✅ | ❌ |
| 文件变更监听 | ✅ | ❌ |
| HTML 注释过滤 | ✅ | ❌ |
| 内容截断 | ✅ | ❌ |

---

## 与 TS 版的对应关系

| Python | TypeScript | 功能 |
|--------|-----------|------|
| `claudemd.py` | `utils/claudemd.ts` | CLAUDE.md 搜索和加载 |
| `MemoryFileInfo` | `MemoryFileInfo` | 文件信息数据类 |
| `find_claude_md_files()` | `getMemoryFiles()` | 搜索所有文件 |
| `load_claude_md()` | `getClaudeMds()` | 格式化为 System Prompt section |
| `_parent_chain()` | 目录遍历逻辑 | 生成从根到 CWD 的目录链 |
| `_read_rules_dir()` | `processMdRules()` | 加载 rules/ 目录 |
