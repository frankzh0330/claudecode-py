"""上下文压缩（Context Compact）。

对应 TS:
- services/compact/compact.ts (主压缩逻辑)
- services/compact/autoCompact.ts (自动触发)
- services/compact/microCompact.ts (轻量工具结果清理)
- services/compact/prompt.ts (摘要 prompt)

TS 版 9 文件 ~3000 行，Python 简化版保留核心：
- token 估算（字符数 → token 数近似）
- micro-compact：清理旧的工具结果，不调用 LLM
- full-compact：用 LLM 为旧消息生成摘要
- auto-compact：自动判断是否需要压缩

三层递进策略：
1. 估算 token → 未超阈值 → 不压缩
2. 超阈值 → micro-compact（清理工具结果）→ 再检查
3. 仍超阈值 → full-compact（LLM 生成摘要）
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CONTEXT_WINDOW_DEFAULT = 200_000  # 默认上下文窗口
COMPACT_THRESHOLD_RATIO = 0.75  # 75% 时触发压缩
COMPACT_TARGET_RATIO = 0.50  # 压缩到 50%

MICROCOMPACT_MAX_TOOL_RESULTS = 10  # count-based: 可清理工具最多保留最近 N 个结果
TOKEN_CHARS_RATIO = 3  # ~3 字符 = 1 token（混合中英文估算）

# 可安全清理工具结果的白名单（对应 TS COMPACTABLE_TOOLS）。
# 只包含只读/幂等工具，它们的结果丢失后可以通过重新执行找回。
# edit_file / write_file 等有副作用的工具不在此列：
#   - 它们的 tool_use 参数里包含完整操作信息（old_string/new_string 等），
#     模型需要这些信息来理解做了什么修改
#   - 清理结果可能丢失错误信息（如编辑冲突），导致模型重复犯错
COMPACTABLE_TOOLS = frozenset({
    "read_file",  # 文件内容可重新读取
    "bash",  # 命令输出可重新执行
    "grep",  # 搜索结果可重新搜索
    "glob",  # 文件列表可重新匹配
})

# Time-based micro-compact 配置（对应 TS timeBasedMCConfig.ts）。
# 当用户闲置超过阈值时间后，服务端 prompt cache 必然已过期，
# 此时清理旧工具结果可以缩小重写量。这是比 count-based 更安全的策略：
# 闲置很久的旧结果大概率不再相关。
TIME_BASED_MC_GAP_MINUTES = 60  # 60 分钟，对齐 TS 默认值和 Anthropic 服务端 cache TTL
TIME_BASED_MC_KEEP_RECENT = 5  # 保留最近 N 个可清理工具的结果

# Time-based 清理后的占位符（对应 TS TIME_BASED_MC_CLEARED_MESSAGE）
TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

# ---------------------------------------------------------------------------
# 压缩 Prompt
# 对应 TS: services/compact/prompt.ts getCompactPrompt()
# ---------------------------------------------------------------------------

_COMPACT_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED. Your entire response must be plain text.

Summarize the conversation below. Your summary must capture:

1. Primary Request and Intent: What the user asked for
2. Key Technical Concepts: Technologies and patterns discussed
3. Files and Code Sections: Files examined or modified (include file paths)
4. Errors and Fixes: Problems encountered and solutions
5. All User Messages: Each user message verbatim
6. Pending Tasks: Unfinished work
7. Current Work: What was being worked on most recently

<analysis>
[Your reasoning about what is important to preserve]
</analysis>

<summary>
[The structured summary following the format above]
</summary>
"""


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

def _count_content_tokens(content: str | list | dict) -> int:
    """估算消息内容的 token 数。"""
    if isinstance(content, str):
        return len(content) // TOKEN_CHARS_RATIO
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                # tool_result 内容、text 文本等
                text = block.get("content", "")
                if isinstance(text, str):
                    total += len(text)
                else:
                    total += len(str(text))
                # tool_use 的 input 也计入
                inp = block.get("input", {})
                if isinstance(inp, dict):
                    total += len(str(inp))
            else:
                total += len(str(block))
        return total // TOKEN_CHARS_RATIO
    return 0


def estimate_tokens(
        messages: list[dict],
        system_prompt: str = "",
) -> int:
    """估算消息列表的总 token 数。

    对应 TS 中 token counting 逻辑。

    简化估算：~3 字符 = 1 token（混合中英文）。
    不依赖 tiktoken，避免额外依赖。
    """
    total = len(system_prompt) // TOKEN_CHARS_RATIO
    for msg in messages:
        total += _count_content_tokens(msg.get("content", ""))
        # role 和其他元数据也消耗少量 token
        total += 4  # 每条消息约 4 tokens 的开销
    return int(total)


