# Session Scoring, LKG Revert, Agreeability, and Reasoning Settings — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add four new features to Sawyer Agent: session scoring, last-known-good version tracking with revert, agreeability control, and reasoning depth settings.

**Architecture:** Extend existing config, session engine, and agent core. All new data stored off-path in `~/.sawyer-harness/` (survives upgrades). Settings exposed via the existing `/api/agent-config` endpoint and the sidebar Settings panel. Revert is a one-click operation that restores a tagged git commit.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite, git CLI

---

## Task 1: Add Agreeability and Reasoning Fields to AgentConfig

**Objective:** Extend the config dataclass with two new settings that control model behavior.

**Files:**
- Modify: `sawyer_harness/config.py`

**Step 1: Add agreeability enum and reasoning levels**

Add to `config.py` after the `VERBOSITY_LEVELS` tuple:

```python
# Agreeability: controls whether the agent tells the user what they want
# to hear (agreeable) or gives honest/truthful suggestions (honest)
AGREEABILITY_LEVELS = ("agreeable", "balanced", "honest")

# Reasoning depth: controls how much the model reasons before responding
REASONING_LEVELS = ("low", "medium", "medium_high", "high")
```

**Step 2: Add fields to AgentConfig dataclass**

Add two new fields to the `AgentConfig` dataclass (after `stream_tool_output`):

```python
agreeability: str = "balanced"   # agreeable | balanced | honest
reasoning: str = "medium"         # low | medium | medium_high | high
```

**Step 3: Update from_file() to load new fields**

In `HarnessConfig.from_file()`, after the `stream_tool_output` line in the `agent_data` section, add:

```python
agreeability=agent_data.get("agreeability", "balanced"),
reasoning=agent_data.get("reasoning", "medium"),
```

Also add validation:

```python
agreeability = agent_data.get("agreeability", "balanced")
if agreeability not in AGREEABILITY_LEVELS:
    agreeability = "balanced"

reasoning = agent_data.get("reasoning", "medium")
if reasoning not in REASONING_LEVELS:
    reasoning = "medium"
```

**Step 4: Update save() to persist new fields**

In `HarnessConfig.save()`, add to the agent dict:

```python
"agreeability": self.agent.agreeability,
"reasoning": self.agent.reasoning,
```

**Step 5: Commit**

```bash
git add sawyer_harness/config.py
git commit -m "feat: add agreeability and reasoning settings to AgentConfig"
```

---

## Task 2: Add Agreeability and Reasoning Prompt Injection to Agent

**Objective:** Inject agreeability and reasoning instructions into the system prompt at runtime.

**Files:**
- Modify: `sawyer_harness/agent.py`

**Step 1: Add agreeability prompt templates**

After `VERBOSITY_PROMPTS` in `agent.py`, add:

```python
AGREEABILITY_PROMPTS = {
    "agreeable": (
        "\n## Response Style: Agreeable\n"
        "Prioritize what the user wants to hear. Be supportive and encouraging. "
        "If the user has an idea, explore how to make it work rather than pointing out flaws. "
        "Offer alternatives only if the user's approach truly cannot work. "
        "Be constructive, not critical.\n"
    ),
    "balanced": (
        "\n## Response Style: Balanced\n"
        "Be honest and direct, but tactful. If something won't work, say so clearly "
        "and offer alternatives. Validate good ideas, challenge bad ones constructively. "
        "Present options and trade-offs so the user can decide.\n"
    ),
    "honest": (
        "\n## Response Style: Honest\n"
        "Always tell the truth, even when it's not what the user wants to hear. "
        "Point out flaws directly. Challenge assumptions. If something is a bad idea, "
        "say so and explain why. Prioritize correctness over comfort. "
        "Never sugar-coat or agree just to please.\n"
    ),
}

REASONING_PROMPTS = {
    "low": (
        "\n## Reasoning: Quick\n"
        "Answer directly with minimal explanation. Skip step-by-step reasoning. "
        "Give the answer and move on. Think fast, respond fast.\n"
    ),
    "medium": (
        "\n## Reasoning: Standard\n"
        "Think through the problem normally. Show key reasoning steps. "
        "Explain your logic when it matters, skip it when it's obvious.\n"
    ),
    "medium_high": (
        "\n## Reasoning: Thorough\n"
        "Think through the problem carefully. Show your reasoning process. "
        "Consider edge cases and alternatives. Explain why you chose this approach "
        "over others. Verify your answer before responding.\n"
    ),
    "high": (
        "\n## Reasoning: Deep\n"
        "Think deeply about every aspect of the problem. Walk through your full reasoning "
        "chain. Consider all edge cases, failure modes, and alternatives. Verify each step. "
        "Challenge your own assumptions. Provide thorough analysis before your conclusion. "
        "Show all work.\n"
    ),
}
```

