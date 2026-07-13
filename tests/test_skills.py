"""Tests for Sawyer Harness skill system."""

from pathlib import Path

from sawyer_harness.skills import Skill, SkillStore


def _write_skill_file(path: Path, name: str, content: str, **kwargs) -> Path:
    """Helper to write a skill file with YAML frontmatter."""
    import yaml

    frontmatter = {"name": name, **kwargs}
    yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    file_path = path / f"{name.replace(' ', '_')}.md"
    file_path.write_text(f"---\n{yaml_str}---\n\n{content}\n", encoding="utf-8")
    return file_path


def test_skill_store_load(tmp_path):
    """SkillStore loads skill files from directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "python-debugging",
        "# Python Debugging\n\n1. Read the error\n2. Check imports",
        version="1.0",
        category="development",
        description="Systematic Python debugging",
        triggers=["debug", "python", "error"],
    )

    store = SkillStore(skills_dir)
    skill = store.get("python-debugging")

    assert skill is not None
    assert skill.name == "python-debugging"
    assert skill.version == "1.0"
    assert skill.category == "development"
    assert skill.description == "Systematic Python debugging"
    assert skill.triggers == ["debug", "python", "error"]
    assert "Read the error" in skill.content


def test_skill_store_list(tmp_path):
    """SkillStore lists all skills with metadata."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "git-workflow",
        "## Git Steps\n\n1. Branch\n2. Commit\n3. Push",
        category="development",
        triggers=["git", "branch"],
    )

    _write_skill_file(
        skills_dir,
        "ssh-setup",
        "## SSH Setup\n\nGenerate key, copy to server.",
        category="infra",
        triggers=["ssh", "server"],
    )

    store = SkillStore(skills_dir)
    listing = store.list_skills()

    assert len(listing) == 2
    names = {s["name"] for s in listing}
    assert "git-workflow" in names
    assert "ssh-setup" in names


def test_skill_search_by_trigger(tmp_path):
    """SkillStore search finds skills by trigger word."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "python-debugging",
        "Debug Python code step by step.",
        triggers=["debug", "python", "error", "traceback"],
    )

    _write_skill_file(
        skills_dir,
        "git-workflow",
        "Git branching and commit workflow.",
        triggers=["git", "branch", "commit"],
    )

    store = SkillStore(skills_dir)

    # Search for "debug" should find python-debugging first
    results = store.search("debug")
    assert len(results) >= 1
    assert results[0].name == "python-debugging"

    # Search for "git" should find git-workflow
    results = store.search("git")
    assert len(results) >= 1
    assert results[0].name == "git-workflow"


def test_skill_search_by_content(tmp_path):
    """SkillStore search matches content substrings."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "docker-basics",
        "How to run containers with Docker.",
        triggers=["docker"],
    )

    store = SkillStore(skills_dir)
    results = store.search("containers")
    assert len(results) >= 1
    assert results[0].name == "docker-basics"


def test_skill_find_relevant(tmp_path):
    """SkillStore finds relevant skills based on context and fits within char budget."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "python-debugging",
        "Debug Python code. Check imports. Read tracebacks.",
        triggers=["debug", "python", "error"],
    )

    _write_skill_file(
        skills_dir,
        "git-workflow",
        "Git branch, commit, push workflow.",
        triggers=["git", "branch"],
    )

    _write_skill_file(
        skills_dir,
        "cooking-recipes",
        "How to bake a cake. Mix flour, sugar, eggs.",
        triggers=["cooking", "recipe", "food"],
    )

    store = SkillStore(skills_dir)

    # "debug my python code" should match python-debugging, not cooking
    relevant = store.find_relevant("debug my python code")
    names = [s.name for s in relevant]
    assert "python-debugging" in names
    assert "cooking-recipes" not in names


def test_skill_find_relevant_char_budget(tmp_path):
    """SkillStore respects character budget when selecting skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "skill-a",
        "A" * 500,
        triggers=["test"],
    )

    _write_skill_file(
        skills_dir,
        "skill-b",
        "B" * 500,
        triggers=["test"],
    )

    store = SkillStore(skills_dir)

    # With budget of 600 chars, only one skill should fit
    relevant = store.find_relevant("test", max_chars=600)
    assert len(relevant) <= 1


