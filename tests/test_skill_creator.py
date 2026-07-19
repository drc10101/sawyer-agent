"""Tests for Sawyer Harness SkillCreator."""

import pytest

from sawyer_harness.skill_creator import (
    SkillCreator,
    SessionPhase,
    SessionStatus,
)
from sawyer_harness.skills import SkillStore


@pytest.fixture
def creator(tmp_path):
    """Create a SkillCreator with a temporary skill store."""
    store = SkillStore(skills_dir=tmp_path / "skills")
    store.skills_dir.mkdir(parents=True, exist_ok=True)
    return SkillCreator(skill_store=store)


class TestSkillCreatorSessions:
    def test_create_session(self, creator):
        session = creator.create_session()
        assert session.id
        assert session.phase == SessionPhase.OBSERVE
        assert session.status == SessionStatus.ACTIVE
        assert session.spec.name == ""

    def test_get_session(self, creator):
        session = creator.create_session()
        retrieved = creator.get_session(session.id)
        assert retrieved is not None
        assert retrieved.id == session.id

    def test_list_sessions(self, creator):
        creator.create_session()
        creator.create_session()
        sessions = creator.list_sessions()
        assert len(sessions) == 2

    def test_list_sessions_by_status(self, creator):
        creator.create_session()
        sessions = creator.list_sessions(status=SessionStatus.ACTIVE)
        assert len(sessions) == 1
        sessions = creator.list_sessions(status=SessionStatus.COMPLETED)
        assert len(sessions) == 0


class TestObserveMessage:
    def test_observe_repeat_signal(self, creator):
        session = creator.create_session()
        signals = creator.observe_message(
            session.id, "user", "I keep having to rebuild the database"
        )
        assert "friction_task" in signals

    def test_observe_routine_signal(self, creator):
        session = creator.create_session()
        signals = creator.observe_message(
            session.id, "user", "Every time I deploy I have to check the logs"
        )
        assert "routine_task" in signals

    def test_observe_automation_request(self, creator):
        session = creator.create_session()
        signals = creator.observe_message(
            session.id, "user", "Let's automate the deployment process"
        )
        assert "automation_request" in signals

    def test_observe_no_signal(self, creator):
        session = creator.create_session()
        signals = creator.observe_message(
            session.id, "user", "What's the weather like?"
        )
        assert len(signals) == 0

    def test_observe_records_notes(self, creator):
        session = creator.create_session()
        creator.observe_message(session.id, "user", "I always forget the API key")
        assert len(session.observation_notes) >= 1


class TestTheorize:
    def test_theorize_basic(self, creator):
        session = creator.create_session()
        spec = creator.theorize(
            session.id,
            task_description="Deploy the application to production",
        )
        assert spec.name
        assert spec.category in ("devops", "general")
        assert spec.description
        assert len(spec.triggers) > 0
        assert len(spec.procedure) > 0

    def test_theorize_debugging_task(self, creator):
        session = creator.create_session()
        spec = creator.theorize(
            session.id,
            task_description="Debug the authentication error in the API",
        )
        assert spec.category == "debugging"
        assert "debug" in spec.triggers or "error" in spec.triggers

    def test_theorize_updates_phase(self, creator):
        session = creator.create_session()
        assert session.phase == SessionPhase.OBSERVE
        creator.theorize(session.id, "Test the login flow")
        assert session.phase == SessionPhase.THEORIZE

    def test_theorize_with_context(self, creator):
        session = creator.create_session()
        spec = creator.theorize(
            session.id,
            task_description="Create a new REST endpoint",
            context="This is for the user management module",
        )
        assert spec.name
        assert len(spec.procedure) > 0

    def test_theorize_invalid_session(self, creator):
        with pytest.raises(ValueError):
            creator.theorize("nonexistent", "do something")


