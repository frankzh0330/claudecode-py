# Harness Engineering 设计

本文档定义 TermPilot 的评测与数据生成 harness 规划。目标是把 TermPilot
从一个交互式终端编程助手，推进成一个可重复运行、可自动验证、可记录轨迹、
可跨模型比较的 agent 工程系统。

## 为什么需要 Harness Engineering

交互式 demo 只能说明 TermPilot 在一次对话里能不能帮上忙。Harness 要回答
更强的问题：TermPilot 能不能稳定、重复、可衡量地完成一类已知任务？

计划让 harness 提供完整的工程闭环：

```text
任务数据集
  -> 隔离 workspace
  -> TermPilot 单次运行
  -> session 与工具轨迹采集
  -> verifier 命令或检查器
  -> 结果指标与失败产物
  -> 反哺 prompt / tool / runtime 优化
```

在这个语境里，大模型本身不是 harness。模型是被驱动和评测的 policy。
Harness 是围绕这个 policy 的 runner、环境、记录器、verifier 和报告层。

## 与现有 Agent Benchmark 的关系

TermPilot 会借鉴 OpenHands、SWE-bench 和 TerminalBench 中有价值的部分，
但不会一开始就照搬它们的完整复杂度。

- OpenHands 是 agent runtime 的参考：终端、文件工具、浏览器/工具集成、
  workspace 状态、任务流和人机协作体验。
- SWE-bench 是真实仓库修复的参考：从 issue 和 base commit 开始，让 agent
  修改代码，然后跑测试评分。
- TerminalBench 是 TermPilot 第一阶段最接近的目标：给 agent 一个终端任务和
  sandbox，让它执行命令、修改文件，再用测试或 shell 检查最终状态。

因此，TermPilot 的第一版 harness 会采用 TerminalBench-style：小型、本地、
可用命令验证的任务，重点覆盖 shell 使用、文件编辑、debug 和测试驱动修复。

## 当前原型

`feature/harness-engineering` 分支已经在 `evals/` 下包含一个轻量原型：

```text
evals/
├── run_eval.py
├── tasks.jsonl
└── templates/
```

这个原型已经体现了正确的核心形态：

- 从 `evals/tasks.jsonl` 加载任务
- 将任务模板复制到临时 workspace
- 为 TermPilot 创建隔离的临时配置目录
- 运行 `python -m termpilot -p <prompt>`
- 通过生成的测试 settings 启用 bypass 权限
- 运行 verifier 命令
- 记录 pass/fail、耗时、token 数、工具调用数、日志、diff 和变更文件
- 可选择保留失败任务的 workspace 供排查

这部分会被视为 harness 架构的种子，而不是一次性的测试脚本。

## 目标架构

计划让 harness 保持在主 runtime loop 之外，同时复用稳定的 TermPilot 入口。

```text
evals/tasks/*.jsonl
        |
        v
scripts 或 evals runner
        |
        v
workspace manager  -> 复制模板 / 创建临时目录 / 计划支持 Docker
        |
        v
TermPilot CLI      -> python -m termpilot -p "..."
        |
        v
session store      -> ~/.termpilot/projects/... 或隔离配置目录
        |
        v
verifier           -> command / file check / Python checker
        |
        v
results            -> JSONL 指标 + 日志 + diff + trajectory 产物
```

这种设计能保持生产 agent 简单。Harness 像用户一样驱动 TermPilot，但输入、
workspace 和输出都是受控且可机器读取的。

## 任务 Schema

任务 schema 第一阶段会保持简洁，只在 runner 真的需要时再扩展。

最小字段：

```json
{
  "id": "fix-python-test",
  "prompt": "Fix the failing test in this project. Run pytest to verify.",
  "workspace": "templates/fix-python-test",
  "verifier": "python -m pytest -q",
  "timeout": 120
}
```

计划支持的字段：

- `id`：稳定的任务标识，用于日志和报告。
- `prompt`：传给 TermPilot 的用户提示词。
- `workspace`：复制到临时 workspace 的 fixture/template 目录。
- `verifier`：TermPilot 退出后运行的验证命令。
- `timeout`：agent 单次运行的最大耗时。
- `max_turns`：计划在 CLI/runtime 暴露后，用于控制 agent loop 长度。
- `tags`：可选标签，例如 `smoke`、`file-edit`、`pytest`、`terminal`。
- `verifier_type`：计划用于选择 `command`、`file_contains` 或 `python`。
- `expected_files`：可选的显式文件级检查。

任务行会保持可读。如果任务需要复杂逻辑，把逻辑放进 verifier 脚本，然后在任务行里引用它。

