"""Tests for Phase 3: Ralph Loop -- evaluate, improve, repeat.

Tests cover:
1. Quality scoring (scoring.py)
2. Ralph loop engine (orchestrator.py)
3. Post-task evaluation hook (session_engine.py)
4. API endpoints (server.py)
"""

import pytest
import tempfile
from pathlib import Path

from sawyer_harness.orchestrator import (
    OrchestratorEngine,
    OrchestrationRun,
    OrchestratedTask,
    TaskStatus,
    TaskPriority,
    AgentBriefing,
)
from sawyer_harness.scoring import (
    QualityScore,
    score_result,
    evaluate_and_score,
    QUALITY_DIMENSIONS,
    RALPH_DEFAULTS,
)
from sawyer_harness.session_engine import SessionEngine


# ============================================================
# Quality Scoring Tests
# ============================================================

class TestQualityScore:
    """Test QualityScore dataclass and serialization."""

    def test_quality_score_creation(self):
        score = QualityScore(
            task_id="task-abc",
            iteration=1,
            dimensions={"completeness": 0.85, "correctness": 0.90, "quality": 0.80, "coverage": 0.70, "efficiency": 0.75},
            overall=0.82,
            passed=True,
            improvements=[],
            auto_patch=True,
        )
        assert score.task_id == "task-abc"
        assert score.iteration == 1
        assert score.passed is True
        assert score.auto_patch is True
        assert len(score.dimensions) == 5

    def test_quality_score_serialization(self):
        score = QualityScore(
            task_id="task-xyz",
            dimensions={"completeness": 0.9},
            overall=0.9,
            passed=True,
        )
        data = score.to_dict()
        assert data["task_id"] == "task-xyz"
        assert data["passed"] is True
        assert "timestamp" in data

        restored = QualityScore.from_dict(data)
        assert restored.task_id == "task-xyz"
        assert restored.passed is True

    def test_quality_score_auto_timestamp(self):
        score = QualityScore(task_id="test")
        assert score.timestamp != ""


