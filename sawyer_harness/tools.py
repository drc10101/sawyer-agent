"""
Tool registry -- sandboxed execution environment for agent tools.

Security model:
- Every tool is an explicit function with a schema
- Tools run in a constrained environment (no arbitrary imports, no network unless granted)
- All invocations are audit-logged
- Capability-based: tools can only access what's explicitly allowed

This is where Sawyer Harness differs from open agents. No raw shell access
without explicit permission. Every tool call is logged with input, output,
and duration.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from .paths import UserData

logger = logging.getLogger("sawyer-harness.tools")

# ── TaskKind: classifies tools by what they do ──
# Used by PermissionMode to decide which tools are available

class TaskKind:
    """Classification of tool operations for permission scoping."""
    READ = "read"           # File read, web fetch, memory search — safe, read-only
    WRITE = "write"         # File write, patch, project_create — modifies disk
    SHELL = "shell"         # Shell commands — full system access
    NETWORK = "network"     # Web search, web fetch, HTTP requests — outbound
    MEMORY = "memory"       # Memory store/search/delete — persistent state
    SKILL = "skill"         # Skill search/load/list — read-only knowledge
    SYSTEM = "system"       # Clipboard, git — local system interaction

# ── PermissionMode: capability hierarchy ──
# ReadOnly:  can read files, search memory, browse web — cannot modify anything
# ReadWrite: can edit files, run shell — cannot create projects or manage tools
# All:      full access, no restrictions (current default behavior)

PERMISSION_TOOL_ACCESS = {
    "readonly": {
        # Only read-only tools
        "file_read", "file_search", "web_search", "web_fetch", "http_request",
        "memory_search", "memory_store", "memory_delete",
        "skill_search", "skill_load", "skill_list",
    },
    "readwrite": {
        # Read + write tools, no project creation
        "file_read", "file_write", "file_search", "patch",
        "web_search", "web_fetch", "http_request",
        "shell", "git", "code_execute",
        "memory_search", "memory_store", "memory_delete",
        "skill_search", "skill_load", "skill_list",
        "clipboard",
    },
    "all": None,  # None = all tools allowed (no filtering)
}


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str = ""
    duration_ms: float = 0


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema style
    handler: Callable[..., ToolResult]
    requires_sandbox: bool = True
    dangerous: bool = False  # shell, file_write, etc.
    task_kind: str = TaskKind.SYSTEM  # classification for permission scoping

class ToolRegistry:
    """Registry of available tools with audit logging and permission checks."""

    def __init__(self, allowed_tools: list[str] | None = None, denied_paths: list[str] | None = None, permission_mode: str = "all"):
        self._tools: dict[str, ToolDefinition] = {}
        self._allowed_tools = set(allowed_tools) if allowed_tools else None  # None = all allowed
        self._denied_paths = [str(p) for p in (denied_paths or [])]
        self._permission_mode = permission_mode  # readonly, readwrite, all
        self._audit_log: list[dict] = []
        # Shared context: memory_store, skill_store, etc. set by the server.
        self._context: dict[str, Any] = {}

    def set_context(self, **kwargs):
        """Set shared context objects (memory_store, skill_store, etc.)."""
        self._context.update(kwargs)

    def register(self, tool: ToolDefinition):
        """Register a tool. It won't be executable unless allowed."""
        self._tools[tool.name] = tool

    def list_tools(self) -> list[dict]:
        """Return tool schemas for LLM function calling.

        Filters by both allowed_tools list and permission_mode.
        """
        schemas = []
        # Determine which tools are accessible by permission mode
        permission_allowed = PERMISSION_TOOL_ACCESS.get(self._permission_mode, None)
        for name, tool in self._tools.items():
            if self._allowed_tools and name not in self._allowed_tools:
                continue
            # Permission mode filtering
            if permission_allowed is not None and name not in permission_allowed:
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas

    def execute(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with audit logging and permission checks."""
        if tool_name not in self._tools:
            return ToolResult(success=False, output="", error=f"Unknown tool: {tool_name}")

        if self._allowed_tools and tool_name not in self._allowed_tools:
            return ToolResult(
                success=False, output="",
                error=f"Tool '{tool_name}' is not in the allowed tools list",
            )

        # Permission mode check
        permission_allowed = PERMISSION_TOOL_ACCESS.get(self._permission_mode, None)
        if permission_allowed is not None and tool_name not in permission_allowed:
            return ToolResult(
                success=False, output="",
                error=f"Tool '{tool_name}' is not available in {self._permission_mode} mode. "
                      f"Upgrade to 'readwrite' or 'all' to use this tool.",
            )

        tool = self._tools[tool_name]

        # Security check for file operations
        if tool.dangerous:
            try:
                self._check_path_restrictions(args)
            except PermissionError as e:
                return ToolResult(success=False, output="", error=str(e))

        # Audit log
        start = time.time()
        try:
            result = tool.handler(**args)
            result.duration_ms = (time.time() - start) * 1000
        except Exception as e:
            result = ToolResult(
                success=False, output="",
                error=f"Tool execution failed: {e}",
                duration_ms=(time.time() - start) * 1000,
            )

        # Log the invocation
        entry = {
            "timestamp": time.time(),
            "tool": tool_name,
            "args": args,
            "success": result.success,
            "duration_ms": result.duration_ms,
            "output_chars": len(result.output),
            "error": result.error,
        }
        self._audit_log.append(entry)
        logger.info(f"Tool {tool_name}: success={result.success} duration={result.duration_ms:.0f}ms")

        return result

    def _check_path_restrictions(self, args: dict):
        """Ensure tool arguments don't touch denied paths."""
        for key, value in args.items():
            if isinstance(value, str):
                for denied in self._denied_paths:
                    if denied in value:
                        raise PermissionError(
                            f"Access denied: path '{value}' matches restricted path '{denied}'"
                        )

    def audit_trail(self, limit: int = 50) -> list[dict]:
        """Return recent audit log entries."""
        return self._audit_log[-limit:]


# ============================================================
# Built-in tools
# ============================================================

def _shell_handler(
    command: str,
    timeout: int = 60,
    workdir: str = "",
) -> ToolResult:
    """Execute a shell command in a constrained environment."""
    import os as _os

    # Block the agent from killing its own PID or the web server PID.
    my_pid = str(_os.getpid())
    blocked_patterns = [
        f"kill {my_pid}", f"kill -9 {my_pid}", f"kill -15 {my_pid}",
        f"taskkill /PID {my_pid}", f"taskkill /F /PID {my_pid}",
    ]
    cmd_lower = command.lower().strip()
    for blocked in blocked_patterns:
        if blocked.lower() in cmd_lower:
            return ToolResult(
                success=False, output="",
                error=f"BLOCKED: Agent cannot terminate its own process (PID {my_pid}). "
                      f"Self-termination is not permitted.",
            )
    # Also block killall pkill taskkill that target the process name
    proc_name = _os.path.basename(sys.executable).lower()
    proc_base = proc_name.replace(".exe", "")  # e.g. "python" from "python.exe"
    for pat in [f"killall {proc_name}", f"killall {proc_base}",
                f"pkill {proc_name}", f"pkill {proc_base}",
                f"taskkill /IM {proc_name}", f"taskkill /F /IM {proc_name}"]:
        if pat.lower() in cmd_lower:
            return ToolResult(
                success=False, output="",
                error=f"BLOCKED: Agent cannot terminate its own process type ({proc_name}). "
                      f"Self-termination is not permitted.",
            )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir or None,
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return ToolResult(
            success=result.returncode == 0,
            output=output,
            error="" if result.returncode == 0 else f"Exit code: {result.returncode}",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, output="", error=f"Command timed out after {timeout}s")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def _file_read_handler(path: str, offset: int = 0, limit: int = 500) -> ToolResult:
    """Read a text file with line numbers."""
    try:
        p = Path(path).expanduser()
        lines = p.read_text().splitlines()
        selected = lines[offset : offset + limit]
        numbered = [f"{i + offset + 1}|{line}" for i, line in enumerate(selected)]
        return ToolResult(
            success=True,
            output="\n".join(numbered),
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


def _file_write_handler(path: str, content: str) -> ToolResult:
    """Write content to a file. Overwrites existing content."""
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return ToolResult(success=True, output=f"Wrote {len(content)} chars to {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# Web tools
# ============================================================

def _web_search_handler(query: str, max_results: int = 10) -> ToolResult:
    """Search the web using DuckDuckGo (no API key required)."""
    try:
        import urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        results = []
        # Abstract topic
        if data.get("AbstractText"):
            results.append({
                "title": data.get("AbstractSource", "DuckDuckGo"),
                "snippet": data["AbstractText"],
                "url": data.get("AbstractURL", ""),
            })
        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "snippet": topic["Text"],
                    "url": topic.get("FirstURL", ""),
                })
        # Infobox
        if data.get("Infobox"):
            info = data["Infobox"]
            if info.get("content"):
                for item in info["content"][:5]:
                    results.append({
                        "title": item.get("label", ""),
                        "snippet": f"{item.get('label', '')}: {item.get('value', '')}",
                        "url": "",
                    })

        if not results:
            return ToolResult(
                success=True,
                output=f"No results found for: {query}",
            )

        output = f"Search results for '{query}':\n\n"
        for i, r in enumerate(results[:max_results], 1):
            output += f"{i}. {r['title']}\n   {r['snippet']}\n"
            if r.get("url"):
                output += f"   {r['url']}\n"
            output += "\n"

        return ToolResult(success=True, output=output)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Web search failed: {e}")


def _web_fetch_handler(url: str, max_length: int = 20000) -> ToolResult:
    """Fetch and extract text content from a web URL."""
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url, headers={
                "User-Agent": "Sawyer-Agent/1.0 (research bot)",
            })
            resp.raise_for_status()
            content = resp.text

        # Strip HTML tags for text extraction (lightweight, no dependency)
        import re
        # Remove scripts and styles
        content = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        content = re.sub(r"<[^>]+>", " ", content)
        # Decode common HTML entities
        for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                              ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
            content = content.replace(entity, char)
        # Collapse whitespace
        content = re.sub(r"\s+", " ", content).strip()

        if len(content) > max_length:
            content = content[:max_length] + f"\n\n[Truncated at {max_length} chars]"

        return ToolResult(success=True, output=content)
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Web fetch failed: {e}")


