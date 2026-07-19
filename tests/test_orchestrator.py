"""Tests for Sawyer Harness orchestration engine."""

import pytest

from sawyer_harness.orchestrator import (
    OrchestratorEngine,
    OrchestrationRun,
    OrchestratedTask,
    TaskStatus,
    TaskPriority,
    AgentBriefing,
)


@pytest.fixture
def engine(tmp_path):
    """Create a fresh OrchestratorEngine with a temp path."""
    path = tmp_path / "orchestrations.yaml"
    return OrchestratorEngine(path=path)


class TestOrchestratorEngine:
    """Test CRUD for orchestration runs."""

    def test_create_run(self, engine):
        run = engine.create_run("Build a REST API")
        assert run.id.startswith("orch-")
        assert run.goal == "Build a REST API"
        assert run.status == "pending"
        assert run.tasks == []
        assert run.created != ""

    def test_get_run(self, engine):
        run = engine.create_run("Test goal")
        retrieved = engine.get_run(run.id)
        assert retrieved is not None
        assert retrieved.id == run.id
        assert retrieved.goal == "Test goal"

    def test_get_run_not_found(self, engine):
        assert engine.get_run("nonexistent") is None

    def test_list_runs(self, engine):
        engine.create_run("Goal 1")
        engine.create_run("Goal 2")
        runs = engine.list_runs()
        assert len(runs) == 2

    def test_list_runs_by_status(self, engine):
        run = engine.create_run("Goal 1")
        engine.create_run("Goal 2")
        engine.start_run(run.id)
        pending = engine.list_runs(status="pending")
        running = engine.list_runs(status="running")
        assert len(pending) == 1
        assert len(running) == 1

    def test_delete_run(self, engine):
        run = engine.create_run("Delete me")
        assert engine.delete_run(run.id) is True
        assert engine.get_run(run.id) is None

    def test_delete_run_not_found(self, engine):
        assert engine.delete_run("nonexistent") is False

    def test_persistence(self, tmp_path):
        path = tmp_path / "orchestrations.yaml"
        engine1 = OrchestratorEngine(path=path)
        run = engine1.create_run("Persist test")
        
        # Create a new engine instance loading from the same file
        engine2 = OrchestratorEngine(path=path)
        retrieved = engine2.get_run(run.id)
        assert retrieved is not None
        assert retrieved.goal == "Persist test"