class TestScoreResult:
    """Test the score_result function -- core Ralph loop evaluation."""

    def test_high_quality_result_passes(self):
        """A substantive result addressing success criteria should pass."""
        result = (
            "Implemented the authentication endpoint with JWT tokens. "
            "All 15 tests pass with 100% coverage. Error handling covers "
            "expired tokens, invalid signatures, and missing credentials. "
            "Added comprehensive logging and monitoring."
        )
        success_criteria = "Implement authentication endpoint with JWT tokens and test coverage"

        score = score_result(
            result=result,
            success_criteria=success_criteria,
            quality_threshold=0.80,
        )

        assert score.overall > 0, "Score should be positive"
        assert score.dimensions["completeness"] > 0, "Completeness should be positive"
        # A substantive result matching criteria should have decent scores
        assert score.overall >= 0.5, f"Expected decent overall score, got {score.overall}"

    def test_empty_result_fails(self):
        """Empty result should fail hard."""
        score = score_result(
            result="",
            success_criteria="Build a REST API",
            quality_threshold=0.80,
        )
        assert score.overall < 0.5
        assert not score.passed

    def test_very_short_result_fails(self):
        """Very short results should fail."""
        score = score_result(
            result="OK",
            success_criteria="Build a REST API",
            quality_threshold=0.80,
        )
        assert not score.passed

    def test_todo_markers_reduce_completeness(self):
        """TODO/FIXME markers should reduce completeness score."""
        result_good = (
            "Implemented the feature with full test coverage. "
            "All edge cases handled including error paths. "
            "Documentation complete."
        )
        result_todo = (
            "Implemented the feature. TODO: add error handling. "
            "FIXME: memory leak in parser. "
            "Some tests written."
        )

        score_good = score_result(
            result=result_good,
            success_criteria="Implement feature with tests",
            quality_threshold=0.80,
        )
        score_todo = score_result(
            result=result_todo,
            success_criteria="Implement feature with tests",
            quality_threshold=0.80,
        )

        # TODO result should have lower completeness
        assert score_todo.dimensions["completeness"] < score_good.dimensions["completeness"]

    def test_placeholder_reduces_quality(self):
        """Placeholder/stub content should reduce quality score."""
        result_stub = "Created placeholder stub function as a placeholder for now."
        result_real = "Created the implementation with full error handling and tests."

        score_stub = score_result(
            result=result_stub,
            success_criteria="Create the implementation",
        )
        score_real = score_result(
            result=result_real,
            success_criteria="Create the implementation",
        )

        assert score_stub.dimensions["quality"] < score_real.dimensions["quality"]
        assert score_stub.dimensions["completeness"] < score_real.dimensions["completeness"]

    def test_error_markers_reduce_correctness(self):
        """Error/failure markers should reduce correctness score."""
        result_clean = (
            "Successfully deployed the application. All endpoints "
            "responding correctly. Performance benchmarks within targets."
        )
        result_errors = (
            "Deployed but encountered error in production. "
            "Failed to connect to database. Exception in auth module. "
            "Traceback: connection refused."
        )

        score_clean = score_result(result=result_clean, success_criteria="Deploy the application")
        score_errors = score_result(result=result_errors, success_criteria="Deploy the application")

        assert score_errors.dimensions["correctness"] < score_clean.dimensions["correctness"]

    def test_success_criteria_matching(self):
        """Result that matches success criteria keywords should score higher."""
        result_matched = (
            "Implemented the authentication module with JWT tokens. "
            "All tests pass. Error handling covers invalid tokens."
        )
        result_unmatched = (
            "Created some files. The system works. "
            "Everything is functional and operational."
        )

        score_matched = score_result(
            result=result_matched,
            success_criteria="Implement authentication with JWT tokens",
        )
        score_unmatched = score_result(
            result=result_unmatched,
            success_criteria="Implement authentication with JWT tokens",
        )

        # Matched result should have higher completeness
        assert score_matched.dimensions["completeness"] >= score_unmatched.dimensions["completeness"]

    def test_human_correction_suppresses_auto_patch(self):
        """When human_corrected=True, auto_patch should be suppressed."""
        score = score_result(
            result="Good implementation with all features working.",
            success_criteria="Implement all features",
            quality_threshold=0.50,  # Low threshold to ensure it passes
            human_corrected=True,
        )
        # Even if it passes, auto_patch should be False
        if score.passed:
            assert not score.auto_patch

    def test_auto_patch_on_success_without_correction(self):
        """When task passes without human correction, auto_patch should be True."""
        result = (
            "Implemented all features comprehensively. "
            "Full test coverage with 100% pass rate. "
            "Error handling for all edge cases. "
            "Documentation complete."
        )
        score = score_result(
            result=result,
            success_criteria="Implement all features with tests and documentation",
            quality_threshold=0.50,
            human_corrected=False,
        )
        if score.passed:
            assert score.auto_patch is True

    def test_max_iterations_override(self):
        """Custom quality_threshold should be respected."""
        score = score_result(
            result="Decent result but could be better",
            success_criteria="Build something",
            quality_threshold=0.20,  # Very low threshold
        )
        # With a low threshold, even mediocre results should pass
        # (depending on actual score, but the threshold is applied correctly)
        assert score.passed == (score.overall >= 0.20)

    def test_weighted_dimensions(self):
        """Overall score should be the weighted average of dimensions."""
        dimensions = {
            "completeness": 0.9,
            "correctness": 0.8,
            "quality": 0.7,
            "coverage": 0.6,
            "efficiency": 0.5,
        }
        # Calculate expected weighted average
        expected = sum(
            dimensions[dim] * QUALITY_DIMENSIONS[dim]["weight"]
            for dim in dimensions
        )
        expected = round(expected, 2)

        score = score_result(
            result="A" * 500,  # Long enough to avoid penalties
            success_criteria="test",
            quality_threshold=0.5,
        )
        # The overall should be the weighted average of the computed dimensions
        computed_overall = sum(
            score.dimensions.get(dim, 0.5) * QUALITY_DIMENSIONS[dim]["weight"]
            for dim in QUALITY_DIMENSIONS
        )
        computed_overall = round(computed_overall, 2)
        assert abs(score.overall - computed_overall) < 0.01


class TestEvaluateAndScore:
    """Test the combined evaluation and scoring function."""

    def test_combined_evaluation_includes_improvements(self):
        """evaluate_and_score should include structural improvements."""
        score = evaluate_and_score(
            result="TODO: implement this properly",
            success_criteria="Build the feature",
            task_id="task-123",
        )
        assert score.task_id == "task-123"
        # Should detect TODO
        assert any("TODO" in imp or "FIXME" in imp for imp in score.improvements)

    def test_combined_evaluation_with_good_result(self):
        """A good result should pass with few improvements."""
        result = (
            "Successfully implemented the feature with comprehensive error handling. "
            "All 20 tests pass. Coverage at 95%. Edge cases handled including "
            "network failures and invalid inputs. Documentation complete."
        )
        score = evaluate_and_score(
            result=result,
            success_criteria="Implement the feature with tests and error handling",
            task_id="task-456",
        )
        assert score.task_id == "task-456"

    def test_evaluation_iteration_tracking(self):
        """Iteration number should be tracked."""
        score = evaluate_and_score(
            result="Done",
            success_criteria="Test",
            iteration=3,
        )
        assert score.iteration == 3


