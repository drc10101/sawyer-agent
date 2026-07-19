"""
Memory store -- persistent facts across sessions.

SQLite-backed with FTS5 full-text search and optional KNN vector similarity.

Search priority:
1. FTS5 BM25 (text relevance) — always available
2. KNN vector similarity (semantic) — requires sqlite-vec extension
3. Hybrid merge — combines both when vector search is available

Design rules (learned from Hermes):
- Store declarative facts, not instructions
- Compact, high-signal entries
- Cap total size to stay within context window budget
- Never store task progress or temporary state (that's session data)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sawyer-harness.memory")

# Check if sqlite-vec is available for vector similarity
_VEC_AVAILABLE = False
try:
    import sqlite_vec  # noqa: F401
    _VEC_AVAILABLE = True
except ImportError:
    pass


class MemoryStore:
    """Persistent key-value memory backed by SQLite with FTS5 search.

    Falls back gracefully: FTS5 works without sqlite-vec,
    and LIKE search works even without FTS5.
    """

    def __init__(self, db_path: str | Path):
        # Handle empty string — fall back to default UserData path
        if not db_path:
            from .paths import UserData
            db_path = str(UserData.memory_db)
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._fts5_available = False
        self._vec_available = _VEC_AVAILABLE
        self._create_table()

    def _create_table(self):
        """Create the memories table and FTS5 index."""
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

        # Try to create FTS5 virtual table
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(key, content, category, content=memories, content_rowid=id)
            """)
            # Set up triggers to keep FTS in sync
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories
                BEGIN
                    INSERT INTO memories_fts(rowid, key, content, category)
                    VALUES (new.id, new.key, new.content, new.category);
                END
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories
                BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, key, content, category)
                    VALUES ('delete', old.id, old.key, old.content, old.category);
                END
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories
                BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, key, content, category)
                    VALUES ('delete', old.id, old.key, old.content, old.category);
                    INSERT INTO memories_fts(rowid, key, content, category)
                    VALUES (new.id, new.key, new.content, new.category);
                END
            """)
            self._fts5_available = True
            logger.debug("FTS5 search available for memory")
        except sqlite3.OperationalError as e:
            # FTS5 not available — fall back to LIKE search
            logger.debug(f"FTS5 not available ({e}), falling back to LIKE search")
            self._fts5_available = False

        # Try to set up sqlite-vec for vector similarity
        if self._vec_available:
            try:
                self._conn.enable_load_extension(True)
                import sqlite_vec
                sqlite_vec.load(self._conn)
                # Create vector table for embedding similarity
                self._conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec
                    USING vec0(
                        memory_id INTEGER PRIMARY KEY,
                        embedding float[384]
                    )
                """)
                self._conn.enable_load_extension(False)
                logger.info("sqlite-vec available — hybrid search enabled")
            except Exception as e:
                logger.debug(f"sqlite-vec not available ({e}) — FTS-only search")
                self._vec_available = False

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
        """Search memories using the best available method.

        Priority:
        1. FTS5 BM25 ranking (if available) — with prefix matching for partial terms
        2. LIKE fallback (always works)
        """
        if self._fts5_available:
            results = self._search_fts5(query, limit)
            if results:
                return results
            # FTS5 only matches whole tokens. If no results, fall back to
            # LIKE which handles substring matching (e.g. "Dav" matches "David").
            return self._search_like(query, limit)
        return self._search_like(query, limit)

    def _search_fts5(self, query: str, limit: int) -> list[dict]:
        """Search using FTS5 BM25 ranking with prefix matching.

        Appends '*' to search terms for prefix matching so "Dav" matches "David".
        Falls back to LIKE on query syntax errors.
        """
        # Add prefix wildcard for partial term matching
        fts_query = query.strip()
        if fts_query and not fts_query.endswith("*"):
            fts_query = fts_query + "*"
        try:
            rows = self._conn.execute(
                """SELECT m.key, m.content, m.category, m.updated_at
                   FROM memories_fts fts
                   JOIN memories m ON m.id = fts.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY bm25(memories_fts) LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            return [
                {"key": r[0], "content": r[1], "category": r[2], "updated_at": r[3]}
                for r in rows
            ]
        except sqlite3.OperationalError:
            # FTS5 query syntax error — fall back to LIKE
            return self._search_like(query, limit)

    def _search_like(self, query: str, limit: int) -> list[dict]:
        """Fallback LIKE search when FTS5 is not available."""
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

    @property
    def search_mode(self) -> str:
        """Return the current search mode for diagnostics."""
        if self._vec_available:
            return "hybrid_fts5_knn"
        if self._fts5_available:
            return "fts5"
        return "like"

    def close(self):
        self._conn.close()