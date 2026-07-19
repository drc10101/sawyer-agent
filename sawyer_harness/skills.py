"""
Skill system -- reusable knowledge that the agent can load and self-update.

Skills are YAML+Markdown files stored in a skills directory. Each skill has:
- YAML frontmatter with metadata (name, version, category, triggers, etc.)
- Markdown body with instructions, steps, pitfalls

The agent loads relevant skills based on the current task, injects them
into the system prompt, and can self-patch skills after successful task
completion (the self-improvement loop in Phase 3).

File format example:

```
---
name: python-debugging
version: 1.0
category: development
triggers:
  - debug
  - python
  - error
  - traceback
description: Systematic Python debugging approach
created: 2026-07-12
updated: 2026-07-12
---

# Python Debugging

## Steps
1. Read the error message carefully
2. Identify the file and line number
3. Read the relevant code
...

## Pitfalls
- Don't change code before understanding the error
- Check imports before assuming a function exists
```
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("sawyer-harness.skills")


@dataclass
class Skill:
    """A loaded skill with metadata and content."""

    name: str
    version: str = "1.0"
    category: str = "general"
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    content: str = ""  # The markdown body
    created: str = ""
    updated: str = ""
    file_path: Optional[str] = None  # Source file path for self-patching

    @property
    def total_chars(self) -> int:
        """Character count of the content (for context budget)."""
        return len(self.content)


class SkillStore:
    """
    Persistent skill registry backed by YAML+Markdown files.

    Skills live in a directory, each as a .md file with YAML frontmatter.
    The store loads them on init, supports search by trigger words,
    and can patch skills in-place (for the self-improvement loop).
    """

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).expanduser()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        """Load all skill files from the skills directory."""
        self._skills.clear()
        for path in self.skills_dir.glob("*.md"):
            try:
                skill = self._parse_file(path)
                if skill:
                    self._skills[skill.name] = skill
            except Exception as e:
                logger.warning(f"Failed to load skill from {path}: {e}")

    def _parse_file(self, path: Path) -> Optional[Skill]:
        """Parse a YAML+Markdown skill file."""
        text = path.read_text(encoding="utf-8")

        # Extract YAML frontmatter between --- delimiters
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            logger.warning(f"Skill file {path} has no YAML frontmatter")
            return None

        frontmatter_str = match.group(1)
        body = match.group(2).strip()

        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML in {path}: {e}")
            return None

        if not isinstance(frontmatter, dict):
            logger.warning(f"YAML frontmatter in {path} is not a dict")
            return None

        return Skill(
            name=frontmatter.get("name", path.stem),
            version=str(frontmatter.get("version", "1.0")),
            category=frontmatter.get("category", "general"),
            description=frontmatter.get("description", ""),
            triggers=frontmatter.get("triggers", []),
            content=body,
            created=frontmatter.get("created", ""),
            updated=frontmatter.get("updated", ""),
            file_path=str(path),
        )

    def _serialize_skill(self, skill: Skill) -> str:
        """Serialize a Skill back to YAML+Markdown format."""
        frontmatter = {
            "name": skill.name,
            "version": skill.version,
            "category": skill.category,
            "description": skill.description,
            "triggers": skill.triggers,
            "created": skill.created,
            "updated": skill.updated,
        }
        # Remove empty/None values for clean output
        frontmatter = {k: v for k, v in frontmatter.items() if v}

        yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        return f"---\n{yaml_str}---\n\n{skill.content}\n"

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        """List all loaded skills with metadata (no content)."""
        return [
            {
                "name": s.name,
                "version": s.version,
                "category": s.category,
                "description": s.description,
                "triggers": s.triggers,
                "chars": s.total_chars,
            }
            for s in self._skills.values()
        ]

    def search(self, query: str, limit: int = 5) -> list[Skill]:
        """
        Search for skills matching a query.

        Matching logic:
        1. Exact name match (highest priority)
        2. Trigger word match
        3. Description/content substring match
        """
        query_lower = query.lower()
        results: list[tuple[int, Skill]] = []  # (score, skill)

        for skill in self._skills.values():
            score = 0

            # Exact name match
            if skill.name.lower() == query_lower:
                score += 100

            # Name prefix match
            if skill.name.lower().startswith(query_lower):
                score += 50

            # Trigger word match
            for trigger in skill.triggers:
                if trigger.lower() in query_lower or query_lower in trigger.lower():
                    score += 20

            # Description match
            if query_lower in skill.description.lower():
                score += 10

            # Content match
            if query_lower in skill.content.lower():
                score += 5

            if score > 0:
                results.append((score, skill))

        # Sort by score descending
        results.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in results[:limit]]

    def find_relevant(self, context: str, max_chars: int = 4000) -> list[Skill]:
        """
        Find skills relevant to the given context string.

        Uses trigger word matching and content scoring to find the most
        relevant skills that fit within a character budget.
        """
        context_lower = context.lower()
        words = set(re.findall(r"\b\w+\b", context_lower))

        scored: list[tuple[int, Skill]] = []
        for skill in self._skills.values():
            score = 0

            # Trigger word overlap
            for trigger in skill.triggers:
                trigger_lower = trigger.lower()
                if trigger_lower in context_lower:
                    score += 30
                # Also check individual words in the trigger
                for word in trigger_lower.split():
                    if word in words:
                        score += 10

            # Category relevance (loose match)
            if skill.category.lower() in context_lower:
                score += 5

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Fill within character budget
        selected: list[Skill] = []
        total = 0
        for _, skill in scored:
            if total + skill.total_chars <= max_chars:
                selected.append(skill)
                total += skill.total_chars

        return selected

    def add_or_update(self, skill: Skill) -> bool:
        """
        Add a new skill or update an existing one.

        Writes the skill to disk as a YAML+Markdown file.
        Returns True on success.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not skill.created:
            skill.created = now
        skill.updated = now

        # Determine file path
        if not skill.file_path:
            # Sanitize name for filename
            safe_name = re.sub(r"[^\w\-]", "_", skill.name)
            skill.file_path = str(self.skills_dir / f"{safe_name}.md")

        try:
            path = Path(skill.file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._serialize_skill(skill), encoding="utf-8")
            self._skills[skill.name] = skill
            return True
        except Exception as e:
            logger.error(f"Failed to write skill {skill.name}: {e}")
            return False

    def patch(self, name: str, old_content: str, new_content: str) -> bool:
        """
        Patch a skill's content (find-and-replace).

        This is the self-improvement mechanism: after a successful task,
        the agent can update a skill to incorporate what it learned.

        Returns True if the old_content was found and replaced.
        """
        skill = self._skills.get(name)
        if not skill:
            logger.warning(f"Cannot patch unknown skill: {name}")
            return False

        if old_content not in skill.content:
            logger.warning(
                f"Cannot patch skill {name}: old_content not found in skill body"
            )
            return False

        skill.content = skill.content.replace(old_content, new_content, 1)
        skill.updated = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Increment version (patch level)
        version_parts = skill.version.split(".")
        if len(version_parts) == 2:
            skill.version = f"{version_parts[0]}.{int(version_parts[1]) + 1}"

        # Write updated file
        try:
            assert skill.file_path is not None, "Skill file_path is None"
            path = Path(skill.file_path)
            path.write_text(self._serialize_skill(skill), encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to write patched skill {name}: {e}")
            return False

    def delete(self, name: str) -> bool:
        """Delete a skill by name. Removes the file from disk."""
        skill = self._skills.get(name)
        if not skill:
            return False

        try:
            if skill.file_path:
                Path(skill.file_path).unlink(missing_ok=True)
            del self._skills[name]
            return True
        except Exception as e:
            logger.error(f"Failed to delete skill {name}: {e}")
            return False

    def format_for_prompt(self, skills: list[Skill]) -> str:
        """
        Format skills for injection into the system prompt.

        Produces a compact, scannable format that the LLM can parse.
        """
        if not skills:
            return ""

        parts = ["\n## Loaded Skills\n"]
        for skill in skills:
            parts.append(f"### {skill.name} (v{skill.version})")
            if skill.description:
                parts.append(f"Category: {skill.category} | {skill.description}")
            parts.append(skill.content)
            parts.append("")  # blank line between skills

        return "\n".join(parts)

    def reload(self):
        """Reload all skills from disk (pick up new files)."""
        self._load_all()
        logger.info(f"Reloaded {len(self._skills)} skills from {self.skills_dir}")