"""Tests for tool registry and shell self-termination blocking."""
import os
from sawyer_harness.tools import create_default_registry


class TestShellSelfKillBlocking:
    """Agent must not be able to kill its own PID or process type."""

    def setup_method(self):
        self.registry = create_default_registry()

    def test_block_kill_own_pid(self):
        pid = os.getpid()
        result = self.registry.execute("shell", {"command": f"kill {pid}"})
        assert not result.success
        assert "BLOCKED" in result.error
        assert str(pid) in result.error

    def test_block_kill_9_own_pid(self):
        pid = os.getpid()
        result = self.registry.execute("shell", {"command": f"kill -9 {pid}"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_block_kill_15_own_pid(self):
        pid = os.getpid()
        result = self.registry.execute("shell", {"command": f"kill -15 {pid}"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_block_taskkill_own_pid(self):
        pid = os.getpid()
        result = self.registry.execute("shell", {"command": f"taskkill /PID {pid}"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_block_taskkill_f_own_pid(self):
        pid = os.getpid()
        result = self.registry.execute("shell", {"command": f"taskkill /F /PID {pid}"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_block_killall_python(self):
        result = self.registry.execute("shell", {"command": "killall python"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_block_pkill_python(self):
        result = self.registry.execute("shell", {"command": "pkill python"})
        assert not result.success
        assert "BLOCKED" in result.error

    def test_allow_other_commands(self):
        """Non-kill commands should pass through normally."""
        result = self.registry.execute("shell", {"command": "echo hello"})
        assert result.success
        assert "hello" in result.output

    def test_allow_kill_other_pid(self):
        """Killing a different PID (not our own) is allowed."""
        fake_pid = 999999
        result = self.registry.execute("shell", {"command": f"kill {fake_pid}"})
        # It will fail because PID doesn't exist, but it should NOT be BLOCKED
        assert "BLOCKED" not in result.error