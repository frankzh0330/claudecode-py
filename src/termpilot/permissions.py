"""权限系统。

对应 TS:
- utils/permissions/permissions.ts (权限检查主逻辑)
- utils/permissions/PermissionMode.ts (权限模式)
- utils/permissions/PermissionRule.ts (规则类型)
- utils/permissions/classifierDecision.ts (工具安全分类)
- utils/permissions/bashClassifier.ts (Bash 命令分类)
- utils/permissions/pathValidation.ts (路径安全验证)
- utils/permissions/shellRuleMatching.ts (通配符规则匹配)
- utils/permissions/dangerousPatterns.ts (危险命令模式)

TS 版 ~8000 行（24 文件），Python 版保留核心：
- 5 种权限模式
- 规则引擎（allow/deny/ask + 通配符匹配）
- 工具安全白名单（只读工具 + 元数据工具）
- Bash 危险命令检测（30+ 模式）
- 路径安全验证（.git/, .claude/, shell 配置, 路径穿越, shell 展开）
- 规则来源优先级（cli > session > local > project > user > policy）
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from termpilot.config import get_settings_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 权限模式 — 对应 TS PermissionMode.ts
# ---------------------------------------------------------------------------

class PermissionMode(Enum):
    """权限模式。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS = "bypassPermissions"
    DONT_ASK = "dontAsk"
    PLAN = "plan"