def test_skill_add_or_update(tmp_path):
    """SkillStore can add new skills and update existing ones."""
    skills_dir = tmp_path / "skills"

    store = SkillStore(skills_dir)

    # Add a new skill
    skill = Skill(
        name="new-skill",
        version="1.0",
        category="testing",
        description="A brand new skill",
        triggers=["test", "new"],
        content="# New Skill\n\nDo the thing.",
    )
    result = store.add_or_update(skill)
    assert result is True

    # Verify it was saved
    loaded = store.get("new-skill")
    assert loaded is not None
    assert loaded.name == "new-skill"
    assert loaded.content == "# New Skill\n\nDo the thing."

    # Verify the file exists on disk
    assert loaded.file_path is not None
    assert Path(loaded.file_path).exists()

    # Update the skill
    skill.content = "# Updated Skill\n\nDo it better."
    result = store.add_or_update(skill)
    assert result is True

    # Re-create store to verify persistence
    store2 = SkillStore(skills_dir)
    loaded2 = store2.get("new-skill")
    assert loaded2 is not None
    assert "Updated" in loaded2.content


def test_skill_patch(tmp_path):
    """SkillStore can patch (find-and-replace) skill content."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "patchable",
        "# My Skill\n\nStep 1: Read the code\nStep 2: Fix the bug",
        version="1.0",
        triggers=["test"],
    )

    store = SkillStore(skills_dir)

    # Patch: replace "Fix the bug" with "Fix the bug with tests"
    result = store.patch("patchable", "Fix the bug", "Fix the bug with tests")
    assert result is True

    patched = store.get("patchable")
    assert "Fix the bug with tests" in patched.content
    assert patched.version == "1.1"  # Version bumped

    # Verify file on disk was updated
    disk_text = Path(patched.file_path).read_text()
    assert "Fix the bug with tests" in disk_text


def test_skill_patch_not_found(tmp_path):
    """Patch returns False if old_content not found."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "stable",
        "This skill is stable.",
        triggers=["test"],
    )

    store = SkillStore(skills_dir)
    result = store.patch("stable", "nonexistent text", "replacement")
    assert result is False


def test_skill_patch_unknown_skill(tmp_path):
    """Patch returns False for unknown skill name."""
    store = SkillStore(tmp_path / "skills")
    result = store.patch("unknown", "old", "new")
    assert result is False


def test_skill_delete(tmp_path):
    """SkillStore can delete skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill_file(
        skills_dir,
        "deletable",
        "This will be deleted.",
        triggers=["test"],
    )

    store = SkillStore(skills_dir)
    assert store.get("deletable") is not None

    result = store.delete("deletable")
    assert result is True
    assert store.get("deletable") is None

    # Verify file removed from disk
    assert not any(s.name == "deletable" for s in store._skills.values())


def test_skill_format_for_prompt(tmp_path):
    """SkillStore formats skills for prompt injection."""
    store = SkillStore(tmp_path / "skills")

    skills = [
        Skill(
            name="test-skill",
            version="1.0",
            category="testing",
            description="A test skill",
            triggers=["test"],
            content="# Test\n\nSteps here.",
        )
    ]

    formatted = store.format_for_prompt(skills)
    assert "## Loaded Skills" in formatted
    assert "### test-skill" in formatted
    assert "v1.0" in formatted
    assert "Steps here" in formatted


def test_skill_format_empty(tmp_path):
    """SkillStore returns empty string for empty skill list."""
    store = SkillStore(tmp_path / "skills")
    assert store.format_for_prompt([]) == ""


def test_skill_reload(tmp_path):
    """SkillStore can reload skills from disk (pick up new files)."""
    skills_dir = tmp_path / "skills"

    store = SkillStore(skills_dir)
    assert len(store.list_skills()) == 0

    # Add a skill file to disk
    _write_skill_file(
        skills_dir,
        "late-addition",
        "Added after initial load.",
        triggers=["test"],
    )

    # Before reload, not found
    assert store.get("late-addition") is None

    # After reload, found
    store.reload()
    assert store.get("late-addition") is not None


def test_skill_total_chars(tmp_path):
    """Skill reports correct character count."""
    skill = Skill(
        name="test",
        content="A" * 100,
    )
    assert skill.total_chars == 100