"""TERMPILOT.md 文件搜索、加载和格式化。

对应 TS: utils/claudemd.ts (~1500 行)

TS 版支持 @include 指令、frontmatter 条件规则、团队记忆等高级特性。
Python 简化版保留核心功能：从多个位置搜索 TERMPILOT.md 文件，加载内容，
格式化为 system prompt section。

搜索路径（按优先级从低到高）：
1. ~/.termpilot/TERMPILOT.md          — 用户全局指令
2. ~/.termpilot/rules/*.md         — 用户全局规则文件
3. 项目根到 CWD 的 TERMPILOT.md       — 项目指令（最近的目录优先级高）
4. 项目根到 CWD 的 .termpilot/TERMPILOT.md
5. 项目根到 CWD 的 TERMPILOT.local.md — 本地私有指令（不提交 git）
6. 项目根到 CWD 的 .termpilot/rules/*.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MemoryFileInfo:
    """一个 TERMPILOT.md 文件的信息。"""

    path: str
    content: str
    file_type: str  # "user" / "project" / "local"


def _parent_chain(cwd: str) -> list[str]:
    """从根目录到 cwd 的目录链（从根到 cwd 排列）。

    例如 cwd="/Users/frank/project" →
    ["/Users", "/Users/frank", "/Users/frank/project"]
    """
    result: list[str] = []
    p = Path(cwd).resolve()
    parts = p.parts
    for i in range(1, len(parts) + 1):
        result.append(str(Path(*parts[:i])))
    return result


def _read_file(path: Path) -> str | None:
    """读取文件内容，失败返回 None。"""
    try:
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            return content if content else None
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _read_rules_dir(rules_dir: Path, file_type: str) -> list[MemoryFileInfo]:
    """读取 rules/ 目录下的所有 .md 文件。"""
    results: list[MemoryFileInfo] = []
    if not rules_dir.is_dir():
        return results
    for md_file in sorted(rules_dir.glob("*.md")):
        content = _read_file(md_file)
        if content:
            results.append(MemoryFileInfo(
                path=str(md_file),
                content=content,
                file_type=file_type,
            ))
    return results


def find_termpilot_md_files(cwd: str = "") -> list[MemoryFileInfo]:
    """搜索所有 TERMPILOT.md 文件。

    对应 TS `getClaudeMds()` + `getMemoryFiles()`。

    搜索顺序（先找到的先加载，后加载的覆盖先加载的）：
    1. ~/.termpilot/TERMPILOT.md
    2. ~/.termpilot/rules/*.md
    3. 从根到 CWD 每个目录的 TERMPILOT.md
    4. 从根到 CWD 每个目录的 .termpilot/TERMPILOT.md
    5. 从根到 CWD 每个目录的 TERMPILOT.local.md
    6. 从根到 CWD 每个目录的 .termpilot/rules/*.md
    """
    if not cwd:
        cwd = str(Path.cwd())

    home = Path.home()
    files: list[MemoryFileInfo] = []

    # 1. ~/.termpilot/TERMPILOT.md — 用户全局
    user_termpilot_md = _read_file(home / ".termpilot" / "TERMPILOT.md")
    if user_termpilot_md:
        files.append(MemoryFileInfo(
            path=str(home / ".termpilot" / "TERMPILOT.md"),
            content=user_termpilot_md,
            file_type="user",
        ))

    # 2. ~/.termpilot/rules/*.md — 用户全局规则
    files.extend(_read_rules_dir(home / ".termpilot" / "rules", "user"))

    # 从根到 CWD 的目录链
    chain = _parent_chain(cwd)

    # 3. 从根到 CWD 每个目录的 TERMPILOT.md — 项目指令
    for dir_path in chain:
        content = _read_file(Path(dir_path) / "TERMPILOT.md")
        if content:
            files.append(MemoryFileInfo(
                path=f"{dir_path}/TERMPILOT.md",
                content=content,
                file_type="project",
            ))

    # 4. 从根到 CWD 每个目录的 .termpilot/TERMPILOT.md
    for dir_path in chain:
        content = _read_file(Path(dir_path) / ".termpilot" / "TERMPILOT.md")
        if content:
            files.append(MemoryFileInfo(
                path=f"{dir_path}/.termpilot/TERMPILOT.md",
                content=content,
                file_type="project",
            ))

    # 5. 从根到 CWD 每个目录的 TERMPILOT.local.md — 本地私有
    for dir_path in chain:
        content = _read_file(Path(dir_path) / "TERMPILOT.local.md")
        if content:
            files.append(MemoryFileInfo(
                path=f"{dir_path}/TERMPILOT.local.md",
                content=content,
                file_type="local",
            ))

    # 6. 从根到 CWD 每个目录的 .termpilot/rules/*.md — 项目规则
    for dir_path in chain:
        files.extend(_read_rules_dir(Path(dir_path) / ".termpilot" / "rules", "project"))

    return files


def load_termpilot_md(cwd: str = "") -> str | None:
    """加载所有 TERMPILOT.md 内容，格式化为 system prompt section。

    对应 TS `getClaudeMds()` 的格式化部分。

    返回 None 表示没有找到任何 TERMPILOT.md 文件。
    返回的字符串格式：
    "# Project & User Instructions\n\n"
    "<file_type>path</file_type>\n"
    "content\n"
    "</file_type>\n\n"
    ...
    """
    files = find_termpilot_md_files(cwd)
    if not files:
        logger.debug("no TERMPILOT.md files found (cwd=%s)", cwd)
        return None

    logger.debug("found %d TERMPILOT.md files: %s", len(files),
                 ", ".join(f.file_type + ":" + f.path.split("/")[-1] for f in files))

    sections: list[str] = [
        "# Project & User Instructions",
        "",
        "Codebase and user instructions are shown below. "
        "Be sure to adhere to these instructions. "
        "IMPORTANT: These instructions OVERRIDE any default behavior.",
        "",
    ]

    for f in files:
        tag = f.file_type
        sections.append(f"<{tag}>{f.path}</{tag}>")
        sections.append(f.content)
        sections.append(f"</{tag}>")
        sections.append("")

    return "\n".join(sections)