class PermissionBehavior(Enum):
    """权限行为。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """权限检查结果。"""

    behavior: PermissionBehavior
    message: str = ""
    rule_updates: list[dict] | None = None


@dataclass
class PermissionRule:
    """权限规则。

    格式: tool_name(pattern) → behavior
    示例: Bash(git push:*) → allow
          FileWrite(*) → deny
          Edit(/tmp/*) → allow
    支持: 通配符 ``*`` 匹配任意字符，``\\*`` 匹配字面量
    """

    tool_name: str
    behavior: PermissionBehavior
    pattern: str = "*"
    source: str = "session"  # cli / session / local / project / user / policy


@dataclass
class PermissionContext:
    """权限上下文。"""

    mode: PermissionMode = PermissionMode.DEFAULT
    allow_rules: list[PermissionRule] = field(default_factory=list)
    deny_rules: list[PermissionRule] = field(default_factory=list)
    ask_rules: list[PermissionRule] = field(default_factory=list)
    working_directory: str = ""
    disallowed_tools: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# 工具安全分类
# 对应 TS classifierDecision.ts SAFE_YOLO_ALLOWLISTED_TOOLS
# ---------------------------------------------------------------------------

# 只读工具 — 自动放行，不需要权限检查
SAFE_TOOLS = frozenset({
    "list_dir",
    # 文件读取/搜索
    "read_file",
    "glob",
    "grep",
    # 元数据工具（无副作用）
    "task_create",
    "task_update",
    "task_list",
    "task_get",
    # 交互工具
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode",
    # MCP 只读
    "list_mcp_resources",
    "read_mcp_resource",
})

# 有副作用的工具 — 默认需要用户确认
UNSAFE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "bash",
    "notebook_edit",
})

# 子代理工具 — 不允许在自动模式放行（防止绕过权限）
AGENT_TOOLS = frozenset({
    "agent",
})

# ---------------------------------------------------------------------------
# 路径安全验证
# 对应 TS permissions/pathValidation.ts
# ---------------------------------------------------------------------------

# 受保护的文件名（不区分大小写）— 禁止写入
_PROTECTED_FILES = frozenset({
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".claude.json",
})

# 受保护的目录名（不区分大小写）
_PROTECTED_DIRS = frozenset({
    ".git",
    ".claude",
    ".ssh",
    ".gnupg",
})

# Shell 展开语法模式 — 文件路径中禁止使用
_SHELL_EXPANSION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\$[\w({]"), "Shell 变量展开 ($VAR/${VAR}/$(cmd))"),
    (re.compile(r"%\w+%"), "Windows 环境变量 (%VAR%)"),
]


def validate_path_safety(file_path: str, operation: str = "write") -> str | None:
    """验证文件路径安全性。

    对应 TS pathValidation.ts。
    返回 None 表示安全，否则返回拒绝原因。

    检查项：
    1. 路径穿越（../）
    2. Shell 展开语法（$VAR, $(cmd)）
    3. 受保护的文件（shell 配置、git 配置等）
    4. 受保护的目录（.git/, .claude/, .ssh/）
    5. 写入操作的 glob 模式
    """
    if not file_path:
        return None

    # 1. 路径穿越（检查原始路径中的 ..）
    raw_parts = file_path.replace("\\", "/").split("/")
    if ".." in raw_parts:
        return "路径包含 '..' 穿越序列"

    # 标准化路径（处理 /./, 重复 / 等）
    normalized = os.path.normpath(file_path)
    basename = os.path.basename(normalized).lower()
    parts = normalized.replace("\\", "/").split("/")

    # 2. Shell 展开语法
    for pattern, desc in _SHELL_EXPANSION_PATTERNS:
        if pattern.search(file_path):
            return f"路径包含 {desc}"

    # 3. 受保护的文件名
    if basename in _PROTECTED_FILES:
        return f"受保护的配置文件: {basename}"

    # 4. 受保护的目录
    for part in parts:
        if part.lower() in _PROTECTED_DIRS:
            # .claude 目录下的 memory/ 允许写入（memory 系统需要）
            if part.lower() == ".claude":
                # 允许 .claude/projects/*/memory/ 写入
                continue
            return f"受保护的目录: {part}/"

    # 5. 写入操作的 glob 模式（防止意外批量操作）
    if operation == "write" and any(c in basename for c in ("*", "?", "[", "]")):
        return "文件名包含 glob 通配符"

    return None


# ---------------------------------------------------------------------------
# Bash 危险命令检测
# 对应 TS permissions/bashClassifier.ts + permissions/dangerousPatterns.ts
# ---------------------------------------------------------------------------

# 高危命令模式 — 需要额外警告或阻止
DANGEROUS_BASH_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # (pattern, description, severity: "high" | "medium")
    # ── 数据破坏 ──
    (re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|-rf\s+|--recursive\s+--force\s+)"),
     "递归强制删除", "high"),
    (re.compile(r"\bsudo\s+rm\b"), "sudo 删除", "high"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), "删除数据库", "high"),
    (re.compile(r"\btruncate\s+table\b", re.IGNORECASE), "清空表", "high"),
    (re.compile(r"\bdd\s+if="), "dd 磁盘操作", "high"),
    (re.compile(r">\s*/dev/sd"), "直接写磁盘设备", "high"),
    (re.compile(r"\bshred\b"), "文件粉碎", "high"),
    (re.compile(r"\bmkfs\b"), "格式化文件系统", "high"),

    # ── Git 破坏性操作 ──
    (re.compile(r"\bgit\s+push\s+.*--force"), "强制推送", "high"),
    (re.compile(r"\bgit\s+reset\s+--hard"), "硬重置", "high"),
    (re.compile(r"\bgit\s+push\s+origin\s+--delete"), "删除远程分支", "high"),
    (re.compile(r"\bgit\s+branch\s+(-D|--delete\s+--force)"), "强制删除分支", "high"),
    (re.compile(r"\bgit\s+clean\s+(-[a-zA-Z]*f[a-zA-Z]*|-df)"), "强制清理未跟踪文件", "medium"),
    (re.compile(r"\bgit\s+checkout\s+\."), "丢弃所有工作区修改", "medium"),

    # ── 权限变更 ──
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "递归设置 777 权限", "high"),
    (re.compile(r"\bchown\s+-R\b"), "递归修改文件所有者", "medium"),

    # ── 进程管理 ──
    (re.compile(r"\bkill\s+-9\b"), "强制终止进程 (SIGKILL)", "medium"),
    (re.compile(r"\bkillall\b"), "终止所有匹配进程", "medium"),
    (re.compile(r"\bpkill\s+-9\b"), "强制终止匹配进程", "medium"),

    # ── 远程执行 ──
    (re.compile(r"\bcurl\s+.*\|\s*(sh|bash)\b"), "管道执行远程脚本 (curl)", "high"),
    (re.compile(r"\bwget\s+.*\|\s*(sh|bash)\b"), "管道执行远程脚本 (wget)", "high"),
    (re.compile(r"\beval\b"), "eval 执行动态代码", "medium"),

    # ── 包管理器全局操作 ──
    (re.compile(r"\bnpm\s+publish\b"), "发布 npm 包", "medium"),
    (re.compile(r"\bpip\s+install\s+--user\b"), "用户级 pip 安装", "low"),
    (re.compile(r"\bpip\s+uninstall\b"), "pip 卸载包", "medium"),

    # ── 网络服务 ──
    (re.compile(r"\bssh\b"), "SSH 远程连接", "medium"),
    (re.compile(r"\bscp\b"), "远程文件传输", "medium"),
    (re.compile(r"\brsync\b"), "远程同步", "low"),

    # ── 系统操作 ──
    (re.compile(r"\bsudo\b"), "sudo 提权执行", "medium"),
    (re.compile(r"\bsystemctl\s+(restart|stop|disable)\b"), "系统服务控制", "medium"),
    (re.compile(r"\bdocker\s+(rm|rmi)\b"), "删除 Docker 容器/镜像", "medium"),
    (re.compile(r"\bdocker\s+system\s+prune\b"), "清理 Docker 系统", "medium"),
]

