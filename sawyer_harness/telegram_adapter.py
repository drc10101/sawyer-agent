"""
Telegram channel adapter -- message routing, session management, rate limiting.

The primary user-facing channel for Dave and Jed. Connects to Telegram via
the python-telegram-bot library and routes messages to the agent.

Features:
- Multi-session support (separate conversations per chat)
- Rate limiting (prevent spam)
- /command handling for agent control
- Message splitting for long responses (Telegram limit: 4096 chars)
- Error handling and reconnection
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .agent import Agent
from .config import HarnessConfig, ChannelConfig
from .memory import MemoryStore
from .paths import UserData
from .skills import SkillStore
from .tools import create_default_registry

logger = logging.getLogger("sawyer-harness.telegram")

TELEGRAM_MSG_LIMIT = 4096  # Telegram message character limit


@dataclass
class TelegramSession:
    """A conversation session for a specific Telegram chat."""

    chat_id: int
    agent: Agent
    created: str = ""
    last_active: str = ""
    message_count: int = 0

    def touch(self):
        """Update last_active timestamp."""
        self.last_active = datetime.now(timezone.utc).isoformat()
        self.message_count += 1


class TelegramRateLimiter:
    """Simple rate limiter per chat_id."""

    def __init__(self, max_messages: int = 20, window_seconds: int = 60):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._timestamps: dict[int, list[float]] = {}

    def check(self, chat_id: int) -> bool:
        """Check if a message is allowed. Returns True if within rate limit."""
        import time

        now = time.time()
        if chat_id not in self._timestamps:
            self._timestamps[chat_id] = []

        # Remove old timestamps outside the window
        self._timestamps[chat_id] = [
            t for t in self._timestamps[chat_id] if now - t < self.window_seconds
        ]

        if len(self._timestamps[chat_id]) >= self.max_messages:
            return False

        self._timestamps[chat_id].append(now)
        return True


class TelegramAdapter:
    """
    Telegram Bot adapter that routes messages between Telegram and the agent.

    Handles:
    - Incoming messages from Telegram users
    - Outgoing responses from the agent
    - Session management (one agent per chat)
    - Rate limiting
    - Command handling (/start, /clear, /memory, /skills, /help)
    """

    def __init__(self, config: HarnessConfig, channel_config: ChannelConfig):
        self.config = config
        self.channel_config = channel_config
        self.token = channel_config.config.get("token", "")
        self.allowed_chat_ids: list[int] = channel_config.config.get("allowed_chats", [])
        self.sessions: dict[int, TelegramSession] = {}
        self.rate_limiter = TelegramRateLimiter(
            max_messages=channel_config.config.get("rate_limit", 20),
            window_seconds=channel_config.config.get("rate_window", 60),
        )
        self._bot = None
        self._memory = None
        self._skills = None

    def _create_agent(self, chat_id: int) -> Agent:
        """Create a new agent instance for a chat session."""
        from .llm import LLMClient

        memory = MemoryStore(self.config.memory.path or str(UserData.memory_db))
        tools = create_default_registry(
            allowed_tools=self.config.security.allowed_tools or None,
            denied_paths=self.config.security.denied_paths,
        )
        llm = LLMClient(self.config.llm, tool_registry=tools)

        system_prompt = self.channel_config.config.get(
            "system_prompt",
            "You are Sawyer Agent, a secure AI assistant. Be helpful, direct, and thorough.",
        )

        agent = Agent(
            config=self.config,
            llm=llm,
            memory=memory,
            tools=tools,
            system_prompt=system_prompt,
            skills=self._skills,
        )
        return agent

    def _get_or_create_session(self, chat_id: int) -> TelegramSession:
        """Get existing session or create a new one."""
        if chat_id not in self.sessions:
            agent = self._create_agent(chat_id)
            session = TelegramSession(
                chat_id=chat_id,
                agent=agent,
                created=datetime.now(timezone.utc).isoformat(),
            )
            self.sessions[chat_id] = session
            logger.info(f"Created new session for chat {chat_id}")

        self.sessions[chat_id].touch()
        return self.sessions[chat_id]

    async def handle_message(self, chat_id: int, text: str, username: str = "") -> list[str]:
        """
        Process an incoming Telegram message.

        Returns a list of response chunks (split for Telegram's 4096 char limit).
        """
        # Check rate limit
        if not self.rate_limiter.check(chat_id):
            return ["Rate limit exceeded. Please wait a moment."]

        # Handle commands
        if text.startswith("/"):
            return await self._handle_command(chat_id, text)

        # Get session and run agent
        session = self._get_or_create_session(chat_id)

        try:
            response_parts = []
            async for chunk in session.agent.run(text):
                response_parts.append(chunk)

            full_response = "".join(response_parts)

            # Split for Telegram's message limit
            return self._split_message(full_response)

        except Exception as e:
            logger.error(f"Agent error for chat {chat_id}: {e}")
            return [f"Error: {e}"]

    async def _handle_command(self, chat_id: int, command: str) -> list[str]:
        """Handle /commands."""
        cmd = command.strip().lower().split()[0]

        if cmd == "/start":
            return [
                "Sawyer Agent online. I'm your secure AI assistant.\n"
                "Commands: /clear /memory /skills /help"
            ]

        elif cmd == "/clear":
            session = self._get_or_create_session(chat_id)
            session.agent.reset_conversation()
            return ["Conversation cleared."]

        elif cmd == "/memory":
            session = self._get_or_create_session(chat_id)
            entries = session.agent.memory.all_entries()
            if not entries:
                return ["No memories stored."]
            lines = []
            for entry in entries:
                lines.append(f"  {entry['key']}: {entry['content'][:80]}")
            return self._split_message("\n".join(lines))

        elif cmd == "/skills":
            if not self._skills:
                return ["Skills system not initialized."]
            skill_list = self._skills.list_skills()
            if not skill_list:
                return ["No skills loaded."]
            lines = []
            for s in skill_list:
                lines.append(f"  {s['name']} v{s['version']} [{s['category']}] {s['description'][:60]}")
            return lines

        elif cmd == "/help":
            return [
                "Sawyer Agent Commands:\n"
                "/start - Welcome message\n"
                "/clear - Clear conversation\n"
                "/memory - Show stored memories\n"
                "/skills - List loaded skills\n"
                "/help - This message"
            ]

        else:
            return [f"Unknown command: {command}"]

    def _split_message(self, text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
        """Split a long message into chunks that fit Telegram's limit.

        Tries to split on paragraph boundaries (double newlines).
        Falls back to sentence boundaries, then character boundaries.
        """
        if len(text) <= limit:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            # Try to split at paragraph boundary
            split_point = remaining.rfind("\n\n", 0, limit)

            if split_point <= 0:
                # Try sentence boundary
                split_point = remaining.rfind(". ", 0, limit)

            if split_point <= 0:
                # Try newline
                split_point = remaining.rfind("\n", 0, limit)

            if split_point <= 0:
                # Hard split at limit
                split_point = limit

            chunks.append(remaining[:split_point].strip())
            remaining = remaining[split_point:].strip()

        return chunks

    async def start(self):
        """Start the Telegram bot polling."""
        try:
            from telegram import Update
            from telegram.ext import Application, CommandHandler, MessageHandler, filters
        except ImportError:
            logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return

        if not self.token:
            logger.error("Telegram bot token not configured")
            return

        # Initialize shared components
        self._memory = MemoryStore(self.config.memory.path or str(UserData.memory_db))
        self._skills = SkillStore(
            __import__("sawyer_harness.paths", fromlist=["UserData"]).UserData.skills_dir
        )

        application = Application.builder().token(self.token).build()

        async def message_handler(update: Update, context):
            """Handle incoming messages."""
            if not update.message or not update.message.text:
                return

            chat_id = update.message.chat_id
            text = update.message.text
            username = update.message.from_user.username if update.message.from_user else ""

            # Check allowed chats
            if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
                logger.warning(f"Unauthorized chat: {chat_id}")
                await update.message.reply_text("Unauthorized.")
                return

            # Process message
            responses = await self.handle_message(chat_id, text, username)

            # Send responses
            for response in responses:
                try:
                    await update.message.reply_text(response)
                except Exception as e:
                    logger.error(f"Failed to send message: {e}")

        # Register handlers
        application.add_handler(CommandHandler("start", message_handler))
        application.add_handler(CommandHandler("help", message_handler))
        application.add_handler(CommandHandler("clear", message_handler))
        application.add_handler(CommandHandler("memory", message_handler))
        application.add_handler(CommandHandler("skills", message_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

        logger.info("Starting Telegram bot polling...")
        await application.run_polling()

    def get_session_count(self) -> int:
        """Return the number of active sessions."""
        return len(self.sessions)

    def get_stats(self) -> dict[str, Any]:
        """Return adapter statistics."""
        return {
            "active_sessions": len(self.sessions),
            "total_messages": sum(s.message_count for s in self.sessions.values()),
            "allowed_chats": len(self.allowed_chat_ids),
        }