"""
Last Known Good (LKG) version tracking.

Stores tagged git commits that the user has confirmed as working.
When something breaks, the user can revert to the most recent LKG.

LKG data is stored off-path in ~/.sawyer-harness/lkg.json so it
survives package upgrades.

Each entry contains:
  - commit: git SHA
  - tag: short name (e.g. "v0.7.4-stable")
  - timestamp: when it was marked good
  - session_score_id: link to the scoring session that confirmed it
  - note: user's description of what was working
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sawyer-harness.lkg")

LKG_FILE = Path.home() / ".sawyer-harness" / "lkg.json"

# Where the sawyer-agent repo lives (used for git operations)
REPO_DIR = Path(__file__).parent.parent


@dataclass
class LKGEntry:
    """A last-known-good version entry."""
    commit: str
    tag: str = ""
    timestamp: str = ""
    session_score_id: str = ""
    note: str = ""
    average_score: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class LKGStore:
    """Persistent storage for last-known-good versions."""

    def __init__(self, path: Path | None = None):
        self.path = path or LKG_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[LKGEntry] = []
        self._load()

    def _load(self):
        if not self.path.exists():
            self._entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._entries = [LKGEntry(**e) for e in data.get("entries", [])]
        except Exception as e:
            logger.error(f"Failed to load LKG data: {e}")
            self._entries = []

    def _save(self):
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "entries": [asdict(e) for e in self._entries],
        }
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def mark_good(
        self,
        commit: str = "",
        tag: str = "",
        note: str = "",
        session_score_id: str = "",
        average_score: float = 0.0,
    ) -> LKGEntry:
        """Mark the current commit (or a specific one) as last known good."""
        if not commit:
            commit = self._get_current_commit()
        entry = LKGEntry(
            commit=commit,
            tag=tag or f"lkg-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            note=note,
            session_score_id=session_score_id,
            average_score=average_score,
        )
        self._entries.append(entry)
        self._save()
        logger.info(f"Marked LKG: {entry.tag} ({entry.commit[:8]})")
        return entry

    def get_latest(self) -> LKGEntry | None:
        """Get the most recent LKG entry."""
        if not self._entries:
            return None
        return self._entries[-1]

    def list_all(self, limit: int = 20) -> list[LKGEntry]:
        """List all LKG entries, newest first."""
        return sorted(self._entries, key=lambda e: e.timestamp, reverse=True)[:limit]

    def _get_current_commit(self) -> str:
        """Get the current git commit SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(REPO_DIR),
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def revert_to_latest(self) -> dict[str, str]:
        """Revert to the latest LKG commit. Returns git output."""
        entry = self.get_latest()
        if not entry:
            return {"error": "No LKG version found"}

        try:
            # Stash any uncommitted changes first
            subprocess.run(
                ["git", "stash"],
                capture_output=True, text=True, timeout=10,
                cwd=str(REPO_DIR),
            )
            # Checkout the LKG commit
            result = subprocess.run(
                ["git", "checkout", entry.commit],
                capture_output=True, text=True, timeout=10,
                cwd=str(REPO_DIR),
            )
            if result.returncode != 0:
                return {"error": result.stderr, "commit": entry.commit}
            return {
                "status": "reverted",
                "commit": entry.commit,
                "tag": entry.tag,
                "note": entry.note,
                "timestamp": entry.timestamp,
            }
        except Exception as e:
            return {"error": str(e)}

    def revert_to_tag(self, tag: str) -> dict[str, str]:
        """Revert to a specific LKG entry by tag."""
        for entry in self._entries:
            if entry.tag == tag:
                try:
                    subprocess.run(
                        ["git", "stash"],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(REPO_DIR),
                    )
                    result = subprocess.run(
                        ["git", "checkout", entry.commit],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(REPO_DIR),
                    )
                    if result.returncode != 0:
                        return {"error": result.stderr, "commit": entry.commit}
                    return {
                        "status": "reverted",
                        "commit": entry.commit,
                        "tag": entry.tag,
                        "note": entry.note,
                        "timestamp": entry.timestamp,
                    }
                except Exception as e:
                    return {"error": str(e)}
        return {"error": f"Tag '{tag}' not found in LKG history"}