# Bash 安全前缀 — 这些命令前缀默认认为是低风险的
_SAFE_BASH_PREFIXES = frozenset({
    "git status",
    "git log",
    "git diff",
    "git branch",
    "git show",
    "git remote",
    "git stash list",
    "git tag",
    "ls",
    "cat",
    "head",
    "tail",
    "echo",
    "pwd",
    "which",
    "where",
    "type",
    "find",
    "wc",
    "sort",
    "uniq",
    "diff",
    "grep",
    "rg",
    "fd",
    "tree",
    "du",
    "df",
    "free",
    "top",
    "ps",
    "env",
    "printenv",
    "node --version",
    "python --version",
    "python3 --version",
    "pip list",
    "pip show",
    "npm list",
    "npm --version",
})


def classify_bash_command(command: str) -> tuple[str | None, str]:
    """检查 Bash 命令是否危险。

    对应 TS bashClassifier.ts + permissionSetup.ts。
    返回 (warning_message, severity)。warning_message 为 None 表示安全。

    逻辑：
    1. 安全前缀快速放行
    2. 高危模式检测
    3. 默认需要确认
    """
    stripped = command.strip()

    # 1. 安全前缀快速放行
    for prefix in _SAFE_BASH_PREFIXES:
        if stripped == prefix or stripped.startswith(prefix + " ") or stripped.startswith(prefix + "\t"):
            return None, "safe"

    # 2. 高危模式检测
    for pattern, description, severity in DANGEROUS_BASH_PATTERNS:
        if pattern.search(command):
            return f"危险操作: {description}", severity

    return None, "unknown"


# ---------------------------------------------------------------------------
# 规则匹配 — 对应 TS shellRuleMatching.ts
# ---------------------------------------------------------------------------

def _wildcard_to_regex(pattern: str) -> re.Pattern:
    """将通配符模式转为正则表达式。

    对应 TS shellRuleMatching.ts 的 wildcard 匹配逻辑。
    支持: ``*`` (任意字符), ``\\*`` (字面量星号), ``\\\\`` (字面量反斜杠)
    """
    regex_parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '\\' and i + 1 < len(pattern):
            next_c = pattern[i + 1]
            if next_c == '*':
                regex_parts.append(re.escape('*'))
                i += 2
                continue
            elif next_c == '\\':
                regex_parts.append(re.escape('\\'))
                i += 2
                continue
            else:
                regex_parts.append(re.escape(c))
                i += 1
                continue
        elif c == '*':
            regex_parts.append('.*')
            i += 1
            continue
        else:
            regex_parts.append(re.escape(c))
            i += 1
    return re.compile('^' + ''.join(regex_parts) + '$', re.DOTALL)


