from __future__ import annotations

import os
import pytest
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


def test_save_and_get(skill_store):
    skill = SkillDefinition(id="test", name="Test", steps=[])
    skill_store.save(skill)
    loaded = skill_store.get("test")
    assert loaded is not None
    assert loaded.name == "Test"


def test_list_all(skill_store):
    skill_store.save(SkillDefinition(id="a", name="A", steps=[]))
    skill_store.save(SkillDefinition(id="b", name="B", steps=[]))
    skills = skill_store.list_all()
    assert len(skills) == 2


def test_delete(skill_store):
    skill_store.save(SkillDefinition(id="x", name="X", steps=[]))
    assert skill_store.delete("x") is True
    assert skill_store.get("x") is None
    assert skill_store.delete("nonexistent") is False


def test_backup_on_overwrite(skill_store, temp_skills_dir):
    skill = SkillDefinition(id="backup-test", name="V1", steps=[])
    skill_store.save(skill)
    skill.name = "V2"
    skill_store.save(skill)
    assert os.path.exists(os.path.join(temp_skills_dir, "backup-test.yaml.backup"))
    loaded = skill_store.get("backup-test")
    assert loaded.name == "V2"


def test_get_by_name(skill_store):
    skill_store.save(SkillDefinition(id="abc", name="My Skill", steps=[]))
    found = skill_store.get_by_name("My Skill")
    assert found is not None
    assert found.id == "abc"
    assert skill_store.get_by_name("Nonexistent") is None


def test_list_all_empty_dir(skill_store):
    """list_all returns empty list when no skills are saved."""
    skills = skill_store.list_all()
    assert skills == []


def test_list_all_skips_non_yaml(skill_store, temp_skills_dir):
    """list_all ignores non-YAML files in the skills directory."""
    skill_store.save(SkillDefinition(id="valid", name="Valid", steps=[]))
    # Create a non-yaml file
    with open(os.path.join(temp_skills_dir, "README.md"), "w") as f:
        f.write("This is not a skill")
    skills = skill_store.list_all()
    assert len(skills) == 1
    assert skills[0].id == "valid"


def test_list_all_handles_corrupted_yaml(skill_store, temp_skills_dir):
    """list_all skips corrupted YAML files instead of crashing."""
    skill_store.save(SkillDefinition(id="good", name="Good", steps=[]))
    # Write corrupted YAML
    with open(os.path.join(temp_skills_dir, "bad.yaml"), "w") as f:
        f.write(": : : bad: yaml: [\n")
    # Current behavior: may raise an exception. This test documents the expected behavior.
    try:
        skills = skill_store.list_all()
        # If it doesn't crash, it should at least include the valid skill
        assert any(s.id == "good" for s in skills)
    except Exception:
        pytest.fail("list_all() should handle corrupted YAML gracefully")
