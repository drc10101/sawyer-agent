"""Tests for SessionStore -- SQLite-backed persistent session storage."""


import pytest

from sawyer_harness.session_store import SessionStore, init_db


@pytest.fixture
def store(tmp_path):
    """Create a SessionStore with a temp database."""
    db_path = tmp_path / "test_sessions.db"
    return SessionStore(db_path=db_path)


class TestSessionStoreInit:
    def test_creates_db_file(self, tmp_path):
        db_path = tmp_path / "test_sessions.db"
        SessionStore(db_path=db_path)
        assert db_path.exists()

    def test_init_db_idempotent(self, tmp_path):
        db_path = tmp_path / "test_sessions.db"
        init_db(db_path)
        init_db(db_path)  # Should not raise
        assert db_path.exists()


class TestSessionCRUD:
    def test_create_session(self, store):
        session = store.create_session("test-1", model="glm-5.1")
        assert session["session_id"] == "test-1"
        assert session["model"] == "glm-5.1"
        assert session["is_active"] is True
        assert session["title"] == "New Session"

    def test_create_session_with_title(self, store):
        session = store.create_session("test-2", title="My Session")
        assert session["title"] == "My Session"

    def test_get_session(self, store):
        store.create_session("test-1")
        session = store.get_session("test-1")
        assert session is not None
        assert session["session_id"] == "test-1"

    def test_get_nonexistent_session(self, store):
        assert store.get_session("nope") is None

    def test_list_sessions(self, store):
        store.create_session("s1")
        store.create_session("s2")
        store.create_session("s3")
        sessions = store.list_sessions()
        assert len(sessions) == 3

    def test_list_sessions_empty(self, store):
        sessions = store.list_sessions()
        assert sessions == []

    def test_update_session_title(self, store):
        store.create_session("test-1")
        updated = store.update_session("test-1", title="New Title")
        assert updated["title"] == "New Title"

    def test_update_session_active(self, store):
        store.create_session("test-1")
        updated = store.update_session("test-1", is_active=0)
        assert updated["is_active"] is False

    def test_delete_session(self, store):
        store.create_session("test-1")
        assert store.delete_session("test-1") is True
        assert store.get_session("test-1") is None

    def test_delete_nonexistent(self, store):
        assert store.delete_session("nope") is False

    def test_auto_title(self, store):
        store.create_session("test-1")
        title = store.auto_title("test-1", "How do I configure the router?")
        assert "configure" in title
        assert "router" in title

    def test_auto_title_truncates_long(self, store):
        store.create_session("test-1")
        long_msg = "A" * 100
        title = store.auto_title("test-1", long_msg)
        assert len(title) <= 83  # 80 chars + "..."


class TestMessages:
    def test_add_message(self, store):
        store.create_session("test-1")
        msg = store.add_message("test-1", "user", "Hello there")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello there"
        assert msg["session_id"] == "test-1"

    def test_add_message_auto_creates_session(self, store):
        msg = store.add_message("auto-1", "user", "Auto-created")
        assert msg["session_id"] == "auto-1"
        session = store.get_session("auto-1")
        assert session is not None

    def test_add_message_auto_titles(self, store):
        store.add_message("test-1", "user", "How do I set up the router?")
        session = store.get_session("test-1")
        assert "router" in session["title"].lower() or "set" in session["title"].lower()

    def test_add_message_increments_count(self, store):
        store.create_session("test-1")
        store.add_message("test-1", "user", "Hi")
        store.add_message("test-1", "assistant", "Hello")
        session = store.get_session("test-1")
        assert session["message_count"] == 2

    def test_get_messages(self, store):
        store.create_session("test-1")
        store.add_message("test-1", "user", "First")
        store.add_message("test-1", "assistant", "Second")
        store.add_message("test-1", "user", "Third")
        messages = store.get_messages("test-1")
        assert len(messages) == 3
        assert messages[0]["content"] == "First"
        assert messages[2]["content"] == "Third"

    def test_message_count(self, store):
        store.create_session("test-1")
        store.add_message("test-1", "user", "Hi")
        store.add_message("test-1", "assistant", "Hey")
        assert store.message_count("test-1") == 2

    def test_messages_deleted_with_session(self, store):
        store.create_session("test-1")
        store.add_message("test-1", "user", "Hi")
        store.delete_session("test-1")
        messages = store.get_messages("test-1")
        assert messages == []

    def test_get_messages_nonexistent_session(self, store):
        messages = store.get_messages("nope")
        assert messages == []


class TestBulkOperations:
    def test_save_conversation(self, store):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        session = store.save_conversation(
            "conv-1",
            messages=messages,
            title="Test Conversation",
            model="glm-5.1",
        )
        assert session["session_id"] == "conv-1"
        assert session["title"] == "Test Conversation"
        assert session["message_count"] == 3

        # Verify messages are stored
        stored = store.get_messages("conv-1")
        assert len(stored) == 3
        assert stored[0]["content"] == "Hello"


class TestExport:
    def test_export_markdown(self, store):
        store.create_session("test-1", title="My Session", model="glm-5.1")
        # add_message auto-titles on first user message, so the title
        # will be derived from "Hello", not "My Session"
        store.add_message("test-1", "user", "Hello")
        store.add_message("test-1", "assistant", "Hi there!")

        md = store.export_markdown("test-1")
        # Title was auto-set from first user message
        assert "Hello" in md
        assert "Hi there!" in md
        assert "glm-5.1" in md

    def test_export_nonexistent(self, store):
        assert store.export_markdown("nope") is None

    def test_export_empty_session(self, store):
        store.create_session("empty-1", title="Empty")
        md = store.export_markdown("empty-1")
        assert "Empty" in md
        assert "No messages" in md


class TestFiltering:
    def test_list_active_only(self, store):
        store.create_session("s1")
        store.create_session("s2")
        store.update_session("s2", is_active=0)
        active = store.list_sessions(active_only=True)
        assert len(active) == 1
        assert active[0]["session_id"] == "s1"

    def test_list_with_pagination(self, store):
        for i in range(5):
            store.create_session(f"s-{i}")
        page = store.list_sessions(limit=3, offset=0)
        assert len(page) == 3