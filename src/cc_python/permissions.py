"""权限系统。

对应 TS:
- utils/permissions/permissions.ts (权限检查主逻辑)
- utils/permissions/PermissionMode.ts (权限模式)
- utils/permissions/PermissionRule.ts (规则类型)
- utils/permissions/classifierDecision.ts (工具安全分类)
- utils/permissions/bashClassifier.ts (Bash 命令分类)

TS 版 ~8000 行（24 文件），Python 简化版保留核心流程：
- 4 种权限模式
- 规则引擎（allow/deny/ask）
- 工具安全白名单
- Bash 危险命令检测
- 用户确认回调集成
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PermissionMode(Enum):
    """权限模式。对应 TS PermissionMode.ts。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS = "bypassPermissions"
    DONT_ASK = "dontAsk"


class PermissionBehavior(Enum):
    """权限行为。对应 TS PermissionBehavior。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """权限检查结果。对应 TS PermissionDecision 类型。"""

    behavior: PermissionBehavior
    message: str = ""
    rule_updates: list[dict] | None = None


@dataclass
class PermissionRule:
    """权限规则。对应 TS PermissionRule。

    格式: tool_name(pattern) → behavior
    示例: Bash(git push:*) → allow
          FileWrite(*) → deny
          Edit(/tmp/*) → allow
    """

    tool_name: str
    behavior: PermissionBehavior
    pattern: str = "*"
    source: str = "session"


@dataclass
class PermissionContext:
    """权限上下文。对应 TS ToolPermissionContext。"""

    mode: PermissionMode = PermissionMode.DEFAULT
    allow_rules: list[PermissionRule] = field(default_factory=list)
    deny_rules: list[PermissionRule] = field(default_factory=list)
    ask_rules: list[PermissionRule] = field(default_factory=list)
    working_directory: str = ""


# ---------------------------------------------------------------------------
# 工具安全分类
# 对应 TS classifierDecision.ts 的 SAFE_YOLO_ALLOWLISTED_TOOLS
# ---------------------------------------------------------------------------

# 只读工具 — 自动放行，不需要权限检查
SAFE_TOOLS = frozenset({
    "read_file",
    "glob",
    "grep",
})

# 有副作用的工具 — 默认需要用户确认
UNSAFE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "bash",
})


# ---------------------------------------------------------------------------
# Bash 危险命令检测
# 对应 TS permissions/bashClassifier.ts + permissions/dangerousPatterns.ts
# ---------------------------------------------------------------------------

# 危险命令模式 — 需要额外警告
DANGEROUS_BASH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|-rf\s+|--recursive\s+--force\s+)"), "递归强制删除"),
    (re.compile(r"\bgit\s+push\s+.*--force"), "强制推送"),
    (re.compile(r"\bgit\s+reset\s+--hard"), "硬重置"),
    (re.compile(r"\bgit\s+push\s+origin\s+--delete"), "删除远程分支"),
    (re.compile(r"\bgit\s+branch\s+(-D|--delete\s+--force)"), "强制删除分支"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), "删除数据库"),
    (re.compile(r"\btruncate\s+table\b", re.IGNORECASE), "清空表"),
    (re.compile(r"\bkill\s+-9\b"), "强制终止进程"),
    (re.compile(r"\bdd\s+if="), "dd 磁盘操作"),
    (re.compile(r">\s*/dev/sd"), "直接写磁盘设备"),
    (re.compile(r"\bsudo\s+rm\b"), "sudo 删除"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "递归设置 777 权限"),
    (re.compile(r"\bcurl\s+.*\|\s*sh\b"), "管道执行远程脚本"),
    (re.compile(r"\bwget\s+.*\|\s*sh\b"), "管道执行远程脚本"),
]


def classify_bash_command(command: str) -> str | None:
    """检查 Bash 命令是否危险。

    对应 TS bashClassifier.ts。返回警告信息，None 表示安全。
    """
    for pattern, description in DANGEROUS_BASH_PATTERNS:
        if pattern.search(command):
            return f"危险操作: {description}"
    return None


# ---------------------------------------------------------------------------
# 规则匹配
# ---------------------------------------------------------------------------

def _match_rule(rule: PermissionRule, tool_name: str, tool_input: dict) -> bool:
    """检查规则是否匹配当前工具调用。

    规则格式:
    - tool_name="Bash", pattern="*" → 匹配所有 Bash 调用
    - tool_name="Bash", pattern="git push:*" → 匹配 git push 子命令
    - tool_name="write_file", pattern="/tmp/*" → 匹配路径前缀
    """
    if rule.tool_name != tool_name:
        return False

    if rule.pattern == "*":
        return True

    # Bash 工具 — 匹配命令前缀
    if tool_name == "bash" and "command" in tool_input:
        command = tool_input.get("command", "")
        # "git push:*" 匹配 "git push origin main"
        rule_prefix = rule.pattern.rstrip(":*")
        return command.strip().startswith(rule_prefix)

    # 文件工具 — 匹配路径模式
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False

    # 简单 glob 匹配: /tmp/* 匹配 /tmp/xxx
    pattern = rule.pattern
    if pattern.endswith("/*"):
        prefix = pattern[:-1]  # 去掉 *，保留 /
        return file_path.startswith(prefix)
    return file_path == pattern


def _find_matching_rule(
    rules: list[PermissionRule],
    tool_name: str,
    tool_input: dict,
) -> PermissionRule | None:
    """在规则列表中查找匹配的规则。"""
    for rule in rules:
        if _match_rule(rule, tool_name, tool_input):
            return rule
    return None


# ---------------------------------------------------------------------------
# 权限检查主函数
# 对应 TS permissions.ts hasPermissionsToUseToolInner()
# ---------------------------------------------------------------------------