def _match_rule(rule: PermissionRule, tool_name: str, tool_input: dict) -> bool:
    """检查规则是否匹配当前工具调用。

    对应 TS shellRuleMatching.ts + permissionRuleParser.ts。

    支持的模式:
    - pattern="*" → 匹配所有
    - pattern="git push:*" → 匹配以 "git push" 开头的命令（旧语法）
    - pattern="git push*" → 通配符匹配（新语法）
    - pattern="/tmp/*" → 路径通配符
    - pattern="git add file*" → 混合通配符
    """
    if rule.tool_name != tool_name:
        return False

    if rule.pattern == "*":
        return True

    pattern = rule.pattern

    # Bash 工具 — 匹配命令
    if tool_name == "bash" and "command" in tool_input:
        command = tool_input.get("command", "").strip()
        # 旧语法: "git push:*" → 前缀匹配
        if pattern.endswith(":*"):
            prefix = pattern[:-2]  # 去掉 :*
            return command.startswith(prefix)
        # 新语法: 通配符匹配
        try:
            regex = _wildcard_to_regex(pattern)
            return bool(regex.match(command))
        except re.error:
            # 正则编译失败，fallback 到前缀匹配
            return command.startswith(pattern)

    # 文件工具 — 匹配路径
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False

    # 通配符路径匹配
    if '*' in pattern or '?' in pattern:
        try:
            regex = _wildcard_to_regex(pattern)
            return bool(regex.match(file_path))
        except re.error:
            pass

    # 简单前缀匹配: /tmp/* → 匹配 /tmp/xxx
    if pattern.endswith("/*"):
        prefix = pattern[:-1]  # 去掉 *，保留 /
        return file_path.startswith(prefix)

    # 精确匹配
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
# 规则解析 — 对应 TS permissionRuleParser.ts
# ---------------------------------------------------------------------------

def parse_rule_string(rule_str: str, behavior: PermissionBehavior = PermissionBehavior.ALLOW,
                      source: str = "session") -> PermissionRule | None:
    """解析规则字符串。

    格式: ToolName(pattern) 或 ToolName
    示例: "Bash(git push:*)", "write_file(/tmp/*)", "Bash"

    对应 TS permissionRuleValueFromString()。
    """
    rule_str = rule_str.strip()
    if not rule_str:
        return None

    # 查找 ToolName(pattern) 格式
    paren_open = rule_str.find("(")
    if paren_open > 0 and rule_str.endswith(")"):
        tool_name = rule_str[:paren_open]
        pattern = rule_str[paren_open + 1:-1]
        # 处理转义
        pattern = pattern.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    else:
        # 无括号 = 匹配所有
        tool_name = rule_str
        pattern = "*"

    # 标准化工具名（兼容 TS 版大写命名）
    tool_name = _normalize_tool_name(tool_name)

    return PermissionRule(
        tool_name=tool_name,
        behavior=behavior,
        pattern=pattern,
        source=source,
    )


