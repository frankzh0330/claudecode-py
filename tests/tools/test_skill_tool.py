"""skill_tool 工具测试。"""

import pytest

from cc_python.tools.skill_tool import SkillTool
from cc_python.skills import SkillDefinition, register_skill, _skills


@pytest.fixture(autouse=True)
def clean():
    _tasks = _skills.clear() if hasattr(_skills, 'clear') else None
    _skills.clear()
    yield
    _skills.clear()


@pytest.fixture
def tool():
    return SkillTool()


class TestSkillTool:
    @pytest.mark.asyncio
    async def test_call(self, tool):
        register_skill(SkillDefinition(
            name="test-skill",
            description="Test",
            prompt_template="Do something with {args}",
        ))
        result = await tool.call(skill="test-skill", args="hello")
        assert "Do something with hello" in result

    @pytest.mark.asyncio
    async def test_not_found(self, tool):
        result = await tool.call(skill="nonexistent")
        assert "not found" in result.lower() or "error" in result.lower()

    def test_is_safe(self, tool):
        assert tool.is_concurrency_safe is True

    def test_name(self, tool):
        assert tool.name == "skill"
