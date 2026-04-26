# Harness Engineering Design

This document defines the evaluation and data-generation harness plan for TermPilot.
The goal is to turn TermPilot from an interactive terminal coding assistant into a
repeatable agent engineering system: tasks can be run in isolated workspaces,
verified automatically, recorded as trajectories, and compared across models or
agent changes.

## Why Harness Engineering Matters

Interactive demos answer whether TermPilot can help in one conversation. A harness
answers a stronger question: can TermPilot complete a known class of tasks
reliably, repeatedly, and measurably?

Planned harness loop:

```text
task dataset
  -> isolated workspace
  -> termpilot one-shot run
  -> session and tool trace capture
  -> verifier command or checker
  -> result metrics and failure artifacts
  -> prompt/tool/runtime improvements
```

In this framing, the language model is not the harness. The model is the policy
being driven and evaluated. The harness is the runner, environment, recorder,
verifier, and reporting layer around that policy.

## Relationship To Existing Agent Benchmarks

TermPilot will borrow the useful parts of OpenHands, SWE-bench, and
TerminalBench without copying their full complexity up front.

- OpenHands is a reference for agent runtime design: terminal, file tools,
  browser/tool integrations, workspace state, task flow, and human-in-the-loop
  ergonomics.
- SWE-bench is a reference for real repository repair: start from an issue and a
  base commit, let the agent patch code, then run tests to score the result.
- TerminalBench is the closest first target for TermPilot: give the agent a
  terminal task in a sandbox, run commands and file edits, then verify final
  state with tests or shell checks.

The first TermPilot harness will therefore be TerminalBench-style: small,
local, command-verifiable tasks that exercise shell use, file edits, debugging,
and test-driven repair.

## Current Prototype

The `feature/harness-engineering` branch already contains a first lightweight
prototype under `evals/`:

```text
evals/
├── run_eval.py
├── tasks.jsonl
└── templates/
```

The prototype already demonstrates the right core shape:

- loads tasks from `evals/tasks.jsonl`
- copies a task template into a temporary workspace
- creates an isolated temporary TermPilot config directory
- runs `python -m termpilot -p <prompt>`
- applies bypass permissions through generated test settings
- runs a verifier command
- records pass/fail, duration, token count, tool count, logs, diffs, and changed files
- optionally preserves failed workspaces for inspection

This prototype will be treated as the seed of the harness architecture, not as throwaway
test code.

## Target Architecture

The harness will remain outside the main runtime loop, while reusing stable
TermPilot entry points.

```text
evals/tasks/*.jsonl
        |
        v
scripts or evals runner
        |
        v
workspace manager  -> copies templates / creates temp dirs / later Docker
        |
        v
TermPilot CLI      -> python -m termpilot -p "..."
        |
        v
session store      -> ~/.termpilot/projects/... or isolated config dir
        |
        v
verifier           -> command / file check / Python checker
        |
        v
results            -> JSONL metrics + logs + diffs + trajectory artifacts
```

This keeps the production agent simple. The harness drives TermPilot as a user
would, but with controlled inputs, controlled workspaces, and machine-readable
outputs.

## Task Schema

The task schema will stay small at first and grow only when the runner needs
more structure.

Minimum fields:

```json
{
  "id": "fix-python-test",
  "prompt": "Fix the failing test in this project. Run pytest to verify.",
  "workspace": "templates/fix-python-test",
  "verifier": "python -m pytest -q",
  "timeout": 120
}
```

Planned fields:

- `id`: stable task identifier used in logs and reports.
- `prompt`: user prompt passed to TermPilot.
- `workspace`: fixture/template directory copied into a temporary workspace.
- `verifier`: command run after TermPilot exits.
- `timeout`: maximum wall-clock time for the agent run.
- `max_turns`: planned control for agent loop length once exposed by CLI/runtime.
- `tags`: optional labels such as `smoke`, `file-edit`, `pytest`, `terminal`.
- `verifier_type`: planned selector for `command`, `file_contains`, or `python`.
- `expected_files`: optional explicit file-level checks.

Task rows will remain human-readable. If a task needs complex logic, move that
logic into a verifier script and reference it from the row.

## Result Schema

Result rows will remain stable enough for dashboards and regressions.

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

Future additions:

- `provider`
- `permission_mode`
- `workspace_kept`
- `session_file`
- `trajectory_file`
- `failure_category`
- `cost_usd`
- `api_calls`
- per-tool call counts and failures

## Evaluation Levels

Benchmark coverage will be built in layers. Each level will stay small and
reliable before expanding.

### Level 0: Smoke

Purpose: prove the harness, CLI, permissions, and verifier loop work.

Examples:

- create a file with exact content
- read a file and write a computed answer
- run a simple command and capture output
- make a one-line Python script executable

### Level 1: File Operations

Purpose: test read/search/edit behavior without large project complexity.

Examples:

- replace a config value
- update JSON/YAML safely
- edit a Markdown table
- modify one function while preserving surrounding code

### Level 2: Coding Unit Tasks

Purpose: test small code repair with real tests.

Examples:

- fix one failing Python unit test
- repair an import error across two files
- add a parameter while keeping old behavior
- update a test and implementation together

### Level 3: Terminal Tasks

Purpose: test shell fluency and environment reasoning.

Examples:

- inspect logs and write a summary result file
- debug a failing command
- create a small CLI and verify it with subprocess
- run a pipeline over text files

