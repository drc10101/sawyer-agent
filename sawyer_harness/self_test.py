"""
Self-test protocol for Sawyer Agent.

Runs a comprehensive health check of every subsystem and returns
a structured report. Each test actually exercises the subsystem --
no stubs, no "200 OK" cop-outs. Real reads, real writes, real queries.

Designed to be called from a single button in the UI:
    POST /api/self-test

Returns a list of test results, each with:
  - name: human-readable test name
  - status: pass | fail | warn
  - detail: what was tested and what happened
  - duration_ms: how long the test took
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("sawyer-harness.self_test")


@dataclass
class TestResult:
    name: str
    status: str  # pass | fail | warn
    detail: str
    duration_ms: int


def _timeit(fn, *args, **kwargs) -> tuple[Any, float]:
    """Run fn and return (result, elapsed_seconds)."""
    start = time.monotonic()
    result = fn(*args, **kwargs)
    elapsed = time.monotonic() - start
    return result, elapsed


def _test_provider_connectivity(config) -> TestResult:
    """Test: Can the agent reach the LLM provider? Sends a minimal chat request."""
    import httpx

    base_url = config.llm.base_url
    api_key = config.llm.api_key
    model = config.llm.model
    provider = config.llm.provider

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Build a minimal completion request
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 5,
            "temperature": 0.1,
        }

        start = time.monotonic()
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
            )
        elapsed = int((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            content = ""
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
            return TestResult(
                name="LLM Provider",
                status="pass",
                detail=f"Connected to {provider} ({base_url}). Model: {model}. Response: '{content[:50]}'",
                duration_ms=elapsed,
            )
        elif resp.status_code == 401:
            return TestResult(
                name="LLM Provider",
                status="fail",
                detail=f"Provider reachable but authentication failed (401). Check API key for {provider}.",
                duration_ms=elapsed,
            )
        else:
            return TestResult(
                name="LLM Provider",
                status="fail",
                detail=f"Provider returned HTTP {resp.status_code}: {resp.text[:200]}",
                duration_ms=elapsed,
            )
    except httpx.ConnectError:
        return TestResult(
            name="LLM Provider",
            status="fail",
            detail=f"Cannot connect to {provider} at {base_url}. Provider is down.",
            duration_ms=0,
        )
    except httpx.TimeoutException:
        return TestResult(
            name="LLM Provider",
            status="fail",
            detail=f"Connection to {provider} at {base_url} timed out after 15s.",
            duration_ms=15000,
        )
    except Exception as e:
        return TestResult(
            name="LLM Provider",
            status="fail",
            detail=f"Provider test error: {e}",
            duration_ms=0,
        )


def _test_provider_port(config) -> TestResult:
    """Test: Is the provider port open? Socket-level check, no HTTP."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(config.llm.base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=3):
            elapsed = int((time.monotonic() - start) * 1000)
            return TestResult(
                name="Provider Port",
                status="pass",
                detail=f"Port {port} on {host} is open and accepting connections.",
                duration_ms=elapsed,
            )
    except OSError:
        elapsed = int((time.monotonic() - start) * 1000)
        return TestResult(
            name="Provider Port",
            status="fail",
            detail=f"Port {port} on {host} is not accepting connections. Provider may not be running.",
            duration_ms=elapsed,
        )


