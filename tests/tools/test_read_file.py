"""read_file 工具测试。"""

import pytest

from cc_python.tools.read_file import ReadFileTool


@pytest.fixture
def tool():
    return ReadFileTool()


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_basic(self, tool, sample_py_file):
        result = await tool.call(file_path=str(sample_py_file))
        assert "1" in result  # 行号
        assert "def hello" in result

    @pytest.mark.asyncio
    async def test_with_offset(self, tool, sample_py_file):
        result = await tool.call(file_path=str(sample_py_file), offset=2)
        assert "2" in result
        assert "def hello" not in result  # 第 1 行被跳过

    @pytest.mark.asyncio
    async def test_with_limit(self, tool, sample_py_file):
        result = await tool.call(file_path=str(sample_py_file), limit=1)
        assert "def hello" in result

    @pytest.mark.asyncio
    async def test_not_found(self, tool, tmp_path):
        result = await tool.call(file_path=str(tmp_path / "nonexistent.txt"))
        assert "错误" in result or "error" in result.lower() or "不存在" in result

    def test_is_safe(self, tool):
        assert tool.is_concurrency_safe is True

    def test_name(self, tool):
        assert tool.name == "read_file"

    def test_has_schema(self, tool):
        schema = tool.input_schema
        assert "file_path" in schema["properties"]
        assert "file_path" in schema["required"]