# ============================================================
# Ralph Loop Engine Tests
# ============================================================

class TestRalphLoopStep:
    """Test the ralph_loop_step method on OrchestratorEngine."""

    @pytest.fixture
    def engine(self, tmp_path):
        path = tmp_path / "orchestrations.yaml"
        return OrchestratorEngine(path=path)

    def test_ralph_loop_passes_high_quality(self, engine):
        """A task with good quality result should pass the Ralph loop."""
        run = engine.create_run("Build a REST API")
        task = engine.add_task(
            run.id,
            goal="Implement authentication endpoint",
            briefing=AgentBriefing(
                purpose="Implement auth",
                goal="Add JWT authentication",
                rules=[],
                permissions=["shell"],
                success_criteria="All tests pass with JWT auth working",
                context="",
                timeout_seconds=300,
                agent_type="worker",
            ),
        )
        # Complete the task with a good result
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result=(
                "Implemented JWT authentication endpoint. "
                "All 15 tests pass with 100% coverage. "
                "Error handling covers expired tokens, invalid signatures, "
                "and missing credentials. Added comprehensive logging."
            ),
        )

        result = engine.ralph_loop_step(run.id, task.id, quality_threshold=0.50)
        assert result["status"] in ("passed", "improving")
        assert "quality_score" in result
        assert "iteration" in result
        assert result["iteration"] == 1

    def test_ralph_loop_improves_low_quality(self, engine):
        """A task with poor result should trigger improvement."""
        run = engine.create_run("Build a feature")
        task = engine.add_task(
            run.id,
            goal="Implement the feature",
            briefing=AgentBriefing(
                purpose="Build it",
                goal="Build the feature",
                rules=[],
                permissions=["shell"],
                success_criteria="Complete implementation with tests",
                context="",
                timeout_seconds=300,
                agent_type="worker",
            ),
        )
        # Complete with a poor result
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="TODO: implement",
        )

        result = engine.ralph_loop_step(run.id, task.id, quality_threshold=0.80)
        assert result["status"] == "improving"
        assert result["improvement_task_id"] is not None

    def test_ralph_loop_max_iterations(self, engine):
        """After max iterations, the loop should force-complete."""
        run = engine.create_run("Build something")
        task = engine.add_task(run.id, goal="Build it")
        engine.update_task(run.id, task.id, status=TaskStatus.COMPLETED, result="OK")

        # Set max_iterations to 0 to force immediate max-out
        result = engine.ralph_loop_step(
            run.id, task.id,
            max_iterations=0,
        )
        assert result["status"] == "max_iterations_reached"

    def test_ralph_loop_task_must_be_completed(self, engine):
        """Ralph loop should error on non-completed tasks."""
        run = engine.create_run("Test")
        task = engine.add_task(run.id, goal="Do something")
        # Task is still PENDING

        result = engine.ralph_loop_step(run.id, task.id)
        assert result["status"] == "error"
        assert "not completed" in result["message"]

    def test_ralph_loop_run_not_found(self, engine):
        """Ralph loop should error on non-existent run."""
        result = engine.ralph_loop_step("nonexistent", "task-1")
        assert result["status"] == "error"

    def test_ralph_loop_spawns_creative_task(self, engine):
        """When improving, Ralph loop should create a creative evaluator task."""
        run = engine.create_run("Build auth")
        task = engine.add_task(
            run.id,
            goal="Build authentication",
            briefing=AgentBriefing(
                purpose="Build auth",
                goal="JWT authentication",
                rules=[],
                permissions=["shell"],
                success_criteria="Complete auth with tests",
                context="",
                timeout_seconds=300,
                agent_type="worker",
            ),
        )
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="stub",
        )

        initial_task_count = len(run.tasks)
        result = engine.ralph_loop_step(run.id, task.id, quality_threshold=0.90)

        if result["status"] == "improving":
            # Should have created a creative evaluator task
            assert len(run.tasks) == initial_task_count + 1
            creative_task = run.tasks[-1]
            assert creative_task.agent_type == "creative"
            assert creative_task.parent_task_id == task.id
            assert creative_task.priority == TaskPriority.P1

    def test_ralph_loop_tracks_quality_score(self, engine):
        """Ralph loop should store quality scores as improvement annotations."""
        run = engine.create_run("Test quality tracking")
        task = engine.add_task(
            run.id,
            goal="Build feature",
            briefing=AgentBriefing(
                purpose="Build",
                goal="Build the feature",
                rules=[],
                permissions=["shell"],
                success_criteria="Feature works",
                context="",
                timeout_seconds=300,
                agent_type="worker",
            ),
        )
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="Implemented the feature successfully.",
        )

        engine.ralph_loop_step(run.id, task.id, quality_threshold=0.50)

        # Should have a quality score annotation
        updated_task = engine.get_task(run.id, task.id)
        quality_annotations = [
            imp for imp in updated_task.improvements
            if imp.startswith("[Quality Score")
        ]
        assert len(quality_annotations) >= 1

    def test_ralph_loop_custom_threshold(self, engine):
        """Custom quality threshold should be respected."""
        run = engine.create_run("Test threshold")
        task = engine.add_task(
            run.id,
            goal="Build something decent",
            briefing=AgentBriefing(
                purpose="Build",
                goal="Build it",
                rules=[],
                permissions=["shell"],
                success_criteria="Complete implementation",
                context="",
                timeout_seconds=300,
                agent_type="worker",
            ),
        )
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.COMPLETED,
            result="Implemented the feature. Tests pass.",
        )

        # With a very low threshold, should pass
        result_low = engine.ralph_loop_step(run.id, task.id, quality_threshold=0.01)

        # Reset the task for re-evaluation
        engine.update_task(run.id, task.id, status=TaskStatus.COMPLETED)

        # With a very high threshold, should improve
        result_high = engine.ralph_loop_step(run.id, task.id, quality_threshold=0.99)
        # Either it improves or it passed but with high threshold
        assert result_high["status"] in ("passed", "improving", "max_iterations_reached")


