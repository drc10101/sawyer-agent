"""Sawyer Persistent Storage — SQLite-backed node registry and token accounting.

Schema:
  - nodes: registered Sawyer nodes with heartbeat tracking
  - accounts: user token accounts with balances
  - inference_records: every inference request with token counts
  - host_earnings: per-node earnings tracking
  - audit_log: Bedrock audit trail entries
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from sawyer.token.accounting import InferenceRecord, UserAccount
from sawyer.token.budget import HostEarnings, SubscriptionTier, TokenBalance

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_name TEXT,
    gpu TEXT,
    vram_gb REAL,
    bandwidth_mbps REAL,
    latency_ms REAL,
    experts_json TEXT,
    healthy INTEGER DEFAULT 1,
    last_heartbeat REAL,
    requests_served INTEGER DEFAULT 0,
    avg_response_ms REAL DEFAULT 0.0,
    active_requests INTEGER DEFAULT 0,
    max_concurrent_requests INTEGER DEFAULT 10,
    region TEXT DEFAULT '',
    registered_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS accounts (
    user_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    monthly_budget INTEGER NOT NULL,
    current_balance INTEGER NOT NULL,
    rollover INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,
    total_inferences INTEGER DEFAULT 0,
    last_inference_at REAL DEFAULT 0.0,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS inference_records (
    record_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    expert_ids_json TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    node_id TEXT DEFAULT '',
    routing_strategy TEXT DEFAULT '',
    finish_reason TEXT DEFAULT 'stop',
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS host_earnings (
    node_id TEXT PRIMARY KEY,
    tokens_served INTEGER DEFAULT 0,
    credits_earned REAL DEFAULT 0.0,
    usd_earned REAL DEFAULT 0.0,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    silo TEXT NOT NULL,
    details_json TEXT DEFAULT '{}',
    timestamp REAL NOT NULL,
    entry_hash TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_inference_user ON inference_records(user_id);
CREATE INDEX IF NOT EXISTS idx_inference_timestamp ON inference_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
"""


