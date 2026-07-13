"""
Agent Rules Engine -- custom rules that supersede runtime defaults.

Rules are persisted in rules.yaml alongside capabilities.yaml.
They have priority levels (P0-P3) that determine injection order:
  P0 (critical) -> P1 (high) -> P2 (medium) -> P3 (low)

Rules supersede the behavior_rules from capabilities.yaml because
they're loaded AFTER and injected into the system prompt explicitly.
User-created rules always win over defaults.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("sawyer-harness.rules")


class RulePriority(str, Enum):
    P0 = "P0"  # Critical -- always enforced
    P1 = "P1"  # High -- strong preference
    P2 = "P2"  # Medium -- default level
    P3 = "P3"  # Low -- soft guidance


class RuleScope(str, Enum):
    GLOBAL = "global"        # Applies to all agents
    AGENT = "agent"          # Applies to a specific agent template
    SESSION = "session"      # Applies to current session only
    CRON = "cron"            # Applies to cron job runs only


@dataclass
class AgentRule:
    """A single agent behavior rule."""
    id: str
    name: str
    rule: str
    detail: str = ""
    priority: RulePriority = RulePriority.P2
    scope: RuleScope = RuleScope.GLOBAL
    agent: str = ""          # Target agent template name (when scope=AGENT)
    enabled: bool = True
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "rule": self.rule,
            "detail": self.detail,
            "priority": self.priority.value,
            "scope": self.scope.value,
            "agent": self.agent,
            "enabled": self.enabled,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentRule:
        data = dict(data)
        if "priority" in data and isinstance(data["priority"], str):
            data["priority"] = RulePriority(data["priority"])
        if "scope" in data and isinstance(data["scope"], str):
            data["scope"] = RuleScope(data["scope"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class RulesStore:
    """
    Persistent storage and management for agent rules.

    Rules are stored in rules.yaml. They're loaded on startup and
    whenever a reload is requested. The store supports CRUD operations
    and filtering by scope/agent.
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path.home() / ".sawyer-harness" / "rules.yaml"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rules: dict[str, AgentRule] = {}
        self._load()

    def _load(self):
        """Load rules from YAML file."""
        if not self.path.exists():
            self._rules = {}
            return

        try:
            raw = self.path.read_text()
            data = yaml.safe_load(raw) or {}
            rules_list = data.get("rules", [])
            self._rules = {}
            for r in rules_list:
                if isinstance(r, dict):
                    rule = AgentRule.from_dict(r)
                    self._rules[rule.id] = rule
            logger.info(f"Loaded {len(self._rules)} agent rules from {self.path}")
        except Exception as e:
            logger.error(f"Failed to load rules: {e}")
            self._rules = {}

    def _save(self):
        """Persist rules to YAML file."""
        rules_list = [r.to_dict() for r in sorted(
            self._rules.values(),
            key=lambda r: (r.priority.value, r.name),
        )]
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "rules": rules_list,
        }
        self.path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        logger.info(f"Saved {len(rules_list)} rules to {self.path}")

    def add_rule(
        self,
        name: str,
        rule: str,
        detail: str = "",
        priority: RulePriority | str = RulePriority.P2,
        scope: RuleScope | str = RuleScope.GLOBAL,
        agent: str = "",
        enabled: bool = True,
    ) -> AgentRule:
        """Create a new agent rule."""
        if isinstance(priority, str):
            priority = RulePriority(priority)
        if isinstance(scope, str):
            scope = RuleScope(scope)

        rule_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        ar = AgentRule(
            id=rule_id,
            name=name,
            rule=rule,
            detail=detail,
            priority=priority,
            scope=scope,
            agent=agent,
            enabled=enabled,
            created=now,
            updated=now,
        )
        self._rules[rule_id] = ar
        self._save()
        return ar

    def get_rule(self, rule_id: str) -> AgentRule | None:
        return self._rules.get(rule_id)

    def update_rule(self, rule_id: str, **updates) -> AgentRule | None:
        """Update fields on an existing rule."""
        rule = self._rules.get(rule_id)
        if not rule:
            return None

        for key, value in updates.items():
            if key == "priority" and isinstance(value, str):
                value = RulePriority(value)
            if key == "scope" and isinstance(value, str):
                value = RuleScope(value)
            if hasattr(rule, key):
                setattr(rule, key, value)

        rule.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        if rule_id not in self._rules:
            return False
        del self._rules[rule_id]
        self._save()
        return True

    def list_rules(
        self,
        scope: RuleScope | str | None = None,
        agent: str | None = None,
        enabled_only: bool = False,
    ) -> list[AgentRule]:
        """List rules, optionally filtered by scope and/or agent."""
        if isinstance(scope, str) and scope:
            scope = RuleScope(scope)

        results = []
        for rule in self._rules.values():
            if enabled_only and not rule.enabled:
                continue
            if scope and rule.scope != scope:
                continue
            if agent and rule.scope == RuleScope.AGENT and rule.agent != agent:
                continue
            results.append(rule)

        results.sort(key=lambda r: (r.priority.value, r.name))
        return results

    def get_rules_prompt(
        self,
        scope: RuleScope | str | None = None,
        agent: str | None = None,
    ) -> str:
        """
        Format rules for injection into the system prompt.

        Rules are sorted by priority (P0 first) and formatted as:
        - [P0] Rule text
          Detail text (if any)

        This output is designed to be appended after capabilities.yaml
        rules, so user-created rules supersede defaults.
        """
        rules = self.list_rules(scope=scope, agent=agent, enabled_only=True)
        if not rules:
            return ""

        lines = ["\n## Custom Agent Rules\n",
                 "These rules supersede all default behavior rules above.",
                 ""]

        for r in rules:
            lines.append(f"- [{r.priority.value}] {r.rule}")
            if r.detail:
                lines.append(f"  {r.detail}")

        lines.append("")
        return "\n".join(lines)

    def reload(self):
        """Reload rules from disk."""
        self._load()

    def count(self) -> int:
        return len(self._rules)