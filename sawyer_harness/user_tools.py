"""
User tools -- load custom tools from ~/.sawyer-harness/tools/

User tools survive upgrades because they live outside the Python package.
Each tool is a Python file that defines a `register(registry)` function.

Example user tool file (~/.sawyer-harness/tools/my_tool.py):

    from sawyer_harness.tools import ToolDefinition, ToolResult

    def _my_tool_handler(query: str) -> ToolResult:
        return ToolResult(output=f"Result for: {query}")

    def register(registry):
        registry.register(ToolDefinition(
            name="my_tool",
            description="My custom tool that does something useful",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                },
                "required": ["query"],
            },
            handler=_my_tool_handler,
            requires_sandbox=False,
            dangerous=False,
        ))
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import ToolRegistry

logger = logging.getLogger("sawyer-harness.user_tools")

USER_TOOLS_DIR = Path.home() / ".sawyer-harness" / "tools"

# Example user tool template written to disk on first load
_EXAMPLE_TOOL = '''"""
Example user tool for Sawyer Agent.

User tools live in ~/.sawyer-harness/tools/ and survive upgrades.
Each tool file must define a register(registry) function.

To create your own tool:
1. Copy this file and rename it
2. Edit the handler, name, description, and parameters
3. Restart Sawyer (or call /api/tools/reload)

Your tool will appear in the Tools panel alongside the built-in ones.
"""

from sawyer_harness.tools import ToolDefinition, ToolResult


def _example_handler(query: str) -> ToolResult:
    """Your tool's logic goes here. Return ToolResult with output or error."""
    return ToolResult(output=f"Example result for: {query}")


def register(registry):
    """Called on startup to register this tool with Sawyer."""
    registry.register(ToolDefinition(
        name="example_user_tool",
        description="An example user tool. Customize or remove this.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query or input for the tool",
                },
            },
            "required": ["query"],
        },
        handler=_example_handler,
        requires_sandbox=False,
        dangerous=False,
    ))
'''


def ensure_user_tools_dir() -> Path:
    """Create the user tools directory and seed it with an example if empty."""
    USER_TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    # Write an example tool file if the directory is empty
    example_path = USER_TOOLS_DIR / "example_user_tool.py"
    if not example_path.exists() and not any(USER_TOOLS_DIR.glob("*.py")):
        example_path.write_text(_EXAMPLE_TOOL, encoding="utf-8")
        logger.info(f"Created example user tool at {example_path}")

    return USER_TOOLS_DIR


def load_user_tools(registry: ToolRegistry) -> list[str]:
    """Load all user tools from ~/.sawyer-harness/tools/.

    Each .py file must define a register(registry) function.

    Returns a list of (filename, tool_name, status) tuples.
    """
    tools_dir = ensure_user_tools_dir()
    loaded: list[str] = []
    errors: list[str] = []

    # Skip __init__.py and pycache files
    tool_files = sorted(
        f for f in tools_dir.glob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("__")
    )

    if not tool_files:
        # Only the example file exists (which starts with _)
        logger.info("No user tools found in %s", tools_dir)
        return loaded

    for tool_file in tool_files:
        try:
            spec = importlib.util.spec_from_file_location(
                f"user_tool_{tool_file.stem}", str(tool_file)
            )
            if spec is None or spec.loader is None:
                errors.append(f"{tool_file.name}: could not load module")
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "register"):
                errors.append(f"{tool_file.name}: no register() function")
                continue

            # Count tools before and after to see what was registered
            tools_before = len(registry.list_tools())
            module.register(registry)
            tools_after = len(registry.list_tools())
            new_tools = tools_after - tools_before

            if new_tools > 0:
                # Find the names of newly registered tools
                new_names = [
                    t["function"]["name"]
                    for t in registry.list_tools()[tools_before:]
                ]
                for name in new_names:
                    loaded.append(name)
                logger.info(
                    f"Loaded user tool {tool_file.name}: "
                    f"registered {', '.join(new_names)}"
                )
            else:
                errors.append(f"{tool_file.name}: register() didn't add any tools")

        except Exception as e:
            errors.append(f"{tool_file.name}: {e}")
            logger.error(f"Error loading user tool {tool_file.name}: {e}")

    if errors:
        logger.warning(f"User tool load errors: {errors}")

    return loaded