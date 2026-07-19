"""Tests for Sawyer Harness cron scheduler."""

from datetime import datetime, timezone, timedelta

import pytest

from sawyer_harness.scheduler import CronJob, CronScheduler, ScheduleType


@pytest.fixture
def scheduler(tmp_path):
    """Create a scheduler with a temp database."""
    db_path = tmp_path / "test_cron.db"
    sched = CronScheduler.__new__(CronScheduler)
    sched.db_path = db_path
    sched._jobs = {}
    sched._scheduler = None
    sched._agent_factory = None
    sched._init_db()
    return sched


def test_cron_job_serialization():
    """CronJob serializes to dict and back."""
    job = CronJob(
        id="test123",
        name="Morning Brief",
        schedule_type=ScheduleType.CRON,
        schedule_expr="0 7 * * *",
        prompt="Summarize the morning news",
        channel="telegram",
        enabled=True,
        created="2026-07-12T00:00:00",
        metadata={"priority": "high"},
    )

    data = job.to_dict()
    assert data["id"] == "test123"
    assert data["schedule_type"] == "cron"
    assert data["metadata"] == {"priority": "high"}

    restored = CronJob.from_dict(data)
    assert restored.name == "Morning Brief"
    assert restored.schedule_type == ScheduleType.CRON
    assert restored.metadata == {"priority": "high"}


def test_add_interval_job(scheduler):
    """Add an interval job and verify it's stored."""
    job = scheduler.add_job(
        name="Health Check",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="300",  # every 5 minutes
        prompt="Check system health",
    )

    assert job.id in scheduler._jobs
    assert job.schedule_type == ScheduleType.INTERVAL
    assert job.schedule_expr == "300"
    assert job.enabled is True
    assert job.run_count == 0

    # Verify persisted to DB
    loaded = scheduler.get_job(job.id)
    assert loaded is not None
    assert loaded.name == "Health Check"


def test_add_cron_job(scheduler):
    """Add a cron job with standard expression."""
    job = scheduler.add_job(
        name="Daily Standup",
        schedule_type=ScheduleType.CRON,
        schedule_expr="0 9 * * 1-5",  # weekdays 9am
        prompt="Generate standup summary",
        channel="telegram",
    )

    assert job.schedule_type == ScheduleType.CRON
    assert job.schedule_expr == "0 9 * * 1-5"


def test_add_oneshot_job(scheduler):
    """Add a one-shot job with ISO datetime."""
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    job = scheduler.add_job(
        name="Reminder",
        schedule_type=ScheduleType.ONESHOT,
        schedule_expr=future_time,
        prompt="Remind Dave about the meeting",
    )

    assert job.schedule_type == ScheduleType.ONESHOT


def test_remove_job(scheduler):
    """Remove a job and verify it's gone."""
    job = scheduler.add_job(
        name="Temp Job",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="60",
        prompt="Temporary task",
    )

    assert scheduler.get_job(job.id) is not None
    result = scheduler.remove_job(job.id)
    assert result is True
    assert scheduler.get_job(job.id) is None


def test_remove_nonexistent_job(scheduler):
    """Removing a nonexistent job returns False."""
    result = scheduler.remove_job("nonexistent")
    assert result is False


def test_disable_job(scheduler):
    """Disable a job keeps it in DB but marks it disabled."""
    job = scheduler.add_job(
        name="Pausable",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="60",
        prompt="Check something",
    )

    result = scheduler.disable_job(job.id)
    assert result is True

    disabled = scheduler.get_job(job.id)
    assert disabled is not None
    assert disabled.enabled is False


def test_enable_job(scheduler):
    """Enable a disabled job."""
    job = scheduler.add_job(
        name="Toggle",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="60",
        prompt="Toggle test",
    )

    scheduler.disable_job(job.id)
    assert scheduler.get_job(job.id).enabled is False

    scheduler.enable_job(job.id)
    assert scheduler.get_job(job.id).enabled is True


def test_list_jobs(scheduler):
    """List all jobs returns complete data."""
    scheduler.add_job(
        name="Job A",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="60",
        prompt="Task A",
    )
    scheduler.add_job(
        name="Job B",
        schedule_type=ScheduleType.CRON,
        schedule_expr="0 9 * * *",
        prompt="Task B",
    )

    jobs = scheduler.list_jobs()
    assert len(jobs) == 2
    names = {j["name"] for j in jobs}
    assert "Job A" in names
    assert "Job B" in names


def test_persistence_across_restart(tmp_path):
    """Jobs persist in SQLite across scheduler restarts."""
    db_path = tmp_path / "persist_test.db"

    # Create first scheduler, add a job
    sched1 = CronScheduler.__new__(CronScheduler)
    sched1.db_path = db_path
    sched1._jobs = {}
    sched1._scheduler = None
    sched1._agent_factory = None
    sched1._init_db()

    job = sched1.add_job(
        name="Persistent Job",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="300",
        prompt="I should survive a restart",
    )
    job_id = job.id

    # Create second scheduler (simulating restart)
    sched2 = CronScheduler.__new__(CronScheduler)
    sched2.db_path = db_path
    sched2._jobs = {}
    sched2._scheduler = None
    sched2._agent_factory = None
    sched2._init_db()

    # Load from DB
    sched2._load_jobs_from_db()

    loaded = sched2.get_job(job_id)
    assert loaded is not None
    assert loaded.name == "Persistent Job"
    assert loaded.prompt == "I should survive a restart"


def test_parse_interval():
    """Interval parser handles various formats."""
    sched = CronScheduler.__new__(CronScheduler)

    assert sched._parse_interval("60") == 60
    assert sched._parse_interval("30s") == 30
    assert sched._parse_interval("5m") == 300
    assert sched._parse_interval("1h") == 3600
    assert sched._parse_interval("2d") == 172800

    with pytest.raises(ValueError):
        sched._parse_interval("invalid")


def test_job_run_count(scheduler):
    """Job tracks run count and last run time."""
    job = scheduler.add_job(
        name="Counter",
        schedule_type=ScheduleType.INTERVAL,
        schedule_expr="60",
        prompt="Count my runs",
    )

    assert job.run_count == 0
    assert job.last_run == ""

    # Simulate a run by manually updating
    job.last_run = datetime.now(timezone.utc).isoformat()
    job.run_count += 1
    scheduler._save_job_to_db(job)

    loaded = scheduler.get_job(job.id)
    assert loaded.run_count == 1
    assert loaded.last_run != ""