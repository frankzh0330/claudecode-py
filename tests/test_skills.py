"""skills.py 测试。"""

import pytest

from termpilot.skills import (
    SkillDefinition, _parse_frontmatter,
    load_skills_from_dir, register_skill, register_bundled_skill,
    find_skill, get_all_skills, discover_and_load_skills,
    get_skills_description_for_prompt,
)


class TestParseFrontmatter:
    def test_valid(self):
        content = "---\nname: my-skill\ndescription: Test skill\n---\n\nSkill body content"
        meta, body = _parse_frontmatter(content)
        assert meta["name"] == "my-skill"
        assert meta["description"] == "Test skill"
        assert body == "Skill body content"

    def test_no_frontmatter(self):
        content = "Just plain markdown content"
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert body == "Just plain markdown content"

    def test_bool_types(self):
        content = "---\nuserInvocable: true\n---\nbody"
        meta, body = _parse_frontmatter(content)
        assert meta["userInvocable"] is True

    def test_false_type(self):
        content = "---\nuserInvocable: false\n---\nbody"
        meta, body = _parse_frontmatter(content)
        assert meta["userInvocable"] is False

    def test_list_type(self):
        content = "---\nallowedTools: ['Bash', 'Read']\n---\nbody"
        meta, body = _parse_frontmatter(content)
        assert meta["allowedTools"] == ["Bash", "Read"]

    def test_quoted_string(self):
        content = '---\nname: "my skill"\n---\nbody'
        meta, body = _parse_frontmatter(content)
        assert meta["name"] == "my skill"

    def test_comments_ignored(self):
        content = "---\n# comment\nname: test\n---\nbody"
        meta, body = _parse_frontmatter(content)
        assert "name" in meta
        assert "# comment" not in str(meta)


class TestLoadSkillsFromDir:
    def test_empty_dir(self, tmp_path):
        assert load_skills_from_dir(tmp_path / "nonexistent") == []
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_skills_from_dir(empty) == []

    def test_with_files(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "test.md").write_text(
            "---\nname: test-skill\ndescription: A test\n---\n\nDo something useful",
            encoding="utf-8",
        )
        (skills_dir / "other.md").write_text(
            "---\nname: other-skill\ndescription: Another\n---\n\nOther content",
            encoding="utf-8",
        )

        skills = load_skills_from_dir(skills_dir)
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert "test-skill" in names
        assert "other-skill" in names

    def test_ignores_non_md(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "test.md").write_text("---\nname: test\n---\nbody", encoding="utf-8")
        (skills_dir / "data.json").write_text("{}", encoding="utf-8")

        skills = load_skills_from_dir(skills_dir)
        assert len(skills) == 1

    def test_filename_as_default_name(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        # 没有 name frontmatter
        (skills_dir / "my-skill.md").write_text("---\n---\nbody", encoding="utf-8")

        skills = load_skills_from_dir(skills_dir)
        assert skills[0].name == "my-skill"


class TestSkillGetPrompt:
    def test_no_args(self):
        skill = SkillDefinition(name="test", description="desc", prompt_template="Hello")
        assert skill.get_prompt() == "Hello"

    def test_with_placeholder(self):
        skill = SkillDefinition(name="test", description="desc", prompt_template="Hello {args}")
        assert skill.get_prompt("world") == "Hello world"

    def test_append_args(self):
        skill = SkillDefinition(name="test", description="desc", prompt_template="Hello")
        result = skill.get_prompt("world")
        assert "Hello" in result
        assert "world" in result


class TestSkillRegistration:
    def test_register_and_find(self, clean_skills):
        skill = SkillDefinition(name="test", description="desc")
        register_skill(skill)
        assert find_skill("test") is skill

    def test_find_not_found(self, clean_skills):
        assert find_skill("nonexistent") is None

    def test_register_bundled(self, clean_skills):
        register_bundled_skill("bundled", "A bundled skill", "do stuff")
        found = find_skill("bundled")
        assert found is not None
        assert found.source == "bundled"

    def test_get_all_skills(self, clean_skills):
        register_skill(SkillDefinition(name="a", description="A"))
        register_skill(SkillDefinition(name="b", description="B"))
        all_skills = get_all_skills()
        assert len(all_skills) == 2


class TestDiscoverAndLoadSkills:
    def test_from_dirs(self, tmp_path, monkeypatch, clean_skills):
        monkeypatch.setattr("termpilot.skills.Path.home", lambda: tmp_path / "home")
        monkeypatch.setattr("termpilot.skills.Path.cwd", lambda: tmp_path / "project")

        # 用户全局 skills
        user_dir = tmp_path / "home" / ".termpilot" / "skills"
        user_dir.mkdir(parents=True)
        (user_dir / "global.md").write_text(
            "---\nname: global-skill\ndescription: Global\n---\nGlobal content",
            encoding="utf-8",
        )

        # 项目级 skills
        proj_dir = tmp_path / "project" / ".termpilot" / "skills"
        proj_dir.mkdir(parents=True)
        (proj_dir / "local.md").write_text(
            "---\nname: local-skill\ndescription: Local\n---\nLocal content",
            encoding="utf-8",
        )

        discover_and_load_skills(cwd=tmp_path / "project")
        all_skills = get_all_skills()
        names = {s.name for s in all_skills}
        assert "global-skill" in names
        assert "local-skill" in names


class TestGetSkillsDescriptionForPrompt:
    def test_empty(self, clean_skills):
        assert get_skills_description_for_prompt() == ""

    def test_with_skills(self, clean_skills):
        register_skill(SkillDefinition(name="test", description="A test skill", user_invocable=True))
        desc = get_skills_description_for_prompt()
        assert "test" in desc
        assert "user-invocable" in desc
