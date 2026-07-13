"""
Sawyer Harness Web UI -- FastAPI backend + static HTML/CSS/JS frontend.

Provides:
- Control panel: model selection, fallback config, provider health
- Chat interface: conversation with the agent, tool call visibility
- Skills browser: create/edit/patch skills
- Session notes: auto-generated at session end
- Tool control: enable/disable tools, view audit log
- Goal orchestrator: decompose goals into subtasks
"""

from .server import create_app

__all__ = ["create_app"]