**Step 2: Inject new prompts in _build_system_prompt()**

In `_build_system_prompt()`, after the verbosity injection block (after `parts.append(VERBOSITY_PROMPTS.get(...))`), add:

```python
# Inject agreeability from config
agreeability = getattr(self.config.agent, "agreeability", "balanced") if hasattr(self.config, "agent") and self.config.agent else "balanced"
parts.append(AGREEABILITY_PROMPTS.get(agreeability, AGREEABILITY_PROMPTS["balanced"]))

# Inject reasoning depth from config
reasoning = getattr(self.config.agent, "reasoning", "medium") if hasattr(self.config, "agent") and self.config.agent else "medium"
parts.append(REASONING_PROMPTS.get(reasoning, REASONING_PROMPTS["medium"]))
```

**Step 3: Commit**

```bash
git add sawyer_harness/agent.py
git commit -m "feat: inject agreeability and reasoning prompts into system prompt"
```

---

## Task 3: Add API Endpoints for Agreeability and Reasoning

**Objective:** Expose the new settings through the existing agent-config API.

**Files:**
- Modify: `sawyer_harness/web/server.py`

**Step 1: Update AgentConfigUpdate Pydantic model**

Add fields to `AgentConfigUpdate`:

```python
agreeability: str | None = None    # agreeable | balanced | honest
reasoning: str | None = None         # low | medium | medium_high | high
```

**Step 2: Update get_agent_config endpoint**

Add the new fields to the response dict in `get_agent_config()`:

```python
"agreeability": state.config.agent.agreeability,
"reasoning": state.config.agent.reasoning,
```

**Step 3: Update update_agent_config endpoint**

Add validation and update logic in `update_agent_config()`:

```python
if update.agreeability is not None:
    if update.agreeability not in ("agreeable", "balanced", "honest"):
        raise HTTPException(status_code=400, detail="agreeability must be one of: agreeable, balanced, honest")
    state.config.agent.agreeability = update.agreeability
if update.reasoning is not None:
    if update.reasoning not in ("low", "medium", "medium_high", "high"):
        raise HTTPException(status_code=400, detail="reasoning must be one of: low, medium, medium_high, high")
    state.config.agent.reasoning = update.reasoning
```

Also add to the response dict:

```python
"agreeability": state.config.agent.agreeability,
"reasoning": state.config.agent.reasoning,
```

**Step 4: Commit**

```bash
git add sawyer_harness/web/server.py
git commit -m "feat: add agreeability and reasoning to agent-config API"
```

---

## Task 4: Create Session Scoring System

**Objective:** Build a session scoring module that asks the user a set of questions at session end, stores scores, and provides scoring history.

**Files:**
- Create: `sawyer_harness/scoring.py`
- Modify: `sawyer_harness/session_engine.py`

**Step 1: Create scoring.py with question set and storage**

```python
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

logger = logging.getLogger("sawyer-harness.scoring")

SCORES_DIR = Path.home() / ".sawyer-harness" / "session-scores"

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
    trends["_overall"] = round(sum(s.average() for s in scores) / len(scores), 2) if scores else 0
    return trends
```

**Step 2: Commit**

```bash
git add sawyer_harness/scoring.py
git commit -m "feat: add session scoring module with questions and storage"
```

---

## Task 5: Add Session Scoring API Endpoints

**Objective:** Expose scoring questions and score submission/collection via REST API.

**Files:**
- Modify: `sawyer_harness/web/server.py`

**Step 1: Add scoring imports and models**

At the top of server.py, add:

