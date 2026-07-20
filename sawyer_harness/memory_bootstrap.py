"""
Memory bootstrap -- populate essential facts on first run or when missing.

Sawyer starts with near-empty memory, which makes it dumb. This module
checks for essential facts and seeds them if absent. It runs at server
startup and ensures the agent has the context it needs to be useful
from the first message.

Essential facts are grouped by category:
- environment: OS, paths, tools available
- preference: user preferences and style
- project: active projects and their locations

This is NOT a one-time migration. It checks each key individually and
only writes if the key is missing. This means:
- Fresh installs get everything
- Existing installs get new facts they're missing
- Manual edits to memory are preserved (never overwritten)
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path

from .paths import UserData

logger = logging.getLogger("sawyer-harness.memory_bootstrap")

# ── Essential facts to seed ──────────────────────────────────────────
# Each entry: (category, key, content)
# Content uses {placeholders} that are resolved at runtime.
# Only written if the key does not already exist in memory.

ESSENTIAL_FACTS: list[tuple[str, str, str]] = [
    # ── Environment ──
    ("environment", "platform_origin",
     "Sawyer is built on bespoke code written by a Hermes agent named Jade. "
     "Jade combined the best of Hermes (agent behavior) and OpenClaw "
     "(tooling/integration) but wrote everything from scratch -- not a fork or port."),

    ("environment", "sawyer_capabilities",
     "Sawyer has goal-seeking behavior and looping orchestration to refine tools "
     "and apps. This means the agent can set goals, execute toward them, evaluate "
     "results, and iterate until goals are met -- not just single-pass generation. "
     "Combined with ClawHub skill import, this creates a self-improving system."),

    # ── User identity ──
    ("preference", "user_name", "David, also goes by Dave"),
    ("preference", "github_username", "drc10101"),
    ("preference", "communication_style",
     "Prefers concise summaries over verbose output. Keep responses short "
     "and to the point. Less detail, more signal."),
    ("preference", "user_profession",
     "Software developer working on various large-scale projects"),
    ("preference", "company", "InFill Systems, LLC"),
]


def _detect_tools() -> dict[str, str]:
    """Detect available CLI tools and their locations."""
    tools = {}
    for cmd in ["git", "gh", "python", "pip", "node", "npm", "docker", "curl", "ssh"]:
        path = shutil.which(cmd)
        if path:
            tools[cmd] = path
    return tools


def _detect_projects() -> list[tuple[str, str]]:
    """Detect known projects in the user's home directory."""
    projects = []
    home = Path.home()
    # Check for known project directories
    known_dirs = [
        "sawyer-agent",
        "sawyer-harness",
        "Desktop/AVALON",
        "Desktop/avalon",
    ]
    for d in known_dirs:
        p = home / d
        if p.is_dir():
            projects.append((d, str(p)))
    return projects


def bootstrap_memory(memory_store) -> int:
    """Check for missing essential facts and seed them.

    Args:
        memory_store: A MemoryStore instance.

    Returns:
        Number of new facts added.
    """
    added = 0
    existing_keys = {e["key"] for e in memory_store.all_entries()}

    # Platform detection
    os_name = platform.system()
    if os_name == "Windows":
        os_detail = f"Windows ({platform.release()})"
        shell_hint = "Use Windows-compatible commands. MSYS/Git Bash available."
    elif os_name == "Darwin":
        os_detail = f"macOS ({platform.mac_ver()[0]})"
        shell_hint = "Use standard Unix commands."
    else:
        os_detail = f"{os_name} ({platform.release()})"
        shell_hint = "Use standard Unix commands."

    home = str(Path.home())

    # Build runtime facts (these use detected values, not hardcoded)
    runtime_facts: list[tuple[str, str, str]] = [
        ("environment", "platform", f"Running on {os_detail}. {shell_hint} Home: {home}"),
        ("environment", "sawyer_home", f"Sawyer data directory: {UserData.home}. Config: {UserData.config_file}. Keys: {UserData.keys_file}. Memory: {UserData.memory_db}."),
    ]

    # Add tool detection
    tools = _detect_tools()
    if tools:
        tool_list = ", ".join(f"{cmd} ({path})" for cmd, path in tools.items())
        runtime_facts.append(("environment", "available_tools", f"CLI tools available: {tool_list}"))

    # Add project detection
    projects = _detect_projects()
    if projects:
        proj_list = "; ".join(f"{name} at {path}" for name, path in projects)
        runtime_facts.append(("environment", "known_projects", proj_list))

    # Check gh auth status
    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--show-token"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Check if authenticated
            if "Logged in" in (result.stdout + result.stderr):
                runtime_facts.append(("environment", "github_auth",
                    "gh CLI is authenticated. Use 'gh api' for GitHub operations, not web scraping."))
            else:
                runtime_facts.append(("environment", "github_auth",
                    "gh CLI installed but not authenticated. Run 'gh auth login' or add token to keys.yaml."))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Combine static and runtime facts
    all_facts = ESSENTIAL_FACTS + runtime_facts

    # Seed only missing facts
    for category, key, content in all_facts:
        if key not in existing_keys:
            memory_store.add(key=key, content=content, category=category)
            added += 1
            logger.info(f"Bootstrapped memory: [{category}] {key}")

    # Clean up test/junk entries that shouldn't persist
    junk_keys = ["test_key", "audit_test"]
    for key in junk_keys:
        if key in existing_keys:
            memory_store.delete(key)
            logger.info(f"Cleaned up test entry: {key}")

    if added > 0:
        logger.info(f"Memory bootstrap: added {added} essential facts")
    else:
        logger.debug("Memory bootstrap: all facts already present")

    return added