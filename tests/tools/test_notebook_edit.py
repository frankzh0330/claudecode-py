"""notebook_edit 工具测试。"""

import json

import pytest

from cc_python.tools.notebook_edit import NotebookEditTool


@pytest.fixture
def tool():
    return NotebookEditTool()


class TestNotebookEdit:
    @pytest.mark.asyncio
    async def test_replace_cell(self, tool, sample_notebook):
        result = await tool.call(
            notebook_path=str(sample_notebook),
            new_source="print(42)",
            cell_id="cell_0",
            edit_mode="replace",
        )
        assert "updated" in result.lower() or "replace" in result.lower()
        nb = json.loads(sample_notebook.read_text())
        assert nb["cells"][0]["source"] == "print(42)"

    @pytest.mark.asyncio
    async def test_insert_cell(self, tool, sample_notebook):
        result = await tool.call(
            notebook_path=str(sample_notebook),
            new_source="# New cell",
            cell_type="markdown",
            edit_mode="insert",
            cell_id="cell_0",
        )
        assert "updated" in result.lower() or "insert" in result.lower()
        nb = json.loads(sample_notebook.read_text())
        assert len(nb["cells"]) == 3

    @pytest.mark.asyncio
    async def test_delete_cell(self, tool, sample_notebook):
        result = await tool.call(
            notebook_path=str(sample_notebook),
            new_source="",
            cell_id="cell_1",
            edit_mode="delete",
        )
        nb = json.loads(sample_notebook.read_text())
        assert len(nb["cells"]) == 1

    @pytest.mark.asyncio
    async def test_not_found(self, tool, tmp_path):
        result = await tool.call(
            notebook_path=str(tmp_path / "nonexistent.ipynb"),
            new_source="x",
        )
        assert "not found" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_not_ipynb(self, tool, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("not a notebook")
        result = await tool.call(
            notebook_path=str(f),
            new_source="x",
        )
        assert "not a notebook" in result.lower() or "error" in result.lower()

    def test_is_unsafe(self, tool):
        assert tool.is_concurrency_safe is False

    def test_name(self, tool):
        assert tool.name == "notebook_edit"