```python
from ..scoring import SessionScore, SCORING_QUESTIONS, compute_trends
```

Add Pydantic model after existing models:

```python
class SessionScoreSubmit(BaseModel):
    session_id: str
    scores: dict[str, int]      # question_key -> rating (1-5)
    free_text: str = ""
```

**Step 2: Add scoring endpoints**

After the agent-config endpoints:

```python
@app.get("/api/scoring/questions")
async def get_scoring_questions():
    """Get the session scoring question set."""
    return {"questions": {k: v for k, v in SCORING_QUESTIONS.items()}}

@app.post("/api/scoring/submit")
async def submit_session_score(submission: SessionScoreSubmit):
    """Submit a session score."""
    # Validate scores
    for key, value in submission.scores.items():
        if key not in SCORING_QUESTIONS:
            raise HTTPException(status_code=400, detail=f"Unknown question: {key}")
        if not (1 <= value <= 5):
            raise HTTPException(status_code=400, detail=f"Score for {key} must be 1-5, got {value}")

    # Snapshot current agent config
    agent_config = {
        "model": state.config.llm.model,
        "provider": state.config.llm.provider,
        "verbosity": state.config.agent.verbosity,
        "agreeability": state.config.agent.agreeability,
        "reasoning": state.config.agent.reasoning,
    }

    score = SessionScore(
        session_id=submission.session_id,
        scores=submission.scores,
        free_text=submission.free_text,
        agent_config=agent_config,
    )
    path = score.save()
    return {"status": "saved", "average": score.average(), "path": str(path)}

@app.get("/api/scoring/history")
async def get_scoring_history(limit: int = 50):
    """Get scoring history and trends."""
    scores = SessionScore.list_all(limit=limit)
    trends = compute_trends(scores)
    return {
        "scores": [
            {
                "session_id": s.session_id,
                "timestamp": s.timestamp,
                "scores": s.scores,
                "average": s.average(),
                "free_text": s.free_text,
                "agent_config": s.agent_config,
            }
            for s in scores
        ],
        "trends": trends,
        "total_sessions": len(scores),
    }
```

**Step 3: Commit**

```bash
git add sawyer_harness/web/server.py
git commit -m "feat: add session scoring API endpoints"
```

---

## Task 6: Create Last Known Good (LKG) Module

**Objective:** Track git commits as "last known good" versions based on user scoring. Store off-path.

**Files:**
- Create: `sawyer_harness/lkg.py`

**Step 1: Create lkg.py**

```python
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
                cwd=str(Path(__file__).parent.parent),
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
                cwd=str(Path(__file__).parent.parent),
            )
            # Checkout the LKG commit
            result = subprocess.run(
                ["git", "checkout", entry.commit],
                capture_output=True, text=True, timeout=10,
                cwd=str(Path(__file__).parent.parent),
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
                        cwd=str(Path(__file__).parent.parent),
                    )
                    result = subprocess.run(
                        ["git", "checkout", entry.commit],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(Path(__file__).parent.parent),
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
```

**Step 2: Commit**

```bash
git add sawyer_harness/lkg.py
git commit -m "feat: add last-known-good version tracking with revert"
```

---

## Task 7: Add LKG API Endpoints

**Objective:** Expose LKG marking, listing, and reverting via REST API.

**Files:**
- Modify: `sawyer_harness/web/server.py`

**Step 1: Add LKG imports and models**

At the top of server.py, add:

```python
from ..lkg import LKGStore, LKGEntry
```

Add Pydantic models:

```python
class LKGMarkRequest(BaseModel):
    commit: str = ""     # empty = current HEAD
    tag: str = ""
    note: str = ""
    session_score_id: str = ""

class LKGRevertRequest(BaseModel):
    tag: str = ""        # empty = revert to latest LKG
```

**Step 2: Initialize LKGStore in _AppState**

In `_AppState.__init__()`, add:

```python
self.lkg_store = LKGStore()
```

**Step 3: Add LKG endpoints**

