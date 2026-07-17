"""
Tool Creator -- OCUE tool creation from a description.

The user describes what they want a tool to do. The system generates
a working Python file with a register() function, saves it to
~/.sawyer-harness/tools/, and reloads it into the running registry.

This is the OCUE approach: describe it, get it, done. No Python knowledge required.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import ToolRegistry

from .paths import UserData

logger = logging.getLogger("sawyer-harness.tool_creator")

USER_TOOLS_DIR = UserData.tools_dir

# Template for generating a user tool. The agent fills in the blanks.
_TOOL_TEMPLATE = '''"""
{docstring}
"""

from sawyer_harness.tools import ToolDefinition, ToolResult


def _{handler_name}({params}) -> ToolResult:
    """Tool implementation."""
{handler_body}


def register(registry):
    """Register this tool with Sawyer."""
    registry.register(ToolDefinition(
        name="{tool_name}",
        description="{description}",
        parameters={{
            "type": "object",
            "properties": {{
{properties}
            }},
            "required": [{required}],
        }},
        handler=_{handler_name},
        requires_sandbox={sandbox},
        dangerous={dangerous},
    ))
'''

# Prompt template for the LLM to generate tool code
_TOOL_GENERATION_PROMPT = """You are generating a Sawyer Agent tool. The user has described what they want.

Sawyer tools are Python functions that receive typed parameters and return ToolResult(output=...) or ToolResult(error=...).

Available imports (use only these):
- from sawyer_harness.tools import ToolDefinition, ToolResult
- import subprocess (for running commands)
- import json (for JSON parsing)
- import os (for file operations)
- import re (for regex)
- import requests (for HTTP calls -- already installed)
- from pathlib import Path (for path operations)
- from datetime import datetime, timezone (for timestamps)

RULES:
1. The handler function MUST return ToolResult(output=...) on success or ToolResult(error=...) on failure
2. Keep the handler simple and focused -- one tool, one job
3. Always handle errors gracefully with try/except
4. Truncate large outputs: result[:2000] to avoid flooding the agent's context
5. The register() function MUST be present and register the tool with ToolDefinition
6. Use descriptive parameter names and descriptions
7. Set requires_sandbox=True only if the tool runs shell commands
8. Set dangerous=True only if the tool can modify files or run destructive commands

USER DESCRIPTION:
{description}

