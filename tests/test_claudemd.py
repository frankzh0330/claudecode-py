"""claudemd.py 测试。"""

import pytest

from cc_python.claudemd import (
    MemoryFileInfo, _parent_chain, find_claude_md_files, load_claude_md,
)


class TestParentChain:
    def test_basic(self):
        chain = _parent_chain("/Users/frank/project")
        assert chain[0] == "/"
        assert "/Users" in chain
        assert chain[-1] == "/Users/frank/project"

    def test_single_level(self):
        chain = _parent_chain("/Users")
        assert chain == ["/", "/Users"]


class TestFindClaudeMdFiles:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")
        files = find_claude_md_files(str(tmp_path / "project"))
        assert files == []

    def test_project_claude_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")

        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("project instructions", encoding="utf-8")

        files = find_claude_md_files(str(project))
        assert len(files) >= 1
        found = [f for f in files if f.content == "project instructions"]
        assert len(found) == 1
        assert found[0].file_type == "project"

    def test_user_global(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("global instructions", encoding="utf-8")

        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: fake_home)

        files = find_claude_md_files(str(tmp_path / "project"))
        global_files = [f for f in files if f.content == "global instructions"]
        assert len(global_files) == 1
        assert global_files[0].file_type == "user"

    def test_rules_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")

        project = tmp_path / "project"
        rules_dir = project / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text("use python 3.12", encoding="utf-8")

        files = find_claude_md_files(str(project))
        rule_files = [f for f in files if "python 3.12" in f.content]
        assert len(rule_files) == 1

    def test_local_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")

        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.local.md").write_text("local private notes", encoding="utf-8")

        files = find_claude_md_files(str(project))
        local_files = [f for f in files if f.content == "local private notes"]
        assert len(local_files) == 1
        assert local_files[0].file_type == "local"


class TestLoadClaudeMd:
    def test_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")
        assert load_claude_md(str(tmp_path / "empty")) is None

    def test_formats_xml(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")

        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("do this", encoding="utf-8")

        result = load_claude_md(str(project))
        assert result is not None
        assert "<project>" in result
        assert "</project>" in result
        assert "do this" in result

    def test_multiple_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.claudemd.Path.home", lambda: tmp_path / "fake_home")

        project = tmp_path / "project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("project rules", encoding="utf-8")
        (project / "CLAUDE.local.md").write_text("local notes", encoding="utf-8")

        result = load_claude_md(str(project))
        assert result is not None
        assert "project rules" in result
        assert "local notes" in result
