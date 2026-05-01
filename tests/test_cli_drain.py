from termpilot.cli import (
    _assistant_appears_to_wait_for_user,
    _should_defer_slash_for_user_reply,
)
from termpilot.queue import QueuedCommand, Priority


def _slash(name: str, queued_during_active_turn: bool = True) -> QueuedCommand:
    return QueuedCommand(
        mode="slash_command",
        value={
            "name": name,
            "args": "",
            "queued_during_active_turn": queued_during_active_turn,
        },
        priority=Priority.NEXT,
        origin="user",
    )


def test_assistant_wait_detection_handles_confirmation_questions():
    assert _assistant_appears_to_wait_for_user("确认删除 hello.py 文件吗？")
    assert _assistant_appears_to_wait_for_user("Which file should I edit?")
    assert not _assistant_appears_to_wait_for_user("Deleted hello.py successfully.")


def test_defer_state_changing_slash_queued_during_active_turn():
    assert _should_defer_slash_for_user_reply(_slash("clear"), awaiting_user_reply=True)
    assert _should_defer_slash_for_user_reply(_slash("compact"), awaiting_user_reply=True)
    assert not _should_defer_slash_for_user_reply(_slash("help"), awaiting_user_reply=True)
    assert not _should_defer_slash_for_user_reply(_slash("clear"), awaiting_user_reply=False)
    assert not _should_defer_slash_for_user_reply(
        _slash("clear", queued_during_active_turn=False),
        awaiting_user_reply=True,
    )