def check_permission(
    tool_name: str,
    tool_input: dict,
    context: PermissionContext,
) -> PermissionResult:
    """权限检查主函数。

    检查流程（与 TS 版对齐）:
    1. 安全工具白名单 → ALLOW
    2. deny 规则匹配 → DENY
    3. BYPASS 模式 → ALLOW
    4. DONT_ASK 模式 → DENY（对需要询问的）
    5. allow 规则匹配 → ALLOW
    6. ACCEPT_EDITS 模式 + 工作目录内文件编辑 → ALLOW
    7. Bash 危险命令 → ASK（带警告）
    8. 默认: 有副作用工具 → ASK, 其他 → ALLOW
    """
    # 1. 安全工具白名单（只读工具）
    if tool_name in SAFE_TOOLS:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 2. deny 规则（最高优先级）
    deny_rule = _find_matching_rule(context.deny_rules, tool_name, tool_input)
    if deny_rule:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"被规则拒绝: {deny_rule.tool_name}({deny_rule.pattern})",
        )

    # 3. BYPASS 模式 — 跳过所有检查
    if context.mode == PermissionMode.BYPASS:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 4. allow 规则
    allow_rule = _find_matching_rule(context.allow_rules, tool_name, tool_input)
    if allow_rule:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 5. ask 规则 — 强制询问
    ask_rule = _find_matching_rule(context.ask_rules, tool_name, tool_input)
    if ask_rule:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"需要确认: {tool_name}",
        )

    # 6. ACCEPT_EDITS 模式 — 工作目录内的文件编辑自动放行
    if context.mode == PermissionMode.ACCEPT_EDITS:
        if tool_name in ("write_file", "edit_file"):
            file_path = tool_input.get("file_path", "")
            if file_path.startswith(context.working_directory):
                return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 7. Bash 危险命令检测
    if tool_name == "bash":
        command = tool_input.get("command", "")
        danger_msg = classify_bash_command(command)
        if danger_msg:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=danger_msg,
            )

    # 8. 默认策略
    if tool_name in UNSAFE_TOOLS:
        # DONT_ASK 模式下，需要询问的自动拒绝
        if context.mode == PermissionMode.DONT_ASK:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"权限被自动拒绝（DONT_ASK 模式）: {tool_name}",
            )
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"工具 {tool_name} 需要用户确认",
        )

    # 未知工具默认允许（保持向后兼容）
    return PermissionResult(behavior=PermissionBehavior.ALLOW)


# ---------------------------------------------------------------------------
# 规则持久化
# 对应 TS permissionsLoader.ts + settings 中的 permissions 配置
# ---------------------------------------------------------------------------

def _get_settings_path() -> Path:
    """获取 settings.json 路径。"""
    return Path.home() / ".claude" / "settings.json"


def _read_settings() -> dict:
    """读取 settings.json。"""
    path = _get_settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_settings(settings: dict) -> None:
    """写入 settings.json。"""
    path = _get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_permission_rules() -> list[PermissionRule]:
    """从 settings.json 加载权限规则。

    settings.json 格式:
    {
      "permissions": {
        "mode": "default",
        "rules": [
          {"tool_name": "Bash", "pattern": "git push:*", "behavior": "allow"},
          {"tool_name": "write_file", "pattern": "*", "behavior": "allow"}
        ]
      }
    }
    """
    settings = _read_settings()
    perm_config = settings.get("permissions", {})
    raw_rules = perm_config.get("rules", [])

    rules: list[PermissionRule] = []
    for raw in raw_rules:
        try:
            behavior = PermissionBehavior(raw["behavior"])
            rules.append(PermissionRule(
                tool_name=raw["tool_name"],
                pattern=raw.get("pattern", "*"),
                behavior=behavior,
                source="user_settings",
            ))
        except (KeyError, ValueError):
            continue

    return rules


def save_permission_rule(rule: PermissionRule) -> None:
    """持久化权限规则到 settings.json。"""
    settings = _read_settings()
    if "permissions" not in settings:
        settings["permissions"] = {}
    if "rules" not in settings["permissions"]:
        settings["permissions"]["rules"] = []

    # 检查是否已存在相同规则
    rules = settings["permissions"]["rules"]
    rule_dict = {
        "tool_name": rule.tool_name,
        "pattern": rule.pattern,
        "behavior": rule.behavior.value,
    }

    # 避免重复
    for existing in rules:
        if (existing.get("tool_name") == rule_dict["tool_name"]
                and existing.get("pattern") == rule_dict["pattern"]):
            existing["behavior"] = rule_dict["behavior"]
            _write_settings(settings)
            return

    rules.append(rule_dict)
    _write_settings(settings)


def build_permission_context(working_directory: str = "") -> PermissionContext:
    """构建权限上下文。对应 TS 中组装 ToolPermissionContext 的逻辑。"""
    if not working_directory:
        working_directory = str(Path.cwd())

    settings = _read_settings()
    perm_config = settings.get("permissions", {})

    # 解析权限模式
    mode_str = perm_config.get("mode", "default")
    try:
        mode = PermissionMode(mode_str)
    except ValueError:
        mode = PermissionMode.DEFAULT

    # 加载规则
    rules = load_permission_rules()
    allow_rules = [r for r in rules if r.behavior == PermissionBehavior.ALLOW]
    deny_rules = [r for r in rules if r.behavior == PermissionBehavior.DENY]
    ask_rules = [r for r in rules if r.behavior == PermissionBehavior.ASK]

    return PermissionContext(
        mode=mode,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        ask_rules=ask_rules,
        working_directory=working_directory,
    )
