"""共享测试 fixtures。"""

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """创建临时 settings.json 并 mock 路径。"""
    settings_file = tmp_path / ".termpilot" / "settings.json"
    settings_file.parent.mkdir(parents=True)

    def _write(data: dict[str, Any]) -> Path:
        settings_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return settings_file

    monkeypatch.setattr("termpilot.config.get_settings_path", lambda: settings_file)
    _write({})
    return _write


@pytest.fixture
def sample_py_file(tmp_path):
    """创建示例 Python 文件。"""
    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    print('hello')\n", encoding="utf-8")
    return f


@pytest.fixture
def sample_notebook(tmp_path):
    """创建示例 Jupyter notebook。"""
    import json
    nb = tmp_path / "test.ipynb"
    nb.write_text(json.dumps({
        "cells": [
            {"cell_type": "code", "id": "cell_0", "source": "print(1)", "outputs": [], "execution_count": None},
            {"cell_type": "markdown", "id": "cell_1", "source": "# Title"},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }), encoding="utf-8")
    return nb


@pytest.fixture
def clean_skills():
    """清空 skill 注册表。"""
    import termpilot.skills as sk
    sk._skills.clear()
    yield
    sk._skills.clear()


@pytest.fixture
def clean_commands():
    """重置命令注册表（保留内置命令）。"""
    import termpilot.commands as cmd
    cmd._commands.clear()
    cmd.register_builtin_commands()
    yield
    cmd._commands.clear()
    cmd.register_builtin_commands()


@pytest.fixture
def clean_tasks():
    """清空任务注册表。"""
    import termpilot.tools.task as task_mod
    task_mod._reset_tasks()
    yield
    task_mod._reset_tasks()


@pytest.fixture
def env_clean(monkeypatch):
    """清除相关环境变量。"""
    for key in [
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZHIPU_API_KEY",
        "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "TERMPILOT_CONTEXT_WINDOW",
    ]:
        monkeypatch.delenv(key, raising=False)