```python
@app.get("/api/lkg")
async def list_lkg(limit: int = 20):
    """List all last-known-good versions."""
    entries = state.lkg_store.list_all(limit=limit)
    return {
        "entries": [
            {
                "commit": e.commit,
                "tag": e.tag,
                "timestamp": e.timestamp,
                "note": e.note,
                "average_score": e.average_score,
                "session_score_id": e.session_score_id,
            }
            for e in entries
        ],
        "latest": {
            "commit": state.lkg_store.get_latest().commit,
            "tag": state.lkg_store.get_latest().tag,
            "timestamp": state.lkg_store.get_latest().timestamp,
            "note": state.lkg_store.get_latest().note,
        } if state.lkg_store.get_latest() else None,
    }

@app.post("/api/lkg/mark")
async def mark_lkg(request: LKGMarkRequest):
    """Mark current commit (or a specific one) as last known good."""
    # If linked to a scoring session, pull the average score
    avg_score = 0.0
    if request.session_score_id:
        score = SessionScore.load(request.session_score_id)
        if score:
            avg_score = score.average()

    entry = state.lkg_store.mark_good(
        commit=request.commit,
        tag=request.tag,
        note=request.note,
        session_score_id=request.session_score_id,
        average_score=avg_score,
    )
    return {
        "status": "marked",
        "commit": entry.commit,
        "tag": entry.tag,
        "timestamp": entry.timestamp,
        "average_score": entry.average_score,
    }

@app.post("/api/lkg/revert")
async def revert_lkg(request: LKGRevertRequest):
    """Revert to a last-known-good version."""
    if request.tag:
        result = state.lkg_store.revert_to_tag(request.tag)
    else:
        result = state.lkg_store.revert_to_latest()
    return result
```

**Step 4: Commit**

```bash
git add sawyer_harness/web/server.py
git commit -m "feat: add LKG API endpoints for marking and reverting versions"
```

---

## Task 8: Add Frontend Controls for Agreeability and Reasoning

**Objective:** Add agreeability and reasoning controls to the Settings panel in the web UI.

**Files:**
- Modify: `sawyer_harness/web/static/index.html`

**Step 1: Add agreeability and reasoning controls to the Settings panel**

Find the existing Settings panel section (where Verbosity and Mode dropdowns are). After the Mode dropdown, add:

```html
<div class="setting-group">
    <label for="agreeability">Agreeability</label>
    <select id="agreeability" onchange="updateAgentConfig()">
        <option value="agreeable">Agreeable — Tell me what I want to hear</option>
        <option value="balanced" selected>Balanced — Honest but tactful</option>
        <option value="honest">Honest — Always tell me the truth</option>
    </select>
    <small class="setting-hint">Controls whether the agent prioritizes pleasing you or giving honest feedback.</small>
</div>

<div class="setting-group">
    <label for="reasoning">Reasoning Depth</label>
    <select id="reasoning" onchange="updateAgentConfig()">
        <option value="low">Low — Quick answers, minimal explanation</option>
        <option value="medium" selected>Medium — Normal reasoning</option>
        <option value="medium_high">Medium-High — Thorough analysis</option>
        <option value="high">High — Deep reasoning, show all work</option>
    </select>
    <small class="setting-hint">Controls how deeply the agent reasons before responding.</small>
</div>
```

**Step 2: Update updateAgentConfig() JavaScript function**

In the existing `updateAgentConfig()` function, add the new fields to the request body:

```javascript
const agreeability = document.getElementById('agreeability')?.value || 'balanced';
const reasoning = document.getElementById('reasoning')?.value || 'medium';

// Add to the fetch body:
agreeability: agreeability,
reasoning: reasoning,
```

**Step 3: Update loadAgentConfig() to populate new fields**

In the existing config loading function, add:

```javascript
const agreeabilitySelect = document.getElementById('agreeability');
if (agreeabilitySelect) agreeabilitySelect.value = data.agreeability || 'balanced';

const reasoningSelect = document.getElementById('reasoning');
if (reasoningSelect) reasoningSelect.value = data.reasoning || 'medium';
```

**Step 4: Commit**

```bash
git add sawyer_harness/web/static/index.html
git commit -m "feat: add agreeability and reasoning controls to Settings panel"
```

---

## Task 9: Add Session Scoring UI to Frontend

**Objective:** Add a scoring modal that appears when a session ends, and a scoring history view.

**Files:**
- Modify: `sawyer_harness/web/static/index.html`

