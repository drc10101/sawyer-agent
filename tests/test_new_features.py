"""Integration tests for session scoring, LKG, agreeability, and reasoning."""

import tempfile


from sawyer_harness.config import (
    HarnessConfig,
    AGREEABILITY_LEVELS,
    REASONING_LEVELS,
)
from sawyer_harness.scoring import SessionScore, SCORING_QUESTIONS, compute_trends
from sawyer_harness.lkg import LKGEntry


class TestAgreeabilityReasoningConfig:
    """Test agreeability and reasoning config and validation."""

    def test_config_defaults(self):
        config = HarnessConfig()
        assert config.agent.agreeability == "balanced"
        assert config.agent.reasoning == "medium"

    def test_all_agreeability_levels_valid(self):
        for level in AGREEABILITY_LEVELS:
            config = HarnessConfig()
            config.agent.agreeability = level
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                path = config.save(f.name)
                loaded = HarnessConfig.from_file(path)
                assert loaded.agent.agreeability == level

    def test_all_reasoning_levels_valid(self):
        for level in REASONING_LEVELS:
            config = HarnessConfig()
            config.agent.reasoning = level
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                path = config.save(f.name)
                loaded = HarnessConfig.from_file(path)
                assert loaded.agent.reasoning == level

    def test_invalid_agreeability_resets_to_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("agent:\n  agreeability: invalid\n  reasoning: medium\n")
            f.flush()
            loaded = HarnessConfig.from_file(f.name)
            assert loaded.agent.agreeability == "balanced"

    def test_invalid_reasoning_resets_to_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("agent:\n  agreeability: balanced\n  reasoning: extreme\n")
            f.flush()
            loaded = HarnessConfig.from_file(f.name)
            assert loaded.agent.reasoning == "medium"

    def test_config_round_trip(self):
        config = HarnessConfig()
        config.agent.agreeability = "honest"
        config.agent.reasoning = "high"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            path = config.save(f.name)
            loaded = HarnessConfig.from_file(path)
            assert loaded.agent.agreeability == "honest"
            assert loaded.agent.reasoning == "high"


class TestSessionScoring:
    """Test session scoring module."""

    def test_create_score(self):
        score = SessionScore(
            session_id="test-001",
            scores={
                "task_complete": 4,
                "accuracy": 5,
                "autonomy": 3,
                "communication": 4,
                "honesty": 5,
                "speed": 3,
            },
            free_text="Good session",
        )
        assert score.average() == 4.0

    def test_score_save_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sawyer_harness.scoring.SCORES_DIR", tmp_path / "scores")
        score = SessionScore(
            session_id="test-002",
            scores={
                "task_complete": 5,
                "accuracy": 4,
                "autonomy": 5,
                "communication": 4,
                "honesty": 3,
                "speed": 4,
            },
        )
        score.save()
        loaded = SessionScore.load("test-002")
        assert loaded is not None
        assert loaded.session_id == "test-002"
        assert loaded.scores["task_complete"] == 5

    def test_compute_trends(self):
        scores = [
            SessionScore(session_id="s1", scores={"task_complete": 4, "accuracy": 5}),
            SessionScore(session_id="s2", scores={"task_complete": 3, "accuracy": 4}),
        ]
        trends = compute_trends(scores)
        assert trends["task_complete"] == 3.5
        assert trends["accuracy"] == 4.5
        assert trends["_overall"] == 4.0

    def test_scoring_questions_exist(self):
        assert len(SCORING_QUESTIONS) == 6
        assert "honesty" in SCORING_QUESTIONS
        assert "task_complete" in SCORING_QUESTIONS
        assert "accuracy" in SCORING_QUESTIONS
        assert "autonomy" in SCORING_QUESTIONS
        assert "communication" in SCORING_QUESTIONS
        assert "speed" in SCORING_QUESTIONS

    def test_empty_scores_average(self):
        score = SessionScore(session_id="empty", scores={})
        assert score.average() == 0.0


class TestLKG:
    """Test last-known-good version tracking."""

    def test_lkg_entry_creation(self):
        entry = LKGEntry(commit="abc123", tag="test-tag", note="Working well")
        assert entry.commit == "abc123"
        assert entry.tag == "test-tag"
        assert entry.note == "Working well"

    def test_lkg_entry_auto_timestamp(self):
        entry = LKGEntry(commit="abc123")
        assert entry.timestamp  # Auto-generated

    def test_lkg_store_mark_and_list(self, tmp_path):
        from sawyer_harness.lkg import LKGStore

        store = LKGStore(path=tmp_path / "lkg.json")
        store.mark_good(commit="aaa", tag="first", note="Stable release")
        store.mark_good(commit="bbb", tag="second", note="Post hotfix")
        entries = store.list_all()
        assert len(entries) == 2
        # Newest first
        assert entries[0].tag == "second"

    def test_lkg_store_get_latest(self, tmp_path):
        from sawyer_harness.lkg import LKGStore

        store = LKGStore(path=tmp_path / "lkg.json")
        store.mark_good(commit="aaa", tag="first")
        store.mark_good(commit="bbb", tag="second")
        latest = store.get_latest()
        assert latest.tag == "second"

    def test_lkg_persistence(self, tmp_path):
        from sawyer_harness.lkg import LKGStore

        path = tmp_path / "lkg.json"
        store1 = LKGStore(path=path)
        store1.mark_good(commit="abc", tag="persistent-tag")
        # Create new store instance from same file
        store2 = LKGStore(path=path)
        entries = store2.list_all()
        assert len(entries) == 1
        assert entries[0].tag == "persistent-tag"

    def test_lkg_get_latest_empty(self, tmp_path):
        from sawyer_harness.lkg import LKGStore

        store = LKGStore(path=tmp_path / "lkg.json")
        assert store.get_latest() is None