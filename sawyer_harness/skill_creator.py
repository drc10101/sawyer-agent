"""
Skill Creator -- interactive skill design session between agent and user.

The SkillCreator is what makes Sawyer genuinely self-improving. Instead of
a static skill editor, it runs a collaborative session where:

1. OBSERVE -- The agent watches what the user is doing and identifies
   repetitive patterns, friction points, and tasks that could be automated.

2. THEORIZE -- The agent proposes a skill: name, triggers, description,
   step-by-step procedure. This is a DRAFT, not a commitment.

3. REFINE -- The user and agent iterate on the spec. The user can edit
   any part, add constraints, change the approach. The agent adapts.

4. APPROVE -- When both are satisfied, the user approves the skill.
   Only then does it get created.

5. LEARN -- After the skill is used, the agent can propose patches
   based on what worked and what didn't.

This is NOT a form. It's a conversation that produces a skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .skills import Skill, SkillStore


class SessionPhase(str, Enum):
    """Phases of a skill creation session."""
    OBSERVE = "observe"       # Agent is watching, identifying patterns
    THEORIZE = "theorize"     # Agent proposes a skill draft
    REFINE = "refine"         # User and agent iterate on the spec
    APPROVE = "approve"       # User approves, skill gets created
    LEARN = "learn"          # Skill is in use, agent patches it


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass
class SkillSpec:
    """A skill specification being designed collaboratively."""
    name: str = ""
    category: str = "general"
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    procedure: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class SkillCreationSession:
    """A collaborative session for creating a skill."""
    id: str
    phase: SessionPhase = SessionPhase.OBSERVE
    status: SessionStatus = SessionStatus.ACTIVE
    spec: SkillSpec = field(default_factory=SkillSpec)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    messages: list[dict] = field(default_factory=list)
    observation_notes: list[str] = field(default_factory=list)
    revision_count: int = 0


class SkillCreator:
    """
    Interactive skill creation engine.

    Runs a collaborative session between agent and user to design,
    refine, and create skills. The agent theorizes, the user approves.
    """

    # Patterns that suggest a skill opportunity
    REPETITION_SIGNALS = [
        (r"again|another|more of|repeat|like last time", "repeat_task"),
        (r"always|every time|whenever|each time", "routine_task"),
        (r"how do i|how to|what's the way to|can you show me", "learning_task"),
        (r"i keep (?:having to|needing to)|i always have to", "friction_task"),
        (r"this is (?:annoying|tedious|repetitive|slow)", "pain_point"),
        (r"let's automate|let's make this easier|can we streamline", "automation_request"),
    ]

    # Skill category suggestions based on content
    CATEGORY_HINTS = {
        "deploy|ship|release|promote": "devops",
        "debug|error|fix|trace|stack": "debugging",
        "test|spec|assert|coverage|tdd": "testing",
        "security|auth|encrypt|permission|access": "security",
        "design|ui|ux|css|layout|component": "design",
        "data|query|database|sql|migration": "data",
        "api|endpoint|request|response|rest": "api",
        "write|create|generate|draft|compose": "creative",
        "review|check|audit|validate|lint": "quality",
        "plan|organize|schedule|track|manage": "planning",
    }

    def __init__(self, skill_store: SkillStore):
        self.skill_store = skill_store
        self.sessions: dict[str, SkillCreationSession] = {}

    def create_session(self) -> SkillCreationSession:
        """Start a new skill creation session."""
        import uuid
        session = SkillCreationSession(
            id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4],
        )
        self.sessions[session.id] = session
        return session

    def observe_message(self, session_id: str, role: str, content: str) -> list[str]:
        """
        Observe a user message for skill opportunities.
        Returns a list of signals detected (empty if no opportunity).
        """
        session = self.sessions.get(session_id)
        if not session:
            return []

        session.messages.append({"role": role, "content": content})
        session.updated_at = datetime.now(timezone.utc).isoformat()

        signals = []
        content_lower = content.lower()

        for pattern, signal_type in self.REPETITION_SIGNALS:
            if re.search(pattern, content_lower):
                signals.append(signal_type)
                session.observation_notes.append(
                    f"[{signal_type}] Detected in: {content[:100]}"
                )

        return signals

    def theorize(
        self,
        session_id: str,
        task_description: str,
        context: str = "",
    ) -> SkillSpec:
        """
        Generate a skill spec draft based on observed behavior and task description.

        This is the agent's proposal -- NOT a commitment. The user reviews,
        edits, and approves before anything is created.
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.phase = SessionPhase.THEORIZE

        # Generate skill name from task description
        name = self._generate_name(task_description)

        # Infer category
        category = self._infer_category(task_description + " " + context)

        # Generate description
        description = self._generate_description(task_description, context)

        # Generate triggers from the task
        triggers = self._generate_triggers(task_description, category)

        # Generate procedure steps
        procedure = self._generate_procedure(task_description, context)

        # Generate pitfalls
        pitfalls = self._generate_pitfalls(task_description, category)

        # Build the spec
        spec = SkillSpec(
            name=name,
            category=category,
            description=description,
            triggers=triggers,
            procedure=procedure,
            pitfalls=pitfalls,
            notes=f"Theorized from task: {task_description}",
        )

        session.spec = spec
        session.updated_at = datetime.now(timezone.utc).isoformat()

        return spec

    def refine(
        self,
        session_id: str,
        changes: dict,
    ) -> SkillSpec:
        """
        Apply user refinements to the spec.
        User can change any field: name, category, description, triggers,
        procedure steps, pitfalls, constraints.
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.phase = SessionPhase.REFINE

        spec = session.spec

        if "name" in changes and changes["name"]:
            spec.name = changes["name"]
        if "category" in changes and changes["category"]:
            spec.category = changes["category"]
        if "description" in changes and changes["description"]:
            spec.description = changes["description"]
        if "triggers" in changes:
            spec.triggers = changes["triggers"]
        if "procedure" in changes:
            spec.procedure = changes["procedure"]
        if "pitfalls" in changes:
            spec.pitfalls = changes["pitfalls"]
        if "constraints" in changes:
            spec.constraints = changes["constraints"]
        if "examples" in changes:
            spec.examples = changes["examples"]
        if "notes" in changes and changes["notes"]:
            spec.notes = changes["notes"]

        session.revision_count += 1
        session.updated_at = datetime.now(timezone.utc).isoformat()

        return spec

    def approve(self, session_id: str) -> Skill:
        """
        User approves the spec. Create the skill and save it.

        Only called when the user explicitly says "yes, create it."
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if session.phase not in (SessionPhase.THEORIZE, SessionPhase.REFINE):
            raise ValueError(
                f"Cannot approve from phase {session.phase.value}. "
                f"Must be in theorize or refine phase."
            )

        spec = session.spec

        # Build the skill content from the spec
        content = self._spec_to_markdown(spec)

        # Create the skill
        skill = Skill(
            name=spec.name,
            category=spec.category,
            description=spec.description,
            triggers=spec.triggers,
            content=content,
            version="1",
        )

        # Save to skill store
        self.skill_store.add_or_update(skill)

        # Mark session complete
        session.phase = SessionPhase.APPROVE
        session.status = SessionStatus.COMPLETED
        session.updated_at = datetime.now(timezone.utc).isoformat()

        return skill

    def reject(self, session_id: str) -> None:
        """User rejects the spec. Mark session as abandoned."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = SessionStatus.ABANDONED
        session.updated_at = datetime.now(timezone.utc).isoformat()

    def get_session(self, session_id: str) -> SkillCreationSession | None:
        """Get a session by ID."""
        return self.sessions.get(session_id)

    def list_sessions(
        self,
        status: SessionStatus | None = None,
    ) -> list[SkillCreationSession]:
        """List sessions, optionally filtered by status."""
        sessions = list(self.sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def suggest_skill_creation(
        self,
        recent_messages: list[dict],
    ) -> dict | None:
        """
        Analyze recent messages and suggest whether a skill creation
        session would be valuable.

        Returns None if no opportunity is detected, or a dict with:
        - signal_type: what pattern was detected
        - suggested_task: what the skill should address
        - confidence: 0.0 to 1.0
        """
        if len(recent_messages) < 2:
            return None

        # Check for repetition signals
        all_signals = []
        for msg in recent_messages:
            content = msg.get("content", "")
            if msg.get("role") != "user":
                continue
            for pattern, signal_type in self.REPETITION_SIGNALS:
                if re.search(pattern, content.lower()):
                    all_signals.append((signal_type, content))

        if not all_signals:
            return None

        # Get the most frequent signal type
        signal_counts: dict[str, int] = {}
        for signal_type, _ in all_signals:
            signal_counts[signal_type] = signal_counts.get(signal_type, 0) + 1

        top_signal = max(signal_counts, key=lambda k: signal_counts[k])
        confidence = min(signal_counts[top_signal] / len(recent_messages), 1.0)

        # Only suggest if confidence is high enough
        if confidence < 0.2:
            return None

        # Extract a task description from the last relevant message
        relevant_content = [
            content for signal_type, content in all_signals
            if signal_type == top_signal
        ]
        suggested_task = relevant_content[-1] if relevant_content else ""

        return {
            "signal_type": top_signal,
            "suggested_task": suggested_task,
            "confidence": round(confidence, 2),
        }

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _generate_name(self, task: str) -> str:
        """Generate a concise skill name from a task description."""
        # Take the first meaningful phrase, lowercase, hyphenated
        words = re.sub(r"[^a-zA-Z0-9\s]", "", task.lower()).split()
        # Skip common filler words
        skip = {"a", "an", "the", "i", "me", "my", "we", "our", "can",
                "could", "should", "would", "do", "does", "did", "is",
                "are", "was", "were", "be", "been", "being", "have",
                "has", "had", "will", "shall", "may", "might", "must",
                "need", "to", "of", "in", "for", "on", "with", "at",
                "by", "from", "up", "about", "into", "through"}
        meaningful = [w for w in words[:6] if w not in skip]
        if not meaningful:
            meaningful = words[:3]
        return "-".join(meaningful)

    def _infer_category(self, text: str) -> str:
        """Infer a skill category from content."""
        text_lower = text.lower()
        for pattern, category in self.CATEGORY_HINTS.items():
            if re.search(pattern, text_lower):
                return category
        return "general"

    def _generate_description(self, task: str, context: str) -> str:
        """Generate a skill description."""
        desc = task.strip()
        if context:
            desc += f" Context: {context.strip()}"
        # Keep it concise
        if len(desc) > 200:
            desc = desc[:197] + "..."
        return desc

    def _generate_triggers(self, task: str, category: str) -> list[str]:
        """Generate trigger words for the skill."""
        # Extract key nouns/verbs from the task
        words = re.sub(r"[^a-zA-Z0-9\s]", "", task.lower()).split()
        skip = {"a", "an", "the", "i", "me", "my", "we", "our", "can",
                "could", "should", "would", "do", "does", "did", "is",
                "are", "was", "were", "be", "been", "being", "have",
                "has", "had", "will", "shall", "may", "might", "must",
                "need", "to", "of", "in", "for", "on", "with", "at",
                "by", "from", "up", "about", "into", "through", "and",
                "or", "but", "not", "this", "that", "it"}
        triggers = [w for w in words if w not in skip and len(w) > 2][:5]
        # Always include the category
        if category != "general" and category not in triggers:
            triggers.append(category)
        return triggers if triggers else ["task"]

    def _generate_procedure(self, task: str, context: str) -> list[str]:
        """Generate a procedure outline for the skill."""
        # Parse task for action verbs and objects
        steps = []

        # Common patterns
        if "deploy" in task.lower():
            steps = [
                "Verify all tests pass before proceeding",
                "Build the project artifact",
                "Run deployment script with target environment",
                "Verify deployment health check endpoint",
                "Confirm rollback plan is documented",
            ]
        elif "debug" in task.lower() or "error" in task.lower():
            steps = [
                "Reproduce the error with minimal steps",
                "Identify the root cause (not just the symptom)",
                "Write a failing test that demonstrates the bug",
                "Fix the root cause",
                "Verify the fix resolves the failing test",
                "Check for similar issues elsewhere",
            ]
        elif "test" in task.lower():
            steps = [
                "Identify what needs testing",
                "Write the test case covering the happy path",
                "Add edge case and error condition tests",
                "Run the test suite to verify",
                "Check coverage report for gaps",
            ]
        elif "create" in task.lower() or "build" in task.lower() or "make" in task.lower():
            steps = [
                "Clarify requirements and constraints",
                "Plan the structure before writing code",
                "Implement the core logic first",
                "Add error handling and edge cases",
                "Test the implementation",
                "Document the result",
            ]
        else:
            steps = [
                "Understand the goal and constraints",
                "Plan the approach",
                "Execute the plan step by step",
                "Verify the result matches expectations",
                "Document what was done and any decisions made",
            ]

        return steps

    def _generate_pitfalls(self, task: str, category: str) -> list[str]:
        """Generate common pitfalls for the skill."""
        pitfalls = [
            "Don't skip verification steps",
            "Don't assume the first approach is correct without testing",
        ]

        category_pitfalls = {
            "devops": [
                "Never deploy without a rollback plan",
                "Always verify in a staging environment first",
            ],
            "debugging": [
                "Don't fix symptoms -- find the root cause",
                "Don't assume the error message tells the whole story",
            ],
            "testing": [
                "Don't test only the happy path",
                "Mock external dependencies, don't call real APIs in tests",
            ],
            "security": [
                "Never hardcode secrets or credentials",
                "Always validate and sanitize user input",
            ],
            "data": [
                "Back up data before any migration",
                "Validate data integrity after transformations",
            ],
            "api": [
                "Handle rate limits and retries gracefully",
                "Never expose internal errors to clients",
            ],
        }

        if category in category_pitfalls:
            pitfalls.extend(category_pitfalls[category])

        return pitfalls

    def _spec_to_markdown(self, spec: SkillSpec) -> str:
        """Convert a SkillSpec to Markdown content for a Skill."""
        lines = [f"# {spec.name}", ""]

        lines.append(spec.description)
        lines.append("")

        if spec.triggers:
            lines.append("## Triggers")
            lines.append("")
            for trigger in spec.triggers:
                lines.append(f"- {trigger}")
            lines.append("")

        if spec.procedure:
            lines.append("## Procedure")
            lines.append("")
            for i, step in enumerate(spec.procedure, 1):
                lines.append(f"{i}. {step}")
            lines.append("")

        if spec.pitfalls:
            lines.append("## Pitfalls")
            lines.append("")
            for pitfall in spec.pitfalls:
                lines.append(f"- {pitfall}")
            lines.append("")

        if spec.constraints:
            lines.append("## Constraints")
            lines.append("")
            for constraint in spec.constraints:
                lines.append(f"- {constraint}")
            lines.append("")

        if spec.examples:
            lines.append("## Examples")
            lines.append("")
            for example in spec.examples:
                lines.append(f"- {example}")
            lines.append("")

        if spec.notes:
            lines.append("## Notes")
            lines.append("")
            lines.append(spec.notes)
            lines.append("")

        return "\n".join(lines)