class TestRefine:
    def test_refine_name(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        spec = creator.refine(session.id, {"name": "production-deploy"})
        assert spec.name == "production-deploy"

    def test_refine_category(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Fix the bug")
        spec = creator.refine(session.id, {"category": "debugging"})
        assert spec.category == "debugging"

    def test_refine_procedure(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Create a feature")
        new_steps = ["Step 1: Plan", "Step 2: Build", "Step 3: Test"]
        spec = creator.refine(session.id, {"procedure": new_steps})
        assert spec.procedure == new_steps

    def test_refine_triggers(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        spec = creator.refine(session.id, {"triggers": ["deploy", "ship", "release"]})
        assert spec.triggers == ["deploy", "ship", "release"]

    def test_refine_pitfalls(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        pitfalls = ["Never deploy on Friday", "Always have a rollback plan"]
        spec = creator.refine(session.id, {"pitfalls": pitfalls})
        assert spec.pitfalls == pitfalls

    def test_refine_increments_revision(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        assert session.revision_count == 0
        creator.refine(session.id, {"name": "better-name"})
        assert session.revision_count == 1
        creator.refine(session.id, {"name": "even-better"})
        assert session.revision_count == 2

    def test_refine_updates_phase(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        creator.refine(session.id, {"name": "deploy"})
        assert session.phase == SessionPhase.REFINE


class TestApprove:
    def test_approve_creates_skill(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app to production")
        creator.refine(session.id, {"name": "production-deploy"})

        skill = creator.approve(session.id)
        assert skill.name == "production-deploy"
        assert skill.category
        assert skill.version == 1
        assert session.status == SessionStatus.COMPLETED
        assert session.phase == SessionPhase.APPROVE

    def test_approve_saves_to_store(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        creator.approve(session.id)

        # Verify it's in the skill store
        skills = creator.skill_store.list_skills()
        assert len(skills) >= 1

    def test_approve_invalid_session(self, creator):
        with pytest.raises(ValueError):
            creator.approve("nonexistent")

    def test_approve_from_observe_fails(self, creator):
        session = creator.create_session()
        # Session is in OBSERVE phase, should not be approvable
        with pytest.raises(ValueError):
            creator.approve(session.id)


class TestReject:
    def test_reject_session(self, creator):
        session = creator.create_session()
        creator.theorize(session.id, "Deploy the app")
        creator.reject(session.id)
        assert session.status == SessionStatus.ABANDONED


class TestSuggestSkillCreation:
    def test_suggest_on_repetition(self, creator):
        messages = [
            {"role": "user", "content": "I keep having to rebuild the database"},
            {"role": "user", "content": "I always forget the migration command"},
            {"role": "user", "content": "This is tedious, can we automate it?"},
        ]
        suggestion = creator.suggest_skill_creation(messages)
        assert suggestion is not None
        assert suggestion["signal_type"]
        assert suggestion["confidence"] > 0

    def test_no_suggestion_for_normal_chat(self, creator):
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "It's sunny."},
        ]
        suggestion = creator.suggest_skill_creation(messages)
        assert suggestion is None

    def test_no_suggestion_insufficient_messages(self, creator):
        suggestion = creator.suggest_skill_creation([])
        assert suggestion is None


class TestSpecToMarkdown:
    def test_full_spec_to_markdown(self, creator):
        session = creator.create_session()
        creator.theorize(
            session.id,
            "Deploy the application to production",
        )
        # Refine with full details
        creator.refine(session.id, {
            "name": "production-deploy",
            "description": "Deploy the app to production safely",
            "procedure": [
                "Run all tests",
                "Build the artifact",
                "Deploy to staging first",
                "Verify staging health",
                "Deploy to production",
                "Verify production health",
            ],
            "pitfalls": [
                "Never deploy on Friday",
                "Always have a rollback plan",
            ],
            "constraints": [
                "Must pass all tests before deploy",
            ],
        })
        skill = creator.approve(session.id)

        # Verify the skill content includes all sections
        assert "# production-deploy" in skill.content
        assert "## Procedure" in skill.content
        assert "## Pitfalls" in skill.content
        assert "## Constraints" in skill.content
        assert "Never deploy on Friday" in skill.content