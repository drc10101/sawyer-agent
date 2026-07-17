"""
Agent Discovery -- per-project and global agent definition discovery.

Agents are defined as markdown files with YAML frontmatter:

    ---
    name: code-reviewer
    description: Reviews code for bugs and style issues
    model: glm-5.1:cloud
    permission_mode: readwrite
    tools: file_read, file_search, git, patch
    skills: code-review
    ---

    # Code Reviewer

    You are a senior code reviewer. Focus on:
    - Bug detection
    - Security vulnerabilities
    - Performance issues

Discovery searches two locations, with project-level definitions
shadowing (overriding) global ones:

1. Project-level:  <project_root>/.sawyer/agents/*.md
2. Global:         ~/.sawyer-harness/user/agents/*.md

Name-based dedup: if "code-reviewer.md" exists in both locations,
the project-level one wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import UserData

logger = logging.getLogger("sawyer-harness.agent_discovery")


@dataclass
class AgentDefinition:
    """A discovered agent definition from an .md file."""
    name: str
    description: str = ""
    model: str = ""
    provider: str = ""
    base_url: str = ""
    system_prompt: str = ""
    permission_mode: str = "all"
    tools: list[str] = field(default_factory=lambda: ["all"])
    skills: list[str] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    source: str = ""  # "global" or "project"
    source_path: str = ""  # Full path to the .md file

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "system_prompt": self.system_prompt,
            "permission_mode": self.permission_mode,
            "tools": self.tools,
            "skills": self.skills,
            "rules": self.rules,
            "source": self.source,
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentDefinition:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            base_url=data.get("base_url", ""),
            system_prompt=data.get("system_prompt", ""),
            permission_mode=data.get("permission_mode", "all"),
            tools=data.get("tools", ["all"]),
            skills=data.get("skills", []),
            rules=data.get("rules", []),
            source=data.get("source", ""),
            source_path=data.get("source_path", ""),
        )


def _parse_agent_md(path: Path, source: str = "") -> AgentDefinition | None:
    """Parse an agent definition from a markdown file with YAML frontmatter.

    The file format is:
        ---
        name: my-agent
        description: What this agent does
        model: glm-5.1:cloud
        permission_mode: readwrite
        tools: file_read, file_search, git
        skills: code-review
        ---

        # Agent Name

        System prompt content goes here.
        Everything after the frontmatter becomes the system prompt.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read agent file {path}: {e}")
        return None

    # Parse frontmatter
    frontmatter: dict[str, Any] = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as e:
                logger.warning(f"Invalid YAML frontmatter in {path}: {e}")
                return None
            body = parts[2].strip()

    name = frontmatter.get("name", path.stem)
    if not name:
        name = path.stem

    # Parse tools: can be a list or comma-separated string
    tools_raw = frontmatter.get("tools", ["all"])
    if isinstance(tools_raw, str):
        tools = [t.strip() for t in tools_raw.split(",")]
    else:
        tools = tools_raw if isinstance(tools_raw, list) else ["all"]

    # Parse skills: can be a list or comma-separated string
    skills_raw = frontmatter.get("skills", [])
    if isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(",")]
    else:
        skills = skills_raw if isinstance(skills_raw, list) else []

    # Parse rules: list of dicts
    rules = frontmatter.get("rules", [])
    if not isinstance(rules, list):
        rules = []

    # System prompt: frontmatter override or body content
    system_prompt = frontmatter.get("system_prompt", "")
    if not system_prompt and body:
        system_prompt = body

    return AgentDefinition(
        name=name,
        description=frontmatter.get("description", ""),
        model=frontmatter.get("model", ""),
        provider=frontmatter.get("provider", ""),
        base_url=frontmatter.get("base_url", ""),
        system_prompt=system_prompt,
        permission_mode=frontmatter.get("permission_mode", "all"),
        tools=tools,
        skills=skills,
        rules=rules,
        source=source,
        source_path=str(path),
    )


class AgentDiscovery:
    """Discovers agent definitions from global and project-level directories.

    Project-level agents shadow (override) global agents with the same name.
    This allows project-specific customizations without modifying global defaults.
    """

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root) if project_root else None
        self._cache: dict[str, AgentDefinition] | None = None

    def discover(self, force_reload: bool = False) -> dict[str, AgentDefinition]:
        """Discover all agent definitions, with project-level shadowing.

        Returns a dict mapping agent name -> AgentDefinition.
        Project-level agents override global ones with the same name.
        """
        if self._cache is not None and not force_reload:
            return self._cache

        agents: dict[str, AgentDefinition] = {}

        # 1. Load global agents from ~/.sawyer-harness/user/agents/
        global_dir = UserData.agents_dir
        if global_dir.exists():
            for md_file in global_dir.glob("*.md"):
                agent = _parse_agent_md(md_file, source="global")
                if agent and agent.name:
                    agents[agent.name] = agent
                    logger.debug(f"Loaded global agent: {agent.name}")

        # 2. Load project-level agents from <project>/.sawyer/agents/
        #    These shadow global agents with the same name
        if self.project_root:
            project_agents_dir = self.project_root / ".sawyer" / "agents"
            if project_agents_dir.exists():
                for md_file in project_agents_dir.glob("*.md"):
                    agent = _parse_agent_md(md_file, source="project")
                    if agent and agent.name:
                        agents[agent.name] = agent
                        logger.debug(f"Loaded project agent: {agent.name} (shadows global if any)")

        self._cache = agents
        return agents

    def get(self, name: str) -> AgentDefinition | None:
        """Get a specific agent definition by name."""
        agents = self.discover()
        return agents.get(name)

    def list_names(self) -> list[str]:
        """List all discovered agent names."""
        agents = self.discover()
        return sorted(agents.keys())

    def invalidate_cache(self):
        """Clear the cache so next discover() re-reads from disk."""
        self._cache = None