# ---------------------------------------------------------------------------
# Micro-compact：清理旧工具结果
# 对应 TS: services/compact/microCompact.ts
#
# 两条路径（与 TS 对齐）：
# 1. Time-based: 用户闲置 > 60min → 清理旧结果（更安全，cache 已过期）
# 2. Count-based: 可清理结果数 > MAX → 截断最早的
# ---------------------------------------------------------------------------

def _collect_tool_use_id_to_name(messages: list[dict]) -> dict[str, str]:
    """从 assistant 消息中收集 tool_use_id → tool_name 的映射。

    tool_result 块本身不包含工具名，需要通过 tool_use_id 关联到
    对应的 tool_use 块才能知道是哪个工具的结果。
    """
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id", "")
                tool_name = block.get("name", "")
                if tool_id and tool_name:
                    id_to_name[tool_id] = tool_name
    return id_to_name


def _collect_compactable_tool_ids(messages: list[dict]) -> list[str]:
    """按出现顺序收集所有白名单内工具的 tool_use_id。

    对应 TS collectCompactableToolIds()。
    用于 time-based 路径，需要知道哪些 tool_use_id 属于可清理工具。
    """
    id_to_name = _collect_tool_use_id_to_name(messages)
    ids: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id", "")
                tool_name = block.get("name", "")
                if tool_id and tool_name in COMPACTABLE_TOOLS:
                    ids.append(tool_id)
    return ids


# --- Time-based micro-compact ---
# 对应 TS: microCompact.ts maybeTimeBasedMicrocompact()

def _evaluate_time_based_trigger(messages: list[dict]) -> float | None:
    """检查 time-based 触发条件是否满足。

    对应 TS evaluateTimeBasedTrigger()。

    条件：距离最后一条 assistant 消息的时间间隔 >= TIME_BASED_MC_GAP_MINUTES。
    此时 Anthropic 服务端 prompt cache（TTL 1h）必然已过期，
    下次请求会完整重写 prompt —— 提前清理旧结果可以缩小重写量。

    Returns:
        间隔分钟数（如果触发），None（如果未触发）。
    """
    # 从后往前找最后一条 assistant 消息的时间戳
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            ts = msg.get("_timestamp")
            if ts is None:
                return None
            gap_minutes = (_time.time() - ts) / 60.0
            if gap_minutes >= TIME_BASED_MC_GAP_MINUTES:
                return gap_minutes
            return None
    return None


def _time_based_micro_compact(messages: list[dict], gap_minutes: float) -> list[dict] | None:
    """Time-based micro-compact：清理所有旧的（非最近的）可清理工具结果。

    对应 TS maybeTimeBasedMicrocompact()。

    与 count-based 不同：
    - 不受数量阈值限制，直接清理除最近 N 个之外的所有白名单内结果
    - 使用固定占位符，不含字符数（与 TS 对齐）
    - 只在用户长时间闲置后触发，更安全
    """
    compactable_ids = _collect_compactable_tool_ids(messages)
    if not compactable_ids:
        return None

    # 保留最近 N 个，清理其余
    keep_recent = max(1, TIME_BASED_MC_KEEP_RECENT)
    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(tool_id for tool_id in compactable_ids if tool_id not in keep_set)

    if not clear_set:
        return None

    # 构建 tool_use_id → tool_name 映射（用于占位符中的工具名）
    id_to_name = _collect_tool_use_id_to_name(messages)

    # 遍历消息，清理命中 clear_set 的 tool_result
    tokens_saved = 0
    result: list[dict] = []
    any_touched = False

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        touched = False
        new_blocks: list[dict] = []
        for block in content:
            if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in clear_set
                    and block.get("content") != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                # 估算节省的 token 数（用于日志）
                original = block.get("content", "")
                tokens_saved += len(str(original)) // TOKEN_CHARS_RATIO
                tool_name = id_to_name.get(block.get("tool_use_id", ""), "unknown")
                new_block = {**block}
                new_block["content"] = (
                    f"{TIME_BASED_MC_CLEARED_MESSAGE} "
                    f"[tool={tool_name}]"
                )
                new_blocks.append(new_block)
                touched = True
            else:
                new_blocks.append(block)

        if touched:
            any_touched = True
            result.append({**msg, "content": new_blocks})
        else:
            result.append(msg)

    if not any_touched:
        return None

    logger.info(
        "Time-based micro-compact: gap=%.0fmin >= %dmin, "
        "cleared %d tool results (kept last %d), ~%d tokens saved",
        gap_minutes, TIME_BASED_MC_GAP_MINUTES,
        len(clear_set), keep_recent, tokens_saved,
    )
    return result