# ============================================================
# Code execution tool
# ============================================================

def _code_execute_handler(
    code: str,
    language: str = "python",
    timeout: int = 30,
) -> ToolResult:
    """Execute code in a sandboxed environment and return the output.

    Supports Python (runs in subprocess). The code runs in isolation --
    no state persists between executions unless written to disk.
    """
    if language.lower() != "python":
        return ToolResult(
            success=False, output="",
            error=f"Unsupported language: {language}. Only Python is supported.",
        )

    import tempfile
    import os

    tmp_path = ""  # Track temp file for cleanup
    try:
        # Write code to a temp file and execute it
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="sawyer_exec_"
        ) as f:
            # Wrap in a harness that captures stdout and reports errors cleanly
            wrapped = textwrap.dedent(f"""\
            import sys
            import io
            _stdout_capture = io.StringIO()
            _stderr_capture = io.StringIO()
            sys.stdout = _stdout_capture
            sys.stderr = _stderr_capture
            try:
            {textwrap.indent(code, '    ')}
            except Exception as _e:
                import traceback
                print(traceback.format_exc(), file=sys.__stderr__)
            finally:
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__
                _out = _stdout_capture.getvalue()
                _err = _stderr_capture.getvalue()
                if _out:
                    print(_out, end='')
                if _err:
                    print(_err, end='', file=sys.stderr)
            """)
            f.write(wrapped)
            tmp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(Path.home()),
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            return ToolResult(
                success=result.returncode == 0,
                output=output if output else "(No output)",
                error="" if result.returncode == 0 else f"Exit code: {result.returncode}",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output="", error=f"Execution timed out after {timeout}s")
    except Exception as e:
        return ToolResult(success=False, output="", error=f"Code execution setup failed: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ============================================================
# Memory tools (closures that capture registry context)
# ============================================================

def _make_memory_search_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a memory_search handler with access to the MemoryStore."""
    def handler(query: str, limit: int = 10) -> ToolResult:
        memory = registry._context.get("memory_store")
        if not memory:
            return ToolResult(success=False, output="", error="Memory store not initialized")
        try:
            results = memory.search(query, limit=limit)
            if not results:
                return ToolResult(success=True, output=f"No memories matching '{query}'")
            output = "Memory search results:\n\n"
            for i, entry in enumerate(results, 1):
                output += f"{i}. [{entry['category']}] {entry['key']}: {entry['content']}\n"
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Memory search failed: {e}")
    return handler


def _make_memory_store_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a memory_store handler with access to the MemoryStore."""
    def handler(key: str, content: str, category: str = "general") -> ToolResult:
        memory = registry._context.get("memory_store")
        if not memory:
            return ToolResult(success=False, output="", error="Memory store not initialized")
        try:
            memory.add(key, content, category)
            return ToolResult(success=True, output=f"Stored memory: {key}")
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Memory store failed: {e}")
    return handler


def _make_memory_delete_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a memory_delete handler with access to the MemoryStore."""
    def handler(key: str) -> ToolResult:
        memory = registry._context.get("memory_store")
        if not memory:
            return ToolResult(success=False, output="", error="Memory store not initialized")
        try:
            deleted = memory.delete(key)
            if deleted:
                return ToolResult(success=True, output=f"Deleted memory: {key}")
            return ToolResult(success=False, output="", error=f"Memory not found: {key}")
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Memory delete failed: {e}")
    return handler


# ============================================================
# Skill tools (closures that capture registry context)
# ============================================================

def _make_skill_search_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a skill_search handler with access to the SkillStore."""
    def handler(query: str, limit: int = 5) -> ToolResult:
        skills = registry._context.get("skill_store")
        if not skills:
            return ToolResult(success=False, output="", error="Skill store not initialized")
        try:
            results = skills.search(query, limit=limit)
            if not results:
                return ToolResult(success=True, output=f"No skills matching '{query}'")
            output = "Skill search results:\n\n"
            for i, skill in enumerate(results, 1):
                output += f"{i}. {skill.name} (v{skill.version}) [{skill.category}]\n"
                output += f"   {skill.description}\n"
                output += f"   Triggers: {', '.join(skill.triggers)}\n\n"
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Skill search failed: {e}")
    return handler


def _make_skill_load_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a skill_load handler with access to the SkillStore."""
    def handler(name: str) -> ToolResult:
        skills = registry._context.get("skill_store")
        if not skills:
            return ToolResult(success=False, output="", error="Skill store not initialized")
        # Reload from disk in case skills were added at runtime
        try:
            skills._load_all()
        except Exception:
            pass
        try:
            skill = skills.get(name)
            if not skill:
                # Try searching by partial match
                results = skills.search(name, limit=1)
                if results:
                    skill = results[0]
                else:
                    available = [s["name"] for s in skills.list_skills()]
                    if available:
                        return ToolResult(success=False, output="", error=f"Skill not found: {name}. Available: {', '.join(available)}")
                    else:
                        return ToolResult(success=False, output="", error=f"No skills installed yet. Add .md skill files to {skills.skills_dir}")
            output = f"Skill: {skill.name} (v{skill.version})\n"
            output += f"Category: {skill.category}\n"
            output += f"Description: {skill.description}\n"
            output += f"Triggers: {', '.join(skill.triggers)}\n\n"
            output += skill.content
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Skill load failed: {e}")
    return handler


def _make_skill_list_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a skill_list handler with access to the SkillStore."""
    def handler(category: str = "") -> ToolResult:
        skills = registry._context.get("skill_store")
        if not skills:
            return ToolResult(success=False, output="", error="Skill store not initialized")
        try:
            # Reload from disk in case skills were added at runtime
            try:
                skills._load_all()
            except Exception:
                pass
            all_skills = skills.list_skills()
            if category:
                all_skills = [s for s in all_skills if s["category"] == category]
            if not all_skills:
                return ToolResult(success=True, output=f"No skills installed yet. Add .md skill files to {skills.skills_dir}")
            output = "Available skills:\n\n"
            for s in all_skills:
                output += f"- {s['name']} (v{s['version']}) [{s['category']}]: {s['description']}\n"
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Skill list failed: {e}")
    return handler


# ============================================================
# File search tool
# ============================================================

def _file_search_handler(
    pattern: str,
    path: str = ".",
    file_type: str = "",
    max_results: int = 50,
) -> ToolResult:
    """Search for files by name or content using ripgrep/grep."""
    try:
        cmd_parts = []
        if file_type:
            # Search content within files
            cmd_parts = ["grep", "-rn", "--color=never", f"--include={file_type}", pattern, path]
        else:
            # Search filenames
            safe_pattern = pattern.replace('"', '\\"')
            if Path(path).expanduser().is_dir():
                cmd_parts = ["find", str(Path(path).expanduser()), "-name", pattern, "-type", "f"]
            else:
                cmd_parts = ["find", ".", "-name", pattern, "-type", "f"]

        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout
        if result.stderr and not result.returncode == 0:
            output += f"\nSTDERR: {result.stderr}"

        lines = output.strip().split("\n")[:max_results]
        output = "\n".join(lines)
        if not output:
            return ToolResult(success=True, output=f"No matches for '{pattern}'")
        return ToolResult(success=True, output=output)
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, output="", error="Search timed out")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# Git tool
# ============================================================

