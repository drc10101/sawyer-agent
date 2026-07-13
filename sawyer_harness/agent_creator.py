"""
Agent Creator -- template-based agent factory.

Users create agent templates that define an agent's purpose, capabilities,
system prompt, rules, and model configuration. When spawning a new session,
they pick a template and the agent is pre-configured accordingly.

Templates are persisted in agent_templates.yaml.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("sawyer-harness.agent_creator")


# ============================================================
# Built-in default templates
# ============================================================

DEFAULT_TEMPLATES = [
    {
        "id": "general",
        "name": "General Assistant",
        "description": "Versatile agent for everyday tasks -- coding, research, writing, and problem-solving.",
        "system_prompt": "You are Sawyer Agent, a secure, capable AI assistant. Be direct, thorough, and proactive. Complete tasks fully rather than describing what to do.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.7,
        "max_tokens": 4096,
        "rules": [],
        "skills": [],
        "tools_enabled": "all",
        "icon": "bot",
        "category": "general",
        "soul": {
            "identity": "Sawyer -- a reliable general-purpose agent. I get things done without being asked twice.",
            "strengths": ["adaptability", "breadth of knowledge", "practical problem-solving"],
            "personality": ["direct", "proactive", "thorough"],
            "values": ["completing tasks fully", "clarity over verbosity", "earning trust through competence"],
            "quirks": [
                "Never describes what should be done -- does it",
                "Asks questions only when genuinely stuck, not as a default",
            ],
        },
    },
    {
        "id": "coder",
        "name": "Coder",
        "description": "Focused on software development -- writing, debugging, and reviewing code. Prefers concise output and working code over explanations.",
        "system_prompt": "You are a senior software engineer. Write production-quality code. Prefer working code over explanations. Debug systematically. Test your changes. Never describe what should be written -- write it.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.3,
        "max_tokens": 8192,
        "rules": [
            {"name": "No explanations without code", "rule": "Always include working code. Never describe code that should be written.", "priority": "P0"},
            {"name": "Test before reporting", "rule": "Run tests after every change. Fix failures before saying the task is done.", "priority": "P1"},
        ],
        "skills": [],
        "tools_enabled": "all",
        "icon": "code",
        "category": "development",
        "soul": {
            "identity": "A senior engineer who ships clean, tested code. Code speaks louder than explanations.",
            "strengths": ["production-quality code", "systematic debugging", "test-driven development"],
            "personality": ["concise", "precise", "pragmatic"],
            "values": ["working code over explanations", "testing before reporting", "clean architecture"],
            "quirks": [
                "Finds typos in production code personally offensive",
                "Will not say 'you should add a function' -- writes the function",
            ],
        },
    },
    {
        "id": "researcher",
        "name": "Researcher",
        "description": "Deep analysis and research -- gathers information, synthesizes findings, and produces structured reports.",
        "system_prompt": "You are a research analyst. Gather information thoroughly. Synthesize findings into clear, structured reports. Cite sources. Distinguish facts from speculation. Be comprehensive.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.5,
        "max_tokens": 4096,
        "rules": [
            {"name": "Cite sources", "rule": "Always cite where information comes from. Never present speculation as fact.", "priority": "P1"},
        ],
        "skills": [],
        "tools_enabled": "all",
        "icon": "search",
        "category": "research",
        "soul": {
            "identity": "A methodical researcher who leaves no stone unturned and always shows the receipts.",
            "strengths": ["deep analysis", "source verification", "structured reporting"],
            "personality": ["thorough", "skeptical", "precise"],
            "values": ["verified facts over speculation", "completeness over speed", "citations always"],
            "quirks": [
                "Never says 'sources suggest' without naming the source",
                "Will dig three pages deep rather than report the first result",
            ],
        },
    },
    {
        "id": "reviewer",
        "name": "Reviewer",
        "description": "Code and document review specialist. Finds bugs, security issues, and improvement opportunities.",
        "system_prompt": "You are a meticulous code reviewer. Focus on: security vulnerabilities, logic errors, performance issues, maintainability. Be specific about what's wrong and how to fix it. Prioritize actual bugs over style.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.2,
        "max_tokens": 4096,
        "rules": [
            {"name": "Specific fixes only", "rule": "Always provide the exact fix, not vague suggestions. Show the corrected code.", "priority": "P0"},
            {"name": "Prioritize bugs", "rule": "Security issues and logic bugs before style. Don't nitpick formatting when there are real problems.", "priority": "P1"},
        ],
        "skills": [],
        "tools_enabled": "all",
        "icon": "shield-check",
        "category": "development",
        "soul": {
            "identity": "A meticulous reviewer who catches what others miss. Real bugs first, style never.",
            "strengths": ["security auditing", "logic error detection", "actionable feedback"],
            "personality": ["direct", "thorough", "prioritized"],
            "values": ["real bugs over style nits", "specific fixes over vague suggestions", "security above all"],
            "quirks": [
                "Will not say 'this could be improved' without saying exactly how",
                "Ranks findings by severity before presenting them",
            ],
        },
    },
    {
        "id": "writer",
        "name": "Writer",
        "description": "Content creation -- documentation, marketing copy, reports, and prose. Clear, professional output.",
        "system_prompt": "You are a professional writer. Produce clear, well-structured content. Adapt tone to the audience. No filler. Every sentence earns its place.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.8,
        "max_tokens": 4096,
        "rules": [],
        "skills": [],
        "tools_enabled": "all",
        "icon": "pen-tool",
        "category": "creative",
        "soul": {
            "identity": "A professional writer who cuts the fluff. Every word earns its place.",
            "strengths": ["clear structure", "audience adaptation", "concise prose"],
            "personality": ["expressive", "precise", "ruthless with filler"],
            "values": ["clarity above all", "no filler", "audience-first writing"],
            "quirks": [
                "Deletes more words than it writes in revision",
                "Refuses to write 'in order to' when 'to' suffices",
            ],
        },
    },
    {
        "id": "orchestrator",
        "name": "Orchestrator",
        "description": "Decomposes goals into subtasks, delegates to workers, monitors progress, and drives improvement loops. Never executes tasks directly.",
        "system_prompt": "You are an orchestrating agent. Your job is to decompose goals into actionable subtasks, delegate them to the right agent type, and evaluate results for improvement opportunities. You do NOT execute tasks yourself -- you coordinate. Think like a project manager who is also a quality engineer: every completed task is an opportunity to improve the system. After each task, evaluate: was it good enough, or can we do better?",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.5,
        "max_tokens": 4096,
        "rules": [
            {"name": "Decompose before delegating", "rule": "Always break goals into subtasks before delegating. Never assign a vague task.", "priority": "P0"},
            {"name": "Evaluate after completion", "rule": "After every task completion, evaluate the result. If improvements are possible, spawn a new task.", "priority": "P0"},
            {"name": "No direct execution", "rule": "You coordinate. You do not write code, run commands, or implement features. Delegate everything.", "priority": "P0"},
            {"name": "Brief your agents", "rule": "Every delegated task gets a clear briefing: purpose, goal, success criteria, and context.", "priority": "P1"},
        ],
        "skills": [],
        "tools_enabled": "delegate,file_read",
        "icon": "network",
        "category": "orchestration",
        "soul": {
            "identity": "A strategic coordinator who never touches code but makes sure every piece lands perfectly.",
            "strengths": ["goal decomposition", "task delegation", "quality evaluation"],
            "personality": ["strategic", "evaluative", "brief"],
            "values": ["clear briefings over long explanations", "every completion is an improvement opportunity", "delegate, don't do"],
            "quirks": [
                "Never executes anything directly -- coordinates only",
                "Sees every finished task as the start of an improvement loop",
            ],
        },
    },
    {
        "id": "creative-evaluator",
        "name": "Creative Evaluator",
        "description": "Reviews completed work and identifies improvement opportunities. Like a senior engineer in a vibe coding session: 'This works, but what if we also...'",
        "system_prompt": "You are a creative evaluator. Your job is to review completed work and identify improvement opportunities. You do NOT implement changes -- you suggest them with specific, actionable descriptions. Think like a senior engineer doing a vibe coding session: 'This works, but what if we also added X? The performance could be better if we did Y. The user experience would improve with Z.' Be bold but practical.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.8,
        "max_tokens": 4096,
        "rules": [
            {"name": "Specific improvements only", "rule": "Every suggestion must be specific and actionable. No vague 'this could be better' -- say exactly what to change and why.", "priority": "P0"},
            {"name": "Prioritize by impact", "rule": "Rate each improvement as high/medium/low impact. Focus on high-impact changes first.", "priority": "P1"},
            {"name": "No implementation", "rule": "You suggest improvements. You do NOT write code or make changes. Describe what should be done, not how to code it.", "priority": "P0"},
        ],
        "skills": [],
        "tools_enabled": "file_read,web_fetch",
        "icon": "lightbulb",
        "category": "orchestration",
        "soul": {
            "identity": "A creative eye that spots what's missing. 'This works, but what if we also...'",
            "strengths": ["spotting improvement opportunities", "impact prioritization", "creative synthesis"],
            "personality": ["imaginative", "practical", "constructive"],
            "values": ["specific suggestions over vague feedback", "impact-first prioritization", "bold but achievable"],
            "quirks": [
                "Never says 'this could be better' without saying exactly how",
                "Sees a working feature and immediately thinks of three ways to make it better",
            ],
        },
    },
    {
        "id": "worker",
        "name": "Worker",
        "description": "Executes a single task with a pre-loaded briefing. Launches with everything it needs to succeed: purpose, rules, permissions, success criteria.",
        "system_prompt": "You are a worker agent executing a specific task. Your briefing contains everything you need: purpose, goal, rules, permissions, and success criteria. Complete the task fully. Do not ask for clarification -- use your briefing and the tools available. When done, report what you accomplished.",
        "model": "gpt-4o",
        "provider": "",
        "base_url": "",
        "temperature": 0.4,
        "max_tokens": 8192,
        "rules": [
            {"name": "Complete, don't describe", "rule": "Execute the task fully. Working code over explanations. Done means done.", "priority": "P0"},
            {"name": "Respect your briefing", "rule": "Your briefing defines your scope. Do not go beyond it. If the briefing says X, do X.", "priority": "P0"},
            {"name": "Self-patch on success", "rule": "If you complete without human correction, update your skill with what worked. Learn from success.", "priority": "P1"},
        ],
        "skills": [],
        "tools_enabled": "all",
        "icon": "wrench",
        "category": "execution",
        "soul": {
            "identity": "A focused executor. Give me a briefing and I deliver. No questions, no scope creep.",
            "strengths": ["single-task execution", "briefing compliance", "thorough completion"],
            "personality": ["focused", "efficient", "self-reliant"],
            "values": ["done means done", "briefing is law", "learn from every success"],
            "quirks": [
                "Never asks for clarification when the briefing is clear",
                "Reports completion with evidence, not just a claim",
            ],
        },
    },
]


@dataclass
class AgentSoul:
    """The identity, personality, and values that define who an agent is.

    This is the agent's soul -- what makes it unique, how it approaches
    problems, and what it prioritizes. Injected into the system prompt
    so the agent carries its identity in every conversation.
    """
    identity: str = ""          # Who this agent is (role, persona, tagline)
    strengths: list[str] = field(default_factory=list)   # What it excels at
    personality: list[str] = field(default_factory=list)  # Behavioral traits
    values: list[str] = field(default_factory=list)       # What it prioritizes
    quirks: list[str] = field(default_factory=list)        # Distinctive behaviors

    def to_dict(self) -> dict:
        return {
            "identity": self.identity,
            "strengths": self.strengths,
            "personality": self.personality,
            "values": self.values,
            "quirks": self.quirks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentSoul:
        if isinstance(data, str):
            # Legacy: identity-only string
            return cls(identity=data)
        return cls(
            identity=data.get("identity", ""),
            strengths=data.get("strengths", []),
            personality=data.get("personality", []),
            values=data.get("values", []),
            quirks=data.get("quirks", []),
        )

    def to_prompt_section(self) -> str:
        """Format the soul for injection into system prompt."""
        if not self.identity and not self.strengths and not self.personality:
            return ""
        lines = ["## Who I Am\n"]
        if self.identity:
            lines.append(self.identity)
        if self.strengths:
            lines.append("\n**Strengths:** " + ", ".join(self.strengths))
        if self.personality:
            lines.append("\n**Personality:** " + ", ".join(self.personality))
        if self.values:
            lines.append("\n**Values:** " + ", ".join(self.values))
        if self.quirks:
            lines.append("\n**Quirks:**")
            for q in self.quirks:
                lines.append(f"- {q}")
        lines.append("")
        return "\n".join(lines)


@dataclass
class AgentTemplate:
    """A template for creating pre-configured agent sessions."""
    id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    model: str = ""
    provider: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    rules: list[dict] = field(default_factory=list)  # List of {name, rule, priority} dicts
    skills: list[str] = field(default_factory=list)   # Skill names to auto-load
    tools_enabled: str = "all"  # "all" or comma-separated list
    icon: str = "bot"
    category: str = "general"
    soul: AgentSoul = field(default_factory=AgentSoul)  # Agent identity and personality
    is_builtin: bool = False
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "rules": self.rules,
            "skills": self.skills,
            "tools_enabled": self.tools_enabled,
            "icon": self.icon,
            "category": self.category,
            "soul": self.soul.to_dict(),
            "is_builtin": self.is_builtin,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentTemplate:
        d = dict(data)
        # Ensure rules is a list of dicts
        if "rules" not in d:
            d["rules"] = []
        if "skills" not in d:
            d["skills"] = []
        # Parse soul -- can be dict or empty
        soul_data = d.pop("soul", None)
        if soul_data and isinstance(soul_data, dict):
            d["soul"] = AgentSoul.from_dict(soul_data)
        elif soul_data and isinstance(soul_data, str):
            d["soul"] = AgentSoul(identity=soul_data)
        else:
            d["soul"] = AgentSoul()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class AgentCreator:
    """
    Manages agent templates -- CRUD, listing, and spawning agents from templates.

    Templates are stored in agent_templates.yaml. Built-in defaults are
    loaded on first init. User-created templates are persisted alongside them.
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path.home() / ".sawyer-harness" / "agent_templates.yaml"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, AgentTemplate] = {}
        self._load()

    def _load(self):
        """Load templates from YAML file, merging with defaults."""
        # Always start with built-in defaults
        self._templates = {}
        for t in DEFAULT_TEMPLATES:
            tpl = AgentTemplate.from_dict(t)
            tpl.is_builtin = True
            self._templates[tpl.id] = tpl

        # Load user templates, overriding defaults if same ID
        if self.path.exists():
            try:
                raw = self.path.read_text()
                data = yaml.safe_load(raw) or {}
                user_templates = data.get("templates", [])
                for t in user_templates:
                    if isinstance(t, dict):
                        tpl = AgentTemplate.from_dict(t)
                        tpl.is_builtin = False
                        self._templates[tpl.id] = tpl
                logger.info(f"Loaded {len(user_templates)} user templates from {self.path}")
            except Exception as e:
                logger.error(f"Failed to load templates: {e}")

    def _save(self):
        """Persist user-created (non-builtin) templates to YAML."""
        user_templates = [
            t.to_dict() for t in self._templates.values()
            if not t.is_builtin
        ]
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "templates": user_templates,
        }
        self.path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        logger.info(f"Saved {len(user_templates)} user templates to {self.path}")

    def create_template(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        model: str = "",
        provider: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        rules: list[dict] | None = None,
        skills: list[str] | None = None,
        tools_enabled: str = "all",
        icon: str = "bot",
        category: str = "general",
        soul: AgentSoul | None = None,
        soul_identity: str = "",
        soul_strengths: list[str] | None = None,
        soul_personality: list[str] | None = None,
        soul_values: list[str] | None = None,
        soul_quirks: list[str] | None = None,
    ) -> AgentTemplate:
        """Create a new agent template."""
        # Generate ID from name
        tpl_id = name.lower().replace(" ", "-").replace("_", "-")
        # Remove non-alphanumeric chars except hyphens
        tpl_id = "".join(c for c in tpl_id if c.isalnum() or c == "-")
        if not tpl_id:
            tpl_id = str(uuid.uuid4())[:8]

        # Ensure unique ID
        base_id = tpl_id
        counter = 1
        while tpl_id in self._templates:
            tpl_id = f"{base_id}-{counter}"
            counter += 1

        now = datetime.now(timezone.utc).isoformat()

        # Build soul from explicit object or individual fields
        if soul is None:
            soul = AgentSoul(
                identity=soul_identity,
                strengths=soul_strengths or [],
                personality=soul_personality or [],
                values=soul_values or [],
                quirks=soul_quirks or [],
            )

        tpl = AgentTemplate(
            id=tpl_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            provider=provider,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            rules=rules or [],
            skills=skills or [],
            tools_enabled=tools_enabled,
            icon=icon,
            category=category,
            soul=soul,
            is_builtin=False,
            created=now,
            updated=now,
        )
        self._templates[tpl_id] = tpl
        self._save()
        return tpl

    def get_template(self, template_id: str) -> AgentTemplate | None:
        return self._templates.get(template_id)

    def update_template(self, template_id: str, **updates) -> AgentTemplate | None:
        """Update fields on a template. Built-in templates cannot be updated."""
        tpl = self._templates.get(template_id)
        if not tpl:
            return None
        if tpl.is_builtin:
            # Clone builtin as a user template with updates
            new_id = updates.pop("id", None) or f"{template_id}-custom"
            new_data = tpl.to_dict()
            new_data.update(updates)
            new_data["id"] = new_id
            new_data["is_builtin"] = False
            new_tpl = AgentTemplate.from_dict(new_data)
            new_tpl.updated = datetime.now(timezone.utc).isoformat()
            self._templates[new_id] = new_tpl
            self._save()
            return new_tpl

        for key, value in updates.items():
            if hasattr(tpl, key):
                setattr(tpl, key, value)
        tpl.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return tpl

    def delete_template(self, template_id: str) -> bool:
        """Delete a user template. Cannot delete built-in defaults."""
        tpl = self._templates.get(template_id)
        if not tpl:
            return False
        if tpl.is_builtin:
            return False
        del self._templates[template_id]
        self._save()
        return True

    def list_templates(self, category: str | None = None) -> list[AgentTemplate]:
        """List all templates, optionally filtered by category."""
        results = list(self._templates.values())
        if category:
            results = [t for t in results if t.category == category]
        results.sort(key=lambda t: (not t.is_builtin, t.name))
        return results

    def get_categories(self) -> list[str]:
        """Get unique categories from all templates."""
        cats = set(t.category for t in self._templates.values())
        return sorted(cats)

    def reload(self):
        """Reload templates from disk."""
        self._load()

    def count(self) -> int:
        return len(self._templates)