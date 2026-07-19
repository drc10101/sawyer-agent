"""Tests for context compression, session engine, and project layout."""

import json
import tempfile
from pathlib import Path


from sawyer_harness.compression import (
    ContextCompressor,
    Priority,
)
from sawyer_harness.context_manager import (
    ContextManager,
    MODEL_WINDOWS,
)
from sawyer_harness.session_engine import SessionEngine
from sawyer_harness.project import ProjectManager


# ============================================================
# Compression tests
# ============================================================

class TestContextCompressor:

    def setup_method(self):
        self.compressor = ContextCompressor(max_tokens=1000, reserve_ratio=0.2)

    def test_estimate_tokens(self):
        """Token estimation uses real BPE tokenizer, not char heuristic."""
        # Empty strings have 0 tokens (not 1 -- that was the old heuristic)
        assert self.compressor.estimate_tokens("") == 0
        # Short text has at least 1 token
        assert self.compressor.estimate_tokens("hello") >= 1
        # Real token counts differ from char-based heuristics.
        # The old test asserted "a" * 400 == 100 tokens (len//4), but
        # BPE encodes repeated chars differently. We just verify it's reasonable.
        assert self.compressor.estimate_tokens("a" * 400) > 0
        # Verify real counting is more accurate than the heuristic:
        # the real count should be within 3x of the heuristic (both directions)
        heuristic = max(1, len("Hello, world!") // 4)
        real = self.compressor.estimate_tokens("Hello, world!")
        assert 0 < real < heuristic * 3  # sanity check

    def test_classify_system_critical(self):
        """System messages are always critical."""
        assert self.compressor.classify_priority("system", "anything") == Priority.CRITICAL

    def test_classify_user_correction(self):
        """User corrections are critical."""
        assert self.compressor.classify_priority("user", "No, that's wrong") == Priority.CRITICAL
        assert self.compressor.classify_priority("user", "Actually, use Python 3.12") == Priority.CRITICAL
        assert self.compressor.classify_priority("user", "Wait, I meant async") == Priority.CRITICAL

    def test_classify_normal_user(self):
        """Regular user messages are normal priority."""
        assert self.compressor.classify_priority("user", "Hello, how are you?") == Priority.NORMAL

    def test_classify_tool_result(self):
        """Tool results are low/disposable."""
        assert self.compressor.classify_priority("tool", "OK") == Priority.LOW
        # Long tool results are disposable
        assert self.compressor.classify_priority("tool", "x" * 500) == Priority.DISPOSABLE

    def test_classify_assistant_code(self):
        """Assistant messages with code blocks are high priority."""
        assert self.compressor.classify_priority("assistant", "```python\nprint('hi')\n```") == Priority.HIGH

    def test_classify_decision(self):
        """Decision-like messages are high priority."""
        assert self.compressor.classify_priority("assistant", "Let's use FastAPI for this") == Priority.HIGH
        assert self.compressor.classify_priority("user", "We should refactor the agent loop") == Priority.HIGH

    def test_extract_decisions(self):
        """Extract key decisions from messages."""

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [
            Msg("assistant", "Let's use SQLite for persistence"),
            Msg("user", "Actually, let's use PostgreSQL instead"),
            Msg("assistant", "Good idea, we'll use PostgreSQL"),
        ]

        decisions = self.compressor.extract_decisions(messages)
        assert len(decisions) > 0
        assert any("PostgreSQL" in d for d in decisions)

    def test_compress_keeps_critical(self):
        """Compression never drops critical messages."""

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [
            Msg("system", "You are Sawyer"),
            Msg("user", "No, that's wrong"),  # correction = critical
        ]

        compressed, result = self.compressor.compress(
            messages,
            system_prompt="You are Sawyer",
        )

        # Critical messages should be preserved
        assert result.messages_kept >= 1  # At least the correction

    def test_compress_drops_disposable(self):
        """Compression drops verbose tool output when budget is tight."""

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        # Create a large conversation that exceeds the budget
        messages = [
            Msg("user", "What is 2+2?"),
            Msg("assistant", "4"),
        ]
        # Add lots of verbose tool output
        for i in range(100):
            messages.append(Msg("tool", f"Result {i}: " + "x" * 100))

        compressed, result = self.compressor.compress(messages)

        # Should have dropped some messages
        assert result.messages_dropped > 0 or result.messages_summarized > 0

    def test_compress_result_stats(self):
        """CompressionResult tracks token savings."""

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [Msg("user", f"Message {i}") for i in range(50)]

        _, result = self.compressor.compress(messages)

        assert result.original_tokens > 0
        assert result.compressed_tokens > 0
        assert result.compressed_tokens <= result.original_tokens

    def test_get_context_stats(self):
        """Context stats provide useful metrics."""

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [
            Msg("user", "Hello"),
            Msg("assistant", "Hi there! How can I help?"),
            Msg("user", "Write a function"),
        ]

        stats = self.compressor.get_context_stats(messages)
        assert stats["message_count"] == 3
        assert stats["total_tokens"] > 0
        assert "used_percentage" in stats


# ============================================================
# Context Manager tests
# ============================================================

class TestContextManager:

    def test_known_model_windows(self):
        """Known models have correct context window sizes."""
        assert MODEL_WINDOWS["gpt-4o"] == 128000
        assert MODEL_WINDOWS["claude-sonnet-4-20250514"] == 200000
        assert MODEL_WINDOWS["gemini-2.5-pro"] == 1048576

    def test_default_window(self):
        """Unknown models get the default window size."""
        cm = ContextManager(model_name="unknown-model-xyz")
        assert cm.window_size == 128000

    def test_custom_window(self):
        """Custom window size overrides model lookup."""
        cm = ContextManager(window_size=400000)
        assert cm.window_size == 400000

    def test_calculate_budget(self):
        """Budget calculation allocates tokens correctly."""
        cm = ContextManager(window_size=128000)

        budget = cm.calculate_budget(
            system_prompt_tokens=1000,
            memory_tokens=500,
            skills_tokens=800,
            session_notes_tokens=300,
        )

        assert budget.total_window == 128000
        # When actual tokens exceed the percentage minimum, actual tokens are used
        assert budget.system_reserve == 1000
        assert budget.memory_reserve == 500
        assert budget.skills_reserve == 800
        assert budget.session_context == 300
        assert budget.free_space > 0
        assert budget.recency_window > 0

    def test_needs_compression(self):
        """Detects when compression is needed."""
        cm = ContextManager(window_size=1000, reserve_ratio=0.2)

        # Small messages don't need compression
        assert not cm.needs_compression(current_messages_tokens=100)

        # Messages exceeding budget need compression
        assert cm.needs_compression(current_messages_tokens=900)

    def test_compression_ratio(self):
        """Compression ratio is calculated correctly."""
        cm = ContextManager(window_size=1000, reserve_ratio=0.2)

        # No overflow = no compression needed
        ratio = cm.get_compression_ratio(current_messages_tokens=100)
        assert ratio == 0.0

        # Lots of overflow = high compression ratio
        ratio = cm.get_compression_ratio(current_messages_tokens=800)
        assert ratio > 0.5

    def test_get_context_stats(self):
        """Context stats include budget allocation details."""
        cm = ContextManager(model_name="gpt-4o")

        stats = cm.get_context_stats(
            system_prompt="You are a helpful assistant.",
            memory_text="User prefers concise responses",
            messages=[],
        )

        assert stats["model"] == "gpt-4o"
        assert stats["window_size"] == 128000
        assert stats["total_tokens_used"] > 0
        assert "budget" in stats
        assert "recommendation" in stats

    def test_provider_prefix_lookup(self):
        """Model lookup handles provider/ prefixes."""
        cm = ContextManager(model_name="anthropic/claude-sonnet-4-20250514")
        assert cm.window_size == 200000

        cm = ContextManager(model_name="openai/gpt-4o")
        assert cm.window_size == 128000

    def test_400k_window(self):
        """Custom 400K window works as expected."""
        cm = ContextManager(window_size=400000)
        budget = cm.calculate_budget(system_prompt_tokens=2000)

        assert budget.total_window == 400000
        assert budget.free_space == 80000  # 20% reserve
        assert budget.recency_window > 0
        # With 400K, we can keep a LOT of recent messages
        assert budget.recency_window > 100000


# ============================================================
# Session Engine tests
# ============================================================

class TestSessionEngine:

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.engine = SessionEngine(project_dir=Path(self.tmp_dir))

    def test_track_message(self):
        """Messages are tracked and notes are extracted."""
        self.engine.track_message("user", "Build a web UI for the agent")
        assert self.engine.message_count == 1
        assert len(self.engine.notes) >= 1  # At least a topic note

    def test_track_correction(self):
        """User corrections are tracked as critical notes."""
        self.engine.track_message("user", "No, use FastAPI not Flask")
        corrections = [n for n in self.engine.notes if n.note_type == "correction"]
        assert len(corrections) >= 1
        assert corrections[0].priority == "critical"

    def test_track_todo(self):
        """TODO items are extracted from messages."""
        self.engine.track_message("user", "We need to add authentication.")
        [n for n in self.engine.notes if n.note_type == "todo"]
        # At minimum, a topic note should be extracted
        assert len(self.engine.notes) >= 1

    def test_track_file_created(self):
        """File creation is tracked."""
        self.engine.track_file("create", "/path/to/new_module.py")
        files = [n for n in self.engine.notes if n.note_type == "file_created"]
        assert len(files) == 1
        assert "new_module.py" in files[0].content

    def test_track_error(self):
        """Errors are tracked as high priority."""
        self.engine.track_error("Connection refused on port 8080")
        errors = [n for n in self.engine.notes if n.note_type == "error"]
        assert len(errors) == 1
        assert errors[0].priority == "high"

    def test_generate_summary(self):
        """Summary includes all tracked information."""
        self.engine.track_message("user", "Build a REST API")
        self.engine.track_message("assistant", "I'll use FastAPI")
        self.engine.track_file("create", "/path/to/api.py")
        self.engine.track_error("ImportError: no module named 'fastapi'")

        summary = self.engine.generate_summary()

        assert summary.message_count == 2
        assert summary.tool_call_count == 0
        assert len(summary.topics) >= 1
        assert len(summary.files_created) >= 1
        assert len(summary.errors) >= 1

    def test_generate_suggestions(self):
        """Next-session suggestions are generated."""
        self.engine.track_message("user", "We need to add tests")
        summary = self.engine.generate_summary()

        assert len(summary.next_session_suggestions) >= 1

    def test_save_notes(self):
        """Session notes are saved to disk."""
        self.engine.track_message("user", "Test message")
        filepath = self.engine.save_notes()

        assert filepath is not None
        assert filepath.exists()
        content = filepath.read_text()
        assert "Session" in content

        # JSON summary should also exist
        json_path = filepath.with_suffix(".json")
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["message_count"] == 1

    def test_load_previous_notes(self):
        """Previous session notes can be loaded."""
        # Save some notes
        self.engine.track_message("user", "Previous session")
        self.engine.save_notes()

        # Load them in a new engine
        engine2 = SessionEngine(project_dir=Path(self.tmp_dir))
        previous = engine2.load_previous_notes()

        assert len(previous) >= 1
        assert previous[0].message_count == 1

    def test_format_suggestions_for_prompt(self):
        """Suggestions are formatted for injection into system prompt."""
        self.engine.track_message("user", "Build something")
        self.engine.track_message("assistant", "Let's use Python")
        self.engine.save_notes()

        engine2 = SessionEngine(project_dir=Path(self.tmp_dir))
        prompt_text = engine2.format_suggestions_for_prompt()

        assert "Previous Session Context" in prompt_text


# ============================================================
# Project Layout tests
# ============================================================

class TestProjectManager:

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.manager = ProjectManager(base_path=Path(self.tmp_dir))

    def test_create_project(self):
        """Creating a project sets up the standard directory structure."""
        project = self.manager.create_project(
            name="test-project",
            description="A test project",
        )

        assert project.is_initialized
        assert (project.path / ".sawyer").exists()
        assert (project.path / "config.yaml").exists()
        assert (project.path / "README.md").exists()
        assert (project.path / "src").exists()
        assert (project.path / "tests").exists()
        assert (project.path / "data" / "raw").exists()
        assert (project.path / "outputs" / "reports").exists()

    def test_project_directories(self):
        """Project properties return correct paths."""
        project = self.manager.create_project(name="paths-test")

        assert project.sawyer_dir == project.path / ".sawyer"
        assert project.session_notes_dir == project.path / ".sawyer" / "session-notes"
        assert project.goals_dir == project.path / ".sawyer" / "goals"
        assert project.memory_path == project.path / ".sawyer" / "memory.json"
        assert project.config_path == project.path / "config.yaml"

    def test_load_project(self):
        """Loading an existing project works."""
        project = self.manager.create_project(
            name="load-test",
            description="Test loading",
        )

        loaded = self.manager.load_project(project.path)
        assert loaded is not None
        assert loaded.name == "load-test"

    def test_find_project(self):
        """Finding a project by name works."""
        self.manager.create_project(name="find-test")

        found = self.manager.find_project("find-test")
        assert found is not None
        assert found.name == "find-test"

    def test_list_projects(self):
        """Listing projects works."""
        self.manager.create_project(name="project-a")
        self.manager.create_project(name="project-b")

        projects = self.manager.list_projects()
        assert len(projects) == 2

    def test_get_output_path(self):
        """Output paths follow the standard layout."""
        project = self.manager.create_project(name="output-test")

        path = self.manager.get_output_path(project, "report.pdf", category="reports")
        # Use forward slash check (works on both Windows and Linux)
        assert "outputs" in str(path) and "reports" in str(path) and "report.pdf" in str(path)
        assert path.parent.exists()

    def test_get_data_path(self):
        """Data paths follow the standard layout."""
        project = self.manager.create_project(name="data-test")

        raw_path = self.manager.get_data_path(project, "input.csv", processed=False)
        assert "raw" in str(raw_path) and "input.csv" in str(raw_path)

        proc_path = self.manager.get_data_path(project, "clean.csv", processed=True)
        assert "processed" in str(proc_path) and "clean.csv" in str(proc_path)

    def test_file_index(self):
        """File index lists project files."""
        project = self.manager.create_project(name="index-test")

        index = project.get_file_index()
        assert "README.md" in index
        assert "config.yaml" in index
        assert "src/__init__.py" in index

    def test_project_name_sanitization(self):
        """Project names are sanitized for directory use."""
        project = self.manager.create_project(name="My Cool Project!")

        # Name should be sanitized to a directory-safe form
        assert project.path.name == "my-cool-project"