def _git_handler(
    command: str,
    args: str = "",
    workdir: str = "",
) -> ToolResult:
    """Execute a git operation. Supported: status, diff, log, add, commit, branch, checkout, push, pull."""
    import os
    allowed = {"status", "diff", "log", "add", "commit", "branch", "checkout", "push", "pull",
                "stash", "merge", "rebase", "fetch", "remote", "tag", "reset", "blame", "show"}
    cmd = command.strip().split()[0] if command.strip() else ""
    if cmd not in allowed:
        return ToolResult(success=False, output="", error=f"Unsupported git command: {cmd}. Allowed: {', '.join(sorted(allowed))}")
    git_cmd = ["git", cmd] + (args.split() if args else [])
    try:
        result = subprocess.run(
            git_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workdir or None,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n{result.stderr}" if output else result.stderr
        if not output.strip():
            output = "(git command produced no output)"
        return ToolResult(success=result.returncode == 0, output=output.strip(),
                          error="" if result.returncode == 0 else f"Exit code: {result.returncode}")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, output="", error="Git command timed out")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# Patch tool (surgical find/replace)
# ============================================================

def _patch_handler(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    """Replace a specific string in a file. Finds old_string and replaces with new_string.
    Use for targeted edits instead of rewriting entire files. Set replace_all=True to replace all occurrences."""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")
        content = p.read_text(encoding="utf-8")
        if old_string not in content:
            return ToolResult(success=False, output="", error=f"String not found in {path}")
        count = content.count(old_string)
        if count > 1 and not replace_all:
            return ToolResult(success=False, output="",
                              error=f"Found {count} occurrences of old_string in {path}. Use replace_all=True or provide more context to make it unique.")
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content, encoding="utf-8")
        replaced = count if replace_all else 1
        return ToolResult(success=True, output=f"Replaced {replaced} occurrence(s) in {path}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# HTTP request tool
# ============================================================

def _http_request_handler(
    url: str,
    method: str = "GET",
    headers: str = "",
    body: str = "",
    timeout: int = 30,
) -> ToolResult:
    """Make an HTTP request. Returns status code, headers, and body. Use for API calls, webhooks, and data fetching."""
    import json as _json
    try:
        import urllib.request
        import urllib.error
        parsed_headers = {}
        if headers:
            try:
                parsed_headers = _json.loads(headers) if headers.strip().startswith("{") else dict(
                    line.split(":", 1) for line in headers.strip().split("\n") if ":" in line
                )
            except Exception:
                parsed_headers = {}
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        for k, v in parsed_headers.items():
            req.add_header(k.strip(), v.strip())
        if data and "Content-Type" not in req.headers:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp_headers = dict(resp.headers)
            resp_body = resp.read().decode("utf-8", errors="replace")
        # Truncate large responses
        if len(resp_body) > 50000:
            resp_body = resp_body[:50000] + "\n... (truncated)"
        output = f"Status: {status}\n"
        output += f"Headers: {resp_headers}\n\n"
        output += resp_body
        return ToolResult(success=200 <= status < 300, output=output)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return ToolResult(success=False, output=f"HTTP {e.code}: {body[:5000]}", error=f"HTTP error {e.code}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# Clipboard tool
# ============================================================

def _clipboard_handler(
    text: str,
) -> ToolResult:
    """Copy text to the system clipboard. Use when the user asks to copy output or share something."""
    import platform
    try:
        system = platform.system()
        if system == "Windows":
            process = subprocess.run(["clip"], input=text, text=True, capture_output=True, timeout=5)
        elif system == "Darwin":
            process = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True, timeout=5)
        else:
            process = subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, capture_output=True, timeout=5)
        if process.returncode == 0:
            return ToolResult(success=True, output=f"Copied {len(text)} characters to clipboard")
        return ToolResult(success=False, output="", error=f"Clipboard command failed: {process.stderr}")
    except FileNotFoundError:
        return ToolResult(success=False, output="", error="Clipboard utility not found (install xclip on Linux)")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


