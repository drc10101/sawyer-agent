"""
Cron scheduling -- scheduled tasks that wake the agent on interval.

Supports three schedule types:
- interval: run every N seconds/minutes/hours
- cron: run on a cron expression (e.g., "0 9 * * *" for 9am daily)
- one-shot: run once at a specific datetime

Uses APScheduler for scheduling, SQLite for persistence so jobs
survive restarts. When a job fires, it constructs a prompt from
the job config and runs the agent.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .agent import Agent
from .config import HarnessConfig
from .paths import UserData

logger = logging.getLogger("sawyer-harness.scheduler")


class ScheduleType(str, Enum):
    INTERVAL = "interval"
    CRON = "cron"
    ONESHOT = "one_shot"


@dataclass
class CronJob:
    """A scheduled job definition."""

    id: str
    name: str
    schedule_type: ScheduleType
    schedule_expr: str  # interval seconds, cron expression, or ISO datetime
    prompt: str  # The prompt to send to the agent when the job fires
    channel: str = "cli"  # Which channel to deliver results to
    enabled: bool = True
    created: str = ""
    updated: str = ""
    last_run: str = ""
    next_run: str = ""
    run_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "id": self.id,
            "name": self.name,
            "schedule_type": self.schedule_type.value,
            "schedule_expr": self.schedule_expr,
            "prompt": self.prompt,
            "channel": self.channel,
            "enabled": self.enabled,
            "created": self.created,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CronJob:
        """Deserialize from dict."""
        data = dict(data)  # copy
        data["schedule_type"] = ScheduleType(data["schedule_type"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CronScheduler:
    """
    Manages scheduled jobs with APScheduler and SQLite persistence.

    Jobs are stored in SQLite so they survive restarts.
    When a job fires, it runs the agent with the configured prompt.
    """

    def __init__(self, config: HarnessConfig, db_path: str | Path | None = None):
        self.config = config
        memory_path = config.memory.path or str(UserData.memory_db)
        self.db_path = Path(db_path or memory_path.replace("memory.db", "cron.db"))
        self.db_path = self.db_path.expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, CronJob] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._agent_factory: Callable[[], Agent] | None = None
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite persistence store."""
        import sqlite3

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cron_jobs (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _load_jobs_from_db(self):
        """Load all jobs from SQLite."""
        import sqlite3

        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT id, data FROM cron_jobs").fetchall()
        conn.close()

        for row_id, data_str in rows:
            try:
                data = json.loads(data_str)
                job = CronJob.from_dict(data)
                self._jobs[job.id] = job
            except Exception as e:
                logger.warning(f"Failed to load job {row_id}: {e}")

    def _save_job_to_db(self, job: CronJob):
        """Persist a single job to SQLite."""
        import sqlite3

        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO cron_jobs (id, data) VALUES (?, ?)",
            (job.id, json.dumps(job.to_dict())),
        )
        conn.commit()
        conn.close()

    def _delete_job_from_db(self, job_id: str):
        """Remove a job from SQLite."""
        import sqlite3

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()

    def set_agent_factory(self, factory: Callable[[], Agent]):
        """Set the factory that creates agent instances for job execution."""
        self._agent_factory = factory

    async def start(self):
        """Start the scheduler and load persisted jobs."""
        self._load_jobs_from_db()
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()

        # Re-schedule persisted enabled jobs
        for job in self._jobs.values():
            if job.enabled:
                self._add_job_to_scheduler(job)

        logger.info(f"Cron scheduler started with {len(self._jobs)} jobs")

    async def stop(self):
        """Stop the scheduler."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("Cron scheduler stopped")

    def add_job(
        self,
        name: str,
        schedule_type: ScheduleType,
        schedule_expr: str,
        prompt: str,
        channel: str = "cli",
        metadata: dict | None = None,
        enabled: bool = True,
    ) -> CronJob:
        """
        Add a new scheduled job.

        schedule_expr format depends on schedule_type:
        - interval: seconds as integer, or "30s", "5m", "1h"
        - cron: standard cron expression like "0 9 * * *"
        - one_shot: ISO datetime like "2026-07-13T09:00:00"
        """
        job_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        job = CronJob(
            id=job_id,
            name=name,
            schedule_type=schedule_type,
            schedule_expr=schedule_expr,
            prompt=prompt,
            channel=channel,
            enabled=enabled,
            created=now,
            metadata=metadata or {},
        )

        self._jobs[job_id] = job
        self._save_job_to_db(job)

        if self._scheduler and enabled:
            self._add_job_to_scheduler(job)

        logger.info(f"Added cron job '{name}' ({job_id}): {schedule_type}={schedule_expr}")
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        job = self._jobs.pop(job_id, None)
        if not job:
            return False

        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass  # Job may not be in scheduler if disabled

        self._delete_job_from_db(job_id)
        logger.info(f"Removed cron job {job_id}")
        return True

    def enable_job(self, job_id: str) -> bool:
        """Enable a disabled job."""
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.enabled = True
        self._save_job_to_db(job)

        if self._scheduler:
            self._add_job_to_scheduler(job)

        logger.info(f"Enabled cron job {job_id}")
        return True

    def disable_job(self, job_id: str) -> bool:
        """Disable an enabled job (keeps it in DB, just stops it from running)."""
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.enabled = False
        self._save_job_to_db(job)

        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

        logger.info(f"Disabled cron job {job_id}")
        return True

    def update_job(self, job_id: str, **updates) -> CronJob | None:
        """Update fields on an existing job. Reschedules if enabled."""
        job = self._jobs.get(job_id)
        if not job:
            return None

        for key, value in updates.items():
            if hasattr(job, key):
                setattr(job, key, value)

        job.updated = datetime.now(timezone.utc).isoformat()
        self._save_job_to_db(job)

        # Reschedule if enabled
        if job.enabled and self._scheduler:
            self._add_job_to_scheduler(job)

        logger.info(f"Updated cron job {job_id}")
        return job

    async def run_job_now(self, job_id: str) -> str | None:
        """Trigger a job to run immediately, regardless of schedule."""
        job = self._jobs.get(job_id)
        if not job:
            return None

        logger.info(f"Manually triggering cron job '{job.name}' ({job_id})")
        await self._run_job(job_id)

        # Return updated job info
        job = self._jobs.get(job_id)
        if job:
            return job.to_dict()
        return None

    def list_jobs(self) -> list[dict]:
        """List all jobs with their status."""
        result = []
        for job in self._jobs.values():
            d = job.to_dict()
            # Add scheduler info if available
            if self._scheduler:
                try:
                    scheduled = self._scheduler.get_job(job.id)
                    if scheduled:
                        d["next_run"] = str(scheduled.next_run_time)
                except Exception:
                    pass
            result.append(d)
        return result

    def get_job(self, job_id: str) -> Optional[CronJob]:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    async def _run_job(self, job_id: str):
        """Callback: run the agent with the job's prompt."""
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return

        logger.info(f"Running cron job '{job.name}' ({job_id})")
        job.last_run = datetime.now(timezone.utc).isoformat()
        job.run_count += 1
        self._save_job_to_db(job)

        if not self._agent_factory:
            logger.warning(f"No agent factory set for cron job {job_id}")
            return

        try:
            agent = self._agent_factory()
            response_parts = []
            async for chunk in agent.run(job.prompt):
                response_parts.append(chunk)

            result = "".join(response_parts)
            logger.info(f"Cron job '{job.name}' completed: {len(result)} chars")
            # TODO: deliver result to channel (telegram, etc.)
            # For now, just log it

        except Exception as e:
            logger.error(f"Cron job '{job.name}' failed: {e}")

    def _parse_interval(self, expr: str) -> int:
        """Parse interval expression to seconds.

        Supports: plain integer (seconds), "30s", "5m", "1h", "2d"
        """
        expr = expr.strip()
        if expr.isdigit():
            return int(expr)

        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        for suffix, mult in multipliers.items():
            if expr.endswith(suffix):
                num = expr[:-1]
                if num.isdigit():
                    return int(num) * mult

        raise ValueError(f"Invalid interval expression: {expr}")

    def _add_job_to_scheduler(self, job: CronJob):
        """Add a job to APScheduler with the appropriate trigger."""
        if not self._scheduler:
            return

        # Remove existing job with same ID if present
        try:
            self._scheduler.remove_job(job.id)
        except Exception:
            pass

        callback = self._run_job

        if job.schedule_type == ScheduleType.INTERVAL:
            seconds = self._parse_interval(job.schedule_expr)
            trigger = IntervalTrigger(seconds=seconds)
            self._scheduler.add_job(
                callback,
                trigger=trigger,
                id=job.id,
                args=[job.id],
                name=job.name,
            )

        elif job.schedule_type == ScheduleType.CRON:
            # Parse cron expression: "min hour day month day_of_week"
            parts = job.schedule_expr.split()
            if len(parts) != 5:
                raise ValueError(f"Invalid cron expression: {job.schedule_expr}")

            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
            self._scheduler.add_job(
                callback,
                trigger=trigger,
                id=job.id,
                args=[job.id],
                name=job.name,
            )

        elif job.schedule_type == ScheduleType.ONESHOT:
            # Parse ISO datetime
            run_time = datetime.fromisoformat(job.schedule_expr)
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone.utc)

            trigger = DateTrigger(run_date=run_time)
            self._scheduler.add_job(
                callback,
                trigger=trigger,
                id=job.id,
                args=[job.id],
                name=job.name,
            )

        else:
            raise ValueError(f"Unknown schedule type: {job.schedule_type}")