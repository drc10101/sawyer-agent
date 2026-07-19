"""
Session suggestion engine -- auto-generate notes, recommend next actions.

What Hermes lacks: when a session ends, it just ends. No notes, no
suggestions, no "here's what you should do next time." You wake up the
next session with zero context about what happened.

Sawyer's session engine:
1. Tracks conversation topics in real-time
2. Auto-generates session notes at session end
3. Suggests next-session actions based on unfinished work
4. Persists notes to the project's .sawyer/ directory
5. Loads previous session notes when a new session starts
6. Tracks files created/modified during the session
7. Extracts "todo" items from conversation

This is the continuity layer that makes multi-session work possible.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sawyer-harness.session_engine")


@dataclass
class SessionNote:
    """A note from a session -- a topic, decision, or action item."""
    note_type: str  # "topic", "decision", "todo", "correction", "file_created", "file_modified"
    content: str
    timestamp: str = ""
    priority: str = "normal"  # "critical", "high", "normal", "low"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class SessionSummary:
    """Complete summary of a session."""
    session_id: str
    started: str
    ended: str = ""
    duration_minutes: float = 0
    message_count: int = 0
    tool_call_count: int = 0
    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    goals_attempted: list[str] = field(default_factory=list)
    goals_completed: list[str] = field(default_factory=list)
    next_session_suggestions: list[str] = field(default_factory=list)
    full_notes: str = ""


class SessionEngine:
    """
    Tracks session state and generates notes/suggestions.

    The engine monitors conversation in real-time, extracting:
    - Topics being discussed
    - Decisions being made
    - Corrections from the user
    - Todo items mentioned
    - Files created or modified
    - Errors encountered

    At session end, it generates a comprehensive summary and suggests
    what to do in the next session.
    """

    # Patterns for extracting structured information
    TODO_PATTERNS = [
        r"(?:TODO|FIXME|HACK|XXX|NOTE)[:]\s*(.+)",
        r"(?:need to|needs to|should|must|have to|going to)\s+(.+?)[.\n]",
        r"(?:next|then|after that|once)\s+(?:we|I|you)\s+(.+?)[.\n]",
        r"(?:still|remaining|left to|pending|outstanding)\s+(.+?)[.\n]",
    ]

    DECISION_PATTERNS = [
        r"(?:let's|we'll|I'll|we should|decided to|going with|opted for)\s+(.+?)[.\n]",
        r"(?:the (?:plan|approach|solution|method|fix))\s+(?:is|will be|should be)\s+(.+?)[.\n]",
    ]

    CORRECTION_PATTERNS = [
        r"^(?:no,?\s+|actually,?\s+|wait,?\s+|that's wrong|wrong|incorrect)\s*(.+?)$",
        r"^(?:I meant|I meant to say|let me rephrase|let me clarify|to be clear)\s*(.+?)$",
        r"^(?:don't|never|stop|avoid)\s+(.+?)$",
    ]

    FILE_PATTERNS = [
        r"(?:created|wrote|saved|generated)\s+(?:to\s+)?[`\"']?([^\s`\"]+\.\w+)[`\"']?",
        r"(?:modified|updated|edited|patched|changed)\s+[`\"']?([^\s`\"]+\.\w+)[`\"']?",
        r"(?:file|path):\s*[`\"']?([^\s`\"]+\.\w+)[`\"']?",
    ]

    def __init__(self, project_dir: Path | None = None):
        """
        Args:
            project_dir: Root directory of the project. Session notes are
                        saved to project_dir/.sawyer/session-notes/
        """
        self.project_dir = project_dir
        self.notes: list[SessionNote] = []
        self.session_start = datetime.now(timezone.utc)
        self.message_count = 0
        self.tool_call_count = 0
        self.session_id = self.session_start.strftime("%Y%m%d_%H%M%S")

        # Ensure .sawyer directory exists
        if project_dir:
            self.sawyer_dir = project_dir / ".sawyer"
            self.notes_dir = self.sawyer_dir / "session-notes"
            self.notes_dir.mkdir(parents=True, exist_ok=True)

    def track_message(self, role: str, content: str):
        """Track a message in the conversation. Extracts notes automatically."""
        self.message_count += 1

        # Extract topics from user messages
        if role == "user":
            # First 100 chars as topic
            topic = content.strip()[:100].replace("\n", " ")
            if topic:
                self.notes.append(SessionNote(
                    note_type="topic",
                    content=topic,
                    priority="normal",
                ))

            # Extract corrections
            for pattern in self.CORRECTION_PATTERNS:
                match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
                if match:
                    self.notes.append(SessionNote(
                        note_type="correction",
                        content=content[:200],
                        priority="critical",
                    ))
                    break

            # Extract todo items
            for pattern in self.TODO_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    self.notes.append(SessionNote(
                        note_type="todo",
                        content=match.group(1).strip()[:200],
                        priority="high",
                    ))

        # Extract decisions from assistant messages
        if role == "assistant":
            for pattern in self.DECISION_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    self.notes.append(SessionNote(
                        note_type="decision",
                        content=match.group(1).strip()[:200],
                        priority="high",
                    ))

            # Extract file references
            for pattern in self.FILE_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    filepath = match.group(1)
                    self.notes.append(SessionNote(
                        note_type="file_created",
                        content=filepath,
                        priority="normal",
                    ))

        # Track tool calls
        if role == "tool":
            self.tool_call_count += 1

    def track_file(self, action: str, filepath: str):
        """Track a file being created or modified by the agent."""
        note_type = "file_created" if action == "create" else "file_modified"
        self.notes.append(SessionNote(
            note_type=note_type,
            content=filepath,
            priority="normal",
        ))

    def track_error(self, error: str):
        """Track an error encountered during the session."""
        self.notes.append(SessionNote(
            note_type="error",
            content=error[:200],
            priority="high",
        ))

    def track_skill_use(self, skill_name: str):
        """Track a skill being used."""
        self.notes.append(SessionNote(
            note_type="skill_use",
            content=skill_name,
            priority="low",
        ))

    def generate_summary(self) -> SessionSummary:
        """Generate a complete session summary from tracked notes."""
        now = datetime.now(timezone.utc)
        duration = (now - self.session_start).total_seconds() / 60

        # Deduplicate and organize notes
        topics = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "topic"
        ))[:10]  # Top 10 topics

        decisions = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "decision"
        ))

        corrections = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "correction"
        ))

        todos = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "todo"
        ))

        files_created = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "file_created"
        ))

        files_modified = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "file_modified"
        ))

        errors = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "error"
        ))

        skills_used = list(dict.fromkeys(
            n.content for n in self.notes if n.note_type == "skill_use"
        ))

        # Generate next-session suggestions
        suggestions = self._generate_suggestions(todos, errors, files_created, files_modified)

        # Build full notes markdown
        full_notes = self._build_notes_markdown(
            topics=topics,
            decisions=decisions,
            corrections=corrections,
            todos=todos,
            files_created=files_created,
            files_modified=files_modified,
            errors=errors,
            suggestions=suggestions,
        )

        return SessionSummary(
            session_id=self.session_id,
            started=self.session_start.isoformat(),
            ended=now.isoformat(),
            duration_minutes=round(duration, 1),
            message_count=self.message_count,
            tool_call_count=self.tool_call_count,
            topics=topics,
            decisions=decisions,
            corrections=corrections,
            todos=todos,
            files_created=files_created,
            files_modified=files_modified,
            errors=errors,
            skills_used=skills_used,
            next_session_suggestions=suggestions,
            full_notes=full_notes,
        )

    def _generate_suggestions(
        self,
        todos: list[str],
        errors: list[str],
        files_created: list[str],
        files_modified: list[str],
    ) -> list[str]:
        """Generate suggestions for what to do in the next session."""
        suggestions = []

        # Unfinished todos
        if todos:
            suggestions.append(f"Continue with unfinished tasks: {todos[0][:80]}")
            if len(todos) > 1:
                suggestions.append(f"Pending items: {len(todos)} remaining todos from this session")

        # Errors that might need fixing
        if errors:
            suggestions.append(f"Resolve errors: {errors[0][:80]}")

        # Files that were created but might need testing
        if files_created:
            suggestions.append(
                f"Test new files: {', '.join(files_created[:3])}"
                + (f" and {len(files_created) - 3} more" if len(files_created) > 3 else "")
            )

        # Files that were modified and might need review
        if files_modified:
            suggestions.append(f"Review changes: {', '.join(files_modified[:3])}")

        # Default suggestion if nothing specific
        if not suggestions:
            if files_created or files_modified:
                suggestions.append("Continue development -- review and test changes from this session")
            else:
                suggestions.append("Pick up where we left off -- check memory for context")

        return suggestions

    def _build_notes_markdown(
        self,
        topics: list[str],
        decisions: list[str],
        corrections: list[str],
        todos: list[str],
        files_created: list[str],
        files_modified: list[str],
        errors: list[str],
        suggestions: list[str],
    ) -> str:
        """Build a full markdown document of session notes."""
        lines = [
            f"# Session {self.session_id}",
            "",
            f"**Started:** {self.session_start.isoformat()}",
            f"**Messages:** {self.message_count} | **Tool calls:** {self.tool_call_count}",
            "",
        ]

        if topics:
            lines.append("## Topics")
            lines.append("")
            for t in topics[:10]:
                lines.append(f"- {t}")
            lines.append("")

        if decisions:
            lines.append("## Decisions")
            lines.append("")
            for d in decisions:
                lines.append(f"- {d}")
            lines.append("")

        if corrections:
            lines.append("## Corrections")
            lines.append("")
            for c in corrections:
                lines.append(f"- {c}")
            lines.append("")

        if files_created:
            lines.append("## Files Created")
            lines.append("")
            for f in files_created:
                lines.append(f"- `{f}`")
            lines.append("")

        if files_modified:
            lines.append("## Files Modified")
            lines.append("")
            for f in files_modified:
                lines.append(f"- `{f}`")
            lines.append("")

        if errors:
            lines.append("## Errors")
            lines.append("")
            for e in errors:
                lines.append(f"- {e}")
            lines.append("")

        if todos:
            lines.append("## TODO")
            lines.append("")
            for t in todos:
                lines.append(f"- [ ] {t}")
            lines.append("")

        if suggestions:
            lines.append("## Next Session")
            lines.append("")
            for s in suggestions:
                lines.append(f"- {s}")
            lines.append("")

        return "\n".join(lines)

    def save_notes(self) -> Path | None:
        """Save session notes to the project's .sawyer directory."""
        if not self.project_dir:
            logger.warning("No project directory set, cannot save notes")
            return None

        summary = self.generate_summary()
        filepath = self.notes_dir / f"{self.session_id}.md"

        filepath.write_text(summary.full_notes, encoding="utf-8")
        logger.info(f"Session notes saved to {filepath}")

        # Also save a JSON summary for programmatic access
        json_path = self.notes_dir / f"{self.session_id}.json"
        json_data = {
            "session_id": summary.session_id,
            "started": summary.started,
            "ended": summary.ended,
            "duration_minutes": summary.duration_minutes,
            "message_count": summary.message_count,
            "tool_call_count": summary.tool_call_count,
            "topics": summary.topics,
            "decisions": summary.decisions,
            "corrections": summary.corrections,
            "todos": summary.todos,
            "files_created": summary.files_created,
            "files_modified": summary.files_modified,
            "errors": summary.errors,
            "skills_used": summary.skills_used,
            "next_session_suggestions": summary.next_session_suggestions,
        }
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

        return filepath

    def load_previous_notes(self, limit: int = 3) -> list[SessionSummary]:
        """Load notes from previous sessions to provide context."""
        if not self.project_dir or not self.notes_dir.exists():
            return []

        summaries = []
        json_files = sorted(self.notes_dir.glob("*.json"), reverse=True)

        for json_file in json_files[:limit]:
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                summaries.append(SessionSummary(
                    session_id=data["session_id"],
                    started=data["started"],
                    ended=data.get("ended", ""),
                    duration_minutes=data.get("duration_minutes", 0),
                    message_count=data.get("message_count", 0),
                    tool_call_count=data.get("tool_call_count", 0),
                    topics=data.get("topics", []),
                    decisions=data.get("decisions", []),
                    corrections=data.get("corrections", []),
                    todos=data.get("todos", []),
                    files_created=data.get("files_created", []),
                    files_modified=data.get("files_modified", []),
                    errors=data.get("errors", []),
                    skills_used=data.get("skills_used", []),
                    goals_attempted=data.get("goals_attempted", []),
                    goals_completed=data.get("goals_completed", []),
                    next_session_suggestions=data.get("next_session_suggestions", []),
                ))
            except Exception as e:
                logger.warning(f"Failed to load session notes from {json_file}: {e}")
                continue

        return summaries

    def evaluate_task_completion(
        self,
        goal: str,
        result: str,
        success_criteria: str = "",
        tool_calls: int = 0,
        errors: list[str] | None = None,
    ) -> dict:
        """Post-task evaluation hook for the Ralph loop.

        Called after each tool call round to check if the task is complete
        and whether the result meets quality standards. Returns an evaluation
        dict that can be fed into the orchestrator's ralph_loop_step.

        This hook is the bridge between the session engine (which tracks
        conversation state) and the orchestrator (which manages the Ralph
        loop). The session engine provides context; the orchestrator makes
        the decision.

        Args:
            goal: The task goal being evaluated.
            result: The accumulated result text so far.
            success_criteria: What success looks like for this task.
            tool_calls: Number of tool calls made in this session.
            errors: List of errors encountered during the session.

        Returns:
            Dict with:
                - is_complete: Whether the task appears done
                - quality_score: QualityScore dict (if evaluated)
                - suggestions: List of suggestions for improvement
                - needs_ralph_loop: Whether to trigger Ralph loop evaluation
        """
        from .scoring import evaluate_and_score

        suggestions = []
        is_complete = False

        # Check for explicit completion signals
        result_lower = result.lower() if result else ""

        # Signals that the task is likely complete
        completion_signals = [
            "done", "completed", "finished", "implemented",
            "created", "wrote", "built", "deployed", "fixed",
        ]
        has_completion_signal = any(
            signal in result_lower for signal in completion_signals
        )

        # Signals that the task is NOT complete
        incompletion_signals = [
            "todo:", "fixme:", "not yet", "still need to",
            "work in progress", "in progress", "pending",
        ]
        has_incompletion_signal = any(
            signal in result_lower for signal in incompletion_signals
        )

        # Determine completeness
        if has_completion_signal and not has_incompletion_signal:
            is_complete = True
        elif result and len(result.strip()) > 200 and not has_incompletion_signal:
            # Substantial result without incompletion markers
            is_complete = True
        elif not result:
            is_complete = False

        # Generate suggestions based on session state
        if errors:
            suggestions.append(
                f"Session had {len(errors)} error(s) -- may need retry or fix."
            )
        if tool_calls == 0 and result:
            suggestions.append(
                "No tool calls made -- result may lack verification."
            )
        if not result or len(result.strip()) < 50:
            suggestions.append(
                "Very short or empty result -- task may not be complete."
            )

        # Quick quality evaluation if we have a result
        quality_score = None
        needs_ralph_loop = False
        if result and len(result.strip()) >= 50:
            quality = evaluate_and_score(
                result=result,
                success_criteria=success_criteria or goal,
                task_id="",  # Will be set by the orchestrator
                iteration=1,
            )
            quality_score = quality.to_dict()

            # If quality is below threshold, suggest Ralph loop
            if not quality.passed:
                needs_ralph_loop = True
                suggestions.extend(quality.improvements)

        return {
            "is_complete": is_complete,
            "quality_score": quality_score,
            "suggestions": suggestions,
            "needs_ralph_loop": needs_ralph_loop,
            "tool_calls": tool_calls,
            "result_length": len(result) if result else 0,
        }

    def format_suggestions_for_prompt(self) -> str:
        """Format next-session suggestions for injection into a new session's system prompt."""
        previous = self.load_previous_notes(limit=3)
        if not previous:
            return ""

        parts = ["## Previous Session Context\n"]

        for summary in previous:
            parts.append(f"### Session {summary.session_id}")
            parts.append(f"Duration: {summary.duration_minutes}min, Messages: {summary.message_count}")

            if summary.decisions:
                parts.append("Decisions:")
                for d in summary.decisions[:5]:
                    parts.append(f"  - {d}")

            if summary.todos:
                parts.append("Unfinished:")
                for t in summary.todos[:5]:
                    parts.append(f"  - {t}")

            if summary.next_session_suggestions:
                parts.append("Suggested next steps:")
                for s in summary.next_session_suggestions[:3]:
                    parts.append(f"  - {s}")

            parts.append("")

        return "\n".join(parts)