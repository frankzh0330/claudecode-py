"""grep 工具测试。"""

import pytest

from cc_python.tools.grep_tool import GrepTool


@pytest.fixture
def tool():
    return GrepTool()


@pytest.fixture
def sample_dir(tmp_path):
    """创建示例文件。"""
    (tmp_path / "a.py").write_text("def hello():\n    print('hello')\n\ndef world():\n    print('world')\n")
    (tmp_path / "b.py").write_text("import os\n\nprint('no match here')\n")
    (tmp_path / "c.txt").write_text("hello from text file\n")
    return tmp_path


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_basic(self, tool, sample_dir):
        result = await tool.call(pattern="def \\w+", path=str(sample_dir))
        assert "hello" in result
        assert "world" in result

    @pytest.mark.asyncio
    async def test_no_match(self, tool, sample_dir):
        result = await tool.call(pattern="nonexistent_pattern_xyz", path=str(sample_dir))
        assert "未找到" in result or "No matches" in result or "0 matches" in result or "无匹配" in result

    @pytest.mark.asyncio
    async def test_with_path(self, tool, sample_dir):
        result = await tool.call(pattern="hello", path=str(sample_dir / "c.txt"))
        assert "hello from text file" in result

    def test_is_safe(self, tool):
        assert tool.is_concurrency_safe is True

    def test_name(self, tool):
        assert tool.name == "grep"
