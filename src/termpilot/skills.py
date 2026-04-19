"""Skills 系统。

对应 TS: skills/（~300 行）+ tools/SkillTool/SkillTool.ts
支持从磁盘加载 skill 定义（Markdown + YAML frontmatter）和内置 skill 注册。

Skill 是一种可被模型通过 SkillTool 调用的可复用 prompt 模板。
用户可在 .termpilot/skills/ 目录下创建 .md 文件定义自定义 skill。

Skill 文件格式：
---
name: my-skill
description: 描述
allowedTools: ['Bash', 'Read']
model: claude-sonnet-4-20250514
userInvocable: true
---

Skill 的 prompt 内容（Markdown 格式）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from termpilot.config import get_config_home

logger = logging.getLogger(__name__)

# 全局 skill 注册表
_skills: dict[str, SkillDefinition] = {}


@dataclass
class SkillDefinition:
    """Skill 定义。

    对应 TS: skills/bundledSkills.ts BundledSkillDefinition
    """
    name: str
    description: str
    prompt_template: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    user_invocable: bool = True
    source: str = "disk"  # "disk" | "bundled"

    def get_prompt(self, args: str = "") -> str:
        """获取 skill 的 prompt 内容。

        如果 prompt_template 中包含 {args} 占位符，则替换为实际参数。
        否则将参数追加到 prompt 末尾。
        """
        if "{args}" in self.prompt_template:
            return self.prompt_template.replace("{args}", args)
        if args:
            return f"{self.prompt_template}\n\n{args}"
        return self.prompt_template


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 文件的 YAML frontmatter。

    格式：
    ---
    key: value
    ---
    正文内容

    返回 (metadata_dict, body_text)。
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return {}, content

    yaml_str = match.group(1).strip()
    body = match.group(2).strip()

    # 简化 YAML 解析（避免引入 pyyaml 依赖）
    meta: dict[str, Any] = {}
    for line in yaml_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # 去掉引号
        if (value.startswith('"') and value.endswith('"')) or \
                (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        # 解析简单类型
        if value.lower() in ("true", "yes"):
            meta[key] = True
        elif value.lower() in ("false", "no"):
            meta[key] = False
        elif value.startswith("[") and value.endswith("]"):
            # 简单列表解析 ["a", "b"]
            items = re.findall(r'["\']([^"\']+)["\']', value)
            meta[key] = items
        else:
            meta[key] = value

    return meta, body


def load_skills_from_dir(path: Path) -> list[SkillDefinition]:
    """从目录加载 skill 定义文件。

    对应 TS: skills/loadSkillsDir.ts loadSkillsDir()
    搜索路径下的所有 .md 文件，解析 frontmatter。
    """
    skills = []
    if not path.exists() or not path.is_dir():
        return skills

    for md_file in sorted(path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)

            name = meta.get("name", md_file.stem)
            description = meta.get("description", f"Skill: {name}")

            skill = SkillDefinition(
                name=name,
                description=description,
                prompt_template=body,
                allowed_tools=meta.get("allowedTools", []),
                model=meta.get("model"),
                user_invocable=meta.get("userInvocable", True),
                source="disk",
            )
            skills.append(skill)
            logger.debug("Loaded skill '%s' from %s", name, md_file)
        except Exception as e:
            logger.warning("Failed to load skill from %s: %s", md_file, e)

    return skills


def register_skill(skill: SkillDefinition) -> None:
    """注册一个 skill。"""
    _skills[skill.name] = skill


def register_bundled_skill(
        name: str,
        description: str,
        prompt_template: str,
        **kwargs: Any,
) -> None:
    """注册内置 skill。

    对应 TS: skills/bundledSkills.ts registerBundledSkill()
    """
    skill = SkillDefinition(
        name=name,
        description=description,
        prompt_template=prompt_template,
        source="bundled",
        **kwargs,
    )
    register_skill(skill)


def find_skill(name: str) -> SkillDefinition | None:
    """按名称查找 skill。"""
    return _skills.get(name)


def get_all_skills() -> list[SkillDefinition]:
    """获取所有已注册 skill。"""
    return list(_skills.values())


def discover_and_load_skills(cwd: str | Path | None = None) -> None:
    """从所有位置搜索并加载 skill。

    搜索路径（按优先级从低到高）：
    1. ~/.termpilot/skills/*.md（用户全局）
    2. .termpilot/skills/*.md（项目级）

    后加载的覆盖先加载的（同名 skill 以项目级为准）。
    """
    from pathlib import Path

    cwd_path = Path(cwd) if cwd else Path.cwd()

    # 用户全局 skills
    user_skills_dir = get_config_home() / "skills"
    for skill in load_skills_from_dir(user_skills_dir):
        register_skill(skill)

    # 项目级 skills
    project_skills_dir = cwd_path / ".termpilot" / "skills"
    for skill in load_skills_from_dir(project_skills_dir):
        register_skill(skill)

    if _skills:
        logger.info("Loaded %d skill(s): %s", len(_skills), ", ".join(_skills.keys()))


def get_skills_description_for_prompt() -> str:
    """生成 skill 列表的文本描述，用于 System Prompt 注入。"""
    skills = get_all_skills()
    if not skills:
        return ""

    lines = ["Available skills (invoke with Skill tool):", ""]
    for skill in skills:
        line = f"- {skill.name}: {skill.description}"
        if skill.user_invocable:
            line += " (user-invocable)"
        lines.append(line)

    return "\n".join(lines)
