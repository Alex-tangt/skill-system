from __future__ import annotations

import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillMetadata:
    """Thin wrapper around Agent Skills SKILL.md frontmatter + body.

    Replaces the v0.1.0 SkillDefinition/StepDefinition DAG model.
    Follows the agentskills.io specification.
    """

    name: str
    description: str
    body: str = ""  # Markdown body (L2: instructions, loaded when triggered)
    version: str = "1.0.0"
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] | None = None

    # Path to the SKILL.md file on disk (not part of the standard, engine-internal)
    source_path: str | None = None

    @property
    def frontmatter_dict(self) -> dict:
        """Serialize frontmatter fields to a dict for YAML output."""
        result: dict = {
            "name": self.name,
            "description": self.description,
        }
        if self.version != "1.0.0":
            result["version"] = self.version
        if self.license:
            result["license"] = self.license
        if self.compatibility:
            result["compatibility"] = self.compatibility
        if self.metadata:
            result["metadata"] = self.metadata
        if self.allowed_tools:
            result["allowed-tools"] = " ".join(self.allowed_tools)
        return result

    @classmethod
    def from_skill_md(cls, filepath: str | Path) -> SkillMetadata | None:
        """Parse a SKILL.md file and return a SkillMetadata instance."""
        path = Path(filepath)
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8")

        # Parse YAML frontmatter
        frontmatter: dict = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    return None
                body = parts[2].strip()

        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")

        if not name or not description:
            return None

        version = str(frontmatter.get("version", "1.0.0"))
        # Validate version in metadata if present
        meta = frontmatter.get("metadata", {})
        if isinstance(meta, dict) and "version" in meta and version == "1.0.0":
            version = str(meta["version"])

        allowed_tools = None
        raw_tools = frontmatter.get("allowed-tools")
        if isinstance(raw_tools, str) and raw_tools.strip():
            allowed_tools = raw_tools.split()

        metadata_dict: dict[str, str] = {}
        raw_meta = frontmatter.get("metadata")
        if isinstance(raw_meta, dict):
            metadata_dict = {str(k): str(v) for k, v in raw_meta.items()}

        return cls(
            name=name,
            description=description,
            body=body,
            version=version,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            metadata=metadata_dict,
            allowed_tools=allowed_tools,
            source_path=str(path),
        )

    def to_skill_md(self) -> str:
        """Serialize to SKILL.md format string."""
        fm = yaml.safe_dump(
            self.frontmatter_dict,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{fm}\n---\n\n{self.body}\n"

    def validate(self) -> list[str]:
        """Validate against agentskills.io constraints."""
        errors: list[str] = []

        # name: 1-64 chars, lowercase alphanumeric + hyphens only
        if not self.name:
            errors.append("name is required")
        elif len(self.name) > 64:
            errors.append(f"name exceeds 64 characters (got {len(self.name)})")
        else:
            # Check consecutive hyphens first (before regex, which allows hyphens)
            if "--" in self.name:
                errors.append("name must not contain consecutive hyphens (--)")
            if self.name.startswith("-") or self.name.endswith("-"):
                errors.append("name must not start or end with a hyphen")
            if re.search(r"[A-Z]", self.name):
                errors.append("name must be lowercase")
            if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", self.name):
                errors.append(f"name contains invalid characters: '{self.name}'")

        # description: 1-1024 chars
        if not self.description:
            errors.append("description is required")
        elif len(self.description) > 1024:
            errors.append(f"description exceeds 1024 characters (got {len(self.description)})")

        # compatibility: max 500 chars
        if self.compatibility and len(self.compatibility) > 500:
            errors.append(f"compatibility exceeds 500 characters (got {len(self.compatibility)})")

        return errors