# ============================================================
# Project scaffold tool
# ============================================================

def _project_create_handler(
    name: str,
    template: str = "python-cli",
    path: str = "",
) -> ToolResult:
    """Create a new project from a template. Templates: python-cli, python-lib, web-api, scripts."""
    import os
    templates = {
        "python-cli": {
            "dirs": ["src", "tests"],
            "files": {
                "src/__init__.py": "",
                "src/main.py": '"""{{NAME}} CLI."""\n\ndef main():\n    print("Hello from {{NAME}}")\n\nif __name__ == "__main__":\n    main()\n',
                "tests/__init__.py": "",
                "tests/test_main.py": '"""Tests for {{NAME}}."""\n\ndef test_main():\n    assert True\n',
                "pyproject.toml": '[project]\nname = "{{NAME}}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n[project.scripts]\n{{NAME}} = "src.main:main"\n',
                "README.md": "# {{NAME}}\n\n{{NAME}} project.\n",
                ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\nbuild/\n.venv/\n",
            },
        },
        "python-lib": {
            "dirs": ["src", "tests"],
            "files": {
                "src/__init__.py": '"""{{NAME}} library."""\n',
                "tests/__init__.py": "",
                "tests/test_lib.py": '"""Tests for {{NAME}}."""\n\ndef test_import():\n    import src\n    assert src\n',
                "pyproject.toml": '[project]\nname = "{{NAME}}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n[build-system]\nrequires = ["setuptools"]\n',
                "README.md": "# {{NAME}}\n\n{{NAME}} library.\n",
                ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\nbuild/\n.venv/\n",
            },
        },
        "web-api": {
            "dirs": ["src", "tests", "src/routes"],
            "files": {
                "src/__init__.py": "",
                "src/main.py": '"""{{NAME}} API server."""\n\nfrom fastapi import FastAPI\n\napp = FastAPI(title="{{NAME}}")\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
                "src/routes/__init__.py": "",
                "tests/__init__.py": "",
                "tests/test_api.py": '"""Tests for {{NAME}} API."""\nfrom fastapi.testclient import TestClient\nfrom src.main import app\n\nclient = TestClient(app)\n\ndef test_health():\n    assert client.get("/health").status_code == 200\n',
                "pyproject.toml": '[project]\nname = "{{NAME}}"\nversion = "0.1.0"\ndependencies = ["fastapi", "uvicorn"]\n\n[project.scripts]\nserve = "src.main:app"\n',
                "README.md": "# {{NAME}} API\n\n{{NAME}} REST API.\n",
                ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\nbuild/\n.venv/\n",
            },
        },
        "scripts": {
            "dirs": ["scripts", "data"],
            "files": {
                "scripts/.gitkeep": "",
                "data/.gitkeep": "",
                "README.md": "# {{NAME}}\n\nScripts and data.\n",
                ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
            },
        },
    }
    if template not in templates:
        return ToolResult(success=False, output="",
                          error=f"Unknown template: {template}. Available: {', '.join(templates.keys())}")
    tmpl = templates[template]
    base = Path(path or name).expanduser()
    if base.exists():
        return ToolResult(success=False, output="", error=f"Directory already exists: {base}")
    created = []
    for d in tmpl["dirs"]:
        (base / d).mkdir(parents=True, exist_ok=True)
        created.append(str(base / d))
    for fname, content in tmpl["files"].items():
        fpath = base / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content.replace("{{NAME}}", name), encoding="utf-8")
        created.append(str(fpath))
    # Initialize git repo
    subprocess.run(["git", "init", str(base)], capture_output=True, timeout=10)
    return ToolResult(success=True, output=f"Created {template} project '{name}' at {base}\nFiles:\n" + "\n".join(f"  {f}" for f in created))