**Step 1: Add scoring modal HTML**

After the existing modals in index.html, add:

```html
<!-- Session Scoring Modal -->
<div id="scoring-modal" class="modal" style="display:none;">
    <div class="modal-content scoring-modal">
        <div class="modal-header">
            <h2>Rate This Session</h2>
            <button class="modal-close" onclick="closeScoringModal()">&times;</button>
        </div>
        <div class="scoring-questions" id="scoring-questions">
            <!-- Populated dynamically -->
        </div>
        <div class="scoring-comment">
            <label for="scoring-comment">Any additional feedback?</label>
            <textarea id="scoring-comment" placeholder="What worked? What didn't? What should improve?" rows="3"></textarea>
        </div>
        <div class="scoring-actions">
            <button class="btn btn-secondary" onclick="closeScoringModal()">Skip</button>
            <button class="btn btn-primary" onclick="submitScore()">Submit Rating</button>
        </div>
    </div>
</div>
```

**Step 2: Add scoring CSS**

Add to the existing `<style>` section:

```css
.scoring-modal { max-width: 520px; }
.scoring-questions { padding: 16px 0; }
.scoring-question {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 0; border-bottom: 1px solid #2a2a3a;
}
.scoring-question label { flex: 1; font-size: 14px; color: #ccc; }
.scoring-stars { display: flex; gap: 6px; }
.scoring-star {
    width: 32px; height: 32px; border: 2px solid #3a3a4a;
    border-radius: 50%; cursor: pointer; background: transparent;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; color: #666; transition: all 0.15s;
}
.scoring-star:hover { border-color: #12c7ef; color: #12c7ef; }
.scoring-star.active { border-color: #12c7ef; background: #12c7ef; color: #000; }
.scoring-comment textarea {
    width: 100%; background: #1a1a2e; border: 1px solid #2a2a3a;
    border-radius: 8px; padding: 10px; color: #eee; font-size: 14px;
    resize: vertical; margin-top: 8px;
}
.scoring-actions { display: flex; gap: 12px; justify-content: flex-end; margin-top: 16px; }
```

**Step 3: Add scoring JavaScript functions**

```javascript
let scoringSessionId = '';
let scoringData = {};

async function showScoringModal(sessionId) {
    scoringSessionId = sessionId;
    scoringData = {};
    // Fetch questions
    const resp = await fetch('/api/scoring/questions');
    const data = await resp.json();
    const container = document.getElementById('scoring-questions');
    container.innerHTML = '';
    for (const [key, question] of Object.entries(data.questions)) {
        scoringData[key] = 0;
        const div = document.createElement('div');
        div.className = 'scoring-question';
        div.innerHTML = `
            <label>${question}</label>
            <div class="scoring-stars" data-key="${key}">
                ${[1,2,3,4,5].map(n => `<button class="scoring-star" data-value="${n}" onclick="setScore('${key}', ${n}, this)">${n}</button>`).join('')}
            </div>
        `;
        container.appendChild(div);
    }
    document.getElementById('scoring-modal').style.display = 'flex';
}

function setScore(key, value, el) {
    scoringData[key] = value;
    const stars = el.parentElement.querySelectorAll('.scoring-star');
    stars.forEach(s => s.classList.toggle('active', parseInt(s.dataset.value) <= value));
}

function closeScoringModal() {
    document.getElementById('scoring-modal').style.display = 'none';
}

async function submitScore() {
    // Filter out unanswered questions
    const scores = {};
    for (const [key, value] of Object.entries(scoringData)) {
        if (value > 0) scores[key] = value;
    }
    if (Object.keys(scores).length === 0) {
        closeScoringModal();
        return;
    }
    const comment = document.getElementById('scoring-comment')?.value || '';
    await fetch('/api/scoring/submit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            session_id: scoringSessionId,
            scores: scores,
            free_text: comment,
        }),
    });
    closeScoringModal();
}
```

**Step 4: Wire scoring into session clear/end**

In the existing session clearing function, after the clear request, add a call to show the scoring modal:

```javascript
// After clearing the session, offer to rate it
showScoringModal(currentSessionId);
```

**Step 5: Commit**

