from __future__ import annotations

import os
import tempfile
import pytest
from skill_engine.kernel.skill_store import SkillStore
from skill_engine.kernel.models.skill_metadata import SkillMetadata


@pytest.fixture
def _ts_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def ks(_ts_dir):
    return SkillStore(_ts_dir)


# Each test uses the conftest's "temp_skills_dir" for backup path verification,
# but "ks" for the kernel SkillStore instance.

def test_save_and_get(ks):
    skill = SkillMetadata(name="test-skill", description="A test skill.")
    ks.save(skill)
    loaded = ks.get("test-skill")
    assert loaded is not None
    assert loaded.name == "test-skill"
    assert loaded.description == "A test skill."


def test_save_with_body(ks):
    skill = SkillMetadata(name="with-body", description="Has body.", body="# Instructions\n\nDo this.")
    ks.save(skill)
    loaded = ks.get("with-body")
    assert loaded is not None
    assert "# Instructions" in loaded.body


def test_list_all(ks):
    ks.save(SkillMetadata(name="skill-a", description="First."))
    ks.save(SkillMetadata(name="skill-b", description="Second."))
    skills = ks.list_all()
    assert len(skills) == 2
    assert {s.name for s in skills} == {"skill-a", "skill-b"}


def test_delete(ks):
    ks.save(SkillMetadata(name="to-delete", description="Gone."))
    assert ks.delete("to-delete") is True
    assert ks.get("to-delete") is None
    assert ks.delete("nonexistent") is False


def test_backup_on_overwrite(ks, _ts_dir):
    skill = SkillMetadata(name="backup-test", description="V1.")
    ks.save(skill)
    skill.description = "V2."
    ks.save(skill)
    backup_path = os.path.join(_ts_dir, "backup-test", "SKILL.md.backup")
    assert os.path.exists(backup_path)
    loaded = ks.get("backup-test")
    assert loaded.description == "V2."


def test_get_by_name(ks):
    ks.save(SkillMetadata(name="my-skill", description="Case-insensitive."))
    found = ks.get_by_name("MY-SKILL")
    assert found is not None
    assert found.name == "my-skill"
    assert ks.get_by_name("Nonexistent") is None


def test_list_all_empty_dir(ks):
    assert ks.list_all() == []


def test_list_all_skips_non_skill_dirs(ks, _ts_dir):
    ks.save(SkillMetadata(name="valid", description="Valid."))
    os.makedirs(os.path.join(_ts_dir, "not-a-skill"))
    skills = ks.list_all()
    assert len(skills) == 1
    assert skills[0].name == "valid"


def test_list_all_handles_bad_frontmatter(ks, _ts_dir):
    ks.save(SkillMetadata(name="good", description="Valid."))
    bad_dir = os.path.join(_ts_dir, "bad-skill")
    os.makedirs(bad_dir)
    with open(os.path.join(bad_dir, "SKILL.md"), "w") as f:
        f.write("---\n: : : broken: yaml: [\n---\n\nBad body.")
    skills = ks.list_all()
    assert any(s.name == "good" for s in skills)