# --- Count-based micro-compact ---
# 对应 TS 中基于数量的截断路径（Python 简化版原有逻辑）

def _count_based_micro_compact(messages: list[dict]) -> list[dict]:
    """基于数量的轻量压缩：可清理工具结果超过阈值时截断最早的。

    策略：
    - 只清理 COMPACTABLE_TOOLS 白名单内的只读工具结果
    - 保留最近 MICROCOMPACT_MAX_TOOL_RESULTS 个结果
    - 更早的替换为 "[tool_result truncated: N chars, tool=<name>]"
    """
    id_to_name = _collect_tool_use_id_to_name(messages)

    # 收集所有【白名单内】tool_result 的位置
    compactable_positions: list[tuple[int, int, str]] = []  # (msg_idx, block_idx, tool_name)
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block_idx, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                tool_name = id_to_name.get(tool_use_id, "")
                if tool_name in COMPACTABLE_TOOLS:
                    compactable_positions.append((msg_idx, block_idx, tool_name))

    if len(compactable_positions) <= MICROCOMPACT_MAX_TOOL_RESULTS:
        return messages

    # 需要截断的位置（保留最近 N 个，截断更早的）
    truncate_count = len(compactable_positions) - MICROCOMPACT_MAX_TOOL_RESULTS
    positions_to_truncate = set(
        (p[0], p[1]) for p in compactable_positions[:truncate_count]
    )

    # 构建新消息列表
    result: list[dict] = []
    truncated_count = 0
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_blocks: list[dict] = []
        for block_idx, block in enumerate(content):
            if (msg_idx, block_idx) in positions_to_truncate:
                original_text = ""
                if isinstance(block, dict):
                    original_text = str(block.get("content", ""))
                truncated_len = len(original_text)
                tool_use_id = block.get("tool_use_id", "") if isinstance(block, dict) else ""
                tool_name = id_to_name.get(tool_use_id, "unknown")
                new_block = {**block}
                new_block["content"] = (
                    f"[tool_result truncated: {truncated_len} chars, "
                    f"tool={tool_name}]"
                )
                new_blocks.append(new_block)
                truncated_count += 1
            else:
                new_blocks.append(block)

        result.append({**msg, "content": new_blocks})

    logger.info(
        "Count-based micro-compact: truncated %d/%d compactable tool results "
        "(whitelist: %s)",
        truncated_count, len(compactable_positions),
        ", ".join(sorted(COMPACTABLE_TOOLS)),
    )
    return result


# --- 统一入口 ---

def micro_compact(messages: list[dict]) -> list[dict]:
    """轻量级压缩入口：按优先级尝试两条路径。

    对应 TS microcompactMessages() 的调度逻辑。

    优先级：
    1. Time-based（闲置 > 60min）→ 清理更激进但更安全
    2. Count-based（结果数 > MAX）→ 按数量阈值截断

    不调用 LLM，只做本地文本替换。
    """
    # Path 1: Time-based（优先）
    gap_minutes = _evaluate_time_based_trigger(messages)
    if gap_minutes is not None:
        time_result = _time_based_micro_compact(messages, gap_minutes)
        if time_result is not None:
            return time_result

    # Path 2: Count-based（兜底）
    return _count_based_micro_compact(messages)


# ---------------------------------------------------------------------------
# Full-compact：LLM 生成摘要
# 对应 TS: services/compact/compact.ts compactConversation()
# ---------------------------------------------------------------------------

def _messages_to_text(messages: list[dict]) -> str:
    """将消息列表转为纯文本，用于摘要生成。"""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        parts.append(
                            f"[Tool call: {block.get('name', '')}("
                            f"{block.get('input', {})})]"
                        )
                    elif btype == "tool_result":
                        parts.append(f"[Tool result: {block.get('content', '')}]")
                    else:
                        parts.append(str(block))
                else:
                    parts.append(str(block))
            content = "\n".join(parts)
        lines.append(f"[{role}]: {content}")
    return "\n\n".join(lines)


