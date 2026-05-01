"""queue.py 单元测试。"""
import asyncio
import pytest
from termpilot.queue import (
    MessageQueue, QueuedCommand, Priority,
    get_main_queue, reset_main_queue,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_main_queue()
    yield
    reset_main_queue()


def _cmd(mode="prompt", value="hi", priority=Priority.NEXT, **kw):
    return QueuedCommand(mode=mode, value=value, priority=priority, **kw)


class TestEnqueueDequeue:
    def test_enqueue_dequeue_nowait(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="a"))
        cmd = q.dequeue_nowait()
        assert cmd is not None
        assert cmd.value == "a"
        assert q.is_empty()

    def test_dequeue_empty_returns_none(self):
        q = MessageQueue()
        assert q.dequeue_nowait() is None

    def test_priority_ordering(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="later", priority=Priority.LATER))
        q.enqueue(_cmd(value="now", priority=Priority.NOW))
        q.enqueue(_cmd(value="next", priority=Priority.NEXT))

        assert q.dequeue_nowait().value == "now"
        assert q.dequeue_nowait().value == "next"
        assert q.dequeue_nowait().value == "later"

    def test_fifo_same_priority(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="first"))
        q.enqueue(_cmd(value="second"))
        q.enqueue(_cmd(value="third"))

        assert q.dequeue_nowait().value == "first"
        assert q.dequeue_nowait().value == "second"
        assert q.dequeue_nowait().value == "third"

    def test_qsize(self):
        q = MessageQueue()
        assert q.qsize() == 0
        q.enqueue(_cmd())
        q.enqueue(_cmd())
        assert q.qsize() == 2

    def test_peek_does_not_remove(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="peek-me"))
        assert q.peek().value == "peek-me"
        assert q.qsize() == 1
        assert q.dequeue_nowait().value == "peek-me"

    def test_filtered_dequeue_preserves_non_matching_commands(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="main-1"))
        q.enqueue(_cmd(value="agent-1", agent_id="agent-123"))
        q.enqueue(_cmd(value="main-2"))

        cmd = q.dequeue_nowait(filter_fn=lambda c: c.agent_id == "agent-123")

        assert cmd is not None
        assert cmd.value == "agent-1"
        assert q.dequeue_nowait().value == "main-1"
        assert q.dequeue_nowait().value == "main-2"

    def test_filtered_dequeue_keeps_priority_and_fifo(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="agent-later", priority=Priority.LATER, agent_id="agent-123"))
        q.enqueue(_cmd(value="main-now", priority=Priority.NOW))
        q.enqueue(_cmd(value="agent-next", priority=Priority.NEXT, agent_id="agent-123"))

        cmd = q.dequeue_nowait(filter_fn=lambda c: c.agent_id == "agent-123")

        assert cmd is not None
        assert cmd.value == "agent-next"
        assert q.dequeue_nowait().value == "main-now"
        assert q.dequeue_nowait().value == "agent-later"

    def test_filtered_peek_does_not_remove_or_reorder(self):
        q = MessageQueue()
        q.enqueue(_cmd(value="main-1"))
        q.enqueue(_cmd(value="agent-1", agent_id="agent-123"))
        q.enqueue(_cmd(value="agent-2", agent_id="agent-123"))

        assert q.peek(filter_fn=lambda c: c.agent_id == "agent-123").value == "agent-1"
        assert q.qsize() == 3
        assert q.dequeue_nowait(filter_fn=lambda c: c.agent_id == "agent-123").value == "agent-1"
        assert q.dequeue_nowait(filter_fn=lambda c: c.agent_id == "agent-123").value == "agent-2"


@pytest.mark.asyncio
async def test_async_dequeue():
    q = MessageQueue()
    q.enqueue(_cmd(value="async-test"))
    cmd = await q.dequeue(timeout=1.0)
    assert cmd is not None
    assert cmd.value == "async-test"


@pytest.mark.asyncio
async def test_async_dequeue_timeout():
    q = MessageQueue()
    cmd = await q.dequeue(timeout=0.05)
    assert cmd is None


@pytest.mark.asyncio
async def test_async_filtered_dequeue_timeout_keeps_non_matching_command():
    q = MessageQueue()
    q.enqueue(_cmd(value="main-only"))

    cmd = await q.dequeue(timeout=0.05, filter_fn=lambda c: c.agent_id == "agent-123")

    assert cmd is None
    assert q.qsize() == 1
    assert q.dequeue_nowait().value == "main-only"


class TestBatching:
    def test_can_batch_same_mode_origin(self):
        q = MessageQueue()
        head = _cmd(mode="prompt", value="a", origin="user")
        next_cmd = _cmd(mode="prompt", value="b", origin="user")
        assert q.can_batch_with(head, next_cmd) is True

    def test_cannot_batch_different_mode(self):
        q = MessageQueue()
        head = _cmd(mode="prompt", value="a")
        next_cmd = _cmd(mode="task_notification", value="b")
        assert q.can_batch_with(head, next_cmd) is False

    def test_cannot_batch_different_origin(self):
        q = MessageQueue()
        head = _cmd(mode="prompt", value="a", origin="user")
        next_cmd = _cmd(mode="prompt", value="b", origin="task-watcher")
        assert q.can_batch_with(head, next_cmd) is False

    def test_cannot_batch_none(self):
        q = MessageQueue()
        assert q.can_batch_with(_cmd(), None) is False


class TestGlobalQueue:
    def test_get_main_queue_singleton(self):
        q1 = get_main_queue()
        q2 = get_main_queue()
        assert q1 is q2

    def test_reset_creates_new(self):
        q1 = get_main_queue()
        reset_main_queue()
        q2 = get_main_queue()
        assert q1 is not q2


class TestDiscard:
    def test_discard_removes_matching_commands_only(self):
        q = MessageQueue()
        q.enqueue(_cmd(mode="prompt", value="drop", origin="user"))
        q.enqueue(_cmd(mode="slash_command", value="/keep", origin="user"))
        q.enqueue(_cmd(mode="prompt", value="keep-agent", origin="agent"))

        removed = q.discard(lambda cmd: cmd.mode == "prompt" and cmd.origin == "user")

        assert removed == 1
        assert q.dequeue_nowait().value == "/keep"
        assert q.dequeue_nowait().value == "keep-agent"
