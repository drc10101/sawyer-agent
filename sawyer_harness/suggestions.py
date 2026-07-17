"""
Suggestions — user-submitted feature requests and pain points.

Each suggestion is stored as a JSON file in UserData.suggestions_dir
so it survives upgrades and even full reinstalls.

When a suggestion is submitted, an email notification is sent if
SMTP is configured (notifications section in config.yaml).
If SMTP is not configured, the suggestion is still stored locally.

Structure of each suggestion file:
  {
    "id": "uuid4",
    "name": "User's name (for credit)",
    "suggestion": "What they want",
    "biggest_problem": "What frustrates them most about their current agent",
    "created": "ISO timestamp",
    "status": "new" | "acknowledged" | "implemented",
    "credit_given": false
  }
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .paths import UserData

logger = logging.getLogger("sawyer-harness.suggestions")


@dataclass
class Suggestion:
    """A single user suggestion."""
    id: str = ""
    name: str = ""
    suggestion: str = ""
    biggest_problem: str = ""
    created: str = ""
    status: str = "new"
    credit_given: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created:
            self.created = datetime.now(timezone.utc).isoformat()


class SuggestionStore:
    """Persistent storage for user suggestions."""

    def __init__(self, path: Path | None = None):
        self.path = path or UserData.suggestions_dir
        self.path.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, suggestion: str, biggest_problem: str = "") -> Suggestion:
        """Add a new suggestion. Returns the created Suggestion."""
        entry = Suggestion(
            name=name.strip(),
            suggestion=suggestion.strip(),
            biggest_problem=biggest_problem.strip(),
        )
        file_path = self.path / f"{entry.id}.json"
        file_path.write_text(
            json.dumps(asdict(entry), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("New suggestion from %s: %s", entry.name, entry.suggestion[:50])
        return entry

    def list_all(self, status: str | None = None) -> list[Suggestion]:
        """List all suggestions, newest first. Optionally filter by status."""
        suggestions = []
        for f in sorted(self.path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = Suggestion(**data)
                if status is None or s.status == status:
                    suggestions.append(s)
            except Exception:
                continue
        return suggestions

    def get(self, suggestion_id: str) -> Suggestion | None:
        """Get a suggestion by ID."""
        file_path = self.path / f"{suggestion_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return Suggestion(**data)
        except Exception:
            return None

    def update_status(self, suggestion_id: str, status: str) -> Suggestion | None:
        """Update the status of a suggestion (new -> acknowledged -> implemented)."""
        file_path = self.path / f"{suggestion_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            data["status"] = status
            file_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return Suggestion(**data)
        except Exception:
            return None

    def count(self) -> dict[str, int]:
        """Return count of suggestions by status."""
        counts = {"new": 0, "acknowledged": 0, "implemented": 0}
        for s in self.list_all():
            if s.status in counts:
                counts[s.status] += 1
        return counts


def send_suggestion_email(
    suggestion: Suggestion,
    smtp_host: str,
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
    from_address: str = "",
    to_address: str = "",
    use_tls: bool = True,
) -> bool:
    """Send an email notification for a new suggestion.

    Returns True if sent successfully, False if SMTP is not configured or
    sending fails. Failures are logged but never raise — the local JSON
    record is the source of truth.
    """
    if not smtp_host or not to_address:
        logger.debug("SMTP not configured — skipping email notification")
        return False

    subject = f"Sawyer Suggestion from {suggestion.name}: {suggestion.suggestion[:60]}"
    body_parts = [
        f"Name: {suggestion.name}",
        f"Suggestion: {suggestion.suggestion}",
    ]
    if suggestion.biggest_problem:
        body_parts.append(f"\nBiggest Problem:\n{suggestion.biggest_problem}")
    body_parts.append(f"\nID: {suggestion.id}")
    body_parts.append(f"Submitted: {suggestion.created}")
    body = "\n".join(body_parts)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_address or smtp_user
    msg["To"] = to_address

    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=context)
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
        logger.info("Suggestion email sent to %s", to_address)
        return True
    except Exception as e:
        logger.warning("Failed to send suggestion email: %s", e)
        return False