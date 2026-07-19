"""
Session Scoring -- ask the user to rate each session, and quality scoring
for the Ralph loop's automated evaluation.

Two scoring systems:

1. SessionScore: User-facing satisfaction ratings after each session.
   Stored in ~/.sawyer-harness/session-scores/

2. QualityScore: Automated evaluation of task output against success criteria.
   Used by the Ralph loop to decide whether a task result meets the bar,
   or needs another improvement iteration. Quality scores are persisted
   alongside orchestration data so the loop can track improvement over time.

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
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from .paths import UserData
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

# Ralph loop quality dimensions -- each dimension is scored 0-1
# and weighted toward the overall quality score.
QUALITY_DIMENSIONS = {
    "completeness": {
        "weight": 0.30,
        "description": "Does the output fully address the success criteria?",
    },
    "correctness": {
        "weight": 0.25,
        "description": "Is the output technically correct with no errors?",
    },
    "quality": {
        "weight": 0.20,
        "description": "Is the output well-structured, clean, and free of stubs?",
    },
    "coverage": {
        "weight": 0.15,
        "description": "Does the output cover edge cases and error handling?",
    },
    "efficiency": {
        "weight": 0.10,
        "description": "Is the output concise and efficient, without unnecessary verbosity?",
    },
}

# Default Ralph loop configuration
RALPH_DEFAULTS = {
    "quality_threshold": 0.80,   # Score needed to mark a task as done
    "max_iterations": 3,         # Max improvement iterations before forcing done
    "min_result_length": 50,     # Minimum result length to consider substantive
    "auto_patch_on_success": True, # Auto-patch skills when task succeeds without human correction
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


# ============================================================
# Quality Scoring -- Ralph Loop Evaluation
# ============================================================

@dataclass
class QualityScore:
    """Automated quality score for a task result, evaluated against
    success criteria. Used by the Ralph loop to decide whether a
    task is done or needs improvement.

    Each dimension is scored 0-1 and weighted per QUALITY_DIMENSIONS.
    The overall score is the weighted average. A score >= quality_threshold
    means the task passes and can be marked done.
    """
    task_id: str
    iteration: int = 1               # Which Ralph loop iteration (1 = first eval)
    dimensions: dict[str, float] = field(default_factory=dict)  # dimension -> 0-1
    overall: float = 0.0             # Weighted average
    passed: bool = False             # overall >= quality_threshold
    improvements: list[str] = field(default_factory=list)  # Suggested improvements
    auto_patch: bool = False        # Whether skill should be auto-patched
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QualityScore":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def score_result(
    result: str,
    success_criteria: str,
    iteration: int = 1,
    quality_threshold: float = RALPH_DEFAULTS["quality_threshold"],
    improvements: list[str] | None = None,
    human_corrected: bool = False,
) -> QualityScore:
    """Score a task result against its success criteria.

    This is the core of the Ralph loop's evaluation step. It performs
    structural analysis on the result text and compares it against the
    success criteria. It does NOT call an LLM -- that happens separately
    in the creative evaluation step. This provides the deterministic
    quality baseline.

    Args:
        result: The task's output text.
        success_criteria: What the task was supposed to accomplish.
        iteration: Which Ralph loop iteration (starts at 1).
        quality_threshold: Minimum score to pass (0-1).
        improvements: List of improvement suggestions from evaluation.
        human_corrected: Whether a human had to correct the output.
            When True, suppresses auto-patch (the skill didn't succeed
            on its own).

    Returns:
        QualityScore with per-dimension scores, overall score, and
        pass/fail determination.
    """
    dimensions: dict[str, float] = {}
    result_lower = result.lower().strip() if result else ""
    criteria_lower = success_criteria.lower().strip() if success_criteria else ""
    result_len = len(result.strip()) if result else 0

    # --- Completeness: Does the result address the success criteria? ---
    completeness = 0.5  # Start neutral

    if not result or result_len < RALPH_DEFAULTS["min_result_length"]:
        completeness = 0.1  # Empty or trivial result
    else:
        # Check for keywords from success criteria appearing in result
        if criteria_lower:
            # Extract significant words from criteria (skip short/common words)
            stop_words = {"a", "an", "the", "is", "are", "was", "were", "be",
                          "been", "being", "have", "has", "had", "do", "does",
                          "did", "will", "would", "shall", "should", "may",
                          "might", "must", "can", "could", "to", "of", "in",
                          "for", "on", "with", "at", "by", "from", "as", "and",
                          "or", "but", "not", "no", "all", "any", "that",
                          "this", "it", "its", "itself"}
            criteria_words = set(
                w for w in re.findall(r"\b\w{3,}\b", criteria_lower)
                if w not in stop_words
            )
            if criteria_words:
                matches = sum(1 for w in criteria_words if w in result_lower)
                match_ratio = matches / len(criteria_words)
                completeness = 0.3 + (0.7 * match_ratio)  # 0.3 base + up to 0.7 for matching
            else:
                # No extractable criteria words; judge by length
                completeness = 0.6 if result_len > 200 else 0.4
        else:
            # No success criteria provided; judge by result substance
            completeness = min(1.0, 0.4 + result_len / 1000)

        # Penalties for completeness red flags
        if "todo" in result_lower or "fixme" in result_lower:
            completeness *= 0.7  # Incomplete work left
        if "placeholder" in result_lower or "stub" in result_lower:
            completeness *= 0.6  # Placeholder content
        if result_len < 100:
            completeness *= 0.5  # Suspiciously short

    dimensions["completeness"] = round(min(1.0, completeness), 2)

    # --- Correctness: No obvious errors ---
    correctness = 0.8  # Start high; structural issues reduce it

    # Error indicators
    error_count = 0
    for marker in ["error", "exception", "traceback", "failed", "failure"]:
        error_count += result_lower.count(marker)
    if error_count > 0:
        correctness = max(0.2, correctness - error_count * 0.1)

    # Failure indicators in result
    if "does not work" in result_lower or "broken" in result_lower:
        correctness *= 0.6
    if "not implemented" in result_lower:
        correctness *= 0.4

    dimensions["correctness"] = round(min(1.0, correctness), 2)

    # --- Quality: Well-structured, clean, no stubs ---
    quality = 0.7  # Neutral baseline

    # Structure indicators (headers, lists, code blocks)
    structure_count = 0
    for pattern in [r"^\s*#.*$", r"^\s*[-*]\s", r"^\s*\d+\.\s", r"```"]:
        structure_count += len(re.findall(pattern, result, re.MULTILINE))

    if structure_count > 5:
        quality = min(1.0, 0.7 + structure_count * 0.01)
    elif structure_count == 0 and result_len > 200:
        quality = 0.5  # Long text with no structure

    # Stub/placeholder penalty
    if "placeholder" in result_lower:
        quality *= 0.6
    if "stub" in result_lower and "no stub" not in result_lower:
        quality *= 0.7
    if result_len < RALPH_DEFAULTS["min_result_length"]:
        quality *= 0.5

    dimensions["quality"] = round(min(1.0, quality), 2)

    # --- Coverage: Edge cases, error handling ---
    coverage = 0.5  # Start neutral; can't determine from text alone

    if result_len > 500:
        coverage += 0.1
    if "error" in result_lower and "handle" in result_lower:
        coverage += 0.1  # Error handling mentioned
    if "edge case" in result_lower or "corner case" in result_lower:
        coverage += 0.1
    if "test" in result_lower and ("pass" in result_lower or "coverage" in result_lower):
        coverage += 0.1

    dimensions["coverage"] = round(min(1.0, coverage), 2)

    # --- Efficiency: Concise, not verbose ---
    efficiency = 0.7  # Default for reasonable output

    if result_len > 0:
        # Very short results are efficient; very long results may be bloated
        if result_len < 100:
            efficiency = 0.8  # Concise
        elif result_len > 5000:
            efficiency = 0.5  # Potentially bloated
        elif result_len > 2000:
            efficiency = 0.6

        # Repetition check (crude)
        sentences = re.split(r"[.!?\n]", result)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
        if len(sentences) > 3:
            unique = len(set(sentences))
            ratio = unique / len(sentences) if sentences else 1.0
            if ratio < 0.8:
                efficiency *= 0.8  # Repetitive

    dimensions["efficiency"] = round(min(1.0, efficiency), 2)

    # --- Compute weighted overall score ---
    overall = 0.0
    for dim_name, dim_config in QUALITY_DIMENSIONS.items():
        weight = dim_config["weight"]
        score = dimensions.get(dim_name, 0.5)
        overall += score * weight
    overall = round(overall, 2)

    # --- Determine pass/fail ---
    passed = overall >= quality_threshold

    # --- Auto-patch: only if passed without human correction ---
    auto_patch = passed and not human_corrected

    return QualityScore(
        task_id="",
        iteration=iteration,
        dimensions=dimensions,
        overall=overall,
        passed=passed,
        improvements=improvements or [],
        auto_patch=auto_patch,
    )


def evaluate_and_score(
    result: str,
    success_criteria: str,
    task_id: str = "",
    iteration: int = 1,
    quality_threshold: float = RALPH_DEFAULTS["quality_threshold"],
    human_corrected: bool = False,
) -> QualityScore:
    """Full evaluation: structural checks + quality scoring.

    Combines the orchestrator's structural evaluation (TODO markers,
    stubs, errors) with the quality scoring system. This is the
    primary entry point for the Ralph loop.
    """
    # Gather structural improvements
    improvements = []
    result_lower = result.lower() if result else ""

    if result and len(result.strip()) < RALPH_DEFAULTS["min_result_length"]:
        improvements.append("Result is very short -- may indicate incomplete execution.")
    if "todo" in result_lower or "fixme" in result_lower:
        improvements.append("Result contains TODO/FIXME items -- incomplete implementation.")
    if "placeholder" in result_lower or "stub" in result_lower:
        improvements.append("Result contains placeholders/stubs -- needs real implementation.")
    if "error" in result_lower and "fail" in result_lower:
        improvements.append("Result mentions errors and failures -- may need retry or fix.")
    if "not implemented" in result_lower:
        improvements.append("Result explicitly states something is not implemented.")

    # Score against success criteria
    score = score_result(
        result=result,
        success_criteria=success_criteria,
        iteration=iteration,
        quality_threshold=quality_threshold,
        improvements=improvements,
        human_corrected=human_corrected,
    )
    score.task_id = task_id
    return score