```bash
git add sawyer_harness/web/static/index.html
git commit -m "feat: add session scoring modal and star-rating UI"
```

---

## Task 10: Add LKG and Scoring History Section to Settings

**Objective:** Add a "Version Control" section to the Settings panel showing LKG history and a "Revert" button.

**Files:**
- Modify: `sawyer_harness/web/static/index.html`

**Step 1: Add Version Control section to Settings panel**

After the agent settings, add:

```html
<div class="settings-section">
    <h3>Version Control</h3>
    <div class="lkg-actions">
        <button class="btn btn-primary" onclick="markLKG()">Mark Current as Good</button>
        <button class="btn btn-danger" onclick="revertToLKG()">Revert to Last Known Good</button>
    </div>
    <div id="lkg-history" class="lkg-history">
        <!-- Populated dynamically -->
    </div>
    <input type="text" id="lkg-note" placeholder="Note: What's working well?" class="lkg-note-input">
</div>

<div class="settings-section">
    <h3>Session Scores</h3>
    <div id="score-history" class="score-history">
        <!-- Populated dynamically -->
    </div>
</div>
```

**Step 2: Add CSS for version control section**

```css
.settings-section { margin-top: 24px; padding-top: 16px; border-top: 1px solid #2a2a3a; }
.settings-section h3 { font-size: 14px; color: #12c7ef; margin-bottom: 12px; }
.lkg-actions { display: flex; gap: 12px; margin-bottom: 12px; }
.lkg-note-input { width: 100%; background: #1a1a2e; border: 1px solid #2a2a3a; border-radius: 6px; padding: 8px; color: #eee; font-size: 13px; margin-bottom: 8px; }
.lkg-history, .score-history { max-height: 200px; overflow-y: auto; }
.lkg-entry, .score-entry { padding: 8px; border-bottom: 1px solid #1a1a2e; font-size: 13px; }
.lkg-entry .tag { color: #12c7ef; font-weight: 600; }
.lkg-entry .commit { color: #888; font-family: monospace; font-size: 12px; }
.score-entry .dimension { color: #aaa; }
.score-entry .rating { color: #12c7ef; font-weight: 600; }
.btn-danger { background: #c0392b; color: #fff; }
.btn-danger:hover { background: #e74c3c; }
```

**Step 3: Add LKG JavaScript functions**

```javascript
async function markLKG() {
    const note = document.getElementById('lkg-note')?.value || '';
    const resp = await fetch('/api/lkg/mark', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ note: note }),
    });
    const data = await resp.json();
    if (data.status === 'marked') {
        loadLKGHistory();
        document.getElementById('lkg-note').value = '';
    }
}

async function revertToLKG() {
    if (!confirm('Revert to the last known good version? Uncommitted changes will be stashed.')) return;
    const resp = await fetch('/api/lkg/revert', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({}),
    });
    const data = await resp.json();
    if (data.status === 'reverted') {
        alert(`Reverted to ${data.tag} (${data.commit?.substring(0, 8)}). Restart Sawyer for changes to take effect.`);
    } else {
        alert(`Revert failed: ${data.error}`);
    }
}

async function loadLKGHistory() {
    const resp = await fetch('/api/lkg');
    const data = await resp.json();
    const container = document.getElementById('lkg-history');
    if (!container) return;
    if (!data.entries || data.entries.length === 0) {
        container.innerHTML = '<div class="lkg-entry" style="color:#888;">No versions marked as good yet.</div>';
        return;
    }
    container.innerHTML = data.entries.slice(0, 10).map(e => `
        <div class="lkg-entry">
            <span class="tag">${e.tag}</span>
            <span class="commit">${e.commit?.substring(0, 8)}</span>
            <span style="color:#888; margin-left:8px;">${e.note}</span>
            <span style="float:right; color:#888; font-size:11px;">${new Date(e.timestamp).toLocaleDateString()}</span>
        </div>
    `).join('');
}

async function loadScoreHistory() {
    const resp = await fetch('/api/scoring/history?limit=10');
    const data = await resp.json();
    const container = document.getElementById('score-history');
    if (!container) return;
    if (!data.scores || data.scores.length === 0) {
        container.innerHTML = '<div class="score-entry" style="color:#888;">No session scores yet.</div>';
        return;
    }
    container.innerHTML = data.scores.map(s => {
        const dims = Object.entries(s.scores).map(([k, v]) =>
            `<span class="dimension">${k}</span>: <span class="rating">${v}/5</span>`
        ).join(' &middot; ');
        return `<div class="score-entry">
            <strong>${s.average.toFixed(1)}/5.0</strong> &middot; ${dims}
            <span style="float:right; color:#888; font-size:11px;">${new Date(s.timestamp).toLocaleDateString()}</span>
        </div>`;
    }).join('');
}
```

