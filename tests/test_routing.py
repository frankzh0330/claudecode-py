"""Intent routing and delegation planning."""

from termpilot.routing import build_routing_plan, build_routing_reminder


def test_multi_file_inspection_builds_batch_delegation_plan():
    plan = build_routing_plan("分别检查 cli.py、api.py、context.py 的主要功能")

    assert plan.kind == "delegate_batch"
    assert len(plan.tasks) == 3
    assert [task.target for task in plan.tasks] == ["cli.py", "api.py", "context.py"]
    assert all(task.subagent_type == "Explore" for task in plan.tasks)


def test_english_multi_file_inspection_builds_batch_delegation_plan():
    plan = build_routing_plan("Inspect cli.py, api.py, and context.py separately")

    assert plan.kind == "delegate_batch"
    assert [task.target for task in plan.tasks] == ["cli.py", "api.py", "context.py"]


def test_multi_file_inspection_gets_delegation_reminder():
    reminder = build_routing_reminder("分别检查 cli.py、api.py、context.py 的主要功能")

    assert reminder is not None
    assert "Agent tool with the tasks array" in reminder
    assert "Inspect cli.py" in reminder


def test_single_file_inspection_does_not_get_delegation_plan():
    plan = build_routing_plan("检查 cli.py 的主要功能")

    assert plan.kind == "none"
    assert plan.to_system_reminder() is None


def test_multi_file_without_separate_intent_does_not_get_delegation_plan():
    assert build_routing_plan("cli.py api.py context.py").kind == "none"


def test_plain_file_list_does_not_get_delegation_plan():
    assert build_routing_plan("cli.py, api.py, context.py").kind == "none"