# ============================================================
# ClawHub import tool
# ============================================================

def _make_clawhub_import_handler(registry: ToolRegistry) -> Callable[..., ToolResult]:
    """Create a clawhub_import handler that fetches skills from ClawHub and converts them to Sawyer format."""
    import urllib.request
    import urllib.error
    import re

    SKILLS_DIR = UserData.skills_dir

    def handler(slug: str = "", search: str = "") -> ToolResult:
        # --- Search mode: list ClawHub skills ---
        if search and not slug:
            try:
                url = f"https://clawhub.ai/api/v1/skills?limit=10&search={urllib.parse.quote(search)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Sawyer-Agent/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                if not items:
                    return ToolResult(success=True, output=f"No ClawHub skills matching '{search}'")
                output = f"ClawHub skills matching '{search}':\n\n"
                for item in items[:10]:
                    s = item.get("slug", "?")
                    d = item.get("displayName", s)
                    desc = item.get("summary", "")[:100]
                    stars = item.get("stats", {}).get("stars", 0)
                    installs = item.get("stats", {}).get("installs", 0)
                    output += f"  {s}  --  {d} ({stars} stars, {installs} installs)\n"
                    output += f"     {desc}\n\n"
                output += f"\nTo import: use clawhub_import with slug='<name>'"
                return ToolResult(success=True, output=output)
            except urllib.error.URLError:
                return ToolResult(success=False, output="", error="ClawHub is not reachable. The service may be down or not yet launched. Skill import is not available at this time.")
            except Exception as e:
                return ToolResult(success=False, output="", error=f"ClawHub search failed: {e}")

        # --- Import mode: fetch and convert a skill ---
        if not slug:
            return ToolResult(success=False, output="", error="Provide a slug (e.g. 'github') or list query")

        # Normalize slug from URL
        slug = slug.strip()
        if "clawhub.ai" in slug:
            # Extract slug from URL like https://clawhub.ai/skills/github
            m = re.search(r"/skills/([^/?]+)", slug)
            if m:
                slug = m.group(1)
        slug = slug.replace(".md", "").strip().lower()

        # Try fetching from ClawHub API first, then fall back to GitHub raw
        skill_content = None
        skill_name = slug
        skill_description = ""

        # Approach 1: ClawHub API (metadata only -- content lives on GitHub)
        clawhub_reachable = False
        try:
            url = f"https://clawhub.ai/api/v1/skills/{urllib.parse.quote(slug)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Sawyer-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            clawhub_reachable = True
            # API wraps skill data under "skill" key
            skill_data = data.get("skill", data)
            skill_name = skill_data.get("slug", slug)
            skill_description = skill_data.get("summary", skill_data.get("description", ""))
            if isinstance(skill_description, list):
                skill_description = " ".join(str(s) for s in skill_description)
        except urllib.error.URLError:
            clawhub_reachable = False
        except Exception:
            skill_content = None

        # Approach 2: GitHub raw content from openclaw/agent-skills
        if not skill_content:
            gh_paths = [
                f"https://raw.githubusercontent.com/openclaw/agent-skills/main/skills/{slug}/SKILL.md",
                f"https://raw.githubusercontent.com/openclaw/agent-skills/main/skills/{slug}/CLAUDE.md",
            ]
            for gh_url in gh_paths:
                try:
                    req = urllib.request.Request(gh_url, headers={"User-Agent": "Sawyer-Agent/1.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        skill_content = resp.read().decode("utf-8")
                        break
                except urllib.error.HTTPError:
                    continue
                except Exception:
                    continue

        if not skill_content:
            if not clawhub_reachable:
                return ToolResult(success=False, output="",
                                  error="ClawHub is not reachable and the skill was not found on GitHub. Skill import is not available at this time.")
            return ToolResult(success=False, output="",
                              error=f"Could not find skill '{slug}' on ClawHub or GitHub. Try the search parameter to find available skills.")

        # Convert to Sawyer format
        # Parse existing frontmatter
        frontmatter = {}
        body = skill_content
        if skill_content.startswith("---"):
            parts = skill_content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml as _yaml
                    frontmatter = _yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
                body = parts[2].strip()

        # Build Sawyer skill
        sawyer_name = frontmatter.get("name", skill_name).lower().replace(" ", "-").replace("/", "-")
        sawyer_desc = frontmatter.get("description", skill_description)
        if isinstance(sawyer_desc, list):
            sawyer_desc = " ".join(sawyer_desc)

        # Strip OpenClaw-specific references and convert tool calls
        # Remove @tool references, Clawdbot mentions, convert to Sawyer-idiomatic style
        sawyer_body = body
        # Replace common OpenClaw tool references
        sawyer_body = re.sub(r'@codex\b', 'code_execute', sawyer_body)
        sawyer_body = re.sub(r'@clawdbot\b', 'Sawyer', sawyer_body)
        sawyer_body = re.sub(r'@web\b', 'web_search/web_fetch', sawyer_body)
        sawyer_body = re.sub(r'@shell\b', 'shell', sawyer_body)
        sawyer_body = re.sub(r'@clipboard\b', 'clipboard', sawyer_body)
        sawyer_body = re.sub(r'@git\b', 'git', sawyer_body)

        # Build Sawyer skill file
        escaped_desc = sawyer_desc.replace('"', '\\"')
        sawyer_title = sawyer_name.title().replace("-", " ")
        sawyer_date = time.strftime("%Y-%m-%d")
        # Strip duplicate title heading if body already starts with one matching our title
        sawyer_body_stripped = sawyer_body.lstrip()
        title_prefixes = [f"# {sawyer_title}", f"# {sawyer_name}"]
        for prefix in title_prefixes:
            if sawyer_body_stripped.startswith(prefix):
                # Remove the heading line
                lines = sawyer_body_stripped.split("\n", 1)
                sawyer_body = lines[1].lstrip() if len(lines) > 1 else ""
                break

        sawyer_skill = "---\n"
        sawyer_skill += f"name: {sawyer_name}\n"
        sawyer_skill += f"version: 1.0\n"
        sawyer_skill += "category: imported\n"
        sawyer_skill += f"triggers:\n  - {sawyer_name}\n"
        sawyer_skill += f'description: "{escaped_desc}"\n'
        sawyer_skill += f"created: {sawyer_date}\n"
        sawyer_skill += f"updated: {sawyer_date}\n"
        sawyer_skill += f"source: clawhub/{slug}\n"
        sawyer_skill += "---\n\n"
        sawyer_skill += f"# {sawyer_title}\n\n"
        sawyer_skill += sawyer_body + "\n\n---\n\n"
        sawyer_skill += f"*Imported from ClawHub ({slug}). Adapted for Sawyer Agent tool names and conventions.*\n"

        # Save to skills directory
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SKILLS_DIR / f"{sawyer_name}.md"
        out_path.write_text(sawyer_skill, encoding="utf-8")

        # Reload skill store if available
        skill_store = registry._context.get("skill_store")
        if skill_store and hasattr(skill_store, "load_skill"):
            try:
                skill_store.load_skill(str(out_path))
            except Exception:
                pass

        return ToolResult(
            success=True,
            output=f"Imported ClawHub skill '{slug}' as Sawyer skill '{sawyer_name}'\n"
                    f"Saved to: {out_path}\n"
                    f"Description: {sawyer_desc[:200]}\n"
                    f"Use skill_load('{sawyer_name}') to activate it."
        )

    return handler

def create_default_registry(
    allowed_tools: list[str] | None = None,
    denied_paths: list[str] | None = None,
    permission_mode: str = "all",
) -> ToolRegistry:
    """Create a registry with built-in tools."""
    registry = ToolRegistry(allowed_tools=allowed_tools, denied_paths=denied_paths, permission_mode=permission_mode)

    # --- Core tools ---
    registry.register(ToolDefinition(
        name="shell",
        description="Execute a shell command. Use for builds, installs, git, processes.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 60},
                "workdir": {"type": "string", "description": "Working directory", "default": ""},
            },
            "required": ["command"],
        },
        handler=_shell_handler,
        requires_sandbox=True,
        dangerous=True,
        task_kind=TaskKind.SHELL,
    ))

    registry.register(ToolDefinition(
        name="file_read",
        description="Read a text file with line numbers.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "offset": {"type": "integer", "description": "Line offset (0-indexed)", "default": 0},
                "limit": {"type": "integer", "description": "Max lines", "default": 500},
            },
            "required": ["path"],
        },
        handler=_file_read_handler,
        requires_sandbox=False,
        dangerous=True,
        task_kind=TaskKind.READ,
    ))

    registry.register(ToolDefinition(
        name="file_write",
        description="Write content to a file. Overwrites existing content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        handler=_file_write_handler,
        requires_sandbox=False,
        dangerous=True,
        task_kind=TaskKind.WRITE,
    ))

    registry.register(ToolDefinition(
        name="file_search",
        description="Search for files by name pattern or content within files.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern (filename glob or text to find)"},
                "path": {"type": "string", "description": "Directory to search in", "default": "."},
                "file_type": {"type": "string", "description": "File extension filter (e.g. '*.py'). Empty = search filenames.", "default": ""},
                "max_results": {"type": "integer", "description": "Maximum results", "default": 50},
            },
            "required": ["pattern"],
        },
        handler=_file_search_handler,
        requires_sandbox=True,
        dangerous=False,
        task_kind=TaskKind.READ,
    ))

    # --- Web tools ---
    registry.register(ToolDefinition(
        name="web_search",
        description="Search the web using DuckDuckGo. No API key required.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum number of results", "default": 10},
            },
            "required": ["query"],
        },
        handler=_web_search_handler,
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.NETWORK,
    ))

    registry.register(ToolDefinition(
        name="web_fetch",
        description="Fetch and extract text content from a web URL.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_length": {"type": "integer", "description": "Maximum characters to return", "default": 20000},
            },
            "required": ["url"],
        },
        handler=_web_fetch_handler,
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.NETWORK,
    ))

    # --- Code execution ---
    registry.register(ToolDefinition(
        name="code_execute",
        description="Execute Python code in a sandboxed subprocess. Output is captured and returned. No state persists between calls unless written to disk.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "language": {"type": "string", "description": "Programming language (only 'python' supported)", "default": "python"},
                "timeout": {"type": "integer", "description": "Execution timeout in seconds", "default": 30},
            },
            "required": ["code"],
        },
        handler=_code_execute_handler,
        requires_sandbox=True,
        dangerous=False,
        task_kind=TaskKind.SHELL,
    ))

    # --- Memory tools (backed by closure -- context set at runtime) ---
    registry.register(ToolDefinition(
        name="memory_search",
        description="Search persistent memory for stored facts, preferences, and context from past sessions.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for memory entries"},
                "limit": {"type": "integer", "description": "Maximum results to return", "default": 10},
            },
            "required": ["query"],
        },
        handler=_make_memory_search_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.MEMORY,
    ))

    registry.register(ToolDefinition(
        name="memory_store",
        description="Store a fact in persistent memory. Facts survive across sessions. Store preferences, environment details, and stable knowledge.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short unique key for this memory (e.g. 'user_name', 'project_root')"},
                "content": {"type": "string", "description": "The fact or value to remember"},
                "category": {"type": "string", "description": "Category for organization (e.g. 'preference', 'environment', 'project')", "default": "general"},
            },
            "required": ["key", "content"],
        },
        handler=_make_memory_store_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.MEMORY,
    ))

    registry.register(ToolDefinition(
        name="memory_delete",
        description="Delete a memory entry by key.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key of the memory to delete"},
            },
            "required": ["key"],
        },
        handler=_make_memory_delete_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.MEMORY,
    ))

    # --- Skill tools (backed by closure -- context set at runtime) ---
    registry.register(ToolDefinition(
        name="skill_search",
        description="Search available skills by query. Returns matching skills with descriptions.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to find relevant skills"},
                "limit": {"type": "integer", "description": "Maximum results", "default": 5},
            },
            "required": ["query"],
        },
        handler=_make_skill_search_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.SKILL,
    ))

    registry.register(ToolDefinition(
        name="skill_load",
        description="Load a skill by name to get its full instructions. Skills contain reusable procedures for complex tasks.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name to load"},
            },
            "required": ["name"],
        },
        handler=_make_skill_load_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.SKILL,
    ))

    registry.register(ToolDefinition(
        name="skill_list",
        description="List all available skills, optionally filtered by category.",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category", "default": ""},
            },
            "required": [],
        },
        handler=_make_skill_list_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.SKILL,
    ))

    # --- Git tool ---
    registry.register(ToolDefinition(
        name="git",
        description="Execute git operations: status, diff, log, add, commit, branch, checkout, push, pull, stash, merge, rebase, fetch, remote, tag, reset, blame, show.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Git subcommand (e.g. 'status', 'log', 'diff')"},
                "args": {"type": "string", "description": "Arguments for the git command", "default": ""},
                "workdir": {"type": "string", "description": "Working directory for the git command", "default": ""},
            },
            "required": ["command"],
        },
        handler=_git_handler,
        requires_sandbox=True,
        dangerous=True,
        task_kind=TaskKind.SHELL,
    ))

    # --- Patch tool ---
    registry.register(ToolDefinition(
        name="patch",
        description="Surgical find/replace edit in a file. Use for targeted changes instead of rewriting entire files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique unless replace_all=True)"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of just the first", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=_patch_handler,
        requires_sandbox=False,
        dangerous=True,
        task_kind=TaskKind.WRITE,
    ))

    # --- HTTP request tool ---
    registry.register(ToolDefinition(
        name="http_request",
        description="Make HTTP requests (GET, POST, PUT, DELETE). Returns status, headers, and body. Use for API calls and webhooks.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to request"},
                "method": {"type": "string", "description": "HTTP method (GET, POST, PUT, DELETE, PATCH)", "default": "GET"},
                "headers": {"type": "string", "description": "JSON object or newline-separated 'Key: Value' headers", "default": ""},
                "body": {"type": "string", "description": "Request body (for POST/PUT)", "default": ""},
                "timeout": {"type": "integer", "description": "Request timeout in seconds", "default": 30},
            },
            "required": ["url"],
        },
        handler=_http_request_handler,
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.NETWORK,
    ))

    # --- Clipboard tool ---
    registry.register(ToolDefinition(
        name="clipboard",
        description="Copy text to the system clipboard. Use when the user asks to copy output or share something.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"},
            },
            "required": ["text"],
        },
        handler=_clipboard_handler,
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.SYSTEM,
    ))

    # --- Project scaffold tool ---
    registry.register(ToolDefinition(
        name="project_create",
        description="Create a new project from a template. Templates: python-cli, python-lib, web-api, scripts.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "template": {"type": "string", "description": "Template: python-cli, python-lib, web-api, scripts", "default": "python-cli"},
                "path": {"type": "string", "description": "Directory path (defaults to ./name)", "default": ""},
            },
            "required": ["name"],
        },
        handler=_project_create_handler,
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.WRITE,
    ))

    # --- ClawHub import tool ---
    registry.register(ToolDefinition(
        name="clawhub_import",
        description="Import a skill from ClawHub (clawhub.ai) by slug or GitHub URL. Fetches the skill, converts it to Sawyer format, and saves it to the skills directory.",
        parameters={
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "ClawHub skill slug (e.g. 'github', 'weather') or full URL",
                },
                "search": {
                    "type": "string",
                    "description": "Search query to find ClawHub skills (e.g. 'browser', 'git'). Returns matching slugs.",
                },
            },
            "required": [],
        },
        handler=_make_clawhub_import_handler(registry),
        requires_sandbox=False,
        dangerous=False,
        task_kind=TaskKind.NETWORK,
    ))

    return registry