def _normalize_tool_name(name: str) -> str:
    """标准化工具名。

    对应 TS 中旧名 → 新名的映射。
    """
    _NAME_MAP = {
        "Bash": "bash",
        "Read": "read_file",
        "ReadFile": "read_file",
        "FileRead": "read_file",
        "Write": "write_file",
        "WriteFile": "write_file",
        "FileWrite": "write_file",
        "Edit": "edit_file",
        "EditFile": "edit_file",
        "FileEdit": "edit_file",
        "Glob": "glob",
        "Grep": "grep",
        "Agent": "agent",
        "NotebookEdit": "notebook_edit",
    }
    return _NAME_MAP.get(name, name.lower())


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
    1. disallowed_tools 检查 → DENY
    2. 安全工具白名单 → ALLOW
    3. deny 规则匹配 → DENY
    4. BYPASS 模式 → ALLOW（子代理工具除外）
    5. PLAN 模式 → 只允许只读工具
    6. allow 规则匹配 → ALLOW
    7. ask 规则 → ASK
    8. ACCEPT_EDITS 模式 + 工作目录内文件编辑 → ALLOW
    9. 路径安全验证 → DENY（写入受保护路径）
    10. Bash 命令分类 → ASK（带警告）
    11. 默认策略: UNSAFE → ASK, 其他 → ALLOW
    """
    logger.debug("check_permission: tool=%s, mode=%s, input_keys=%s",
                 tool_name, context.mode.value, list(tool_input.keys()))

    # 1. disallowed_tools（CLI --disallowed-tools 参数）
    if tool_name in context.disallowed_tools:
        logger.debug("→ DENY (disallowed by CLI)")
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"工具被 CLI 参数禁止: {tool_name}",
        )

    # 2. 安全工具白名单（只读工具 + 元数据工具）
    if tool_name in SAFE_TOOLS:
        logger.debug("→ ALLOW (safe tool whitelist)")
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 3. deny 规则（高优先级）
    deny_rule = _find_matching_rule(context.deny_rules, tool_name, tool_input)
    if deny_rule:
        logger.debug("→ DENY (deny rule: %s(%s))", deny_rule.tool_name, deny_rule.pattern)
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"被规则拒绝: {deny_rule.tool_name}({deny_rule.pattern})",
        )

    # 4. BYPASS 模式 — 跳过所有检查（子代理工具除外）
    if context.mode == PermissionMode.BYPASS:
        if tool_name in AGENT_TOOLS:
            logger.debug("→ DENY (bypass mode but agent tool)")
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message="子代理工具不允许在 BYPASS 模式自动放行",
            )
        logger.debug("→ ALLOW (bypass mode)")
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 5. PLAN 模式 — 只允许只读工具
    if context.mode == PermissionMode.PLAN:
        if tool_name in SAFE_TOOLS:
            return PermissionResult(behavior=PermissionBehavior.ALLOW)
        logger.debug("→ DENY (plan mode, write blocked)")
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"规划模式不允许执行写操作: {tool_name}",
        )

    # 6. allow 规则
    allow_rule = _find_matching_rule(context.allow_rules, tool_name, tool_input)
    if allow_rule:
        logger.debug("→ ALLOW (allow rule: %s(%s))", allow_rule.tool_name, allow_rule.pattern)
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 7. ask 规则 — 强制询问
    ask_rule = _find_matching_rule(context.ask_rules, tool_name, tool_input)
    if ask_rule:
        logger.debug("→ ASK (ask rule: %s(%s))", ask_rule.tool_name, ask_rule.pattern)
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"需要确认: {tool_name}",
        )

    # 8. ACCEPT_EDITS 模式 — 工作目录内的文件编辑自动放行
    if context.mode == PermissionMode.ACCEPT_EDITS:
        if tool_name in ("write_file", "edit_file"):
            file_path = tool_input.get("file_path", "")
            if _is_in_working_directory(file_path, context.working_directory):
                # 仍然需要路径安全检查
                path_issue = validate_path_safety(file_path, "write")
                if path_issue:
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        message=f"路径安全检查失败: {path_issue}",
                    )
                logger.debug("→ ALLOW (accept_edits: in workdir)")
                return PermissionResult(behavior=PermissionBehavior.ALLOW)

    # 9. 路径安全验证（文件写入/编辑）
    if tool_name in ("write_file", "edit_file"):
        file_path = tool_input.get("file_path", "")
        path_issue = validate_path_safety(file_path, "write")
        if path_issue:
            logger.debug("→ DENY (path safety: %s)", path_issue)
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"路径安全检查失败: {path_issue}",
            )

    # 10. Bash 命令分类
    if tool_name == "bash":
        command = tool_input.get("command", "")
        danger_msg, severity = classify_bash_command(command)
        if danger_msg and severity == "high":
            logger.debug("→ DENY (high-danger bash: %s)", danger_msg)
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=danger_msg,
            )
        elif danger_msg:
            logger.debug("→ ASK (medium-danger bash: %s)", danger_msg)
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=danger_msg,
            )

    # 11. 默认策略
    if tool_name in UNSAFE_TOOLS:
        # DONT_ASK 模式下，需要询问的自动拒绝
        if context.mode == PermissionMode.DONT_ASK:
            logger.debug("→ DENY (dont_ask mode for unsafe tool)")
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"权限被自动拒绝（DONT_ASK 模式）: {tool_name}",
            )
        logger.debug("→ ASK (default: unsafe tool)")
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"工具 {tool_name} 需要用户确认",
        )

    # 未知工具默认允许（保持向后兼容）
    logger.debug("→ ALLOW (unknown tool, default allow)")
    return PermissionResult(behavior=PermissionBehavior.ALLOW)


def _is_in_working_directory(file_path: str, working_directory: str) -> bool:
    """检查文件路径是否在工作目录内。"""
    if not file_path or not working_directory:
        return False
    try:
        abs_path = Path(file_path).expanduser().resolve()
        work_dir = Path(working_directory).resolve()
        return str(abs_path).startswith(str(work_dir) + os.sep) or abs_path == work_dir
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 规则持久化
# 对应 TS permissionsLoader.ts + settings 中的 permissions 配置
# ---------------------------------------------------------------------------

_RULE_SOURCE_PRIORITY = {
    "cli": 0,
    "session": 1,
    "local": 2,
    "project": 3,
    "user": 4,
    "policy": 5,
}


def _get_settings_path() -> Path:
    """获取 settings.json 路径。"""
    return get_settings_path()


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
    path = get_settings_path()
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

    对应 TS permissionsLoader.ts，支持多来源规则合并。
    """
    settings = _read_settings()
    perm_config = settings.get("permissions", {})
    raw_rules = perm_config.get("rules", [])

    rules: list[PermissionRule] = []
    for raw in raw_rules:
        try:
            behavior = PermissionBehavior(raw["behavior"])
            rules.append(PermissionRule(
                tool_name=_normalize_tool_name(raw["tool_name"]),
                pattern=raw.get("pattern", "*"),
                behavior=behavior,
                source=raw.get("source", "user"),
            ))
        except (KeyError, ValueError):
            continue

    # 按 source priority 排序（高优先级的在前）
    rules.sort(key=lambda r: _RULE_SOURCE_PRIORITY.get(r.source, 99))

    return rules


