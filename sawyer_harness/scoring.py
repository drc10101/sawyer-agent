"""
Session Scoring -- ask the user to rate each session.

Scores are stored off-path in ~/.sawyer-harness/session-scores/
so they survive upgrades. Each score is a JSON file with:
  - session_id
  - timestamp
  - scores (dict of question -> 1-5 rating)
  - free_text (optional user comment)
  - agent_config_snapshot (model, verbosity, agreeability, reasoning at time of session)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import UserData

logger = logging.getLogger("sawyer-harness.scoring")

SCORES_DIR = UserData.session_scores_dir

# The specific set of questions asked after every session
SCORING_QUESTIONS = {
    "task_complete": "Did the agent complete what you asked? (1=Not at all, 5=Fully complete)",
    "accuracy": "How accurate was the agent's work? (1=Many errors, 5=Spot on)",
    "autonomy": "How much did the agent work on its own without needing help? (1=Needed constant guidance, 5=Fully autonomous)",
    "communication": "How clearly did the agent communicate what it was doing? (1=Confusing, 5=Crystal clear)",
    "honesty": "Did the agent give you honest feedback or just tell you what you wanted to hear? (1=Told me what I wanted, 5=Gave honest feedback)",
    "speed": "How efficiently did the agent work? (1=Very slow/wasteful, 5=Fast and efficient)",
}


@dataclass
class SessionScore:
    """A single session's user ratings."""
    session_id: str
    timestamp: str = ""
    scores: dict[str, int] = field(default_factory=dict)  # question_key -> 1-5
    free_text: str = ""
    agent_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def average(self) -> float:
        """Overall score across all rated dimensions."""
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)

    def save(self) -> Path:
        SCORES_DIR.mkdir(parents=True, exist_ok=True)
        path = SCORES_DIR / f"{self.session_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, session_id: str) -> "SessionScore | None":
        path = SCORES_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def list_all(cls, limit: int = 50) -> list["SessionScore"]:
        """Load all scores, newest first."""
        if not SCORES_DIR.exists():
            return []
        scores = []
        for f in sorted(SCORES_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                scores.append(cls(**data))
            except Exception:
                continue
        return scores


def compute_trends(scores: list[SessionScore]) -> dict[str, float]:
    """Compute average scores across all sessions per dimension."""
    if not scores:
        return {}
    trends = {}
    for key in SCORING_QUESTIONS:
        values = [s.scores[key] for s in scores if key in s.scores]
        if values:
            trends[key] = round(sum(values) / len(values), 2)
    if scores:
        trends["_overall"] = round(sum(s.average() for s in scores) / len(scores), 2)
    return trends