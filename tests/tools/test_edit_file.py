"""edit_file 工具测试。"""

import pytest

from cc_python.tools.edit_file import EditFileTool


@pytest.fixture
def tool():
    return EditFileTool()


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "edit_test.txt"
    f.write_text("line one\nline two\nline three\n")
    return f


class TestEditFileTool:
    @pytest.mark.asyncio
    async def test_basic(self, tool, sample_file):
        result = await tool.call(
            file_path=str(sample_file),
            old_string="line two",
            new_string="LINE TWO",
        )
        assert "1 replacement" in result or "替换" in result or "replaced" in result.lower()
        content = sample_file.read_text()
        assert "LINE TWO" in content
        assert "line two" not in content

    @pytest.mark.asyncio
    async def test_not_unique(self, tool, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("same\nsame\n")
        result = await tool.call(
            file_path=str(f),
            old_string="same",
            new_string="different",
        )
        assert "必须唯一" in result or "not unique" in result.lower() or "multiple" in result.lower()

    @pytest.mark.asyncio
    async def test_replace_all(self, tool, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("foo\nbar\nfoo\n")
        result = await tool.call(
            file_path=str(f),
            old_string="foo",
            new_string="baz",
            replace_all=True,
        )
        content = f.read_text()
        assert content == "baz\nbar\nbaz\n"

    @pytest.mark.asyncio
    async def test_not_found(self, tool, sample_file):
        result = await tool.call(
            file_path=str(sample_file),
            old_string="nonexistent string",
            new_string="replacement",
        )
        assert "未找到" in result or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_file_not_exists(self, tool, tmp_path):
        result = await tool.call(
            file_path=str(tmp_path / "nonexistent.txt"),
            old_string="x",
            new_string="y",
        )
        assert "错误" in result or "error" in result.lower() or "不存在" in result

    def test_is_unsafe(self, tool):
        assert tool.is_concurrency_safe is False

    def test_name(self, tool):
        assert tool.name == "edit_file"