Generate the complete Python file content. Output ONLY the Python code, no markdown fences, no explanation.
"""


@dataclass
class ToolDraft:
    """A generated tool draft awaiting user approval."""
    id: str
    name: str
    description: str
    filename: str
    code: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved: bool = False


class ToolCreator:
    """
    Generate custom tools from descriptions.

    Flow:
    1. User describes what they want
    2. System generates a Python file with register() function
    3. User previews the code
    4. User approves -> file is saved and tool is loaded
    5. User rejects -> draft is discarded

    All generated tools live in ~/.sawyer-harness/tools/ and survive upgrades.
    """

    def __init__(self, registry: ToolRegistry, llm_client=None):
        self.registry = registry
        self.llm = llm_client
        self.drafts: dict[str, ToolDraft] = {}

    async def generate(
        self,
        description: str,
        tool_name: str | None = None,
    ) -> ToolDraft:
        """
        Generate a tool draft from a description.

        If an LLM client is available, use it to generate the code.
        Otherwise, use a template-based fallback that produces a
        working skeleton.
        """
        import uuid

        # Sanitize tool name
        if not tool_name:
            tool_name = self._name_from_description(description)
        tool_name = re.sub(r'[^a-z0-9_]', '_', tool_name.lower())[:40]

        # Generate filename
        filename = f"{tool_name}.py"

        # Generate code
        if self.llm:
            code = await self._generate_with_llm(description, tool_name)
        else:
            code = self._generate_from_template(description, tool_name)

        # Create draft
        draft_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
        draft = ToolDraft(
            id=draft_id,
            name=tool_name,
            description=description[:200],
            filename=filename,
            code=code,
        )
        self.drafts[draft_id] = draft
        return draft

    async def _generate_with_llm(self, description: str, tool_name: str) -> str:
        """Use the LLM to generate tool code from a description."""
        from .llm import Message

        prompt = _TOOL_GENERATION_PROMPT.format(description=description)

        messages = [
            Message(role="system", content="You are a Python code generator. Output only valid Python code, no markdown, no explanation."),
            Message(role="user", content=prompt),
        ]

        try:
            response = await self.llm.chat(
                messages=messages,
                system_prompt="You are a Python code generator. Output only valid Python code.",
                tools=False,  # No tool calls, just text
            )
            code = response.content or ""

            # Strip markdown fences if the LLM wrapped the code
            code = re.sub(r'^```(?:python)?\s*\n', '', code)
            code = re.sub(r'\n```\s*$', '', code)
            code = code.strip()

            # Basic validation: must have register() function
            if "def register(" not in code:
                logger.warning("LLM output missing register() function, falling back to template")
                return self._generate_from_template(description, tool_name)

            # Must have ToolDefinition import
            if "ToolDefinition" not in code:
                logger.warning("LLM output missing ToolDefinition import, falling back to template")
                return self._generate_from_template(description, tool_name)

            return code

        except Exception as e:
            logger.error(f"LLM generation failed: {e}, falling back to template")
            return self._generate_from_template(description, tool_name)

    def _generate_from_template(self, description: str, tool_name: str) -> str:
        """Generate a tool skeleton from a template when LLM is not available."""
        handler_name = tool_name
        params = "query: str"
        properties = '                "query": {\n                    "type": "string",\n                    "description": "Input for the tool"\n                },'
        required = '"query"'

        # Parse description for parameters
        # Simple heuristic: if the description mentions a file, add a file param
        desc_lower = description.lower()
        if any(w in desc_lower for w in ["file", "path", "directory"]):
            params = "path: str, query: str = ''"
            properties = (
                '                "path": {\n'
                '                    "type": "string",\n'
                '                    "description": "File or directory path"\n'
                '                },\n'
                '                "query": {\n'
                '                    "type": "string",\n'
                '                    "description": "Search query or filter"\n'
                '                },'
            )
            required = '"path"'

        handler_body = (
            f'    """{description[:150]}"""\n'
            f'    try:\n'
            f'        # TODO: Implement the tool logic\n'
            f'        result = f"Tool {tool_name} executed with: " + str(query)\n'
            f'        return ToolResult(output=result[:2000])\n'
            f'    except Exception as e:\n'
            f'        return ToolResult(error=str(e))'
        )

        docstring = f"{tool_name}: {description[:80]}"

        code = _TOOL_TEMPLATE.format(
            docstring=docstring,
            handler_name=handler_name,
            params=params,
            handler_body=handler_body,
            tool_name=tool_name,
            description=description[:200].replace('"', '\\"'),
            properties=properties,
            required=required,
            sandbox="True" if any(w in desc_lower for w in ["shell", "command", "run", "execute"]) else "False",
            dangerous="True" if any(w in desc_lower for w in ["delete", "remove", "overwrite", "destructive"]) else "False",
        )

        return code

    def approve(self, draft_id: str) -> dict:
        """
        Approve a draft: write the file, reload the tool.

        Returns dict with status and tool info.
        """
        draft = self.drafts.get(draft_id)
        if not draft:
            return {"status": "error", "message": f"Draft {draft_id} not found"}

        if draft.approved:
            return {"status": "error", "message": f"Draft {draft_id} already approved"}

        # Write the file
        USER_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = USER_TOOLS_DIR / draft.filename

        # Check for name collision with existing tool
        existing_names = {t["function"]["name"] for t in self.registry.list_tools()}
        if draft.name in existing_names:
            # Tool with this name already exists -- update it
            logger.info(f"Tool {draft.name} already exists, updating file at {file_path}")

        file_path.write_text(draft.code, encoding="utf-8")
        logger.info(f"Wrote user tool to {file_path}")

        # Reload user tools
        from .user_tools import load_user_tools
        loaded = load_user_tools(self.registry)

        # Verify the tool registered
        registered_names = {t["function"]["name"] for t in self.registry.list_tools()}
        if draft.name in registered_names:
            draft.approved = True
            return {
                "status": "approved",
                "name": draft.name,
                "file": str(file_path),
                "registered": True,
                "total_tools": len(self.registry.list_tools()),
            }
        else:
            # The file was written but didn't load -- likely a syntax error
            return {
                "status": "error",
                "message": f"Tool file written but {draft.name} did not register. Check the Python syntax.",
                "file": str(file_path),
                "registered": False,
                "loaded_tools": loaded,
            }

    def reject(self, draft_id: str) -> dict:
        """Reject a draft: discard it without saving."""
        draft = self.drafts.get(draft_id)
        if not draft:
            return {"status": "error", "message": f"Draft {draft_id} not found"}

        del self.drafts[draft_id]
        return {"status": "rejected", "name": draft.name}

    def get_draft(self, draft_id: str) -> ToolDraft | None:
        """Get a draft by ID."""
        return self.drafts.get(draft_id)

    def list_drafts(self) -> list[dict]:
        """List all pending drafts."""
        return [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "filename": d.filename,
                "approved": d.approved,
                "created_at": d.created_at,
            }
            for d in self.drafts.values()
        ]

    def _name_from_description(self, description: str) -> str:
        """Generate a tool name from a description."""
        # Take first meaningful words
        words = re.sub(r"[^a-zA-Z0-9\s]", "", description.lower()).split()
        skip = {"a", "an", "the", "i", "me", "my", "we", "our", "can",
                "could", "should", "would", "do", "does", "did", "is",
                "are", "was", "were", "be", "been", "being", "have",
                "has", "had", "will", "shall", "may", "might", "must",
                "need", "to", "of", "in", "for", "on", "with", "at",
                "by", "from", "up", "about", "into", "through", "that",
                "this", "it", "and", "or", "but", "not"}
        meaningful = [w for w in words[:6] if w not in skip]
        if not meaningful:
            meaningful = words[:3]
        return "_".join(meaningful[:3])