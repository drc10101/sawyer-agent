"""
CLI interface -- the simplest channel.

Run with: sawyer-harness --channel cli
Uses prompt-toolkit for a nice interactive REPL with history and completion.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

from .agent import Agent
from .config import HarnessConfig
from .llm import LLMClient
from .memory import MemoryStore
from .scheduler import CronScheduler, ScheduleType
from .skills import SkillStore
from .telegram_adapter import TelegramAdapter
from .tools import create_default_registry

logger = logging.getLogger("sawyer-harness.cli")
console = Console()

HISTORY_PATH = Path("~/.sawyer-harness/cli_history").expanduser()

SYSTEM_PROMPT = """You are Sawyer Agent, a secure AI agent. You have access to tools
(shell, file_read, file_write) and persistent memory. You are running on the user's
machine with their full permission. Be helpful, direct, and thorough.

When you use tools, explain what you're doing. When you're done, summarize the result."""


async def run_cli(config: HarnessConfig):
    """Run the interactive CLI loop."""
    # Initialize components
    memory = MemoryStore(config.memory.path)
    skills = SkillStore(Path("~/.sawyer-harness/skills").expanduser())
    tools = create_default_registry(
        allowed_tools=config.security.allowed_tools or None,
        denied_paths=config.security.denied_paths,
    )
    llm = LLMClient(config.llm, tool_registry=tools)
    agent = Agent(
        config=config,
        llm=llm,
        memory=memory,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        skills=skills,
    )

    history = FileHistory(str(HISTORY_PATH))
    session = PromptSession(history=history)

    console.print("[bold cyan]Sawyer Agent[/bold cyan] v0.1.0")
    console.print(f"Model: {config.llm.provider}/{config.llm.model}")
    console.print(f"Memory: {memory.total_chars()} chars stored")
    console.print("Type /quit to exit, /clear to reset conversation, /help for commands\n")

    while True:
        try:
            user_input = await asyncio.to_thread(session.prompt, ">>> ")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle commands
        if user_input == "/quit":
            break
        elif user_input == "/clear":
            agent.reset_conversation()
            console.print("[dim]Conversation cleared.[/dim]")
            continue
        elif user_input == "/memory":
            for entry in memory.all_entries():
                console.print(f"  [dim]{entry['key']}[/dim]: {entry['content'][:80]}")
            continue
        elif user_input == "/skills":
            skill_list = skills.list_skills()
            if not skill_list:
                console.print("[dim]No skills loaded.[/dim]")
            else:
                for s in skill_list:
                    console.print(
                        f"  [cyan]{s['name']}[/cyan] v{s['version']} "
                        f"[dim]{s['category']}[/dim] {s['description'][:60]}"
                    )
            continue
        elif user_input == "/help":
            console.print("Commands: /quit, /clear, /memory, /skills, /help")
            continue

        # Run agent
        try:
            full_response = []
            async for chunk in agent.run(user_input):
                full_response.append(chunk)
                console.print(chunk, end="")

            console.print()  # newline after response

        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            logger.exception("Agent error")

    # Cleanup
    memory.close()
    await llm.close()
    console.print("[dim]Goodbye.[/dim]")


def main():
    """Entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="Sawyer Agent -- Secure AI Agent")
    parser.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    parser.add_argument("--channel", default="cli", choices=["cli", "telegram"], help="Channel to use")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Load config
    config = HarnessConfig.from_file(args.config)

    # Run
    if args.channel == "cli":
        asyncio.run(run_cli(config))
    elif args.channel == "telegram":
        # Find the telegram channel config
        tg_config = None
        for ch in config.channels:
            if ch.name == "telegram" and ch.enabled:
                tg_config = ch
                break
        if not tg_config:
            console.print("[red]No enabled telegram channel in config. Add one:[/red]")
            console.print("""
channels:
  - name: telegram
    enabled: true
    config:
      token: "YOUR_BOT_TOKEN"
      allowed_chats: [12345]
""")
            sys.exit(1)
        adapter = TelegramAdapter(config, tg_config)
        asyncio.run(adapter.start())


if __name__ == "__main__":
    main()