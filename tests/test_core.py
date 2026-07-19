"""Tests for Sawyer Harness core components."""

from sawyer_harness.config import HarnessConfig
from sawyer_harness.memory import MemoryStore
from sawyer_harness.tools import create_default_registry


def test_config_defaults():
    """Config loads with sensible defaults."""
    config = HarnessConfig()
    assert config.llm.provider == "ollama"
    assert config.security.sandbox is True
    assert config.memory.backend == "sqlite"


def test_config_from_file(tmp_path):
    """Config loads from YAML file with env var interpolation."""
    import os
    os.environ["TEST_HARNESS_KEY"] = "sk-test-123"

    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
llm:
  provider: sawyer
  model: sawyer-default
  api_key: ${TEST_HARNESS_KEY}
security:
  sandbox: true
  allowed_tools: [shell, file_read]
""")
    config = HarnessConfig.from_file(config_file)
    assert config.llm.provider == "sawyer"
    assert config.llm.api_key == "sk-test-123"
    assert config.security.allowed_tools == ["shell", "file_read"]


def test_memory_store(tmp_path):
    """Memory store CRUD operations work."""
    db = tmp_path / "test.db"
    store = MemoryStore(str(db))

    store.add("user_name", "Dave", category="user")
    assert store.get("user_name") == "Dave"

    store.add("user_name", "David", category="user")  # update
    assert store.get("user_name") == "David"

    results = store.search("Dav")
    assert len(results) == 1

    store.delete("user_name")
    assert store.get("user_name") is None

    store.close()


def test_memory_char_count(tmp_path):
    """Memory reports total character count."""
    db = tmp_path / "test.db"
    store = MemoryStore(str(db))
    assert store.total_chars() == 0

    store.add("key1", "A" * 100)
    assert store.total_chars() == 100

    store.add("key2", "B" * 50)
    assert store.total_chars() == 150

    store.close()


def test_tool_registry():
    """Tool registry lists and executes tools."""
    registry = create_default_registry()

    # Should have built-in tools
    schemas = registry.list_tools()
    names = [s["function"]["name"] for s in schemas]
    assert "shell" in names
    assert "file_read" in names
    assert "file_write" in names


def test_tool_registry_allowlist():
    """Tool registry respects allowlist."""
    registry = create_default_registry(allowed_tools=["file_read"])
    schemas = registry.list_tools()
    names = [s["function"]["name"] for s in schemas]
    assert names == ["file_read"]


def test_tool_execution_file(tmp_path):
    """File read/write tools work end-to-end."""
    registry = create_default_registry()

    test_file = tmp_path / "test.txt"
    write_result = registry.execute("file_write", {
        "path": str(test_file),
        "content": "Hello, world!",
    })
    assert write_result.success

    read_result = registry.execute("file_read", {"path": str(test_file)})
    assert read_result.success
    assert "Hello, world!" in read_result.output


def test_tool_audit_log(tmp_path):
    """Tool executions are audit-logged."""
    registry = create_default_registry()

    registry.execute("file_write", {
        "path": str(tmp_path / "audit.txt"),
        "content": "test",
    })

    trail = registry.audit_trail()
    assert len(trail) == 1
    assert trail[0]["tool"] == "file_write"
    assert trail[0]["success"] is True


def test_deny_path(tmp_path):
    """Denied paths are blocked."""
    registry = create_default_registry(denied_paths=["/etc/passwd"])

    result = registry.execute("file_read", {"path": "/etc/passwd"})
    assert result.success is False
    assert "Access denied" in result.error