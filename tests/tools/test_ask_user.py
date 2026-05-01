import pytest

from termpilot.tools.ask_user import AskUserQuestionTool


@pytest.mark.asyncio
async def test_ask_user_single_choice_uses_builtin_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")

    result = await AskUserQuestionTool().call(
        questions=[
            {
                "header": "类型",
                "question": "你想创建什么类型的 hello world?",
                "options": [
                    {"label": "Python 脚本", "description": "一个 hello.py 文件"},
                    {"label": "取消", "description": "不删除文件"},
                ],
                "multiSelect": False,
            }
        ]
    )

    assert "- 类型: 取消" in result


@pytest.mark.asyncio
async def test_ask_user_multi_choice_uses_builtin_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "1,3")

    result = await AskUserQuestionTool().call(
        questions=[
            {
                "header": "文件",
                "question": "选择要查看的文件",
                "options": [
                    {"label": "cli.py", "description": "CLI"},
                    {"label": "api.py", "description": "API"},
                    {"label": "context.py", "description": "Context"},
                ],
                "multiSelect": True,
            }
        ]
    )

    assert "- 文件: cli.py, context.py" in result