def save_permission_rule(rule: PermissionRule) -> None:
    """持久化权限规则到 settings.json。"""
    settings = _read_settings()
    if "permissions" not in settings:
        settings["permissions"] = {}
    if "rules" not in settings["permissions"]:
        settings["permissions"]["rules"] = []

    rules = settings["permissions"]["rules"]
    rule_dict = {
        "tool_name": rule.tool_name,
        "pattern": rule.pattern,
        "behavior": rule.behavior.value,
        "source": rule.source,
    }

    # 避免重复
    for existing in rules:
        if (existing.get("tool_name") == rule_dict["tool_name"]
                and existing.get("pattern") == rule_dict["pattern"]):
            existing["behavior"] = rule_dict["behavior"]
            existing["source"] = rule_dict["source"]
            _write_settings(settings)
            return

    rules.append(rule_dict)
    _write_settings(settings)


def build_permission_context(
        working_directory: str = "",
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
) -> PermissionContext:
    """构建权限上下文。

    对应 TS 中组装 ToolPermissionContext 的逻辑。
    支持 CLI 参数覆盖规则。
    """
    if not working_directory:
        working_directory = str(Path.cwd())

    settings = _read_settings()
    perm_config = settings.get("permissions", {})

    # 解析权限模式
    mode_str = perm_config.get("mode") or perm_config.get("defaultMode", "default")
    try:
        mode = PermissionMode(mode_str)
    except ValueError:
        mode = PermissionMode.DEFAULT

    # 加载规则
    rules = load_permission_rules()
    allow_rules = [r for r in rules if r.behavior == PermissionBehavior.ALLOW]
    deny_rules = [r for r in rules if r.behavior == PermissionBehavior.DENY]
    ask_rules = [r for r in rules if r.behavior == PermissionBehavior.ASK]

    # CLI --allowed-tools 参数：添加额外的 allow 规则
    if allowed_tools:
        for tool_spec in allowed_tools:
            rule = parse_rule_string(tool_spec, PermissionBehavior.ALLOW, source="cli")
            if rule and rule not in allow_rules:
                allow_rules.insert(0, rule)  # CLI 优先级最高

    # CLI --disallowed-tools 参数
    disallowed_set: set[str] = set()
    if disallowed_tools:
        disallowed_set = {t.lower().strip() for t in disallowed_tools if t.strip()}

    logger.debug("permission context: mode=%s, allow=%d, deny=%d, ask=%d, cwd=%s, disallowed=%s",
                 mode.value, len(allow_rules), len(deny_rules), len(ask_rules),
                 working_directory, disallowed_set or "none")

    return PermissionContext(
        mode=mode,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        ask_rules=ask_rules,
        working_directory=working_directory,
        disallowed_tools=disallowed_set,
    )
