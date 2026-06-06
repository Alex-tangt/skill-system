from __future__ import annotations

import os
import shutil
from pathlib import Path

from skill_engine.kernel.models.skill_metadata import SkillMetadata


class SkillStore:
    """File-system CRUD for skills in Agent Skills SKILL.md format.

    v0.2: Replaces the YAML DAG-based SkillStore. Reads/writes
    skills/{name}/SKILL.md with YAML frontmatter + Markdown body.
    Preserves the .backup-on-save pattern from v0.1.0.
    """

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        os.makedirs(skills_dir, exist_ok=True)

    def _skill_dir(self, name: str) -> str:
        return os.path.join(self.skills_dir, name)

    def _skill_md_path(self, name: str) -> str:
        return os.path.join(self._skill_dir(name), "SKILL.md")

    def get(self, name: str) -> SkillMetadata | None:
        """Get a skill by name. name must match the directory name."""
        path = self._skill_md_path(name)
        if not os.path.exists(path):
            return None
        return SkillMetadata.from_skill_md(path)

    def get_by_name(self, name: str) -> SkillMetadata | None:
        """Case-insensitive lookup across all skills."""
        for skill in self.list_all():
            if skill.name.lower() == name.lower():
                return skill
        return None

    def save(self, skill: SkillMetadata) -> None:
        """Save a skill. Creates .backup if overwriting. Auto-creates directory."""
        skill_dir = self._skill_dir(skill.name)
        os.makedirs(skill_dir, exist_ok=True)
        path = os.path.join(skill_dir, "SKILL.md")

        if os.path.exists(path):
            backup = path + ".backup"
            shutil.copy2(path, backup)

        content = skill.to_skill_md()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        # Update source path
        skill.source_path = path

    def delete(self, name: str) -> bool:
        """Delete a skill's SKILL.md. Returns True if deleted, False if not found."""
        path = self._skill_md_path(name)
        if os.path.exists(path):
            os.remove(path)
            # Remove the directory if empty
            skill_dir = self._skill_dir(name)
            try:
                os.rmdir(skill_dir)
            except OSError:
                pass  # directory not empty, leave it
            return True
        return False

    def list_all(self) -> list[SkillMetadata]:
        """List all skills. Walks skills/ for SKILL.md files (L1 progressive loading)."""
        skills: list[SkillMetadata] = []
        if not os.path.isdir(self.skills_dir):
            return skills

        for entry in sorted(os.listdir(self.skills_dir)):
            skill_dir = os.path.join(self.skills_dir, entry)
            if not os.path.isdir(skill_dir):
                continue
            md_path = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(md_path):
                skill = SkillMetadata.from_skill_md(md_path)
                if skill:
                    skills.append(skill)

        return skills
