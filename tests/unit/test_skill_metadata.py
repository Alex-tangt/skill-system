from __future__ import annotations

import os
import tempfile
import pytest
from skill_engine.kernel.models.skill_metadata import SkillMetadata


def _write_skill_md(dir_path: str, content: str) -> str:
    path = os.path.join(dir_path, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestFromSkillMd:
    def test_parses_minimal_skill(self):
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, "---\nname: test-skill\ndescription: A minimal skill.\n---\n\n# Body")
        sm = SkillMetadata.from_skill_md(path)
        assert sm is not None
        assert sm.name == "test-skill"
        assert sm.description == "A minimal skill."
        assert sm.body == "# Body"

    def test_parses_all_optional_fields(self):
        d = tempfile.mkdtemp()
        content = """---
name: full-skill
description: A fully specified skill.
license: MIT
compatibility: Requires Python 3.10+
metadata:
  author: test-org
  version: "2.0"
allowed-tools: Read Write Bash(git:*)
---
# Full Skill
Body content here.
"""
        path = _write_skill_md(d, content)
        sm = SkillMetadata.from_skill_md(path)
        assert sm is not None
        assert sm.name == "full-skill"
        assert sm.license == "MIT"
        assert sm.compatibility == "Requires Python 3.10+"
        assert sm.metadata == {"author": "test-org", "version": "2.0"}
        assert sm.allowed_tools == ["Read", "Write", "Bash(git:*)"]
        assert "Body content here." in sm.body

    def test_no_frontmatter_returns_none(self):
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, "# Just markdown, no frontmatter")
        sm = SkillMetadata.from_skill_md(path)
        assert sm is None

    def test_missing_name_returns_none(self):
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, "---\ndescription: Missing name.\n---\n\nBody")
        sm = SkillMetadata.from_skill_md(path)
        assert sm is None

    def test_broken_yaml_returns_none(self):
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, "---\n: : : not valid yaml: [\n---\n\nBody")
        sm = SkillMetadata.from_skill_md(path)
        assert sm is None

    def test_nonexistent_path(self):
        sm = SkillMetadata.from_skill_md("/nonexistent/SKILL.md")
        assert sm is None


class TestValidate:
    def test_valid_skill_passes(self):
        sm = SkillMetadata(name="my-skill", description="Does something useful.")
        assert sm.validate() == []

    def test_missing_name(self):
        sm = SkillMetadata(name="", description="Valid description.")
        errors = sm.validate()
        assert any("name is required" in e for e in errors)

    def test_name_too_long(self):
        long_name = "a" * 65
        sm = SkillMetadata(name=long_name, description="Valid.")
        errors = sm.validate()
        assert any("exceeds 64" in e for e in errors)

    def test_name_uppercase_rejected(self):
        sm = SkillMetadata(name="PDF-Processing", description="Valid.")
        errors = sm.validate()
        assert any("lowercase" in e for e in errors)

    def test_name_starts_with_hyphen(self):
        sm = SkillMetadata(name="-bad", description="Valid.")
        errors = sm.validate()
        assert any("hyphen" in e for e in errors)

    def test_name_double_hyphen(self):
        sm = SkillMetadata(name="bad--name", description="Valid.")
        errors = sm.validate()
        assert any("consecutive hyphens" in e for e in errors)

    def test_description_too_long(self):
        long_desc = "x" * 1025
        sm = SkillMetadata(name="ok", description=long_desc)
        errors = sm.validate()
        assert any("exceeds 1024" in e for e in errors)

    def test_compatibility_too_long(self):
        long_compat = "x" * 501
        sm = SkillMetadata(name="ok", description="Valid.", compatibility=long_compat)
        errors = sm.validate()
        assert any("exceeds 500" in e for e in errors)


class TestToSkillMd:
    def test_roundtrip_minimal(self):
        sm = SkillMetadata(name="roundtrip", description="Roundtrip test.")
        output = sm.to_skill_md()
        # Parse it back
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, output)
        sm2 = SkillMetadata.from_skill_md(path)
        assert sm2 is not None
        assert sm2.name == "roundtrip"
        assert sm2.description == "Roundtrip test."

    def test_roundtrip_with_body(self):
        sm = SkillMetadata(name="rt-body", description="Has body.", body="# Instructions\n\nStep 1.\nStep 2.")
        output = sm.to_skill_md()
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, output)
        sm2 = SkillMetadata.from_skill_md(path)
        assert sm2 is not None
        assert "Step 1." in sm2.body
        assert "Step 2." in sm2.body

    def test_roundtrip_with_metadata_version(self):
        sm = SkillMetadata(name="rt-version", description="Has version metadata.",
                           metadata={"version": "3.0", "author": "test"})
        output = sm.to_skill_md()
        d = tempfile.mkdtemp()
        path = _write_skill_md(d, output)
        sm2 = SkillMetadata.from_skill_md(path)
        assert sm2 is not None
        assert sm2.metadata.get("version") == "3.0"
        assert sm2.metadata.get("author") == "test"
