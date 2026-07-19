"""
Orchestration Engine -- multi-agent coordination with improvement loops.

The orchestrator decomposes goals into subtasks, delegates to worker agents,
and runs a creative evaluation after each task completion. The creative agent
identifies improvement opportunities, spawning new worker tasks when beneficial.

Every task is an opportunity to improve, not just complete.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .paths import UserData
import yaml

logger = logging.getLogger("sawyer-harness.orchestrator")


# ============================================================
# Data Models
# ============================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EVALUATING = "evaluating"
    IMPROVING = "improving"


class TaskPriority(str, Enum):
    P0 = "P0"  # Critical
    P1 = "P1"  # High
    P2 = "P2"  # Medium
    P3 = "P3"  # Low


@dataclass
class AgentBriefing:
    """Everything an agent needs to succeed, provided at launch time.

    No agent asks 'what should I do?' -- the briefing provides all context.
    """
    purpose: str              # What this agent exists to accomplish
    goal: str                 # Specific task goal
    rules: list[dict]        # Priority-ranked behavioral constraints
    permissions: list[str]   # What tools/APIs this agent can access
    success_criteria: str     # How the agent knows it's done
    context: str             # Relevant project state, files, prior work
    timeout_seconds: int     # Maximum execution time
    agent_type: str          # "orchestrator", "creative", or "worker"

    def to_prompt_section(self) -> str:
        """Format the briefing for injection into system prompt."""
        lines = [
            "## Agent Briefing",
            f"**Purpose:** {self.purpose}",
            f"**Goal:** {self.goal}",
            f"**Success Criteria:** {self.success_criteria}",
            f"**Agent Type:** {self.agent_type}",
            f"**Timeout:** {self.timeout_seconds}s",
        ]
        if self.rules:
            lines.append("\n**Rules:**")
            for r in self.rules:
                priority = r.get("priority", "P1")
                lines.append(f"  [{priority}] {r.get('name', 'Rule')}: {r.get('rule', r.get('rule', ''))}")
        if self.permissions:
            lines.append(f"\n**Permissions:** {', '.join(self.permissions)}")
        if self.context:
            lines.append(f"\n**Context:**\n{self.context}")
        return "\n".join(lines)


@dataclass
class OrchestratedTask:
    """A single task in an orchestration run."""
    id: str
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.P2
    agent_type: str = "worker"        # "worker", "creative", "orchestrator"
    briefing: AgentBriefing | None = None
    parent_task_id: str | None = None  # Task that spawned this one
    result: str = ""
    improvements: list[str] = field(default_factory=list)
    created: str = ""
    started: str = ""
    completed: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "priority": self.priority.value,
            "agent_type": self.agent_type,
            "parent_task_id": self.parent_task_id,
            "result": self.result,
            "improvements": self.improvements,
            "created": self.created,
            "started": self.started,
            "completed": self.completed,
            "error": self.error,
        }
        if self.briefing:
            d["briefing"] = {
                "purpose": self.briefing.purpose,
                "goal": self.briefing.goal,
                "rules": self.briefing.rules,
                "permissions": self.briefing.permissions,
                "success_criteria": self.briefing.success_criteria,
                "context": self.briefing.context,
                "timeout_seconds": self.briefing.timeout_seconds,
                "agent_type": self.briefing.agent_type,
            }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> OrchestratedTask:
        briefing = None
        if "briefing" in data and data["briefing"]:
            b = data["briefing"]
            briefing = AgentBriefing(
                purpose=b.get("purpose", ""),
                goal=b.get("goal", ""),
                rules=b.get("rules", []),
                permissions=b.get("permissions", []),
                success_criteria=b.get("success_criteria", ""),
                context=b.get("context", ""),
                timeout_seconds=b.get("timeout_seconds", 300),
                agent_type=b.get("agent_type", "worker"),
            )
        return cls(
            id=data["id"],
            goal=data["goal"],
            status=TaskStatus(data.get("status", "pending")),
            priority=TaskPriority(data.get("priority", "P2")),
            agent_type=data.get("agent_type", "worker"),
            briefing=briefing,
            parent_task_id=data.get("parent_task_id"),
            result=data.get("result", ""),
            improvements=data.get("improvements", []),
            created=data.get("created", ""),
            started=data.get("started", ""),
            completed=data.get("completed", ""),
            error=data.get("error", ""),
        )


@dataclass
class OrchestrationRun:
    """A full orchestration run: goal → decompose → delegate → evaluate → improve."""
    id: str
    goal: str
    status: str = "pending"  # pending, running, completed, failed
    tasks: list[OrchestratedTask] = field(default_factory=list)
    improvements_found: int = 0
    improvements_applied: int = 0
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "tasks": [t.to_dict() for t in self.tasks],
            "improvements_found": self.improvements_found,
            "improvements_applied": self.improvements_applied,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OrchestrationRun:
        tasks = [OrchestratedTask.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            id=data["id"],
            goal=data.get("goal", ""),
            status=data.get("status", "pending"),
            tasks=tasks,
            improvements_found=data.get("improvements_found", 0),
            improvements_applied=data.get("improvements_applied", 0),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
        )


# ============================================================
# Orchestrator Engine
# ============================================================

class OrchestratorEngine:
    """
    Manages orchestration runs: decompose goals, delegate tasks,
    evaluate results, and drive improvement loops.

    The Ralph Loop:
        Goal → Decompose → Delegate → Execute → Evaluate → Improve → Repeat
                                             ↑                              |
                                             └──────── re-delegate ────────┘

    An orchestrator that only completes tasks is a cost center.
    An orchestrator that completes tasks AND makes the system better
    is a force multiplier.
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = UserData.orchestrations_file
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, OrchestrationRun] = {}
        self._load()

    # ── Persistence ──────────────────────────────────────────

    def _load(self):
        """Load orchestration runs from YAML."""
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text()
            data = yaml.safe_load(raw) or {}
            runs = data.get("runs", [])
            for r in runs:
                run = OrchestrationRun.from_dict(r)
                self._runs[run.id] = run
            logger.info(f"Loaded {len(self._runs)} orchestration runs from {self.path}")
        except Exception as e:
            logger.error(f"Failed to load orchestrations: {e}")

    def _save(self):
        """Persist orchestration runs to YAML."""
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "runs": [r.to_dict() for r in self._runs.values()],
        }
        self.path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        logger.info(f"Saved {len(self._runs)} orchestration runs to {self.path}")

    # ── CRUD ─────────────────────────────────────────────────

    def create_run(self, goal: str) -> OrchestrationRun:
        """Create a new orchestration run for a goal."""
        run_id = f"orch-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        run = OrchestrationRun(
            id=run_id,
            goal=goal,
            status="pending",
            created=now,
            updated=now,
        )
        self._runs[run_id] = run
        self._save()
        logger.info(f"Created orchestration run {run_id}: {goal[:60]}")
        return run

    def get_run(self, run_id: str) -> OrchestrationRun | None:
        return self._runs.get(run_id)

    def list_runs(self, status: str | None = None) -> list[OrchestrationRun]:
        runs = list(self._runs.values())
        if status:
            runs = [r for r in runs if r.status == status]
        return sorted(runs, key=lambda r: r.created, reverse=True)

    def delete_run(self, run_id: str) -> bool:
        if run_id not in self._runs:
            return False
        del self._runs[run_id]
        self._save()
        return True

    # ── Task Management ──────────────────────────────────────

    def add_task(
        self,
        run_id: str,
        goal: str,
        agent_type: str = "worker",
        priority: TaskPriority = TaskPriority.P2,
        parent_task_id: str | None = None,
        briefing: AgentBriefing | None = None,
    ) -> OrchestratedTask | None:
        """Add a task to an orchestration run."""
        run = self._runs.get(run_id)
        if not run:
            return None

        task_id = f"task-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        task = OrchestratedTask(
            id=task_id,
            goal=goal,
            status=TaskStatus.PENDING,
            priority=priority,
            agent_type=agent_type,
            briefing=briefing,
            parent_task_id=parent_task_id,
            created=now,
        )
        run.tasks.append(task)
        run.updated = now
        self._save()
        return task

    def update_task(
        self,
        run_id: str,
        task_id: str,
        status: TaskStatus | None = None,
        result: str | None = None,
        error: str | None = None,
        improvements: list[str] | None = None,
    ) -> OrchestratedTask | None:
        """Update a task's status and result."""
        run = self._runs.get(run_id)
        if not run:
            return None

        task = next((t for t in run.tasks if t.id == task_id), None)
        if not task:
            return None

        now = datetime.now(timezone.utc).isoformat()

        if status:
            task.status = status
            if status == TaskStatus.RUNNING:
                task.started = now
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                task.completed = now

        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if improvements is not None:
            task.improvements = improvements

        run.updated = now
        self._save()
        return task

    def get_task(self, run_id: str, task_id: str) -> OrchestratedTask | None:
        run = self._runs.get(run_id)
        if not run:
            return None
        return next((t for t in run.tasks if t.id == task_id), None)

    # ── Briefing Assembly ─────────────────────────────────────

    def assemble_briefing(
        self,
        purpose: str,
        goal: str,
        agent_type: str = "worker",
        rules: list[dict] | None = None,
        permissions: list[str] | None = None,
        success_criteria: str = "",
        context: str = "",
        timeout_seconds: int = 300,
    ) -> AgentBriefing:
        """
        Assemble a briefing from components.

        Every agent launches with a briefing: purpose, rules, permissions,
        success criteria, and context. No agent asks 'what should I do?'
        -- the briefing provides everything needed.
        """
        # Merge global rules with agent-specific rules
        merged_rules = list(rules or [])

        # Default permissions by agent type
        default_perms = {
            "worker": ["shell", "file_read", "file_write", "web_fetch"],
            "creative": ["shell", "file_read", "web_fetch"],
            "orchestrator": ["delegate", "file_read", "file_write"],
        }
        if not permissions:
            permissions = default_perms.get(agent_type, ["shell", "file_read"])

        # Default success criteria if not provided
        if not success_criteria:
            success_by_type = {
                "worker": "Task completes successfully with working output.",
                "creative": "Identifies at least one concrete, actionable improvement.",
                "orchestrator": "All subtasks completed or improved. No pending work remains.",
            }
            success_criteria = success_by_type.get(agent_type, "Goal accomplished.")

        return AgentBriefing(
            purpose=purpose,
            goal=goal,
            rules=merged_rules,
            permissions=permissions,
            success_criteria=success_criteria,
            context=context,
            timeout_seconds=timeout_seconds,
            agent_type=agent_type,
        )

    # ── Evaluation ────────────────────────────────────────────

    def evaluate_result(self, task: OrchestratedTask) -> list[str]:
        """
        Evaluate a completed task's result and identify improvements.

        This is the creative agent's job -- but we provide a structured
        evaluation framework. The actual LLM call happens in the agent loop;
        this provides the evaluation prompt and parses the response.
        """
        improvements: list[str] = []

        if task.status != TaskStatus.COMPLETED:
            return improvements

        if not task.result:
            improvements.append("Task completed without result text -- add result capture.")
            return improvements

        # Structural checks that don't require LLM
        result_lower = task.result.lower()

        # Check for TODO items left in result
        if "todo" in result_lower or "fixme" in result_lower:
            improvements.append("Result contains TODO/FIXME items -- incomplete implementation.")

        # Check for error mentions
        if "error" in result_lower and "fail" in result_lower:
            improvements.append("Result mentions errors and failures -- may need retry or fix.")

        # Check for placeholder content
        if "placeholder" in result_lower or "stub" in result_lower:
            improvements.append("Result contains placeholders/stubs -- needs real implementation.")

        # Check result length (very short results are suspicious)
        if len(task.result.strip()) < 50:
            improvements.append("Result is very short -- may indicate incomplete execution.")

        return improvements

    # ── Decomposition ─────────────────────────────────────────

    def decompose_goal(
        self,
        run_id: str,
        goal: str,
        subtasks: list[dict],
    ) -> list[OrchestratedTask]:
        """
        Decompose a goal into subtasks.

        subtasks is a list of dicts:
            {goal, agent_type, priority, success_criteria, context}

        The orchestrator assembles briefings for each subtask based on
        the run's overall goal and the subtask specifications.
        """
        run = self._runs.get(run_id)
        if not run:
            return []

        created = []
        for sub in subtasks:
            briefing = self.assemble_briefing(
                purpose=f"Subtask of: {goal}",
                goal=sub.get("goal", ""),
                agent_type=sub.get("agent_type", "worker"),
                rules=sub.get("rules"),
                permissions=sub.get("permissions"),
                success_criteria=sub.get("success_criteria", ""),
                context=sub.get("context", ""),
                timeout_seconds=sub.get("timeout_seconds", 300),
            )
            task = self.add_task(
                run_id=run_id,
                goal=sub.get("goal", ""),
                agent_type=sub.get("agent_type", "worker"),
                priority=TaskPriority(sub.get("priority", "P2")),
                briefing=briefing,
            )
            if task:
                created.append(task)

        return created

    # ── Ralph Loop ─────────────────────────────────────────────

    def ralph_loop_step(
        self,
        run_id: str,
        task_id: str,
        quality_threshold: float | None = None,
        max_iterations: int | None = None,
    ) -> dict:
        """Evaluate a completed task and decide: pass, improve, or force-complete.

        The Ralph Loop is the evaluate-improve-repeat cycle at the heart
        of the orchestrator. Each step evaluates the task result quality,
        and either marks it passed, spawns a creative improvement task,
        or force-completes after max iterations.
        """
        from .scoring import evaluate_and_score, RALPH_DEFAULTS

        _quality_threshold: float = quality_threshold if quality_threshold is not None else RALPH_DEFAULTS["quality_threshold"]
        _max_iterations: int = max_iterations if max_iterations is not None else RALPH_DEFAULTS["max_iterations"]

        run = self._runs.get(run_id)
        if not run:
            return {"status": "error", "message": f"Run {run_id} not found"}

        task = next((t for t in run.tasks if t.id == task_id), None)
        if not task:
            return {"status": "error", "message": f"Task {task_id} not found"}

        if task.status != TaskStatus.COMPLETED:
            return {"status": "error", "message": f"Task {task_id} is not completed (status: {task.status.value})"}

        # Count existing improvement iterations for this task
        improvement_iterations = sum(
            1 for t in run.tasks
            if t.parent_task_id == task_id and t.agent_type == "creative"
        )

        if improvement_iterations >= _max_iterations:
            return {
                "status": "max_iterations_reached",
                "quality_score": None,
                "iteration": improvement_iterations,
                "message": f"Max iterations ({_max_iterations}) reached for task {task_id}",
            }

        # Evaluate the task result
        score = evaluate_and_score(
            result=task.result,
            success_criteria=task.briefing.success_criteria if task.briefing else "",
            task_id=task.id,
            quality_threshold=_quality_threshold,
        )

        # Record quality score as improvement annotation
        quality_annotation = f"[Quality Score] iteration={improvement_iterations + 1} overall={score.overall:.2f} passed={score.passed}"
        task.improvements.append(quality_annotation)

        if score.passed:
            run.updated = datetime.now(timezone.utc).isoformat()
            self._save()
            return {
                "status": "passed",
                "quality_score": score.to_dict(),
                "iteration": improvement_iterations + 1,
                "message": f"Task passed with quality score {score.overall:.2f}",
            }

        # Quality below threshold -- spawn a creative evaluator task
        improvement_task = self.add_task(
            run_id=run_id,
            goal=f"Improve: {task.goal}",
            agent_type="creative",
            priority=TaskPriority.P1,
            parent_task_id=task_id,
            briefing=self.assemble_briefing(
                purpose=f"Improve task: {task.goal}",
                goal=f"Identify improvements for: {task.goal}",
                agent_type="creative",
                success_criteria="Identify at least one concrete, actionable improvement.",
                context=f"Original result:\n{task.result}\n\nQuality score: {score.overall:.2f}\nImprovements needed: {', '.join(score.improvements) if score.improvements else 'general quality'}",
            ),
        )

        run.improvements_found += 1
        run.updated = datetime.now(timezone.utc).isoformat()
        self._save()

        return {
            "status": "improving",
            "quality_score": score.to_dict(),
            "iteration": improvement_iterations + 1,
            "improvement_task_id": improvement_task.id if improvement_task else None,
            "message": f"Task needs improvement (score: {score.overall:.2f}). Spawned creative evaluator.",
        }

    def ralph_loop_status(self, run_id: str) -> dict:
        """Get Ralph loop status for a run -- task counts by status."""
        run = self._runs.get(run_id)
        if not run:
            return {"status": "error", "message": f"Run {run_id} not found"}

        by_status: dict[str, int] = {}
        for t in run.tasks:
            s = t.status.value
            by_status[s] = by_status.get(s, 0) + 1

        return {
            "run_id": run.id,
            "goal": run.goal,
            "status": run.status,
            "total_tasks": len(run.tasks),
            "by_status": by_status,
            "improvements_found": run.improvements_found,
            "improvements_applied": run.improvements_applied,
        }

    def apply_improvement(
        self,
        run_id: str,
        task_id: str,
        improvement_result: str = "",
    ) -> OrchestratedTask | None:
        """Apply an improvement to a task -- update result and mark completed."""
        run = self._runs.get(run_id)
        if not run:
            return None

        task = next((t for t in run.tasks if t.id == task_id), None)
        if not task:
            return None

        now = datetime.now(timezone.utc).isoformat()

        # Build improved result
        if improvement_result:
            improved_result = f"{task.result}\n\n---\n### Improvement Evaluation\n{improvement_result}"
        else:
            improved_result = task.result

        task.result = improved_result
        task.status = TaskStatus.COMPLETED
        task.completed = now
        task.improvements.append(f"Improvement applied: {improvement_result[:100]}")

        run.improvements_applied += 1
        run.updated = now
        self._save()
        return task

    # ── Run Lifecycle ─────────────────────────────────────────

    def start_run(self, run_id: str) -> OrchestrationRun | None:
        """Mark a run as started."""
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = "running"
        run.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return run

    def complete_run(self, run_id: str) -> OrchestrationRun | None:
        """Mark a run as completed."""
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = "completed"
        run.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return run

    def fail_run(self, run_id: str, error: str = "") -> OrchestrationRun | None:
        """Mark a run as failed."""
        run = self._runs.get(run_id)
        if not run:
            return None
        run.status = "failed"
        run.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return run

    def run_stats(self, run_id: str) -> dict | None:
        """Get statistics for a run."""
        run = self._runs.get(run_id)
        if not run:
            return None

        total = len(run.tasks)
        by_status: dict[str, int] = {}
        for t in run.tasks:
            s = t.status.value
            by_status[s] = by_status.get(s, 0) + 1

        return {
            "id": run.id,
            "goal": run.goal,
            "status": run.status,
            "total_tasks": total,
            "by_status": by_status,
            "improvements_found": run.improvements_found,
            "improvements_applied": run.improvements_applied,
            "created": run.created,
            "updated": run.updated,
        }

    def reload(self):
        """Reload from disk."""
        self._runs.clear()
        self._load()

    def count(self) -> int:
        return len(self._runs)