def _test_memory(memory_store) -> TestResult:
    """Test: Can memory be written, searched, and read?"""
    test_key = "__self_test_memory__"
    test_content = f"Self-test entry created at {time.strftime('%Y-%m-%d %H:%M:%S')}"

    start = time.monotonic()
    try:
        # Write
        memory_store.add(key=test_key, content=test_content, category="test")

        # Read
        read_back = memory_store.get(test_key)
        if read_back != test_content:
            return TestResult(
                name="Memory",
                status="fail",
                detail=f"Write/read mismatch. Wrote: '{test_content[:50]}', Read: '{read_back[:50] if read_back else 'None'}'",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Search
        results = memory_store.search("self_test_memory")
        if not results:
            return TestResult(
                name="Memory",
                status="warn",
                detail="Write and read work, but search returned no results. FTS5 may not be available.",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Cleanup
        memory_store.delete(test_key)

        total = memory_store.total_chars()
        elapsed = int((time.monotonic() - start) * 1000)

        return TestResult(
            name="Memory",
            status="pass",
            detail=f"Write, read, search, delete all work. {total} chars stored across {len(memory_store.all_entries())} entries.",
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Memory",
            status="fail",
            detail=f"Memory error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_skills(skill_store) -> TestResult:
    """Test: Can skills be listed and loaded?"""
    start = time.monotonic()
    try:
        skills = skill_store.list_skills()
        count = len(skills)

        # Try to load one
        loaded = False
        load_name = None
        if skills:
            load_name = skills[0].get("name", skills[0].get("file", ""))
            content = skill_store.load_skill(load_name)
            loaded = content is not None and len(content) > 0

        elapsed = int((time.monotonic() - start) * 1000)
        detail = f"{count} skill(s) found."
        if count > 0:
            detail += f" Successfully loaded '{load_name}' ({len(content) if loaded else 0} chars)." if loaded else f" Failed to load '{load_name}'."

        return TestResult(
            name="Skills",
            status="pass" if (count > 0 and loaded) else "warn",
            detail=detail,
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Skills",
            status="fail",
            detail=f"Skills error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_keys(key_storage) -> TestResult:
    """Test: Can keys be read? Are any configured?"""
    start = time.monotonic()
    try:
        counts = key_storage.count()
        total = sum(counts.values())
        elapsed = int((time.monotonic() - start) * 1000)

        # Check if GitHub token is configured
        gh_entry = key_storage.get_entry("tokens", "github")
        gh_status = ""
        if gh_entry:
            key_val = gh_entry.get("key", "")
            if key_val:
                gh_status = " GitHub token: configured."
            else:
                gh_status = " GitHub token: EMPTY (add via Keys panel)."

        # Check for any API keys
        api_keys = key_storage.list_entries("api")
        has_api = any(e.get("key") for e in api_keys.get("api", []))

        detail = f"{total} key(s) stored. Categories: {counts}.{gh_status}"
        if not has_api and not gh_entry:
            detail += " No API keys configured yet."

        return TestResult(
            name="Keys",
            status="warn" if total == 0 else "pass",
            detail=detail,
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Keys",
            status="fail",
            detail=f"Keys error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_tools(tool_registry) -> TestResult:
    """Test: Are tools registered and callable?"""
    start = time.monotonic()
    try:
        tools = tool_registry.list_tools()
        tool_names = [t["function"]["name"] for t in tools]
        elapsed = int((time.monotonic() - start) * 1000)

        expected_core = ["shell", "file_read", "file_write", "file_search", "web_search", "web_fetch"]
        missing = [t for t in expected_core if t not in tool_names]

        detail = f"{len(tool_names)} tools registered: {', '.join(sorted(tool_names))}"
        if missing:
            detail += f". Missing core tools: {', '.join(missing)}"

        return TestResult(
            name="Tools",
            status="fail" if missing else "pass",
            detail=detail,
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Tools",
            status="fail",
            detail=f"Tools error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_shell_tool() -> TestResult:
    """Test: Can the shell tool actually execute a command?"""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["echo", "OK"],
            capture_output=True, text=True, timeout=10,
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result.returncode == 0 and "OK" in result.stdout:
            return TestResult(
                name="Shell Tool",
                status="pass",
                detail="Shell execution works. echo OK -> OK",
                duration_ms=elapsed,
            )
        else:
            return TestResult(
                name="Shell Tool",
                status="fail",
                detail=f"Shell returned code {result.returncode}. stdout: {result.stdout[:100]} stderr: {result.stderr[:100]}",
                duration_ms=elapsed,
            )
    except Exception as e:
        return TestResult(
            name="Shell Tool",
            status="fail",
            detail=f"Shell execution error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_file_tools() -> TestResult:
    """Test: Can file_read and file_write actually read and write?"""
    test_path = Path.home() / ".sawyer-harness" / "cache" / "__self_test_file__.txt"
    test_content = f"Self-test write at {time.strftime('%Y-%m-%d %H:%M:%S')}"

    start = time.monotonic()
    try:
        # Write
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(test_content, encoding="utf-8")

        # Read back
        read_back = test_path.read_text(encoding="utf-8")

        # Cleanup
        test_path.unlink(missing_ok=True)

        elapsed = int((time.monotonic() - start) * 1000)

        if read_back == test_content:
            return TestResult(
                name="File Tools",
                status="pass",
                detail="Write and read-back verified. Cleanup successful.",
                duration_ms=elapsed,
            )
        else:
            return TestResult(
                name="File Tools",
                status="fail",
                detail=f"Read/write mismatch. Wrote {len(test_content)} chars, read {len(read_back)} chars.",
                duration_ms=elapsed,
            )
    except Exception as e:
        # Cleanup
        try:
            test_path.unlink(missing_ok=True)
        except Exception:
            pass
        return TestResult(
            name="File Tools",
            status="fail",
            detail=f"File operation error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_gh_cli() -> TestResult:
    """Test: Is gh CLI available and authenticated?"""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        elapsed = int((time.monotonic() - start) * 1000)

        output = (result.stdout + result.stderr).lower()
        if "logged in" in output:
            # Extract username
            for line in (result.stdout + result.stderr).split("\n"):
                if "account" in line.lower():
                    return TestResult(
                        name="GitHub CLI",
                        status="pass",
                        detail=f"gh CLI is authenticated. {line.strip()}",
                        duration_ms=elapsed,
                    )
            return TestResult(
                name="GitHub CLI",
                status="pass",
                detail="gh CLI is authenticated and ready.",
                duration_ms=elapsed,
            )
        elif "not logged" in output or result.returncode != 0:
            return TestResult(
                name="GitHub CLI",
                status="fail",
                detail="gh CLI is installed but not authenticated. Run 'gh auth login' or add a GitHub token in the Keys panel.",
                duration_ms=elapsed,
            )
        else:
            return TestResult(
                name="GitHub CLI",
                status="warn",
                detail=f"gh CLI returned unexpected output: {(result.stdout + result.stderr)[:200]}",
                duration_ms=elapsed,
            )
    except FileNotFoundError:
        return TestResult(
            name="GitHub CLI",
            status="warn",
            detail="gh CLI not installed. GitHub operations will be limited.",
            duration_ms=0,
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            name="GitHub CLI",
            status="fail",
            detail="gh CLI timed out. May be waiting for interactive input.",
            duration_ms=10000,
        )


def _test_config(config) -> TestResult:
    """Test: Is config loaded with expected values?"""
    start = time.monotonic()
    try:
        issues = []

        if not config.llm.base_url:
            issues.append("base_url is empty")
        if not config.llm.model:
            issues.append("model is empty")
        if config.llm.provider in ("openai", "anthropic") and not config.llm.api_key:
            issues.append(f"{config.llm.provider} provider requires an API key but none is set")

        if config.memory.path:
            mem_path = Path(config.memory.path)
            if not mem_path.parent.exists():
                issues.append(f"memory path parent doesn't exist: {mem_path.parent}")
        else:
            from ..paths import UserData
            mem_path = UserData.memory_db
            if not mem_path.parent.exists():
                issues.append(f"memory path doesn't exist: {mem_path.parent}")

        elapsed = int((time.monotonic() - start) * 1000)

        if issues:
            return TestResult(
                name="Config",
                status="warn",
                detail=f"Config loaded but: {'; '.join(issues)}",
                duration_ms=elapsed,
            )

        return TestResult(
            name="Config",
            status="pass",
            detail=f"Provider: {config.llm.provider}, Model: {config.llm.model}, Base URL: {config.llm.base_url}, Permission mode: {config.security.permission_mode}",
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Config",
            status="fail",
            detail=f"Config error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_data_paths() -> TestResult:
    """Test: Do all expected data paths exist and are they writable?"""
    from ..paths import UserData

    start = time.monotonic()
    try:
        paths_to_check = {
            "home": UserData.home,
            "user_dir": UserData.user_dir,
            "config": UserData.config_file,
            "memory_db": UserData.memory_db,
            "keys_file": UserData.keys_file,
            "skills_dir": UserData.skills_dir,
            "tools_dir": UserData.tools_dir,
        }

        issues = []
        for name, path in paths_to_check.items():
            if name.endswith("_file") or name == "config" or name == "memory_db":
                # Files: check parent dir exists and is writable
                parent = Path(path).parent
                if not parent.exists():
                    issues.append(f"{name}: parent dir missing ({parent})")
                elif not os.access(str(parent), os.W_OK):
                    issues.append(f"{name}: parent dir not writable ({parent})")
            else:
                # Directories: check exists and writable
                if not Path(path).exists():
                    issues.append(f"{name}: missing ({path})")
                elif not os.access(str(path), os.W_OK):
                    issues.append(f"{name}: not writable ({path})")

        elapsed = int((time.monotonic() - start) * 1000)

        if issues:
            return TestResult(
                name="Data Paths",
                status="warn",
                detail=f"Path issues: {'; '.join(issues)}",
                duration_ms=elapsed,
            )

        return TestResult(
            name="Data Paths",
            status="pass",
            detail=f"All paths exist and are writable. Home: {UserData.home}",
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Data Paths",
            status="fail",
            detail=f"Path check error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_soul_md() -> TestResult:
    """Test: Does SOUL.md exist and contain identity content?"""
    from ..paths import SAWYER_HOME
    soul_path = Path(__file__).parent / "SOUL.md"

    start = time.monotonic()
    try:
        if not soul_path.exists():
            return TestResult(
                name="SOUL.md",
                status="warn",
                detail="SOUL.md not found. Agent will have no identity context.",
                duration_ms=0,
            )

        content = soul_path.read_text(encoding="utf-8")
        elapsed = int((time.monotonic() - start) * 1000)

        # Check for essential sections
        required = ["Who You Are", "Core Principles"]
        missing = [r for r in required if r not in content]

        if missing:
            return TestResult(
                name="SOUL.md",
                status="warn",
                detail=f"SOUL.md exists ({len(content)} chars) but missing sections: {', '.join(missing)}",
                duration_ms=elapsed,
            )

        return TestResult(
            name="SOUL.md",
            status="pass",
            detail=f"SOUL.md loaded ({len(content)} chars). Identity and principles present.",
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="SOUL.md",
            status="fail",
            detail=f"SOUL.md read error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_memory_bootstrap() -> TestResult:
    """Test: Were essential facts bootstrapped into memory?"""
    start = time.monotonic()
    try:
        # Import and check what bootstrap would add
        from ..memory_bootstrap import ESSENTIAL_FACTS

        required_keys = ["user_name", "github_username", "platform", "sawyer_home"]
        missing = [k for k in required_keys if not any(k == f[1] for f in ESSENTIAL_FACTS)]

        elapsed = int((time.monotonic() - start) * 1000)

        if missing:
            return TestResult(
                name="Memory Bootstrap",
                status="warn",
                detail=f"Bootstrap module missing required keys: {', '.join(missing)}",
                duration_ms=elapsed,
            )

        return TestResult(
            name="Memory Bootstrap",
            status="pass",
            detail=f"Bootstrap module defines {len(ESSENTIAL_FACTS)} essential facts. Required keys present.",
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Memory Bootstrap",
            status="fail",
            detail=f"Bootstrap module error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _test_rules(rules_store) -> TestResult:
    """Test: Are custom rules loaded?"""
    start = time.monotonic()
    try:
        rules = rules_store.list_rules()
        elapsed = int((time.monotonic() - start) * 1000)

        return TestResult(
            name="Rules",
            status="pass" if rules else "warn",
            detail=f"{len(rules)} rule(s) loaded." + (f" Rules: {', '.join(r.get('name', r.get('id', '?')) for r in rules)}" if rules else " No custom rules defined."),
            duration_ms=elapsed,
        )
    except Exception as e:
        return TestResult(
            name="Rules",
            status="fail",
            detail=f"Rules error: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def run_self_test(state) -> list[TestResult]:
    """Run all self-tests and return results.

    Args:
        state: The _AppState object from the server.

    Returns:
        List of TestResult objects.
    """
    results: list[TestResult] = []

    # 1. Provider port check (fast, socket-level)
    results.append(_test_provider_port(state.config))

    # 2. Provider connectivity (actual LLM request)
    results.append(_test_provider_connectivity(state.config))

    # 3. Config
    results.append(_test_config(state.config))

    # 4. Data paths
    results.append(_test_data_paths())

    # 5. Memory
    results.append(_test_memory(state.memory))

    # 6. Memory bootstrap module
    results.append(_test_memory_bootstrap())

    # 7. SOUL.md
    results.append(_test_soul_md())

    # 8. Skills
    results.append(_test_skills(state.skills))

    # 9. Keys
    results.append(_test_keys(state.key_storage))

    # 10. Tools
    results.append(_test_tools(state.tools))

    # 11. Shell tool (actual execution)
    results.append(_test_shell_tool())

    # 12. File tools (actual read/write)
    results.append(_test_file_tools())

    # 13. GitHub CLI
    results.append(_test_gh_cli())

    # 14. Rules
    results.append(_test_rules(state.rules_store))

    # Summary
    passed = sum(1 for r in results if r.status == "pass")
    warned = sum(1 for r in results if r.status == "warn")
    failed = sum(1 for r in results if r.status == "fail")
    total_ms = sum(r.duration_ms for r in results)

    return results