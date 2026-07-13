"""
Memory store -- persistent facts across sessions.

SQLite-backed. Injected into every LLM turn so the agent remembers
preferences, environment details, and stable facts without re-asking.

Design rules (learned from Hermes):
- Store declarative facts, not instructions
- Compact, high-signal entries
- Cap total size to stay within context window budget
- Never store task progress or temporary state (that's session data)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class MemoryStore:
    """Persistent key-value memory backed by SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    def add(self, key: str, content: str, category: str = "general") -> bool:
        """Add or update a memory entry."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                """INSERT INTO memories (key, content, category, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET content=?, updated_at=?, access_count=access_count+1""",
                (key, content, category, now, now, content, now),
            )
            self._conn.commit()
            return True
        except sqlite3.Error:
            return False

    def get(self, key: str) -> Optional[str]:
        """Retrieve a memory by key."""
        row = self._conn.execute(
            "SELECT content FROM memories WHERE key=?", (key,)
        ).fetchone()
        if row:
            self._conn.execute(
                "UPDATE memories SET access_count=access_count+1 WHERE key=?", (key,)
            )
            self._conn.commit()
            return row[0]
        return None

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories by key, content, or category (LIKE query)."""
        rows = self._conn.execute(
            """SELECT key, content, category, updated_at FROM memories
               WHERE content LIKE ? OR key LIKE ? OR category LIKE ?
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [
            {"key": r[0], "content": r[1], "category": r[2], "updated_at": r[3]}
            for r in rows
        ]

    def delete(self, key: str) -> bool:
        """Remove a memory entry."""
        cursor = self._conn.execute("DELETE FROM memories WHERE key=?", (key,))
        self._conn.commit()
        return cursor.rowcount > 0

    def all_entries(self) -> list[dict]:
        """Return all memories for context injection."""
        rows = self._conn.execute(
            "SELECT key, content, category FROM memories ORDER BY category, key"
        ).fetchall()
        return [{"key": r[0], "content": r[1], "category": r[2]} for r in rows]

    def total_chars(self) -> int:
        """Total characters stored (for context budget)."""
        row = self._conn.execute("SELECT SUM(LENGTH(content)) FROM memories").fetchone()
        return row[0] or 0

    def close(self):
        self._conn.close()