"""
GoalLoop -- named, reusable goal loops with iteration control.

A GoalLoop is a named, persistent goal definition that can be run and re-run.
Each loop has:
  - A human-readable name (for reusability)
  - The goal description (what to accomplish)
  - Success criteria (the final goal -- when is it done?)
  - Context (additional info for the agent)
  - Max iterations (how many decompose→execute→evaluate→improve cycles)
  - Iteration history (results from each cycle)

GoalLoop feeds into the orchestrator's Ralph Loop:
  Goal → Decompose → Delegate → Execute → Evaluate → Improve → Repeat (up to max_iterations)

Every loop is saved to YAML so it can be re-run, edited, and reused.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("sawyer-harness.goal_loop")


class LoopStatus(str, Enum):
    DRAFT = "draft"          # Created but not yet run
    RUNNING = "running"       # Currently executing an iteration
    PAUSED = "paused"        # Between iterations, waiting for user
    COMPLETED = "completed"  # Success criteria met
    FAILED = "failed"        # Unrecoverable error


class IterationStatus(str, Enum):
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    EVALUATING = "evaluating"
    IMPROVING = "improving"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Iteration:
    """A single iteration of the Ralph Loop."""
    number: int
    status: IterationStatus = IterationStatus.PENDING
    subtasks: list[dict] = field(default_factory=list)  # [{goal, status, result}]
    evaluation: str = ""          # What the evaluator found
    improvements: list[str] = field(default_factory=list)  # Identified improvements
    started: str = ""
    completed: str = ""

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "status": self.status.value,
            "subtasks": self.subtasks,
            "evaluation": self.evaluation,
            "improvements": self.improvements,
            "started": self.started,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Iteration:
        return cls(
            number=data.get("number", 0),
            status=IterationStatus(data.get("status", "pending")),
            subtasks=data.get("subtasks", []),
            evaluation=data.get("evaluation", ""),
            improvements=data.get("improvements", []),
            started=data.get("started", ""),
            completed=data.get("completed", ""),
        )


@dataclass
class GoalLoop:
    """
    A named, reusable goal loop with iteration control.

    This is what the user defines: a goal with clear success criteria,
    a max iteration budget, and a name so they can re-run it later.
    """
    id: str
    name: str                           # Human-readable name for reusability
    goal: str                           # What to accomplish
    success_criteria: str = ""          # How the agent knows it's done
    context: str = ""                   # Additional context
    max_iterations: int = 3             # How many Ralph Loop cycles to run
    status: LoopStatus = LoopStatus.DRAFT
    current_iteration: int = 0
    iterations: list[Iteration] = field(default_factory=list)
    sub_agents: list[str] = field(default_factory=list)  # Agent template IDs to use
    created: str = ""
    updated: str = ""
    last_run: str = ""                  # When this loop was last started

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "success_criteria": self.success_criteria,
            "context": self.context,
            "max_iterations": self.max_iterations,
            "status": self.status.value,
            "current_iteration": self.current_iteration,
            "iterations": [it.to_dict() for it in self.iterations],
            "sub_agents": self.sub_agents,
            "created": self.created,
            "updated": self.updated,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GoalLoop:
        iterations = [Iteration.from_dict(it) for it in data.get("iterations", [])]
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            goal=data.get("goal", ""),
            success_criteria=data.get("success_criteria", ""),
            context=data.get("context", ""),
            max_iterations=data.get("max_iterations", 3),
            status=LoopStatus(data.get("status", "draft")),
            current_iteration=data.get("current_iteration", 0),
            iterations=iterations,
            sub_agents=data.get("sub_agents", []),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            last_run=data.get("last_run", ""),
        )


class GoalLoopStore:
    """Persistent storage for GoalLoops. Saves to YAML, survives restarts."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path.home() / ".sawyer-harness" / "goal_loops.yaml"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._loops: dict[str, GoalLoop] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text()
            data = yaml.safe_load(raw) or {}
            loops = data.get("loops", [])
            for l in loops:
                loop = GoalLoop.from_dict(l)
                self._loops[loop.id] = loop
            logger.info(f"Loaded {len(self._loops)} goal loops from {self.path}")
        except Exception as e:
            logger.error(f"Failed to load goal loops: {e}")

    def _save(self):
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "loops": [l.to_dict() for l in self._loops.values()],
        }
        self.path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        logger.info(f"Saved {len(self._loops)} goal loops to {self.path}")

    # ── CRUD ──────────────────────────────────────────────────

    def create(
        self,
        name: str,
        goal: str,
        success_criteria: str = "",
        context: str = "",
        max_iterations: int = 3,
        sub_agents: list[str] | None = None,
    ) -> GoalLoop:
        """Create a new GoalLoop."""
        loop_id = f"loop-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        loop = GoalLoop(
            id=loop_id,
            name=name,
            goal=goal,
            success_criteria=success_criteria,
            context=context,
            max_iterations=max_iterations,
            sub_agents=sub_agents or [],
            created=now,
            updated=now,
        )
        self._loops[loop_id] = loop
        self._save()
        logger.info(f"Created goal loop '{name}' ({loop_id})")
        return loop

    def get(self, loop_id: str) -> GoalLoop | None:
        return self._loops.get(loop_id)

    def list_loops(self) -> list[GoalLoop]:
        return sorted(self._loops.values(), key=lambda l: l.updated, reverse=True)

    def update(self, loop_id: str, **updates) -> GoalLoop | None:
        loop = self._loops.get(loop_id)
        if not loop:
            return None
        for key, value in updates.items():
            if hasattr(loop, key):
                setattr(loop, key, value)
        loop.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return loop

    def delete(self, loop_id: str) -> bool:
        if loop_id not in self._loops:
            return False
        del self._loops[loop_id]
        self._save()
        return True

    # ── Iteration Management ─────────────────────────────────

    def start_iteration(self, loop_id: str) -> Iteration | None:
        """Start the next iteration of a loop."""
        loop = self._loops.get(loop_id)
        if not loop:
            return None

        loop.current_iteration += 1
        now = datetime.now(timezone.utc).isoformat()
        iteration = Iteration(
            number=loop.current_iteration,
            status=IterationStatus.DECOMPOSING,
            started=now,
        )
        loop.iterations.append(iteration)
        loop.status = LoopStatus.RUNNING
        loop.last_run = now
        loop.updated = now
        self._save()
        return iteration

    def update_iteration(
        self,
        loop_id: str,
        iteration_number: int,
        status: IterationStatus | None = None,
        subtasks: list[dict] | None = None,
        evaluation: str | None = None,
        improvements: list[str] | None = None,
    ) -> Iteration | None:
        loop = self._loops.get(loop_id)
        if not loop:
            return None

        iteration = next(
            (it for it in loop.iterations if it.number == iteration_number), None
        )
        if not iteration:
            return None

        if status:
            iteration.status = status
            if status in (IterationStatus.COMPLETED, IterationStatus.FAILED):
                iteration.completed = datetime.now(timezone.utc).isoformat()

        if subtasks is not None:
            iteration.subtasks = subtasks
        if evaluation is not None:
            iteration.evaluation = evaluation
        if improvements is not None:
            iteration.improvements = improvements

        loop.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return iteration

    def complete_loop(self, loop_id: str) -> GoalLoop | None:
        """Mark a loop as completed (success criteria met or max iterations reached)."""
        loop = self._loops.get(loop_id)
        if not loop:
            return None
        loop.status = LoopStatus.COMPLETED
        loop.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return loop

    def fail_loop(self, loop_id: str) -> GoalLoop | None:
        loop = self._loops.get(loop_id)
        if not loop:
            return None
        loop.status = LoopStatus.FAILED
        loop.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return loop

    def reset_loop(self, loop_id: str) -> GoalLoop | None:
        """Reset a loop to draft so it can be re-run."""
        loop = self._loops.get(loop_id)
        if not loop:
            return None
        loop.status = LoopStatus.DRAFT
        loop.current_iteration = 0
        loop.iterations = []
        loop.updated = datetime.now(timezone.utc).isoformat()
        self._save()
        return loop