**Step 4: Call loadLKGHistory() and loadScoreHistory() when Settings panel opens**

Find the existing settings panel initialization and add calls to:

```javascript
loadLKGHistory();
loadScoreHistory();
```

**Step 5: Commit**

```bash
git add sawyer_harness/web/static/index.html
git commit -m "feat: add LKG version control and scoring history to Settings panel"
```

---

## Task 11: Update Config Save to Persist All New Settings

**Objective:** Ensure agreeability and reasoning survive restarts by persisting to config.yaml.

**Files:**
- Modify: `sawyer_harness/config.py`

This was already handled in Task 1 (adding to save() and from_file()). Verify by reading the final state.

**Step 1: Verify the config save/load round-trips correctly**

Run:

```bash
cd ~/sawyer-agent && python -c "
from sawyer_harness.config import HarnessConfig, AGREEABILITY_LEVELS, REASONING_LEVELS
c = HarnessConfig()
print('agreeability:', c.agent.agreeability)
print('reasoning:', c.agent.reasoning)
c.agent.agreeability = 'honest'
c.agent.reasoning = 'high'
path = c.save('/tmp/test_config.yaml')
c2 = HarnessConfig.from_file(path)
print('round-trip agreeability:', c2.agent.agreeability)
print('round-trip reasoning:', c2.agent.reasoning)
assert c2.agent.agreeability == 'honest'
assert c2.agent.reasoning == 'high'
print('PASS')
"
```

Expected: PASS

**Step 2: Commit any fixes**

```bash
git add -A && git commit -m "fix: verify agreeability and reasoning config round-trip"
```

---

## Task 12: Integration Test — Full Pipeline

**Objective:** Verify the complete pipeline: config -> system prompt injection -> API -> scoring -> LKG.

**Files:**
- Create: `tests/test_new_features.py`

**Step 1: Write integration tests**