class TestTaskManagement:
    """Test adding, updating, and managing tasks within runs."""

    def test_add_task(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write tests")
        assert task is not None
        assert task.id.startswith("task-")
        assert task.goal == "Write tests"
        assert task.status == TaskStatus.PENDING

    def test_add_task_with_briefing(self, engine):
        run = engine.create_run("Build feature")
        briefing = AgentBriefing(
            purpose="Test implementation",
            goal="Write unit tests for the feature",
            rules=[{"name": "Test first", "rule": "Write tests before code", "priority": "P0"}],
            permissions=["shell", "file_read", "file_write"],
            success_criteria="All tests pass",
            context="Feature is a REST API endpoint",
            timeout_seconds=300,
            agent_type="worker",
        )
        task = engine.add_task(
            run.id,
            goal="Write unit tests",
            agent_type="worker",
            briefing=briefing,
        )
        assert task.briefing is not None
        assert task.briefing.purpose == "Test implementation"
        assert len(task.briefing.rules) == 1

    def test_add_task_with_priority(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(
            run.id,
            goal="Fix critical bug",
            priority=TaskPriority.P0,
        )
        assert task.priority == TaskPriority.P0

    def test_update_task_status(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        
        # Start the task
        updated = engine.update_task(run.id, task.id, status=TaskStatus.RUNNING)
        assert updated.status == TaskStatus.RUNNING
        assert updated.started != ""
        
        # Complete the task
        completed = engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="Feature implemented successfully",
        )
        assert completed.status == TaskStatus.COMPLETED
        assert completed.result == "Feature implemented successfully"
        assert completed.completed != ""

    def test_update_task_with_improvements(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        engine.update_task(run.id, task.id, status=TaskStatus.COMPLETED, result="Done")
        
        updated = engine.update_task(
            run.id, task.id,
            improvements=["Add error handling", "Improve test coverage"],
        )
        assert len(updated.improvements) == 2

    def test_get_task(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        retrieved = engine.get_task(run.id, task.id)
        assert retrieved is not None
        assert retrieved.id == task.id

    def test_add_task_to_nonexistent_run(self, engine):
        result = engine.add_task("nonexistent", "Write code")
        assert result is None

    def test_update_task_not_found(self, engine):
        run = engine.create_run("Build feature")
        result = engine.update_task(run.id, "nonexistent", status=TaskStatus.RUNNING)
        assert result is None


class TestRunLifecycle:
    """Test run state transitions."""

    def test_start_run(self, engine):
        run = engine.create_run("Build feature")
        started = engine.start_run(run.id)
        assert started.status == "running"

    def test_complete_run(self, engine):
        run = engine.create_run("Build feature")
        engine.start_run(run.id)
        completed = engine.complete_run(run.id)
        assert completed.status == "completed"

    def test_fail_run(self, engine):
        run = engine.create_run("Build feature")
        engine.start_run(run.id)
        failed = engine.fail_run(run.id, error="Something broke")
        assert failed.status == "failed"

    def test_run_not_found_operations(self, engine):
        assert engine.start_run("nonexistent") is None
        assert engine.complete_run("nonexistent") is None
        assert engine.fail_run("nonexistent") is None


class TestBriefing:
    """Test briefing assembly."""

    def test_assemble_worker_briefing(self, engine):
        briefing = engine.assemble_briefing(
            purpose="Implement a feature",
            goal="Add authentication endpoint",
            agent_type="worker",
        )
        assert briefing.purpose == "Implement a feature"
        assert briefing.agent_type == "worker"
        assert "shell" in briefing.permissions
        assert briefing.success_criteria != ""

    def test_assemble_creative_briefing(self, engine):
        briefing = engine.assemble_briefing(
            purpose="Review code quality",
            goal="Find improvement opportunities",
            agent_type="creative",
        )
        assert briefing.agent_type == "creative"
        assert "file_read" in briefing.permissions
        assert "file_write" not in briefing.permissions

    def test_assemble_orchestrator_briefing(self, engine):
        briefing = engine.assemble_briefing(
            purpose="Coordinate feature build",
            goal="Build the authentication module",
            agent_type="orchestrator",
        )
        assert briefing.agent_type == "orchestrator"
        assert "delegate" in briefing.permissions

    def test_briefing_to_prompt_section(self):
        briefing = AgentBriefing(
            purpose="Implement a feature",
            goal="Add authentication endpoint",
            rules=[{"name": "Test first", "rule": "Write tests before code", "priority": "P0"}],
            permissions=["shell", "file_read"],
            success_criteria="All tests pass",
            context="Working on the auth module",
            timeout_seconds=300,
            agent_type="worker",
        )
        prompt = briefing.to_prompt_section()
        assert "Agent Briefing" in prompt
        assert "Implement a feature" in prompt
        assert "[P0] Test first" in prompt
        assert "shell, file_read" in prompt

    def test_custom_success_criteria(self, engine):
        briefing = engine.assemble_briefing(
            purpose="Test",
            goal="Test goal",
            success_criteria="All 20 tests pass with 100% coverage",
        )
        assert briefing.success_criteria == "All 20 tests pass with 100% coverage"


class TestDecomposition:
    """Test goal decomposition."""

    def test_decompose_goal(self, engine):
        run = engine.create_run("Build a REST API")
        subtasks = [
            {"goal": "Design API schema", "agent_type": "worker", "priority": "P1"},
            {"goal": "Implement endpoints", "agent_type": "worker", "priority": "P0"},
            {"goal": "Write integration tests", "agent_type": "worker", "priority": "P2"},
        ]
        tasks = engine.decompose_goal(run.id, "Build a REST API", subtasks)
        assert len(tasks) == 3
        assert tasks[0].goal == "Design API schema"
        assert tasks[1].priority == TaskPriority.P0

    def test_decompose_with_briefings(self, engine):
        run = engine.create_run("Build feature")
        subtasks = [
            {
                "goal": "Write tests",
                "agent_type": "worker",
                "success_criteria": "All tests pass",
                "context": "Testing the auth module",
            }
        ]
        tasks = engine.decompose_goal(run.id, "Build feature", subtasks)
        assert len(tasks) == 1
        assert tasks[0].briefing is not None


class TestEvaluation:
    """Test the structural evaluation of task results."""

    def test_evaluate_completed_task(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        engine.update_task(run.id, task.id, status=TaskStatus.COMPLETED, result="Done")
        
        # Re-fetch the task with updated status
        updated = engine.get_task(run.id, task.id)
        improvements = engine.evaluate_result(updated)
        # Short result should trigger improvement
        assert len(improvements) > 0

    def test_evaluate_pending_task(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        improvements = engine.evaluate_result(task)
        # Pending tasks should have no improvements
        assert len(improvements) == 0

    def test_evaluate_todo_in_result(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="Implemented the feature but TODO: add error handling and FIXME: memory leak in parser",
        )
        updated = engine.get_task(run.id, task.id)
        improvements = engine.evaluate_result(updated)
        # Should flag TODO/FIXME
        assert any("TODO" in imp or "FIXME" in imp for imp in improvements)

    def test_evaluate_placeholder_in_result(self, engine):
        run = engine.create_run("Build feature")
        task = engine.add_task(run.id, "Write code")
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="Created the stub function as a placeholder for now.",
        )
        updated = engine.get_task(run.id, task.id)
        improvements = engine.evaluate_result(updated)
        assert any("placeholder" in imp.lower() for imp in improvements)


class TestRunStats:
    """Test run statistics."""

    def test_run_stats(self, engine):
        run = engine.create_run("Build feature")
        engine.add_task(run.id, "Task 1")
        engine.add_task(run.id, "Task 2", priority=TaskPriority.P0)
        task3 = engine.add_task(run.id, "Task 3")
        engine.update_task(run.id, task3.id, status=TaskStatus.COMPLETED, result="Done")
        
        stats = engine.run_stats(run.id)
        assert stats["total_tasks"] == 3
        assert stats["by_status"]["completed"] == 1
        assert stats["by_status"]["pending"] == 2

    def test_run_stats_not_found(self, engine):
        assert engine.run_stats("nonexistent") is None


class TestSerialization:
    """Test round-trip serialization."""

    def test_run_to_dict_from_dict(self, engine):
        run = engine.create_run("Test serialization")
        engine.add_task(run.id, "Subtask 1")
        
        data = run.to_dict()
        assert data["id"] == run.id
        assert len(data["tasks"]) == 1
        
        restored = OrchestrationRun.from_dict(data)
        assert restored.id == run.id
        assert len(restored.tasks) == 1
        assert restored.tasks[0].goal == "Subtask 1"

    def test_task_to_dict_from_dict(self):
        task = OrchestratedTask(
            id="task-test",
            goal="Test goal",
            status=TaskStatus.RUNNING,
            priority=TaskPriority.P1,
            agent_type="worker",
            result="In progress",
        )
        data = task.to_dict()
        restored = OrchestratedTask.from_dict(data)
        assert restored.id == "task-test"
        assert restored.status == TaskStatus.RUNNING
        assert restored.priority == TaskPriority.P1

    def test_briefing_in_task_serialization(self):
        briefing = AgentBriefing(
            purpose="Test",
            goal="Test goal",
            rules=[{"name": "Rule 1", "rule": "Do X", "priority": "P0"}],
            permissions=["shell"],
            success_criteria="Pass",
            context="Test context",
            timeout_seconds=300,
            agent_type="worker",
        )
        task = OrchestratedTask(
            id="task-briefing",
            goal="Test with briefing",
            briefing=briefing,
        )
        data = task.to_dict()
        assert "briefing" in data
        assert data["briefing"]["purpose"] == "Test"
        
        restored = OrchestratedTask.from_dict(data)
        assert restored.briefing is not None
        assert restored.briefing.purpose == "Test"
        assert len(restored.briefing.rules) == 1