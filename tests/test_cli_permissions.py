"""CLI permission prompt mapping."""

from termpilot.cli import _permission_result_from_choice
from termpilot.permissions import PermissionBehavior


def test_permission_choice_always_allow_adds_allow_rule():
    result = _permission_result_from_choice("bash", "always_allow")

    assert result.behavior == PermissionBehavior.ALLOW
    assert result.rule_updates == [{
        "tool_name": "bash",
        "pattern": "*",
        "behavior": "allow",
    }]


def test_permission_choice_label_always_allow_adds_allow_rule():
    result = _permission_result_from_choice("bash", "Always allow  (始终允许同类操作)")

    assert result.behavior == PermissionBehavior.ALLOW
    assert result.rule_updates[0]["behavior"] == "allow"


def test_permission_choice_cancel_does_not_persist_deny_rule():
    result = _permission_result_from_choice("bash", None)

    assert result.behavior == PermissionBehavior.DENY
    assert result.rule_updates is None