class SawyerStorage:
    """SQLite-backed persistent storage for Sawyer.

    Provides durability for:
    - Node registry (registration, heartbeats, deregistration)
    - Token accounts (balances, usage, rollover)
    - Inference records (audit trail)
    - Host earnings (payout tracking)
    """

    def __init__(self, database_url: str = "~/.sawyer/sawyer.db") -> None:
        self.db_path = Path(database_url).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        conn = self._get_conn()
        conn.executescript(SCHEMA)
        conn.commit()
        logger.info("Database initialized at %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Node Registry ──────────────────────────────────────────────

    def upsert_node(self, node: dict[str, Any]) -> None:
        """Insert or update a node in the registry."""
        conn = self._get_conn()
        now = time.time()
        conn.execute(
            """
            INSERT INTO nodes (node_id, node_name, gpu, vram_gb, bandwidth_mbps,
                latency_ms, experts_json, healthy, last_heartbeat,
                requests_served, avg_response_ms, active_requests,
                max_concurrent_requests, region, registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                node_name=excluded.node_name,
                gpu=excluded.gpu,
                vram_gb=excluded.vram_gb,
                bandwidth_mbps=excluded.bandwidth_mbps,
                latency_ms=excluded.latency_ms,
                experts_json=excluded.experts_json,
                healthy=excluded.healthy,
                last_heartbeat=excluded.last_heartbeat,
                requests_served=excluded.requests_served,
                avg_response_ms=excluded.avg_response_ms,
                active_requests=excluded.active_requests,
                max_concurrent_requests=excluded.max_concurrent_requests,
                region=excluded.region,
                updated_at=excluded.updated_at
            """,
            (
                node["node_id"],
                node.get("node_name", node["node_id"]),
                node.get("gpu", ""),
                node.get("vram_gb", 0.0),
                node.get("bandwidth_mbps", 0.0),
                node.get("latency_ms", 0.0),
                json.dumps(node.get("experts", [])),
                int(node.get("healthy", True)),
                node.get("last_heartbeat", now),
                node.get("requests_served", 0),
                node.get("avg_response_ms", 0.0),
                node.get("active_requests", 0),
                node.get("max_concurrent_requests", 10),
                node.get("region", ""),
                node.get("registered_at", now),
                now,
            ),
        )
        conn.commit()

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["experts"] = json.loads(result.pop("experts_json"))
        return result

    def delete_node(self, node_id: str) -> bool:
        """Delete a node from the registry. Returns True if deleted."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_nodes(self) -> list[dict[str, Any]]:
        """List all registered nodes."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM nodes ORDER BY registered_at").fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["experts"] = json.loads(result.pop("experts_json"))
            results.append(result)
        return results

    def update_heartbeat(self, node_id: str, latency_ms: float | None = None) -> None:
        """Update a node's heartbeat timestamp."""
        conn = self._get_conn()
        now = time.time()
        if latency_ms is not None:
            conn.execute(
                "UPDATE nodes SET last_heartbeat = ?, "
                "latency_ms = ?, updated_at = ? WHERE node_id = ?",
                (now, latency_ms, now, node_id),
            )
        else:
            conn.execute(
                "UPDATE nodes SET last_heartbeat = ?, updated_at = ? WHERE node_id = ?",
                (now, now, node_id),
            )
        conn.commit()

    # ── Token Accounts ──────────────────────────────────────────────

    def save_account(self, account: UserAccount) -> None:
        """Insert or update a token account."""
        conn = self._get_conn()
        now = time.time()
        conn.execute(
            """
            INSERT INTO accounts (user_id, tier, monthly_budget, current_balance,
                rollover, total_tokens_used, total_inferences, last_inference_at,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tier=excluded.tier,
                monthly_budget=excluded.monthly_budget,
                current_balance=excluded.current_balance,
                rollover=excluded.rollover,
                total_tokens_used=excluded.total_tokens_used,
                total_inferences=excluded.total_inferences,
                last_inference_at=excluded.last_inference_at,
                updated_at=excluded.updated_at
            """,
            (
                account.user_id,
                account.tier.value,
                account.balance.monthly_budget,
                account.balance.current_balance,
                account.balance.rollover,
                account.total_tokens_used,
                account.total_inferences,
                account.last_inference_at,
                account.created_at or now,
                now,
            ),
        )
        conn.commit()

    def load_account(self, user_id: str) -> UserAccount | None:
        """Load a token account from the database."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None

        tier = SubscriptionTier(row["tier"])
        balance = TokenBalance(
            tier=tier,
            monthly_budget=row["monthly_budget"],
            current_balance=row["current_balance"],
            rollover=row["rollover"],
        )
        return UserAccount(
            user_id=row["user_id"],
            tier=tier,
            balance=balance,
            total_tokens_used=row["total_tokens_used"],
            total_inferences=row["total_inferences"],
            last_inference_at=row["last_inference_at"],
            created_at=row["created_at"],
        )

    def list_accounts(self) -> list[UserAccount]:
        """List all token accounts."""
        conn = self._get_conn()
        rows = conn.execute("SELECT user_id FROM accounts ORDER BY created_at").fetchall()
        accounts = []
        for row in rows:
            account = self.load_account(row["user_id"])
            if account:
                accounts.append(account)
        return accounts

    # ── Inference Records ──────────────────────────────────────────

    def create_account(self, user_id: str, tier: SubscriptionTier) -> UserAccount:
        """Create a new token account with default balance for the given tier."""
        from sawyer.token.budget import TIER_TOKENS

        budget = TIER_TOKENS[tier]
        balance = TokenBalance(
            tier=tier,
            monthly_budget=budget,
            current_balance=budget,
            rollover=0,
        )
        account = UserAccount(
            user_id=user_id,
            tier=tier,
            balance=balance,
            total_tokens_used=0,
            total_inferences=0,
            last_inference_at=0.0,
            created_at=time.time(),
        )
        self.save_account(account)
        logger.info("Created account for user %s (tier=%s, budget=%d)", user_id, tier.value, budget)
        return account

    def deduct_tokens(self, user_id: str, tokens_used: int) -> None:
        """Deduct tokens from a user's current balance."""
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE accounts
            SET current_balance = current_balance - ?,
                total_tokens_used = total_tokens_used + ?,
                total_inferences = total_inferences + 1,
                last_inference_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (tokens_used, tokens_used, time.time(), time.time(), user_id),
        )
        conn.commit()

    def log_inference(
        self,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: float,
        node_id: str = "local",
        routing_strategy: str = "",
        finish_reason: str = "stop",
    ) -> None:
        """Log an inference record."""
        import uuid

        record = InferenceRecord(
            record_id=f"inf_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            model_name=model,
            expert_ids=[],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            node_id=node_id,
            routing_strategy=routing_strategy,
            finish_reason=finish_reason,
            timestamp=time.time(),
        )
        self.save_inference_record(record)

    def save_inference_record(self, record: InferenceRecord) -> None:
        """Save an inference record to the database."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO inference_records (record_id, user_id, model_name,
                expert_ids_json, input_tokens, output_tokens, total_tokens,
                latency_ms, node_id, routing_strategy, finish_reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.record_id,
                record.user_id,
                record.model_name,
                json.dumps(record.expert_ids),
                record.input_tokens,
                record.output_tokens,
                record.total_tokens,
                record.latency_ms,
                record.node_id,
                record.routing_strategy,
                record.finish_reason,
                record.timestamp,
            ),
        )
        conn.commit()

    def get_inference_records(self, user_id: str, limit: int = 100) -> list[InferenceRecord]:
        """Get recent inference records for a user."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM inference_records
            WHERE user_id = ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

        records = []
        for row in rows:
            records.append(
                InferenceRecord(
                    record_id=row["record_id"],
                    user_id=row["user_id"],
                    model_name=row["model_name"],
                    expert_ids=json.loads(row["expert_ids_json"]),
                    input_tokens=row["input_tokens"],
                    output_tokens=row["output_tokens"],
                    total_tokens=row["total_tokens"],
                    latency_ms=row["latency_ms"],
                    timestamp=row["timestamp"],
                    node_id=row["node_id"],
                    routing_strategy=row["routing_strategy"],
                    finish_reason=row["finish_reason"],
                )
            )
        return records

    # ── Host Earnings ──────────────────────────────────────────────

    def save_host_earnings(self, earnings: HostEarnings) -> None:
        """Save or update host earnings."""
        conn = self._get_conn()
        now = time.time()
        conn.execute(
            """
            INSERT INTO host_earnings (node_id, tokens_served, credits_earned,
                usd_earned, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                tokens_served=excluded.tokens_served,
                credits_earned=excluded.credits_earned,
                usd_earned=excluded.usd_earned,
                updated_at=excluded.updated_at
            """,
            (
                earnings.node_id,
                earnings.tokens_served,
                earnings.credits_earned,
                earnings.usd_earned,
                now,
            ),
        )
        conn.commit()

    def load_host_earnings(self, node_id: str) -> HostEarnings | None:
        """Load host earnings for a node."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM host_earnings WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return HostEarnings(
            node_id=row["node_id"],
            tokens_served=row["tokens_served"],
            credits_earned=row["credits_earned"],
            usd_earned=row["usd_earned"],
        )

    # ── Audit Log ──────────────────────────────────────────────────

    def append_audit(
        self,
        action: str,
        actor_id: str,
        target_id: str,
        silo: str,
        details: dict | None = None,
        entry_hash: str = "",
    ) -> None:
        """Append an entry to the audit log."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO audit_log (action, actor_id, target_id, silo,
                details_json, timestamp, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                actor_id,
                target_id,
                silo,
                json.dumps(details or {}),
                time.time(),
                entry_hash,
            ),
        )
        conn.commit()

    def query_audit(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query the audit log."""
        conn = self._get_conn()
        query = "SELECT * FROM audit_log WHERE 1=1"
        params: list[Any] = []

        if actor_id:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if action:
            query += " AND action = ?"
            params.append(action)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["details"] = json.loads(result.pop("details_json"))
            results.append(result)
        return results
