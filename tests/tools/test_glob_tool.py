"""glob 工具测试。"""

import pytest

from cc_python.tools.glob_tool import GlobTool


@pytest.fixture
def tool():
    return GlobTool()


@pytest.fixture
def sample_dir(tmp_path):
    """创建示例目录结构。"""
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.py").write_text("d")
    return tmp_path


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_basic(self, tool, sample_dir):
        result = await tool.call(pattern="**/*.py", path=str(sample_dir))
        assert "a.py" in result
        assert "b.py" in result
        assert "d.py" in result

    @pytest.mark.asyncio
    async def test_no_match(self, tool, sample_dir):
        result = await tool.call(pattern="**/*.rs", path=str(sample_dir))
        assert "未找到" in result or "No files" in result or "0 files" in result or "无匹配" in result

    @pytest.mark.asyncio
    async def test_with_path(self, tool, sample_dir):
        result = await tool.call(pattern="*.txt", path=str(sample_dir))
        assert "c.txt" in result
        assert ".py" not in result

    def test_is_safe(self, tool):
        assert tool.is_concurrency_safe is True

    def test_name(self, tool):
        assert tool.name == "glob"