## 结果 Schema

结果行会保持足够稳定，方便做 dashboard 和回归分析。

```json
{
  "id": "fix-python-test",
  "status": "pass",
  "duration_s": 84.2,
  "tool_calls": 7,
  "tokens": 15240,
  "model": "gpt-4o",
  "verifier_exit": 0,
  "verifier_output": "3 passed",
  "changed_files": ["calc.py", "test_calc.py"],
  "log": "evals/runs/20260426T000000Z/fix-python-test.log",
  "diff": "evals/runs/20260426T000000Z/fix-python-test.diff",
  "timestamp": "2026-04-26T00:00:00Z"
}
```

后续计划增加：

- `provider`
- `permission_mode`
- `workspace_kept`
- `session_file`
- `trajectory_file`
- `failure_category`
- `cost_usd`
- `api_calls`
- 每个工具的调用次数和失败次数

## 评测等级

TermPilot 的 benchmark 覆盖会分层建设。每一层都会先保持小而稳定，再逐步扩展。

### Level 0: Smoke

目的：证明 harness、CLI、权限和 verifier 链路能正常工作。

示例：

- 创建一个包含精确内容的文件
- 读取文件并写入计算结果
- 运行简单命令并捕获输出
- 创建一个单行 Python 脚本并让它可执行

### Level 1: File Operations

目的：在没有大型项目复杂度的情况下，测试 read/search/edit 行为。

示例：

- 替换配置值
- 安全更新 JSON/YAML
- 编辑 Markdown 表格
- 修改一个函数，同时保持周围代码不被破坏

### Level 2: Coding Unit Tasks

目的：用真实测试验证小型代码修复能力。

示例：

- 修复一个失败的 Python 单元测试
- 修复两个文件之间的 import 错误
- 增加一个参数，同时保持旧行为
- 同时更新测试和实现

### Level 3: Terminal Tasks

目的：测试 shell 熟练度和环境推理能力。

示例：

- 检查日志并写入总结结果文件
- debug 一个失败命令
- 创建小型 CLI 并用 subprocess 验证
- 对文本文件运行 pipeline

### Level 4: Repository Tasks

目的：在不接入完整 SWE-bench 的前提下，接近 SWE-bench-like 修复流程。

示例：

- checkout 或复制一个小型真实仓库 fixture
- 提供 issue 风格的 prompt
- 让 TermPilot patch 仓库
- 运行项目测试套件
- 保存最终 diff

## Verifier 设计

计划让 verifier 逻辑保持确定性，并且位于模型之外。Agent 可以声称任务完成，
但真正判定任务是否完成的是 harness。

初始 verifier 类型：

- `command`：在最终 workspace 中运行 shell 命令，用退出码判断 pass/fail。

计划增加的 verifier 类型：

- `file_contains`：检查文件包含或精确等于预期内容。
- `file_absent`：检查不希望出现的文件没有被创建。
- `python`：运行一个输出结构化 JSON 的 Python verifier 脚本。
- `composite`：组合多个检查并给出分数。

所有 verifier 会输出统一的规范化结果：

```json
{
  "passed": true,
  "score": 1.0,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

这样后续会自然支持部分得分和 RL-style reward function。

## Runtime 需求

为了让 harness 变得稳健，TermPilot runtime 需要提供一些面向评测的能力：

- 非交互式权限覆盖
- 每个任务独立的配置目录
- 可选的工作目录覆盖
- 机器可读的运行摘要
- 稳定的 session/trajectory 导出
- timeout 处理：任务失败时不影响后续任务

当前原型已经通过写入临时 settings 和在临时工作目录运行，外部实现了其中一部分。
这是很好的起点。长期来看，计划让 CLI 提供显式的 eval-friendly 控制项，例如：

```bash
python -m termpilot \
  -p "Fix the failing test. Run pytest." \
  --model gpt-4o \
  --permission-mode bypassPermissions \
  --cwd /tmp/termpilot-eval/task-001 \
  --json-summary