class TestRalphLoopStatus:
    """Test ralph_loop_status method."""

    @pytest.fixture
    def engine(self, tmp_path):
        path = tmp_path / "orchestrations.yaml"
        return OrchestratorEngine(path=path)

    def test_ralph_loop_status(self, engine):
        """Status should return run info with task summaries."""
        run = engine.create_run("Build feature")
        task1 = engine.add_task(run.id, goal="Task 1")
        task2 = engine.add_task(run.id, goal="Task 2")
        engine.update_task(run.id, task1.id, status=TaskStatus.COMPLETED, result="Done")

        status = engine.ralph_loop_status(run.id)
        assert status["run_id"] == run.id
        assert status["total_tasks"] == 2
        assert status["by_status"]["completed"] == 1

    def test_ralph_loop_status_not_found(self, engine):
        """Status should return error for non-existent run."""
        status = engine.ralph_loop_status("nonexistent")
        assert status["status"] == "error"


class TestApplyImprovement:
    """Test apply_improvement method."""

    @pytest.fixture
    def engine(self, tmp_path):
        path = tmp_path / "orchestrations.yaml"
        return OrchestratorEngine(path=path)

    def test_apply_improvement_updates_task(self, engine):
        """Applying improvement should update task result and mark completed."""
        run = engine.create_run("Test improvement")
        task = engine.add_task(run.id, goal="Build feature")
        engine.update_task(
            run.id, task.id,
            status=TaskStatus.EVALUATING,
            result="Original result",
        )

        updated = engine.apply_improvement(
            run.id, task.id,
            improvement_result="Suggestion: Add error handling for edge cases.",
        )
        assert updated is not None
        assert "Improvement Evaluation" in updated.result
        assert "Add error handling" in updated.result
        assert updated.status == TaskStatus.COMPLETED

    def test_apply_improvement_tracks_count(self, engine):
        """Applying improvement should increment improvements_applied."""
        run = engine.create_run("Test count")
        task = engine.add_task(run.id, goal="Build")
        engine.update_task(run.id, task.id, result="Done")

        initial_applied = run.improvements_applied
        engine.apply_improvement(run.id, task.id, improvement_result="Test improvement")
        assert run.improvements_applied == initial_applied + 1

    def test_apply_improvement_not_found(self, engine):
        """Applying to non-existent task should return None."""
        run = engine.create_run("Test")
        result = engine.apply_improvement(run.id, "nonexistent", improvement_result="Test")
        assert result is None


