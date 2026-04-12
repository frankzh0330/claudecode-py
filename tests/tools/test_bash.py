"""bash 工具测试。"""

import pytest

from cc_python.tools.bash import BashTool


@pytest.fixture
def tool():
    return BashTool()


class TestBashTool:
    @pytest.mark.asyncio
    async def test_simple(self, tool):
        result = await tool.call(command="echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_with_output(self, tool):
        result = await tool.call(command="echo 'test output'")
        assert "test output" in result

    @pytest.mark.asyncio
    async def test_with_error(self, tool):
        result = await tool.call(command="echo error >&2")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_exit_code(self, tool):
        result = await tool.call(command="exit 1")
        assert "exit code" in result.lower() or "1" in result

    @pytest.mark.asyncio
    async def test_timeout(self, tool):
        result = await tool.call(command="sleep 10", timeout=100)
        assert "超时" in result or "timeout" in result.lower() or "timed out" in result.lower()

    def test_is_unsafe(self, tool):
        assert tool.is_concurrency_safe is False

    def test_name(self, tool):
        assert tool.name == "bash"
