"""write_file 工具测试。"""

import pytest

from cc_python.tools.write_file import WriteFileTool


@pytest.fixture
def tool():
    return WriteFileTool()


class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_new_file(self, tool, tmp_path):
        f = tmp_path / "new.txt"
        result = await tool.call(file_path=str(f), content="hello world")
        assert f.exists()
        assert f.read_text() == "hello world"
        assert "写入" in result or "wrote" in result.lower()

    @pytest.mark.asyncio
    async def test_overwrite(self, tool, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content")
        await tool.call(file_path=str(f), content="new content")
        assert f.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_creates_dirs(self, tool, tmp_path):
        f = tmp_path / "sub" / "dir" / "file.txt"
        result = await tool.call(file_path=str(f), content="deep file")
        assert f.exists()
        assert f.read_text() == "deep file"

    @pytest.mark.asyncio
    async def test_no_path(self, tool):
        result = await tool.call(file_path="", content="data")
        assert "错误" in result or "error" in result.lower()

    def test_is_unsafe(self, tool):
        assert tool.is_concurrency_safe is False

    def test_name(self, tool):
        assert tool.name == "write_file"