def _find_split_index(
        messages: list[dict],
        keep_recent_tokens: int,
) -> int:
    """找到分割点：从末尾往前，保留 keep_recent_tokens 的消息。

    返回分割索引，messages[:index] 是旧消息，messages[index:] 是保留的。
    """
    token_sum = 0
    for i in range(len(messages) - 1, -1, -1):
        token_sum += _count_content_tokens(messages[i].get("content", ""))
        if token_sum >= keep_recent_tokens:
            return i
    return 0


def _extract_summary(text: str) -> str:
    """从 LLM 回复中提取 <summary> 标签内容。"""
    start = text.find("<summary>")
    end = text.find("</summary>")
    if start != -1 and end != -1:
        return text[start + len("<summary>"):end].strip()
    # 没有 summary 标签，返回全文
    return text.strip()


async def full_compact(
        messages: list[dict],
        client: Any,
        model: str,
        context_window: int = CONTEXT_WINDOW_DEFAULT,
) -> list[dict]:
    """完整压缩：用 LLM 为旧消息生成摘要。

    对应 TS compact.ts compactConversation()。

    1. 计算保留最近消息的 token 预算（约 50% 上下文窗口）
    2. 旧消息转为文本 → 发送给 LLM 生成摘要
    3. 返回 [摘要消息] + [最近消息]
    """
    keep_recent_tokens = int(context_window * COMPACT_TARGET_RATIO)
    split_idx = _find_split_index(messages, keep_recent_tokens)

    if split_idx == 0:
        # 全部消息都在预算内，不需要压缩
        return messages

    old_messages = messages[:split_idx]
    recent_messages = messages[split_idx:]

    # 拼接旧消息为文本
    old_text = _messages_to_text(old_messages)
    if not old_text.strip():
        return messages

    # 调用 LLM 生成摘要（OpenAI-compatible 格式）
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"{_COMPACT_PROMPT}\n\n---\n\n{old_text}"}],
            max_tokens=4096,
            stream=False,
        )
        summary_text = response.choices[0].message.content or ""

        summary = _extract_summary(summary_text)
        logger.info(
            "Full-compact: summarized %d old messages into %d chars",
            len(old_messages), len(summary),
        )

    except Exception as e:
        # 压缩失败时，返回最近消息 + 错误提示
        logger.warning("Full-compact failed: %s", e)
        summary = f"[Context compression failed: {e}]"

    # 构造压缩后的消息列表
    compact_summary_msg = {
        "role": "user",
        "content": (
            "<compact-summary>\n"
            "Earlier conversation has been summarized:\n\n"
            f"{summary}\n"
            "</compact-summary>"
        ),
    }

    return [compact_summary_msg] + recent_messages


# ---------------------------------------------------------------------------
# 自动压缩入口
# 对应 TS: services/compact/autoCompact.ts autoCompactIfNeeded()
# ---------------------------------------------------------------------------

async def auto_compact_if_needed(
        messages: list[dict],
        system_prompt: str,
        client: Any,
        model: str,
        context_window: int = CONTEXT_WINDOW_DEFAULT,
        force: bool = False,
) -> list[dict]:
    """自动压缩入口。

    对应 TS autoCompact.ts autoCompactIfNeeded()。
    三层递进：
    1. 估算 token → 未超阈值 → 直接返回
    2. 超阈值 → micro_compact → 再检查
    3. 仍超阈值 → full_compact

    Args:
        force: 强制压缩（忽略阈值检查，用于 /compact 命令）
    """
    threshold = int(context_window * COMPACT_THRESHOLD_RATIO)

    tokens = estimate_tokens(messages, system_prompt)
    if not force and tokens < threshold:
        return messages

    logger.info(
        "Context approaching limit: ~%d/%d tokens (threshold: %d)",
        tokens, context_window, threshold,
    )

    # Step 1: micro-compact
    result = micro_compact(messages)
    tokens_after = estimate_tokens(result, system_prompt)
    if not force and tokens_after < threshold:
        logger.info("Micro-compact reduced tokens: %d → %d", tokens, tokens_after)
        return result

    # Step 2: full-compact
    logger.info("Running full-compact (micro: %d → %d, force=%s)", tokens, tokens_after, force)
    result = await full_compact(
        result, client, model, context_window,
    )
    tokens_final = estimate_tokens(result, system_prompt)
    logger.info("Full-compact: %d → %d tokens", tokens, tokens_final)

    return result