### Level 4: Repository Tasks

Purpose: approach SWE-bench-like repair without the full SWE-bench integration.

Examples:

- checkout or copy a small real repository fixture
- provide an issue-style prompt
- let TermPilot patch the repo
- run the project test suite
- save the final diff

## Verifier Design

Verifier logic will stay deterministic and external to the model. The agent can
say a task is done, but the harness decides whether it is actually done.

Initial verifier type:

- `command`: run a shell command in the final workspace and use exit code as
  pass/fail.

Planned verifier types:

- `file_contains`: check a file contains or exactly equals expected content.
- `file_absent`: check that unwanted files were not created.
- `python`: run a Python verifier script that emits structured JSON.
- `composite`: combine multiple checks into one score.

All verifiers will produce the same normalized output:

```json
{
  "passed": true,
  "score": 1.0,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

This keeps the door open for partial credit and RL-style reward functions later.

## Runtime Requirements

The harness needs a few runtime affordances from TermPilot to become robust:

- non-interactive permission override for evals
- isolated config home per task
- optional working-directory override
- machine-readable run summary
- stable session/trajectory export
- timeout handling that marks a task as failed without corrupting later runs

The current prototype handles some of this externally by writing temporary
settings and running in a temporary working directory. That is a good start.
Long term, the CLI will expose explicit eval-friendly controls such as:

```bash
python -m termpilot \
  -p "Fix the failing test. Run pytest." \
  --model gpt-4o \
  --permission-mode bypassPermissions \
  --cwd /tmp/termpilot-eval/task-001 \
  --json-summary
```

If CLI flags are too invasive initially, environment variables are a useful
intermediate step:

```bash
TERMPILOT_CONFIG_DIR=/tmp/termpilot-eval/config
TERMPILOT_PERMISSION_MODE=bypassPermissions
```

## Trajectory Capture

Session JSONL is already useful for recovery, but eval and training need a more
portable trajectory format. A conversion layer will be added rather than
changing session storage directly.

Planned module:

```text
src/termpilot/trajectory.py
```

Responsibilities:

- read a session JSONL file
- reconstruct user, assistant, tool-use, and tool-result turns
- attach task metadata and verifier outcome
- emit one JSON object per task

Target shape:

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

Trajectory files will enable:

- failure replay
- prompt/tool description analysis
- SFT data generation
- model comparison
- regression debugging

## Reporting And Failure Analysis

The harness will generate a compact human report after every run.

Planned report shape:

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

The report will eventually include:

- pass rate by tag
- pass rate by model
- slowest tasks
- tasks with no file diff
- tasks with no verifier run observed in logs
- most common tool errors
- changed-files summary

This closes the loop: benchmark results will point directly to changes in
system prompts, tool descriptions, permissions, context compaction, and tool
result formatting.

## Model And Configuration Matrix

Once the first task set is stable, the runner will support model/config
matrices:

```bash
python evals/run_eval.py \
  --model gpt-4o \
  --filter coding
```

Planned extension:

```bash
python evals/run_eval.py \
  --models gpt-4o,claude-sonnet-4-20250514,glm-5.1 \
  --permission-mode bypassPermissions \
  --tags smoke,coding
```

Useful comparison dimensions:

- model/provider
- permission mode
- tool availability
- prompt version
- compact enabled/disabled
- sub-agent enabled/disabled

The goal is not only to find the best model, but to identify which agent design
choices improve reliability.

## Integration Roadmap

### Phase 1: Stabilize The Local Harness

- Keep `evals/run_eval.py` as the first runner.
- Expand `evals/tasks.jsonl` to 10-20 small tasks.
- Ensure failed workspaces, logs, and diffs are easy to inspect.
- Make the result schema stable.
- Add a short report file per run.

### Phase 2: Add Eval-Friendly CLI Controls

- Add explicit permission-mode override.
- Add working-directory override if needed.
- Add JSON summary output.
- Add optional max-turn or max-tool-loop controls.
- Avoid depending on terminal-rendered text for metrics.

### Phase 3: Add Trajectory Export

- Implement `src/termpilot/trajectory.py`.
- Attach session file path to eval result rows.
- Emit `trajectories.jsonl` beside `results.jsonl`.
- Include verifier outcome in each trajectory.

### Phase 4: Improve Isolation

- Continue using temporary local directories for smoke tasks.
- Add optional Docker runner for tasks that need clean dependencies.
- Keep API credentials outside task workspaces.
- Preserve failed workspaces and delete passing ones by default.

### Phase 5: TerminalBench-Style Adapters

- Add loaders for external task datasets.
- Map external task fields into the local task schema.
- Reuse the same runner, workspace manager, verifier, and result schema.
- Start with a small subset before full benchmark runs.

### Phase 6: Mini SWE Then SWE-bench

- Build a Mini SWE task set with local repository fixtures.
- Record final git diffs as first-class artifacts.
- Add repo checkout/reset helpers.
- Later, a SWE-bench adapter will handle base commit, issue prompt, test patch,
  final patch capture, and official verifier invocation.

## Design Principles

- Keep the harness deterministic where the agent is not.
- Treat the verifier as the source of truth.
- Store enough artifacts to debug every failure.
- Prefer small stable task sets over large noisy ones early.
- Do not let eval-only concerns pollute the interactive CLI experience.
- Add CLI/runtime affordances only when the harness has a concrete need.
- Make every benchmark result actionable for prompt, tool, or runtime changes.

