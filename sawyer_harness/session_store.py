"""
Session Store -- SQLite-backed persistent session storage.

Sessions reset on restart because they live only in memory.
This module provides a persistent store that:

1. Saves sessions to SQLite on every message (or on demand)
2. Loads sessions on startup so they survive restarts
3. Provides CRUD for the UI: list, resume, delete, export
4. Auto-titles sessions from the first user message
5. Exports sessions as Markdown

The store lives at ~/.sawyer-harness/user/sessions.db
alongside the existing memory.db.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import UserData

logger = logging.getLogger("sawyer-harness.session_store")

DB_PATH = UserData.user_dir / "sessions.db"


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get a connection with WAL mode and foreign keys."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables if they don't exist."""
    conn = _connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                model        TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                is_active    INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                metadata     TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC);
        """)
        conn.commit()
    finally:
        conn.close()


class SessionStore:
    """Persistent session storage backed by SQLite.

    Thread-safe via connection-per-call. WAL mode allows concurrent reads.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        init_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    # ----------------------------------------------------------
    # Session CRUD
    # ----------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        title: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Create a new session record. Returns the session dict."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id, title, created_at, updated_at, model, message_count, is_active)
                   VALUES (?, ?, ?, ?, ?, 0, 1)""",
                (session_id, title or "New Session", now, now, model),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return self._row_to_session(row)
        finally:
            conn.close()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get a session by ID. Returns dict or None."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return self._row_to_session(row) if row else None
        finally:
            conn.close()

    def list_sessions(
        self,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List sessions, newest first. Optionally filter to active only."""
        conn = self._conn()
        try:
            query = "SELECT * FROM sessions"
            params: list[Any] = []
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params = [limit, offset]
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_session(r) for r in rows]
        finally:
            conn.close()

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        model: str | None = None,
        is_active: int | None = None,
    ) -> dict[str, Any] | None:
        """Update session metadata. Returns updated session or None."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            sets: list[str] = ["updated_at = ?"]
            params: list[Any] = [now]
            if title is not None:
                sets.append("title = ?")
                params.append(title)
            if model is not None:
                sets.append("model = ?")
                params.append(model)
            if is_active is not None:
                sets.append("is_active = ?")
                params.append(is_active)
            params.append(session_id)
            conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?",
                params,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return self._row_to_session(row) if row else None
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages. Returns True if deleted."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def auto_title(self, session_id: str, first_message: str) -> str | None:
        """Set session title from first user message (truncated). Returns new title."""
        # Truncate to ~60 chars, break at word boundary
        title = first_message.strip().replace("\n", " ")[:80]
        if len(title) == 80:
            # Try to break at last space before 60
            last_space = title.rfind(" ", 0, 65)
            if last_space > 0:
                title = title[:last_space]
            title = title.rstrip() + "..."
        return (self.update_session(session_id, title=title) or {}).get("title")

    # ----------------------------------------------------------
    # Messages
    # ----------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a message to a session. Auto-creates session if missing."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            # Ensure session exists
            existing = conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not existing:
                self.create_session(session_id)

            # Auto-title from first user message
            if role == "user":
                msg_count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                    (session_id,),
                ).fetchone()[0]
                if msg_count == 0:
                    title = content.strip().replace("\n", " ")[:80]
                    if len(title) == 80:
                        last_space = title.rfind(" ", 0, 65)
                        if last_space > 0:
                            title = title[:last_space]
                        title = title.rstrip() + "..."
                    conn.execute(
                        "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                        (title, now, session_id),
                    )

            conn.execute(
                """INSERT INTO messages (session_id, role, content, timestamp, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, role, content, now, json.dumps(metadata or {})),
            )
            # Update session message count and timestamp
            conn.execute(
                """UPDATE sessions
                   SET message_count = message_count + 1,
                       updated_at = ?
                   WHERE session_id = ?""",
                (now, session_id),
            )
            conn.commit()

            msg_id = conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            return {
                "id": msg_id,
                "session_id": session_id,
                "role": role,
                "content": content,
                "timestamp": now,
                "metadata": metadata or {},
            }
        finally:
            conn.close()

    def get_messages(
        self,
        session_id: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get messages for a session, oldest first."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT id, session_id, role, content, timestamp, metadata
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY timestamp ASC
                   LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "session_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "timestamp": r[4],
                    "metadata": json.loads(r[5]) if r[5] else {},
                }
                for r in rows
            ]
        finally:
            conn.close()

    def message_count(self, session_id: str) -> int:
        """Count messages in a session."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Bulk operations
    # ----------------------------------------------------------

    def save_conversation(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Bulk-save a conversation (used when restoring in-memory session).

        Creates the session if needed, then replaces all messages.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            # Upsert session
            conn.execute(
                """INSERT INTO sessions (session_id, title, created_at, updated_at, model, message_count, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(session_id) DO UPDATE SET
                       title = CASE WHEN ? != '' THEN ? ELSE sessions.title END,
                       updated_at = ?,
                       model = CASE WHEN ? != '' THEN ? ELSE sessions.model END,
                       message_count = ?,
                       is_active = 1""",
                (
                    session_id,
                    title or "New Session",
                    now,
                    now,
                    model,
                    len(messages),
                    # ON CONFLICT params
                    title, title,
                    now,
                    model, model,
                    len(messages),
                ),
            )
            # Delete old messages and re-insert
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                ts = msg.get("timestamp", now)
                meta = json.dumps(msg.get("metadata", {}))
                conn.execute(
                    """INSERT INTO messages (session_id, role, content, timestamp, metadata)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, role, content, ts, meta),
                )
            conn.commit()
            return self._row_to_session(
                conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
            )
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Export
    # ----------------------------------------------------------

    def export_markdown(self, session_id: str) -> str | None:
        """Export a session as Markdown. Returns None if session not found."""
        session = self.get_session(session_id)
        if not session:
            return None

        messages = self.get_messages(session_id, limit=10000)
        if not messages:
            return f"# {session['title']}\n\n_No messages in this session._\n"

        lines = [
            f"# {session['title']}",
            "",
            f"**Session:** `{session_id}`  ",
            f"**Created:** {session['created_at'][:19]}  ",
            f"**Model:** {session['model'] or 'default'}  ",
            f"**Messages:** {session['message_count']}  ",
            "",
            "---",
            "",
        ]

        for msg in messages:
            role = msg["role"].capitalize()
            content = msg["content"]
            if role == "User":
                lines.append(f"## {role}")
            else:
                lines.append(f"## {role}")
            lines.append("")
            lines.append(content)
            lines.append("")

        lines.append("---")
        lines.append(f"_Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
        return "\n".join(lines)

    # ----------------------------------------------------------
    # Internal
    # ----------------------------------------------------------

    @staticmethod
    def _row_to_session(row: tuple) -> dict[str, Any]:
        """Convert a DB row to a session dict."""
        return {
            "session_id": row[0],
            "title": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "model": row[4],
            "message_count": row[5],
            "is_active": bool(row[6]),
        }