```

如果一开始不想加入太多 CLI 参数，环境变量是一个合适的过渡：

```bash
TERMPILOT_CONFIG_DIR=/tmp/termpilot-eval/config
TERMPILOT_PERMISSION_MODE=bypassPermissions
```

## Trajectory 采集

Session JSONL 已经适合恢复会话，但 eval 和训练需要更可移植的 trajectory 格式。
计划会增加一个转换层，而不是直接改变 session 存储。

计划模块：

```text
src/termpilot/trajectory.py
```

职责：

- 读取 session JSONL 文件
- 重建 user、assistant、tool-use 和 tool-result turns
- 附加任务 metadata 与 verifier 结果
- 每个任务输出一个 JSON 对象

目标结构：

```json
{
  "task_id": "fix-python-test",
  "conversations": [
    {"from": "human", "value": "..."},
    {"from": "assistant", "value": "...", "tool_calls": []},
    {"from": "tool", "name": "bash", "value": "..."}
  ],
  "metadata": {
    "model": "gpt-4o",
    "session_id": "...",
    "duration_s": 84.2,
    "tool_count": 7
  },
  "verifier": {
    "command": "python -m pytest -q",
    "passed": true,
    "exit_code": 0
  }
}
```

Trajectory 文件会支持：

- 失败回放
- prompt/tool description 分析
- SFT 数据生成
- 模型对比
- 回归调试

## 报告与失败分析

Harness 每次运行后会生成一份简洁的人类可读报告。

计划报告形态：

```text
Pass rate: 4/5
Total time: 312.4s
Total tool calls: 28

Failures:
- create-cli: verifier failed, hello.py missing
- refactor-function: timeout during pytest

Common signals:
- 2 tasks did not run verifier before final response
- 1 task edited tests but not implementation
```

报告后续会包含：

- 按 tag 统计通过率
- 按 model 统计通过率
- 最慢任务
- 没有文件 diff 的任务
- 日志中未观察到 verifier 运行的任务
- 最常见工具错误
- 变更文件摘要

这会形成闭环：benchmark 结果会直接指向 system prompt、tool description、
permissions、context compaction 和 tool result formatting 的改进。

## 模型与配置矩阵

当第一批任务稳定后，计划让 runner 支持模型和配置矩阵：

```bash
python evals/run_eval.py \
  --model gpt-4o \
  --filter coding
```

计划扩展：

```bash
python evals/run_eval.py \
  --models gpt-4o,claude-sonnet-4-20250514,glm-5.1 \
  --permission-mode bypassPermissions \
  --tags smoke,coding
```

有价值的对比维度：

- model/provider
- permission mode
- 工具可用性
- prompt 版本
- compact 开启/关闭
- sub-agent 开启/关闭

目标不只是找出最强模型，而是识别哪些 agent 设计选择能提升可靠性。

## 集成路线图

### Phase 1: 稳定本地 Harness

- 保留 `evals/run_eval.py` 作为第一版 runner。
- 将 `evals/tasks.jsonl` 扩展到 10-20 个小任务。
- 确保失败 workspace、日志和 diff 易于检查。
- 固定结果 schema。
- 每次运行生成简短 report。

### Phase 2: 增加 Eval-Friendly CLI 控制

- 增加显式 permission-mode 覆盖。
- 按需增加 working-directory 覆盖。
- 增加 JSON summary 输出。
- 增加可选 max-turn 或 max-tool-loop 控制。
- 不再依赖终端渲染文本提取指标。

### Phase 3: 增加 Trajectory 导出

- 实现 `src/termpilot/trajectory.py`。
- 在 eval result row 中附加 session file path。
- 在 `results.jsonl` 旁边输出 `trajectories.jsonl`。
- 将 verifier 结果写入每条 trajectory。

### Phase 4: 改进隔离

- smoke 任务继续使用临时本地目录。
- 为需要干净依赖的任务增加可选 Docker runner。
- API credentials 不进入任务 workspace。
- 默认保留失败 workspace，删除成功 workspace。

### Phase 5: TerminalBench-Style Adapter

- 增加外部任务数据集 loader。
- 将外部任务字段映射成本地 task schema。
- 复用同一个 runner、workspace manager、verifier 和 result schema。
- 先运行小子集，再考虑完整 benchmark。

### Phase 6: Mini SWE 再到 SWE-bench

- 构建带本地仓库 fixture 的 Mini SWE task set。
- 将最终 git diff 作为一等产物记录。
- 增加 repo checkout/reset helper。
- 后续会加入 SWE-bench adapter，处理 base commit、issue prompt、test patch、
  final patch capture 和官方 verifier 调用。

## 设计原则

- Harness 中能确定的部分尽量确定，只有 agent 行为本身是不确定的。
- Verifier 是事实来源。
- 保存足够产物，让每个失败都能被复盘。
- 早期优先维护小而稳定的任务集，而不是追求大而嘈杂的 benchmark。
- 不让 eval-only 需求污染交互式 CLI 体验。
- 只有当 harness 有具体需求时，才给 CLI/runtime 增加新能力。
- 每个 benchmark 结果都会尽量转化为 prompt、tool 或 runtime 的改进线索。