```python
"""Integration tests for session scoring, LKG, agreeability, and reasoning."""

import pytest
from pathlib import Path
import tempfile
import json

from sawyer_harness.config import HarnessConfig, AgentConfig, AGREEABILITY_LEVELS, REASONING_LEVELS
from sawyer_harness.scoring import SessionScore, SCORING_QUESTIONS, compute_trends
from sawyer_harness.lkg import LKGStore, LKGEntry


class TestAgreeabilityReasoning:
    """Test agreeability and reasoning config and prompt injection."""

    def test_config_defaults(self):
        config = HarnessConfig()
        assert config.agent.agreeability == "balanced"
        assert config.agent.reasoning == "medium"

    def test_config_validation_invalid_agreeability(self):
        config = HarnessConfig()
        config.agent.agreeability = "invalid"
        # from_file should reset to default
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("agent:\n  agreeability: invalid\n  reasoning: medium\n")
            f.flush()
            loaded = HarnessConfig.from_file(f.name)
            assert loaded.agent.agreeability == "balanced"

    def test_config_validation_invalid_reasoning(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("agent:\n  agreeability: balanced\n  reasoning: extreme\n")
            f.flush()
            loaded = HarnessConfig.from_file(f.name)
            assert loaded.agent.reasoning == "medium"

    def test_config_round_trip(self):
        config = HarnessConfig()
        config.agent.agreeability = "honest"
        config.agent.reasoning = "high"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            path = config.save(f.name)
            loaded = HarnessConfig.from_file(path)
            assert loaded.agent.agreeability == "honest"
            assert loaded.agent.reasoning == "high"

    def test_all_agreeability_levels_valid(self):
        for level in AGREEABILITY_LEVELS:
            config = HarnessConfig()
            config.agent.agreeability = level
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                path = config.save(f.name)
                loaded = HarnessConfig.from_file(path)
                assert loaded.agent.agreeability == level

    def test_all_reasoning_levels_valid(self):
        for level in REASONING_LEVELS:
            config = HarnessConfig()
            config.agent.reasoning = level
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                path = config.save(f.name)
                loaded = HarnessConfig.from_file(path)
                assert loaded.agent.reasoning == level


class TestSessionScoring:
    """Test session scoring module."""

    def test_create_score(self, tmp_path):
        score = SessionScore(
            session_id="test-001",
            scores={"task_complete": 4, "accuracy": 5, "autonomy": 3, "communication": 4, "honesty": 5, "speed": 3},
            free_text="Good session",
        )
        assert score.average() == 4.0

    def test_score_save_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sawyer_harness.scoring.SCORES_DIR", tmp_path / "scores")
        score = SessionScore(
            session_id="test-002",
            scores={"task_complete": 5, "accuracy": 4, "autonomy": 5, "communication": 4, "honesty": 3, "speed": 4},
        )
        path = score.save()
        loaded = SessionScore.load("test-002")
        assert loaded is not None
        assert loaded.session_id == "test-002"
        assert loaded.scores["task_complete"] == 5

    def test_compute_trends(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sawyer_harness.scoring.SCORES_DIR", tmp_path / "scores2")
        scores = [
            SessionScore(session_id="s1", scores={"task_complete": 4, "accuracy": 5}),
            SessionScore(session_id="s2", scores={"task_complete": 3, "accuracy": 4}),
        ]
        trends = compute_trends(scores)
        assert trends["task_complete"] == 3.5
        assert trends["accuracy"] == 4.5
        assert trends["_overall"] == 4.0

    def test_scoring_questions_exist(self):
        assert len(SCORING_QUESTIONS) == 6
        assert "honesty" in SCORING_QUESTIONS


class TestLKG:
    """Test last-known-good version tracking."""

    def test_mark_good(self, tmp_path):
        store = LKGStore(path=tmp_path / "lkg.json")
        entry = store.mark_good(commit="abc123", tag="test-tag", note="Working well")
        assert entry.commit == "abc123"
        assert entry.tag == "test-tag"
        assert entry.note == "Working well"

    def test_list_all(self, tmp_path):
        store = LKGStore(path=tmp_path / "lkg.json")
        store.mark_good(commit="aaa", tag="first")
        store.mark_good(commit="bbb", tag="second")
        entries = store.list_all()
        assert len(entries) == 2

    def test_get_latest(self, tmp_path):
        store = LKGStore(path=tmp_path / "lkg.json")
        store.mark_good(commit="aaa", tag="first")
        store.mark_good(commit="bbb", tag="second")
        latest = store.get_latest()
        assert latest.tag == "second"

    def test_persistence(self, tmp_path):
        path = tmp_path / "lkg.json"
        store1 = LKGStore(path=path)
        store1.mark_good(commit="abc", tag="persistent-tag")
        # Create new store instance from same file
        store2 = LKGStore(path=path)
        entries = store2.list_all()
        assert len(entries) == 1
        assert entries[0].tag == "persistent-tag"
```

**Step 2: Run tests**

```bash
cd ~/sawyer-agent && python -m pytest tests/test_new_features.py -v
```

Expected: All tests pass.

**Step 3: Commit**

```bash
git add tests/test_new_features.py
git commit -m "test: add integration tests for scoring, LKG, agreeability, reasoning"
```

---

## Summary

| Feature | Files | What It Does |
|---------|-------|-------------|
| Agreeability control | config.py, agent.py, server.py, index.html | 3-level setting: agreeable/balanced/honest — controls whether agent tells user what they want to hear or honest truth |
| Reasoning depth | config.py, agent.py, server.py, index.html | 4-level setting: low/medium/medium_high/high — controls how deeply the model reasons |
| Session scoring | scoring.py, server.py, index.html | 6-question rating (task_complete, accuracy, autonomy, communication, honesty, speed) + free text, stored off-path |
| LKG tracking | lkg.py, server.py, index.html | Mark git commits as "good", list history, one-click revert to last known good |