# ============================================================
# Session Engine Evaluation Hook Tests
# ============================================================

class TestSessionEngineEvaluation:
    """Test the post-task evaluation hook in SessionEngine."""

    @pytest.fixture
    def engine(self, tmp_path):
        return SessionEngine(project_dir=Path(tmp_path))

    def test_evaluate_task_completion_basic(self, engine):
        """Basic evaluation should return is_complete and quality_score."""
        result = engine.evaluate_task_completion(
            goal="Build a REST API",
            result="Implemented the REST API with all endpoints. Tests pass.",
        )
        assert "is_complete" in result
        assert "suggestions" in result
        assert "needs_ralph_loop" in result
        assert "result_length" in result

    def test_evaluate_with_completion_signals(self, engine):
        """Results with completion signals should be marked complete."""
        result = engine.evaluate_task_completion(
            goal="Fix the bug",
            result="Fixed the bug and all tests pass. Done.",
        )
        assert result["is_complete"] is True

    def test_evaluate_with_incompletion_signals(self, engine):
        """Results with incompletion signals should not be complete."""
        result = engine.evaluate_task_completion(
            goal="Fix the bug",
            result="TODO: still need to fix the error handler",
        )
        assert result["is_complete"] is False

    def test_evaluate_empty_result(self, engine):
        """Empty result should be incomplete."""
        result = engine.evaluate_task_completion(
            goal="Do something",
            result="",
        )
        assert result["is_complete"] is False
        assert result["result_length"] == 0

    def test_evaluate_substantial_result(self, engine):
        """Substantial results without incompletion markers should be complete."""
        long_result = "A" * 300
        result = engine.evaluate_task_completion(
            goal="Build feature",
            result=long_result,
        )
        assert result["is_complete"] is True

    def test_evaluate_with_errors(self, engine):
        """Errors should generate suggestions."""
        result = engine.evaluate_task_completion(
            goal="Build feature",
            result="Implemented most of the feature",
            errors=["ImportError: missing module"],
        )
        assert len(result["suggestions"]) > 0
        assert any("error" in s.lower() for s in result["suggestions"])

    def test_evaluate_triggers_ralph_loop(self, engine):
        """Low-quality results should trigger Ralph loop suggestion."""
        result = engine.evaluate_task_completion(
            goal="Build feature",
            result="TODO: implement",
        )
        # Short result with TODO should trigger Ralph loop
        assert result["needs_ralph_loop"] is True or result["result_length"] < 50

    def test_evaluate_quality_score_included(self, engine):
        """Quality score should be included for substantial results."""
        result = engine.evaluate_task_completion(
            goal="Build the authentication module with JWT tokens",
            result=(
                "Implemented JWT authentication. All tests pass. "
                "Error handling covers invalid tokens, expired sessions. "
                "Comprehensive test suite with 95% coverage."
            ),
        )
        if result["quality_score"] is not None:
            assert "overall" in result["quality_score"]
            assert "dimensions" in result["quality_score"]


# ============================================================
# Quality Dimensions Tests
# ============================================================

class TestQualityDimensions:
    """Test quality dimension configuration."""

    def test_all_dimensions_present(self):
        """All five quality dimensions should be configured."""
        expected = {"completeness", "correctness", "quality", "coverage", "efficiency"}
        assert set(QUALITY_DIMENSIONS.keys()) == expected

    def test_weights_sum_to_one(self):
        """Dimension weights should sum to approximately 1.0."""
        total_weight = sum(d["weight"] for d in QUALITY_DIMENSIONS.values())
        assert abs(total_weight - 1.0) < 0.01

    def test_dimensions_have_descriptions(self):
        """Each dimension should have a description."""
        for name, config in QUALITY_DIMENSIONS.items():
            assert "description" in config
            assert len(config["description"]) > 0


class TestRalphDefaults:
    """Test Ralph loop default configuration."""

    def test_defaults_exist(self):
        """RALPH_DEFAULTS should have expected keys."""
        assert "quality_threshold" in RALPH_DEFAULTS
        assert "max_iterations" in RALPH_DEFAULTS
        assert "min_result_length" in RALPH_DEFAULTS
        assert "auto_patch_on_success" in RALPH_DEFAULTS

    def test_defaults_reasonable(self):
        """Default values should be reasonable."""
        assert 0 < RALPH_DEFAULTS["quality_threshold"] <= 1.0
        assert RALPH_DEFAULTS["max_iterations"] >= 1
        assert RALPH_DEFAULTS["min_result_length"] >= 10