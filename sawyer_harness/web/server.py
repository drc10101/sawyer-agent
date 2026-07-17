"""
FastAPI server for Sawyer Harness web UI.

REST API endpoints for:
- Chat (send message, stream response)
- Models (list, select, health check)
- Skills (CRUD, patch)
- Memory (list, add, delete)
- Sessions (notes, history)
- Goals (create, decompose, track)
- Tools (list, enable/disable, audit log)
- Config (get, update)

WebSocket endpoint for streaming agent responses.
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..agent import Agent
from ..config import HarnessConfig, VERBOSITY_LEVELS, AGREEABILITY_LEVELS, REASONING_LEVELS, COMPACTION_LEVELS, PERMISSION_MODES
from .. import __version__
from ..llm import LLMClient
from ..memory import MemoryStore
from ..routing import ModelRouter, ProviderEndpoint, ProviderHealth, TaskType
from ..scheduler import CronJob, CronScheduler, ScheduleType
from ..skills import Skill, SkillStore
from ..scoring import SessionScore, SCORING_QUESTIONS, compute_trends
from ..lkg import LKGStore
from ..tools import create_default_registry
from ..compression import ContextCompressor
from ..session_engine import SessionEngine
from ..context_manager import ContextManager, CompactionPolicy
from ..project import Project, ProjectManager
from ..skill_creator import SkillCreator, SkillCreationSession, SessionPhase
from ..key_storage import KeyStorage
from ..rules import RulesStore, RulePriority, RuleScope
from ..agent_creator import AgentCreator
from ..agent_discovery import AgentDiscovery
from ..orchestrator import OrchestratorEngine, TaskStatus, TaskPriority, AgentBriefing
from ..suggestions import SuggestionStore
from ..paths import UserData

logger = logging.getLogger("sawyer-harness.web")

STATIC_DIR = Path(__file__).parent / "static"


# ============================================================
# Pydantic models for API
# ============================================================

class ChatMessage(BaseModel):
    message: str
    session_id: str = ""

class SkillCreate(BaseModel):
    name: str
    content: str
    category: str = "general"
    description: str = ""
    triggers: list[str] = []

class SkillPatch(BaseModel):
    old_content: str
    new_content: str

class MemoryEntry(BaseModel):
    key: str
    content: str
    category: str = "general"

class GoalCreate(BaseModel):
    goal: str
    context: str = ""

class GoalLoopStart(BaseModel):
    """Start a goal-seeking loop with a predetermined stopping point."""
    goal_id: str
    max_iterations: int = 10       # Hard ceiling: stop after this many agent turns
    stop_on_complete: bool = True  # Stop when all subtasks are done
    auto_advance: bool = True     # Automatically move to next subtask after completion

class ConfigUpdate(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""

class AgentConfigUpdate(BaseModel):
    max_tool_rounds: int | None = None
    verbosity: str | None = None      # concise | normal | thorough
    stream_tool_output: bool | None = None
    mode: str | None = None           # direct | orchestrator | auto
    model: str | None = None          # LLM model name (e.g. "glm-5.1:cloud")
    provider: str | None = None       # LLM provider (e.g. "ollama")
    base_url: str | None = None       # LLM base URL
    agreeability: str | None = None   # agreeable | balanced | honest
    reasoning: str | None = None      # low | medium | medium_high | high
    compaction_policy: str | None = None  # conservative | balanced | aggressive
    permission_mode: str | None = None    # readonly | readwrite | all

class ToolToggle(BaseModel):
    tool_name: str
    enabled: bool

class SessionScoreSubmit(BaseModel):
    session_id: str
    scores: dict[str, int]      # question_key -> rating (1-5)
    free_text: str = ""

class LKGMarkRequest(BaseModel):
    commit: str = ""     # empty = current HEAD
    tag: str = ""
    note: str = ""
    session_score_id: str = ""

class LKGRevertRequest(BaseModel):
    tag: str = ""        # empty = revert to latest LKG


# ============================================================
# App factory
# ============================================================

def create_app(config: HarnessConfig | None = None) -> FastAPI:
    """Create the FastAPI application with all routes."""
    if config is None:
        config = HarnessConfig()

    app = FastAPI(
        title="Sawyer Agent",
        description="Secure, model-agnostic, self-hosted AI agent framework",
        version=__version__,
    )

    # CORS for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared state
    state = _AppState(config)
    app.state.sawyer = state

    # Register routes
    _register_routes(app, state)

    return app


class _AppState:
    """Shared application state."""

    def __init__(self, config: HarnessConfig):
        self.config = config
        memory_path = config.memory.path or str(UserData.memory_db)
        self.memory = MemoryStore(memory_path)
        self.skills = SkillStore(UserData.skills_dir)
        self.tools = create_default_registry(
            allowed_tools=config.security.allowed_tools or None,
            denied_paths=config.security.denied_paths,
            permission_mode=config.security.permission_mode,
        )
        # Load user tools from ~/.sawyer-harness/tools/ (survives upgrades)
        from ..user_tools import load_user_tools
        user_tool_names = load_user_tools(self.tools)
        if user_tool_names:
            logger.info(f"Loaded {len(user_tool_names)} user tools: {', '.join(user_tool_names)}")
        # Inject shared context so tool handlers can access memory and skills
        self.tools.set_context(memory_store=self.memory, skill_store=self.skills)
        self.router = ModelRouter([
            ProviderEndpoint(
                name="sawyer",
                provider_type="sawyer",
                base_url=config.llm.base_url or "https://api.sawyernetwork.ai/v1",
                api_key=config.llm.api_key,
                model=config.llm.model,
                priority=0,
            ),
            ProviderEndpoint(
                name="openai",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
                priority=1,
            ),
            ProviderEndpoint(
                name="anthropic",
                provider_type="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-sonnet-4-20250514",
                priority=2,
            ),
        ])
        self.scheduler = CronScheduler(config)
        self.sessions: dict[str, Agent] = {}
        self.session_notes: dict[str, dict] = {}
        self.session_engines: dict[str, SessionEngine] = {}
        self.goals: dict[str, dict] = {}
        self.goal_loops: dict[str, dict] = {}  # goal_id -> loop state
        self.compressor = ContextCompressor(
            max_tokens=config.llm.context_length or ContextManager(model_name=config.llm.model or "gpt-4o").window_size,
            model=config.llm.model or "gpt-4o",
        )
        self.context_manager = ContextManager(
            model_name=config.llm.model or "gpt-4o",
            context_length_override=config.llm.context_length,
            compaction_policy=CompactionPolicy.from_dict({
                "policy": config.compaction.policy,
                "threshold": config.compaction.threshold,
                "recency_pct": config.compaction.recency_pct,
            }),
        )
        self.project_manager = ProjectManager()
        self.skill_creator = SkillCreator(skill_store=self.skills)
        self.key_storage = KeyStorage()
        self.rules_store = RulesStore()
        self.agent_creator = AgentCreator()
        self.orchestrator = OrchestratorEngine()
        self.lkg_store = LKGStore()
        self.current_project: Project | None = None
        self.upload_dir = UserData.uploads_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_session(self, session_id: str = "") -> tuple[str, Agent]:
        """Get existing session or create a new one."""
        if session_id and session_id in self.sessions:
            return session_id, self.sessions[session_id]

        new_id = session_id or str(uuid.uuid4())[:8]
        llm = LLMClient(self.config.llm, tool_registry=self.tools)
        agent = Agent(
            config=self.config,
            llm=llm,
            memory=self.memory,
            tools=self.tools,
            system_prompt="You are Sawyer Agent, a secure AI agent. Be helpful, direct, and thorough.",
            skills=self.skills,
            rules_store=self.rules_store,
            context_window=self.context_manager.window_size,
            compaction_policy=CompactionPolicy.from_dict({
                "policy": self.config.compaction.policy,
                "threshold": self.config.compaction.threshold,
                "recency_pct": self.config.compaction.recency_pct,
            }),
        )
        self.sessions[new_id] = agent
        return new_id, agent


# ============================================================
# Route registration
# ============================================================

def _register_routes(app: FastAPI, state: _AppState):

    # ----------------------------------------------------------
    # Chat
    # ----------------------------------------------------------

    @app.post("/api/chat")
    async def chat(msg: ChatMessage):
        """Send a message to the agent and get a response."""
        session_id, agent = state.get_or_create_session(msg.session_id)
        try:
            response_parts = []
            async for chunk in agent.run(msg.message):
                response_parts.append(chunk)
            response = "".join(response_parts)

            # Auto-compress if context exceeds budget
            system_prompt = agent._build_system_prompt()
            memory_text = "; ".join(e["content"] for e in agent.memory.all_entries()[:5])
            message_tokens = state.context_manager.count_message_tokens(agent.conversation)
            system_tokens = state.context_manager.count_tokens(system_prompt)
            mem_tokens = state.context_manager.count_tokens(memory_text)
            if state.context_manager.needs_compression(
                system_prompt_tokens=system_tokens,
                memory_tokens=mem_tokens,
                current_messages_tokens=message_tokens,
            ):
                logger.info(f"Auto-compressing session {session_id}")
                compressed, result = state.compressor.compress(
                    messages=agent.conversation,
                    system_prompt=system_prompt,
                    memory_text=memory_text,
                )
                agent.conversation = compressed
                logger.info(
                    f"Compressed {result.original_tokens} -> {result.compressed_tokens} tokens "
                    f"({result.messages_kept} kept, {result.messages_summarized} summarized, "
                    f"{result.messages_dropped} dropped)"
                )

            # Clean up session handoff notes after they've been injected
            # into the response (they're one-shot — only needed for the first
            # message of a new session that's continuing from a full one)
            handoff_keys = [
                e["key"] for e in agent.memory.all_entries()
                if e.get("category") == "session-handoff"
            ]
            for key in handoff_keys:
                agent.memory.delete(key)
                logger.info(f"Cleaned up handoff note: {key}")

            return {
                "session_id": session_id,
                "response": response,
                "tool_calls": len(agent.conversation),
            }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/ws/chat")
    async def websocket_chat(websocket: WebSocket):
        """WebSocket endpoint for streaming agent responses.

        Emits typed events so the frontend can render them appropriately:
        - "thinking": reasoning/thinking content (summarized, not raw)
        - "status": agent status like "Reading file...", "Patching code..."
        - "tool_call": tool being called (name + args summary)
        - "tool_result": tool execution result
        - "text": normal assistant text response
        - "seizure": loop detected warning
        - "done": response complete
        - "error": something went wrong
        """
        await websocket.accept()
        session_id = ""

        try:
            while True:
                data = await websocket.receive_text()
                payload = json.loads(data)
                user_message = payload.get("message", "")
                session_id = payload.get("session_id", session_id)

                # Allow client to cancel mid-stream
                if payload.get("action") == "stop":
                    logger.info(f"Client requested stop for session {session_id}")
                    break

                _, agent = state.get_or_create_session(session_id)

                # Track tool calls for status summaries
                current_tool = ""

                async for chunk in agent.run(user_message):
                    # Classify the chunk by its content markers
                    if chunk.startswith("<thinking>"):
                        # Thinking content -- summarize, don't stream raw reasoning
                        content = chunk.replace("<thinking>", "").replace("</thinking>", "").strip()
                        if content:
                            # Extract first sentence as a status summary
                            summary = content.split(".")[0]
                            if len(summary) > 120:
                                summary = summary[:117] + "..."
                            await websocket.send_json({
                                "type": "thinking",
                                "summary": summary,
                                "session_id": session_id,
                            })
                    elif chunk.startswith("[") and "]" in chunk[:30]:
                        # Tool output: [tool_name] result...
                        # Split into tool_call and tool_result
                        bracket_end = chunk.index("]")
                        tool_name = chunk[1:bracket_end]
                        result = chunk[bracket_end + 1:].strip()

                        # Send tool_call event
                        await websocket.send_json({
                            "type": "tool_call",
                            "name": tool_name,
                            "session_id": session_id,
                        })

                        # Send tool_result event (truncated for display)
                        display_result = result[:2000] if len(result) > 2000 else result
                        await websocket.send_json({
                            "type": "tool_result",
                            "name": tool_name,
                            "content": display_result,
                            "session_id": session_id,
                        })
                    elif chunk.startswith("---\n**Loop detected:**") or chunk.startswith("\n\n---\n**Loop detected:**"):
                        # Seizure warning
                        await websocket.send_json({
                            "type": "seizure",
                            "content": chunk.replace("---\n", "").replace("\n\n---\n", "").strip(),
                            "session_id": session_id,
                        })
                    elif chunk.startswith("*Session context is full") or chunk.startswith("\n\n---\n*Session"):
                        # Handoff signal
                        await websocket.send_json({
                            "type": "handoff",
                            "content": chunk.strip(),
                            "session_id": session_id,
                        })
                    elif chunk.startswith("[") and "ERROR" in chunk[:50]:
                        # Tool error
                        bracket_end = chunk.index("]")
                        tool_name = chunk[1:bracket_end]
                        error_msg = chunk[bracket_end + 1:].strip()
                        await websocket.send_json({
                            "type": "tool_error",
                            "name": tool_name,
                            "content": error_msg,
                            "session_id": session_id,
                        })
                    else:
                        # Normal text content
                        await websocket.send_json({
                            "type": "text",
                            "content": chunk,
                            "session_id": session_id,
                        })

                await websocket.send_json({
                    "type": "done",
                    "session_id": session_id,
                })
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            try:
                await websocket.send_json({
                    "type": "error",
                    "content": str(e),
                    "session_id": session_id,
                })
            except Exception:
                pass  # Connection already closed

    @app.post("/api/session/{session_id}/clear")
    async def clear_session(session_id: str):
        """Clear conversation history for a session."""
        if session_id in state.sessions:
            state.sessions[session_id].reset_conversation()
            return {"status": "cleared"}
        raise HTTPException(status_code=404, detail="Session not found")

    @app.post("/api/kill-foreground")
    async def kill_foreground():
        """Kill the current foreground subprocess (shell, code_execute, etc.).

        Use when the user wants to cancel a running tool execution.
        Returns whether a process was killed.
        """
        killed = state.tools.kill_foreground()
        return {"killed": killed, "status": "killed" if killed else "no_process"}

    @app.post("/api/session/{session_id}/notes")
    async def generate_session_notes(session_id: str):
        """Auto-generate session notes at end of session."""
        if session_id not in state.sessions:
            raise HTTPException(status_code=404, detail="Session not found")

        agent = state.sessions[session_id]
        conversation = agent.conversation

        # Build notes from conversation
        notes = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_count": len(conversation),
            "tool_calls": sum(1 for m in conversation if m.role == "tool"),
            "topics": [],
            "key_decisions": [],
            "summary": "",
        }

        # Extract topics from user messages
        for msg in conversation:
            if msg.role == "user" and msg.content:
                notes["topics"].append(msg.content[:200])

        # Use memory for key facts
        if agent.memory.total_chars() > 0:
            notes["memory_entries"] = len(agent.memory.all_entries())

        # Use tool audit log for decisions
        audit = agent.tools.audit_trail(limit=10)
        if audit:
            notes["tool_usage"] = len(audit)

        # Store notes
        state.session_notes[session_id] = notes

        # Also save to skills as a session-note skill
        note_content = f"# Session {session_id}\n\n"
        note_content += f"Date: {notes['timestamp']}\n"
        note_content += f"Messages: {notes['message_count']}\n"
        note_content += f"Tool calls: {notes['tool_calls']}\n\n"
        if notes["topics"]:
            note_content += "## Topics\n"
            for t in notes["topics"][:5]:
                note_content += f"- {t}\n"

        state.skills.add_or_update(Skill(
            name=f"session-{session_id}",
            category="session-notes",
            description=f"Session notes for {session_id}",
            triggers=["session", "notes", session_id],
            content=note_content,
        ))

        return notes

    # ----------------------------------------------------------
    # Models / Routing
    # ----------------------------------------------------------

    @app.get("/api/models")
    async def list_models():
        """List all configured providers with health status."""
        stats = state.router.get_routing_stats()
        return {"providers": stats}

    @app.post("/api/models/{provider_name}/health")
    async def check_provider_health(provider_name: str):
        """Check health of a specific provider."""
        health = await state.router.health_check(provider_name)
        return {"provider": provider_name, "health": health.value}

    @app.get("/api/models/routing")
    async def get_routing_config():
        """Get current routing configuration and preferences."""
        prefs = {}
        for task_type in TaskType:
            prefs[task_type.value] = state.router._task_preferences.get(task_type, [])
        return {"preferences": prefs, "providers": state.router.get_routing_stats()}

    @app.post("/api/models/routing")
    async def set_routing_preference(task_type: str, providers: list[str]):
        """Set provider preference order for a task type."""
        try:
            tt = TaskType(task_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid task type: {task_type}")

        state.router.set_task_preference(tt, providers)
        return {"status": "updated", "task_type": task_type, "providers": providers}

    @app.post("/api/config")
    async def update_config(update: ConfigUpdate):
        """Update LLM configuration at runtime."""
        if update.provider:
            state.config.llm.provider = update.provider
        if update.model:
            state.config.llm.model = update.model
        if update.api_key:
            state.config.llm.api_key = update.api_key
        if update.base_url:
            state.config.llm.base_url = update.base_url
        return {"status": "updated", "config": {
            "provider": state.config.llm.provider,
            "model": state.config.llm.model,
            "base_url": state.config.llm.base_url,
        }}

    @app.get("/api/agent-config")
    async def get_agent_config():
        """Get current agent configuration including model and mode."""
        return {
            "max_tool_rounds": state.config.agent.max_tool_rounds,
            "verbosity": state.config.agent.verbosity,
            "stream_tool_output": state.config.agent.stream_tool_output,
            "mode": state.config.agent.mode,
            "model": state.config.llm.model,
            "provider": state.config.llm.provider,
            "base_url": state.config.llm.base_url,
            "agreeability": state.config.agent.agreeability,
            "reasoning": state.config.agent.reasoning,
            "compaction_policy": {
                "policy": state.config.compaction.policy,
                "threshold": state.config.compaction.threshold,
                "recency_pct": state.config.compaction.recency_pct,
                "presets": list(CompactionPolicy.PRESETS.keys()),
            },
            "permission_mode": state.config.security.permission_mode,
        }

    @app.post("/api/agent-config")
    async def update_agent_config(update: AgentConfigUpdate):
        """Update agent behavior configuration at runtime."""
        if update.max_tool_rounds is not None:
            if update.max_tool_rounds < 1 or update.max_tool_rounds > 50:
                raise HTTPException(status_code=400, detail="max_tool_rounds must be between 1 and 50")
            state.config.agent.max_tool_rounds = update.max_tool_rounds
        if update.verbosity is not None:
            if update.verbosity not in VERBOSITY_LEVELS:
                raise HTTPException(status_code=400, detail=f"verbosity must be one of: {', '.join(VERBOSITY_LEVELS)}")
            state.config.agent.verbosity = update.verbosity
        if update.stream_tool_output is not None:
            state.config.agent.stream_tool_output = update.stream_tool_output
        if update.mode is not None:
            if update.mode not in ("direct", "orchestrator", "auto"):
                raise HTTPException(status_code=400, detail="mode must be one of: direct, orchestrator, auto")
            state.config.agent.mode = update.mode
        if update.model is not None:
            state.config.llm.model = update.model
            # Update context manager for the new model
            state.context_manager = ContextManager(
                model_name=update.model,
                context_length_override=state.config.llm.context_length,
            )
            state.compressor = ContextCompressor(
                max_tokens=state.config.llm.context_length or state.context_manager.window_size,
                model=update.model,
            )
        if update.provider is not None:
            state.config.llm.provider = update.provider
        if update.base_url is not None:
            state.config.llm.base_url = update.base_url
        if update.agreeability is not None:
            if update.agreeability not in ("agreeable", "balanced", "honest"):
                raise HTTPException(status_code=400, detail="agreeability must be one of: agreeable, balanced, honest")
            state.config.agent.agreeability = update.agreeability
        if update.reasoning is not None:
            if update.reasoning not in ("low", "medium", "medium_high", "high"):
                raise HTTPException(status_code=400, detail="reasoning must be one of: low, medium, medium_high, high")
            state.config.agent.reasoning = update.reasoning
        if update.compaction_policy is not None:
            if update.compaction_policy not in COMPACTION_LEVELS:
                raise HTTPException(status_code=400, detail=f"compaction_policy must be one of: {', '.join(COMPACTION_LEVELS)}")
            state.config.compaction.policy = update.compaction_policy
            # Rebuild context manager with new policy
            state.context_manager = ContextManager(
                model_name=state.config.llm.model or "gpt-4o",
                context_length_override=state.config.llm.context_length,
                compaction_policy=CompactionPolicy.from_dict({
                    "policy": state.config.compaction.policy,
                    "threshold": state.config.compaction.threshold,
                    "recency_pct": state.config.compaction.recency_pct,
                }),
            )
        if update.permission_mode is not None:
            if update.permission_mode not in PERMISSION_MODES:
                raise HTTPException(status_code=400, detail=f"permission_mode must be one of: {', '.join(PERMISSION_MODES)}")
            state.config.security.permission_mode = update.permission_mode
            # Rebuild tool registry with new permission mode
            state.tools = create_default_registry(
                allowed_tools=state.config.security.allowed_tools or None,
                denied_paths=state.config.security.denied_paths,
                permission_mode=state.config.security.permission_mode,
            )
            # Reload user tools into new registry
            from ..user_tools import load_user_tools
            load_user_tools(state.tools)
            # Re-set context on new registry
            state.tools.set_context(memory_store=state.memory, skill_store=state.skills)
        return {"status": "updated", "agent_config": {
            "max_tool_rounds": state.config.agent.max_tool_rounds,
            "verbosity": state.config.agent.verbosity,
            "stream_tool_output": state.config.agent.stream_tool_output,
            "mode": state.config.agent.mode,
            "model": state.config.llm.model,
            "provider": state.config.llm.provider,
            "base_url": state.config.llm.base_url,
            "agreeability": state.config.agent.agreeability,
            "reasoning": state.config.agent.reasoning,
            "compaction_policy": {
                "policy": state.config.compaction.policy,
                "threshold": state.config.compaction.threshold,
                "recency_pct": state.config.compaction.recency_pct,
                "presets": list(CompactionPolicy.PRESETS.keys()),
            },
            "permission_mode": state.config.security.permission_mode,
        }}

    # ----------------------------------------------------------
    # Session Scoring
    # ----------------------------------------------------------

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

    # ----------------------------------------------------------
    # Last Known Good (LKG)
    # ----------------------------------------------------------

    @app.get("/api/lkg")
    async def list_lkg(limit: int = 20):
        """List all last-known-good versions."""
        entries = state.lkg_store.list_all(limit=limit)
        latest = state.lkg_store.get_latest()
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
                "commit": latest.commit,
                "tag": latest.tag,
                "timestamp": latest.timestamp,
                "note": latest.note,
            } if latest else None,
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

    # ----------------------------------------------------------
    # Skills
    # ----------------------------------------------------------

    @app.get("/api/skills")
    async def list_skills():
        """List all loaded skills."""
        return {"skills": state.skills.list_skills()}

    @app.get("/api/skills/{name}")
    async def get_skill(name: str):
        """Get a specific skill."""
        skill = state.skills.get(name)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {
            "name": skill.name,
            "version": skill.version,
            "category": skill.category,
            "description": skill.description,
            "triggers": skill.triggers,
            "content": skill.content,
            "chars": skill.total_chars,
        }

    @app.post("/api/skills")
    async def create_skill(skill_data: SkillCreate):
        """Create a new skill."""
        skill = Skill(
            name=skill_data.name,
            category=skill_data.category,
            description=skill_data.description,
            triggers=skill_data.triggers,
            content=skill_data.content,
        )
        result = state.skills.add_or_update(skill)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create skill")
        return {"status": "created", "name": skill_data.name}

    @app.patch("/api/skills/{name}")
    async def patch_skill(name: str, patch_data: SkillPatch):
        """Patch (find-and-replace) a skill's content."""
        result = state.skills.patch(name, patch_data.old_content, patch_data.new_content)
        if not result:
            raise HTTPException(status_code=400, detail="Patch failed -- old content not found or skill doesn't exist")
        return {"status": "patched", "name": name}

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str):
        """Delete a skill."""
        result = state.skills.delete(name)
        if not result:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"status": "deleted", "name": name}

    @app.post("/api/skills/reload")
    async def reload_skills():
        """Reload all skills from disk."""
        state.skills.reload()
        return {"status": "reloaded", "count": len(state.skills.list_skills())}

    # ----------------------------------------------------------
    # Agent Discovery (per-project .sawyer/agents/*.md)
    # ----------------------------------------------------------

    @app.get("/api/discovered-agents")
    async def list_discovered_agents(project_root: str = ""):
        """List all discovered agent definitions.

        Project-level agents (in .sawyer/agents/) shadow global ones
        with the same name. Provide project_root to include project-level agents.
        """
        discovery = AgentDiscovery(project_root=project_root or None)
        agents = discovery.discover()
        return {
            "agents": {name: agent.to_dict() for name, agent in agents.items()},
            "count": len(agents),
        }

    @app.get("/api/discovered-agents/{name}")
    async def get_discovered_agent(name: str, project_root: str = ""):
        """Get a specific discovered agent definition by name."""
        discovery = AgentDiscovery(project_root=project_root or None)
        agent = discovery.get(name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        return agent.to_dict()

    @app.post("/api/discovered-agents/reload")
    async def reload_discovered_agents(project_root: str = ""):
        """Force re-read agent definitions from disk."""
        discovery = AgentDiscovery(project_root=project_root or None)
        discovery.invalidate_cache()
        agents = discovery.discover(force_reload=True)
        return {"status": "reloaded", "count": len(agents)}

    # ----------------------------------------------------------
    # Skill Creator (interactive vibe-coding sessions)
    # ----------------------------------------------------------

    @app.post("/api/skill-creator/sessions")
    async def create_skill_session():
        """Start a new skill creation session."""
        session = state.skill_creator.create_session()
        return {
            "session_id": session.id,
            "phase": session.phase.value,
            "status": session.status.value,
            "spec": {
                "name": session.spec.name,
                "category": session.spec.category,
                "description": session.spec.description,
                "triggers": session.spec.triggers,
                "procedure": session.spec.procedure,
                "pitfalls": session.spec.pitfalls,
                "constraints": session.spec.constraints,
            },
        }

    @app.get("/api/skill-creator/sessions")
    async def list_skill_sessions(status: str = ""):
        """List skill creation sessions."""
        from ..skill_creator import SessionStatus
        filter_status = SessionStatus(status) if status else None
        sessions = state.skill_creator.list_sessions(status=filter_status)
        return {"sessions": [
            {
                "id": s.id,
                "phase": s.phase.value,
                "status": s.status.value,
                "name": s.spec.name,
                "category": s.spec.category,
                "revision_count": s.revision_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in sessions
        ]}

    @app.get("/api/skill-creator/sessions/{session_id}")
    async def get_skill_session(session_id: str):
        """Get details of a skill creation session."""
        session = state.skill_creator.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": session.id,
            "phase": session.phase.value,
            "status": session.status.value,
            "spec": {
                "name": session.spec.name,
                "category": session.spec.category,
                "description": session.spec.description,
                "triggers": session.spec.triggers,
                "procedure": session.spec.procedure,
                "pitfalls": session.spec.pitfalls,
                "constraints": session.spec.constraints,
                "examples": session.spec.examples,
                "notes": session.spec.notes,
            },
            "revision_count": session.revision_count,
            "observation_notes": session.observation_notes,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    @app.post("/api/skill-creator/sessions/{session_id}/observe")
    async def observe_message(session_id: str, message: str = "", role: str = "user"):
        """Observe a message for skill opportunity signals."""
        session = state.skill_creator.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        signals = state.skill_creator.observe_message(session_id, role, message)
        return {"signals": signals, "observation_notes": session.observation_notes}

    @app.post("/api/skill-creator/sessions/{session_id}/theorize")
    async def theorize_skill(session_id: str, task: str = "", context: str = ""):
        """Generate a skill spec draft based on a task description."""
        try:
            spec = state.skill_creator.theorize(session_id, task, context)
            session = state.skill_creator.get_session(session_id)
            return {
                "spec": {
                    "name": spec.name,
                    "category": spec.category,
                    "description": spec.description,
                    "triggers": spec.triggers,
                    "procedure": spec.procedure,
                    "pitfalls": spec.pitfalls,
                    "constraints": spec.constraints,
                    "examples": spec.examples,
                    "notes": spec.notes,
                },
                "phase": session.phase.value,
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/skill-creator/sessions/{session_id}/refine")
    async def refine_skill(session_id: str, changes: dict = {}):
        """Apply user refinements to the skill spec."""
        try:
            spec = state.skill_creator.refine(session_id, changes)
            session = state.skill_creator.get_session(session_id)
            return {
                "spec": {
                    "name": spec.name,
                    "category": spec.category,
                    "description": spec.description,
                    "triggers": spec.triggers,
                    "procedure": spec.procedure,
                    "pitfalls": spec.pitfalls,
                    "constraints": spec.constraints,
                    "examples": spec.examples,
                    "notes": spec.notes,
                },
                "revision_count": session.revision_count,
                "phase": session.phase.value,
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/skill-creator/sessions/{session_id}/approve")
    async def approve_skill(session_id: str):
        """Approve and create the skill from the session spec."""
        try:
            skill = state.skill_creator.approve(session_id)
            return {
                "status": "created",
                "skill": {
                    "name": skill.name,
                    "category": skill.category,
                    "description": skill.description,
                    "triggers": skill.triggers,
                    "version": skill.version,
                },
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/skill-creator/sessions/{session_id}/reject")
    async def reject_skill(session_id: str):
        """Reject and abandon the skill creation session."""
        try:
            state.skill_creator.reject(session_id)
            return {"status": "abandoned", "session_id": session_id}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/skill-creator/suggest")
    async def suggest_skill_creation(messages: list[dict] = []):
        """Analyze messages and suggest whether a skill creation session would be valuable."""
        suggestion = state.skill_creator.suggest_skill_creation(messages)
        if suggestion:
            return {"suggestion": suggestion}
        return {"suggestion": None}

    # ----------------------------------------------------------
    # Memory
    # ----------------------------------------------------------

    @app.get("/api/memory")
    async def list_memory():
        """List all memory entries."""
        return {"entries": state.memory.all_entries(), "total_chars": state.memory.total_chars()}

    @app.post("/api/memory")
    async def add_memory(entry: MemoryEntry):
        """Add a memory entry."""
        result = state.memory.add(entry.key, entry.content, entry.category)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to add memory")
        return {"status": "added", "key": entry.key}

    @app.delete("/api/memory/{key}")
    async def delete_memory(key: str):
        """Delete a memory entry."""
        result = state.memory.delete(key)
        if not result:
            raise HTTPException(status_code=404, detail="Memory key not found")
        return {"status": "deleted", "key": key}

    # ----------------------------------------------------------
    # Goals / Orchestration
    # ----------------------------------------------------------

    @app.post("/api/goals")
    async def create_goal(goal_data: GoalCreate):
        """Create a new goal for the orchestrator to decompose."""
        goal_id = str(uuid.uuid4())[:8]
        goal = {
            "id": goal_id,
            "goal": goal_data.goal,
            "context": goal_data.context,
            "status": "pending",
            "created": datetime.now(timezone.utc).isoformat(),
            "subtasks": [],
            "progress": 0.0,
        }

        # Decompose the goal into subtasks using a structured prompt
        session_id, agent = state.get_or_create_session(f"goal-{goal_id}")
        decompose_prompt = (
            f"Decompose this goal into 3-7 concrete, actionable subtasks.\n\n"
            f"Goal: {goal_data.goal}\n"
            f"Context: {goal_data.context or 'None'}\n\n"
            f"List each subtask as a numbered item. Be specific and actionable."
        )

        try:
            response_parts = []
            async for chunk in agent.run(decompose_prompt):
                response_parts.append(chunk)
            decomposition = "".join(response_parts)

            # Parse subtasks from numbered list
            subtasks = []
            for line in decomposition.split("\n"):
                line = line.strip()
                if line and line[0].isdigit() and "." in line:
                    task_text = line.split(".", 1)[1].strip()
                    if task_text:
                        subtasks.append({
                            "id": f"{goal_id}-{len(subtasks)+1}",
                            "task": task_text,
                            "status": "pending",
                        })

            if not subtasks:
                # Fallback: treat the whole response as a single subtask
                subtasks.append({
                    "id": f"{goal_id}-1",
                    "task": goal_data.goal,
                    "status": "pending",
                })

            goal["subtasks"] = subtasks
            goal["status"] = "decomposed"

        except Exception as e:
            logger.error(f"Goal decomposition error: {e}")
            goal["subtasks"] = [{"id": f"{goal_id}-1", "task": goal_data.goal, "status": "pending"}]
            goal["status"] = "decomposed"

        state.goals[goal_id] = goal
        return goal

    @app.get("/api/goals")
    async def list_goals():
        """List all goals."""
        return {"goals": list(state.goals.values())}

    @app.get("/api/goals/{goal_id}")
    async def get_goal(goal_id: str):
        """Get a specific goal."""
        if goal_id not in state.goals:
            raise HTTPException(status_code=404, detail="Goal not found")
        return state.goals[goal_id]

    @app.post("/api/goals/{goal_id}/subtask/{subtask_id}/complete")
    async def complete_subtask(goal_id: str, subtask_id: str):
        """Mark a subtask as complete."""
        if goal_id not in state.goals:
            raise HTTPException(status_code=404, detail="Goal not found")

        goal = state.goals[goal_id]
        for st in goal["subtasks"]:
            if st["id"] == subtask_id:
                st["status"] = "complete"
                break

        # Update progress
        total = len(goal["subtasks"])
        complete = sum(1 for st in goal["subtasks"] if st["status"] == "complete")
        goal["progress"] = complete / total if total > 0 else 0.0

        if complete == total:
            goal["status"] = "complete"

        return goal

    @app.delete("/api/goals/{goal_id}")
    async def delete_goal(goal_id: str):
        """Delete a goal and its loop state."""
        if goal_id not in state.goals:
            raise HTTPException(status_code=404, detail="Goal not found")
        # Stop any running loop
        if goal_id in state.goal_loops:
            state.goal_loops[goal_id]["status"] = "stopped"
            del state.goal_loops[goal_id]
        del state.goals[goal_id]
        return {"status": "deleted"}

    # ----------------------------------------------------------
    # Goal-Seeking Loops
    # ----------------------------------------------------------

    @app.post("/api/goals/{goal_id}/start-loop")
    async def start_goal_loop(goal_id: str, loop_config: GoalLoopStart):
        """Start a goal-seeking loop: the orchestrator works through subtasks with a hard stop."""
        if goal_id not in state.goals:
            raise HTTPException(status_code=404, detail="Goal not found")

        goal = state.goals[goal_id]
        if goal["status"] not in ("decomposed", "pending"):
            raise HTTPException(status_code=400, detail=f"Goal status is '{goal['status']}', cannot start loop. Only decomposed or pending goals can loop.")

        # Find next pending subtask
        pending = [st for st in goal["subtasks"] if st["status"] == "pending"]
        if not pending:
            goal["status"] = "complete"
            goal["progress"] = 1.0
            return {"status": "complete", "message": "All subtasks already complete.", "goal": goal}

        # Create loop state
        loop_state = {
            "goal_id": goal_id,
            "status": "running",
            "iteration": 0,
            "max_iterations": loop_config.max_iterations,
            "stop_on_complete": loop_config.stop_on_complete,
            "auto_advance": loop_config.auto_advance,
            "current_subtask": pending[0]["id"],
            "log": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stopped_at": None,
        }
        state.goal_loops[goal_id] = loop_state

        # Mark goal as running
        goal["status"] = "looping"

        # Run the first subtask iteration
        subtask = pending[0]
        loop_state["iteration"] = 1
        loop_state["log"].append({
            "iteration": 1,
            "subtask_id": subtask["id"],
            "task": subtask["task"],
            "status": "started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Kick off the loop task in the background
        asyncio.create_task(_run_goal_loop(goal_id, loop_config))

        return {
            "status": "running",
            "goal_id": goal_id,
            "iteration": 1,
            "max_iterations": loop_config.max_iterations,
            "current_subtask": subtask["id"],
            "current_task": subtask["task"],
        }

    @app.post("/api/goals/{goal_id}/stop-loop")
    async def stop_goal_loop(goal_id: str):
        """Stop a running goal-seeking loop."""
        if goal_id not in state.goal_loops:
            raise HTTPException(status_code=404, detail="No loop running for this goal")

        loop = state.goal_loops[goal_id]
        loop["status"] = "stopped"
        loop["stopped_at"] = datetime.now(timezone.utc).isoformat()

        if goal_id in state.goals:
            state.goals[goal_id]["status"] = "paused"

            # Diagnose why the loop was stopped
            goal = state.goals[goal_id]
            remaining = [st for st in goal["subtasks"] if st["status"] == "pending"]
            completed = [st for st in goal["subtasks"] if st["status"] == "complete"]
            diag = (
                f"Loop stopped manually after {loop['iteration']} iteration(s). "
                f"{len(completed)} of {len(goal['subtasks'])} subtask(s) completed. "
                f"{len(remaining)} subtask(s) remain: "
                + ", ".join(st["task"] for st in remaining[:5])
            )
            loop["diagnosis"] = diag

        return {"status": "stopped", "goal_id": goal_id, "iterations_completed": loop["iteration"]}

    @app.get("/api/goals/{goal_id}/loop-status")
    async def get_loop_status(goal_id: str):
        """Get the status of a goal-seeking loop."""
        if goal_id not in state.goal_loops:
            # No loop exists -- return idle status
            goal = state.goals.get(goal_id)
            if not goal:
                raise HTTPException(status_code=404, detail="Goal not found")
            return {
                "status": "idle",
                "goal_id": goal_id,
                "goal_status": goal["status"],
                "progress": goal["progress"],
            }

        loop = state.goal_loops[goal_id]
        goal = state.goals.get(goal_id, {})
        return {
            "status": loop["status"],
            "goal_id": goal_id,
            "iteration": loop["iteration"],
            "max_iterations": loop["max_iterations"],
            "current_subtask": loop.get("current_subtask"),
            "log": loop["log"][-10:],  # Last 10 log entries
            "goal_progress": goal.get("progress", 0),
            "goal_status": goal.get("status"),
            "diagnosis": loop.get("diagnosis"),
            "started_at": loop["started_at"],
            "stopped_at": loop.get("stopped_at"),
        }

    async def _run_goal_loop(goal_id: str, config: GoalLoopStart):
        """Background task: iterate through subtasks with a hard stop."""
        loop = state.goal_loops.get(goal_id)
        if not loop:
            return

        goal = state.goals.get(goal_id)
        if not goal:
            loop["status"] = "error"
            loop["log"].append({"error": "Goal removed during loop"})
            return

        while loop["status"] == "running" and loop["iteration"] < loop["max_iterations"]:
            # Find next pending subtask
            pending = [st for st in goal["subtasks"] if st["status"] == "pending"]
            if not pending:
                # All subtasks complete
                goal["status"] = "complete"
                goal["progress"] = 1.0
                loop["status"] = "complete"
                loop["log"].append({
                    "iteration": loop["iteration"],
                    "status": "all_subtasks_complete",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return

            subtask = pending[0]
            loop["current_subtask"] = subtask["id"]
            loop["iteration"] += 1

            loop["log"].append({
                "iteration": loop["iteration"],
                "subtask_id": subtask["id"],
                "task": subtask["task"],
                "status": "working",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Send the subtask to the orchestrator
            try:
                session_id, agent = state.get_or_create_session(f"goal-loop-{goal_id}")
                prompt = (
                    f"You are working on subtask {subtask['id']} of goal: {goal['goal']}\n"
                    f"Context: {goal.get('context', 'None')}\n"
                    f"Subtask: {subtask['task']}\n\n"
                    f"Complete this subtask. Use any tools available. "
                    f"When done, briefly state what you accomplished."
                )
                response_parts = []
                async for chunk in agent.run(prompt):
                    response_parts.append(chunk)
                response = "".join(response_parts)

                # Mark subtask complete
                subtask["status"] = "complete"
                total = len(goal["subtasks"])
                complete = sum(1 for st in goal["subtasks"] if st["status"] == "complete")
                goal["progress"] = complete / total if total > 0 else 0.0

                loop["log"][-1]["status"] = "complete"
                loop["log"][-1]["response_preview"] = response[:200]

            except Exception as e:
                logger.error(f"Goal loop iteration {loop['iteration']} error: {e}")
                loop["log"][-1]["status"] = "error"
                loop["log"][-1]["error"] = str(e)

            # Check if we should stop
            if config.stop_on_complete:
                remaining = [st for st in goal["subtasks"] if st["status"] == "pending"]
                if not remaining:
                    goal["status"] = "complete"
                    goal["progress"] = 1.0
                    loop["status"] = "complete"
                    return

            # Brief pause between iterations
            await asyncio.sleep(0.5)

        # Hit max iterations -- diagnose why the goal wasn't met
        if loop["status"] == "running":
            loop["status"] = "max_iterations_reached"
            loop["log"].append({
                "iteration": loop["iteration"],
                "status": "max_iterations_reached",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            goal["status"] = "paused"

            # Ask the agent to diagnose what went wrong
            remaining = [st for st in goal["subtasks"] if st["status"] == "pending"]
            failed = [st for st in goal["subtasks"] if st.get("status") not in ("complete", "pending")]
            completed = [st for st in goal["subtasks"] if st["status"] == "complete"]
            diag_prompt = (
                f"The goal-seeking loop for \"{goal['goal']}\" hit its maximum iteration limit ({loop['max_iterations']}) "
                f"before completing all subtasks.\n\n"
                f"Completed subtasks ({len(completed)}):\n"
                + "\n".join(f"  - {st['task']}" for st in completed) + "\n"
                f"\nRemaining subtasks ({len(remaining)}):\n"
                + "\n".join(f"  - {st['task']}" for st in remaining) + "\n"
                f"\nFailed subtasks ({len(failed)}):\n"
                + "\n".join(f"  - {st['task']}" for st in failed) + "\n"
                f"\nIn 2-3 sentences, explain why the goal was not fully achieved and what the user could do differently. "
                f"Be specific about what's missing."
            )
            try:
                session_id, agent = state.get_or_create_session(f"goal-loop-{goal_id}")
                diag_parts = []
                async for chunk in agent.run(diag_prompt):
                    diag_parts.append(chunk)
                loop["diagnosis"] = "".join(diag_parts)
                loop["log"][-1]["diagnosis"] = loop["diagnosis"]
            except Exception as e:
                logger.error(f"Goal loop diagnosis error: {e}")
                loop["diagnosis"] = f"Loop hit {loop['max_iterations']} iterations. {len(remaining)} subtask(s) remain unfinished."

    # ----------------------------------------------------------
    # Tools / Audit
    # ----------------------------------------------------------

    @app.get("/api/tools")
    async def list_tools():
        """List available tools and their schemas."""
        schemas = state.tools.list_tools()
        audit = state.tools.audit_trail(limit=50)
        return {
            "tools": schemas,
            "allowlist": state.tools._allowed_tools,
            "denied_paths": state.tools._denied_paths,
            "audit_log": audit,
        }

    @app.post("/api/tools/reload")
    async def reload_user_tools():
        """Reload user tools from ~/.sawyer-harness/tools/ without restarting."""
        from ..user_tools import load_user_tools
        loaded = load_user_tools(state.tools)
        return {
            "status": "reloaded",
            "user_tools": loaded,
            "total_tools": len(state.tools.list_tools()),
        }

    # --- Tool Creator (OCUE: describe, generate, preview, approve) ---

    @app.post("/api/tools/create")
    async def create_tool(request: Request):
        """Generate a tool draft from a description. Returns the draft for preview."""
        from ..tool_creator import ToolCreator
        body = await request.json()
        description = body.get("description", "").strip()
        tool_name = body.get("name", "").strip() or None

        if not description:
            raise HTTPException(status_code=400, detail="Description is required")

        creator = ToolCreator(registry=state.tools, llm_client=state.llm if hasattr(state, 'llm') else None)
        draft = await creator.generate(description=description, tool_name=tool_name)

        return {
            "draft_id": draft.id,
            "name": draft.name,
            "description": draft.description,
            "filename": draft.filename,
            "code": draft.code,
        }

    @app.post("/api/tools/create/{draft_id}/approve")
    async def approve_tool(draft_id: str):
        """Approve a tool draft: write the file and reload."""
        from ..tool_creator import ToolCreator
        creator = ToolCreator(registry=state.tools, llm_client=state.llm if hasattr(state, 'llm') else None)

        # Find the draft -- it may be on a different ToolCreator instance
        # so we check the global server-level store
        if not hasattr(state, 'tool_drafts'):
            state.tool_drafts = {}

        # The draft was created in a previous request; we need to store it on state
        raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found. Tool creation is stateless -- approve in the same session or re-generate.")

    @app.post("/api/tools/create-approve")
    async def create_and_approve_tool(request: Request):
        """OCUE: Generate a tool and immediately approve it in one step.

        Describe what you want, get a working tool. One click.
        """
        from ..tool_creator import ToolCreator
        from ..user_tools import load_user_tools
        body = await request.json()
        description = body.get("description", "").strip()
        tool_name = body.get("name", "").strip() or None

        if not description:
            raise HTTPException(status_code=400, detail="Description is required")

        creator = ToolCreator(registry=state.tools, llm_client=state.llm if hasattr(state, 'llm') else None)
        draft = await creator.generate(description=description, tool_name=tool_name)
        result = creator.approve(draft.id)

        return {
            **result,
            "code": draft.code,
            "name": draft.name,
            "description": draft.description,
        }

    @app.get("/api/tools/user")
    async def list_user_tools():
        """List user-created tools from ~/.sawyer-harness/tools/."""
        from ..user_tools import USER_TOOLS_DIR
        USER_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        tools = []
        for f in sorted(USER_TOOLS_DIR.glob("*.py")):
            if f.name.startswith("__"):
                continue
            content = f.read_text(encoding="utf-8")
            # Extract name from register() call
            import re
            name_match = re.search(r'name="(\w+)"', content)
            desc_match = re.search(r'description="([^"]+)"', content)
            tools.append({
                "filename": f.name,
                "name": name_match.group(1) if name_match else f.stem,
                "description": desc_match.group(1) if desc_match else "",
                "path": str(f),
            })
        return {"user_tools": tools}

    @app.post("/api/tools/toggle")
    async def toggle_tool(toggle: ToolToggle):
        """Enable or disable a tool."""
        if toggle.enabled:
            # Add to allowlist (or remove restriction)
            if state.tools._allowed_tools is not None:
                state.tools._allowed_tools.add(toggle.tool_name)
        else:
            # Remove from allowlist
            if state.tools._allowed_tools is not None:
                state.tools._allowed_tools.discard(toggle.tool_name)

        return {"tool": toggle.tool_name, "enabled": toggle.enabled}

    @app.get("/api/tools/audit")
    async def get_audit_log(limit: int = 50):
        """Get recent audit log entries."""
        return {"entries": state.tools.audit_trail(limit=limit)}

    # ----------------------------------------------------------
    # Cron / Scheduler
    # ----------------------------------------------------------

    @app.get("/api/cron")
    async def list_cron_jobs():
        """List all scheduled jobs."""
        return {"jobs": state.scheduler.list_jobs()}

    @app.post("/api/cron")
    async def create_cron_job(
        name: str,
        schedule_type: str,
        schedule_expr: str,
        prompt: str,
        channel: str = "cli",
    ):
        """Create a new scheduled job."""
        try:
            st = ScheduleType(schedule_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid schedule type: {schedule_type}")

        job = state.scheduler.add_job(
            name=name,
            schedule_type=st,
            schedule_expr=schedule_expr,
            prompt=prompt,
            channel=channel,
        )
        return job.to_dict()

    @app.delete("/api/cron/{job_id}")
    async def delete_cron_job(job_id: str):
        """Delete a scheduled job."""
        result = state.scheduler.remove_job(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "deleted", "job_id": job_id}

    @app.post("/api/cron/{job_id}/toggle")
    async def toggle_cron_job(job_id: str, enabled: bool = True):
        """Enable or disable a scheduled job."""
        if enabled:
            result = state.scheduler.enable_job(job_id)
        else:
            result = state.scheduler.disable_job(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "updated", "job_id": job_id, "enabled": enabled}

    # ----------------------------------------------------------
    # Context / Compression
    # ----------------------------------------------------------

    @app.get("/api/context/stats")
    async def get_context_stats(session_id: str = ""):
        """Get context window statistics for a session.

        Uses real token counting (tiktoken BPE) for accurate measurements.
        Also includes API-reported token counts when available (ground truth).
        """
        messages = []
        if session_id and session_id in state.sessions:
            messages = state.sessions[session_id].conversation

        system_prompt = "You are Sawyer Agent, a secure AI agent."
        memory_text = "; ".join(e["content"] for e in state.memory.all_entries()[:5])

        stats = state.context_manager.get_context_stats(
            system_prompt=system_prompt,
            memory_text=memory_text,
            messages=messages,
        )
        return stats

    @app.post("/api/context/compress/{session_id}")
    async def compress_context(session_id: str):
        """Compress the conversation context for a session."""
        if session_id not in state.sessions:
            raise HTTPException(status_code=404, detail="Session not found")

        agent = state.sessions[session_id]
        system_prompt = agent._build_system_prompt()

        memory_text = "; ".join(e["content"] for e in agent.memory.all_entries()[:5])

        compressed, result = state.compressor.compress(
            messages=agent.conversation,
            system_prompt=system_prompt,
            memory_text=memory_text,
        )

        # Replace conversation with compressed version
        agent.conversation = compressed

        return {
            "status": "compressed",
            "original_tokens": result.original_tokens,
            "compressed_tokens": result.compressed_tokens,
            "messages_kept": result.messages_kept,
            "messages_summarized": result.messages_summarized,
            "messages_dropped": result.messages_dropped,
            "decisions_extracted": result.decisions_extracted,
        }

    @app.get("/api/context/models")
    async def get_context_models():
        """List known model context window sizes."""
        from ..context_manager import MODEL_WINDOWS
        return {"models": {k: v for k, v in MODEL_WINDOWS.items()}, "default": 128000}

    @app.post("/api/context/window")
    async def set_context_window(model: str = "", window_size: int = 0, context_length: int = 0):
        """Set the context window size (auto-detect from model or manual override)."""
        ctx_override = context_length if context_length > 0 else None
        if window_size > 0:
            state.context_manager = ContextManager(
                model_name=model or state.context_manager.model_name,
                window_size=window_size,
                context_length_override=ctx_override,
            )
        elif model:
            state.context_manager = ContextManager(
                model_name=model,
                context_length_override=ctx_override,
            )

        state.compressor = ContextCompressor(
            max_tokens=state.context_manager.window_size,
        )

        return {
            "model": state.context_manager.model_name,
            "window_size": state.context_manager.window_size,
        }

    # ----------------------------------------------------------
    # Sessions / Session Engine
    # ----------------------------------------------------------

    @app.get("/api/sessions")
    async def list_sessions():
        """List all active sessions."""
        sessions = []
        for sid, agent in state.sessions.items():
            engine = state.session_engines.get(sid)
            sessions.append({
                "session_id": sid,
                "message_count": len(agent.conversation),
                "memory_chars": agent.memory.total_chars(),
                "notes_count": len(engine.notes) if engine else 0,
            })
        return {"sessions": sessions}

    @app.post("/api/sessions")
    async def create_session():
        """Create a new session."""
        session_id, agent = state.get_or_create_session("")
        # Create a session engine for tracking
        project_dir = state.current_project.path if state.current_project else None
        engine = SessionEngine(project_dir=project_dir)
        state.session_engines[session_id] = engine
        return {"session_id": session_id}

    @app.get("/api/sessions/{session_id}/notes")
    async def get_session_notes(session_id: str):
        """Get auto-generated session notes."""
        if session_id not in state.sessions:
            raise HTTPException(status_code=404, detail="Session not found")

        engine = state.session_engines.get(session_id)
        if not engine:
            raise HTTPException(status_code=404, detail="No session engine for this session")

        summary = engine.generate_summary()
        return {
            "session_id": summary.session_id,
            "started": summary.started,
            "ended": summary.ended,
            "duration_minutes": summary.duration_minutes,
            "message_count": summary.message_count,
            "tool_call_count": summary.tool_call_count,
            "topics": summary.topics,
            "decisions": summary.decisions,
            "corrections": summary.corrections,
            "todos": summary.todos,
            "files_created": summary.files_created,
            "files_modified": summary.files_modified,
            "errors": summary.errors,
            "next_session_suggestions": summary.next_session_suggestions,
            "full_notes": summary.full_notes,
        }

    @app.post("/api/sessions/{session_id}/notes/save")
    async def save_session_notes(session_id: str):
        """Save session notes to disk."""
        if session_id not in state.session_engines:
            raise HTTPException(status_code=404, detail="No session engine")

        engine = state.session_engines[session_id]
        filepath = engine.save_notes()
        if not filepath:
            raise HTTPException(status_code=500, detail="No project directory set for notes")

        return {"status": "saved", "path": str(filepath)}

    @app.get("/api/sessions/suggestions")
    async def get_session_suggestions():
        """Get suggestions from previous sessions for starting a new session."""
        project_dir = state.current_project.path if state.current_project else None
        if not project_dir:
            return {"suggestions": [], "previous_sessions": []}

        engine = SessionEngine(project_dir=project_dir)
        previous = engine.load_previous_notes(limit=3)
        suggestions = []
        for s in previous:
            suggestions.extend(s.next_session_suggestions)

        return {
            "suggestions": suggestions,
            "previous_sessions": [
                {
                    "session_id": s.session_id,
                    "topics": s.topics[:3],
                    "todos": s.todos[:3],
                    "suggestions": s.next_session_suggestions,
                }
                for s in previous
            ],
        }

    # ----------------------------------------------------------
    # Projects
    # ----------------------------------------------------------

    @app.get("/api/projects")
    async def list_projects():
        """List all Sawyer projects."""
        projects = state.project_manager.list_projects()
        return {"projects": [
            {
                "name": p.name,
                "path": str(p.path),
                "description": p.description,
                "initialized": p.is_initialized,
            }
            for p in projects
        ]}

    @app.post("/api/projects")
    async def create_project(name: str, description: str = ""):
        """Create a new Sawyer project with standard layout."""
        project = state.project_manager.create_project(name=name, description=description)
        state.current_project = project
        return {
            "name": project.name,
            "path": str(project.path),
            "initialized": project.is_initialized,
        }

    @app.post("/api/projects/{project_name}/open")
    async def open_project(project_name: str):
        """Open an existing project."""
        project = state.project_manager.find_project(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        state.current_project = project
        return {
            "name": project.name,
            "path": str(project.path),
            "description": project.description,
            "initialized": project.is_initialized,
        }

    @app.get("/api/projects/current")
    async def get_current_project():
        """Get the currently active project."""
        if not state.current_project:
            return {"project": None}
        p = state.current_project
        return {
            "project": {
                "name": p.name,
                "path": str(p.path),
                "description": p.description,
                "initialized": p.is_initialized,
            }
        }

    @app.get("/api/projects/current/files")
    async def get_project_files():
        """Get file index for the current project."""
        if not state.current_project:
            raise HTTPException(status_code=400, detail="No project open")
        index = state.current_project.get_file_index()
        return {"files": index, "project": state.current_project.name}

    # ----------------------------------------------------------
    # Files (Drop Zone)
    # ----------------------------------------------------------

    @app.post("/api/files/upload")
    async def upload_file(file: bytes = None, filename: str = "upload"):
        """Upload a file to the drop zone."""
        if not file:
            raise HTTPException(status_code=400, detail="No file provided")

        # Save to project outputs if project is open, otherwise uploads dir
        if state.current_project:
            dest = state.project_manager.get_output_path(
                state.current_project, filename, category="uploads"
            )
        else:
            dest = state.upload_dir / filename

        dest.write_bytes(file)
        return {"status": "uploaded", "path": str(dest), "filename": filename}

    @app.get("/api/files/uploads")
    async def list_uploaded_files():
        """List all files in the upload directory."""
        files = []
        scan_dir = state.upload_dir
        if state.current_project:
            upload_dir = state.current_project.path / "outputs" / "uploads"
            if upload_dir.exists():
                scan_dir = upload_dir

        for f in scan_dir.iterdir():
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "path": str(f),
                })

        return {"files": sorted(files, key=lambda x: x["modified"], reverse=True)}

    @app.get("/api/files/{file_id}/content")
    async def get_file_content(file_id: str):
        """Get the content of an uploaded or output file."""
        # Search for the file in uploads and outputs
        search_dirs = [state.upload_dir]
        if state.current_project:
            search_dirs.extend([
                state.current_project.path / "outputs",
                state.current_project.path / "data",
            ])

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for f in search_dir.rglob(file_id):
                if f.is_file():
                    try:
                        content = f.read_text(encoding="utf-8")
                        return {"filename": f.name, "path": str(f), "content": content[:50000]}
                    except UnicodeDecodeError:
                        return {"filename": f.name, "path": str(f), "content": "[binary file]", "size": f.stat().st_size}

        raise HTTPException(status_code=404, detail="File not found")

    # ----------------------------------------------------------
    # Status / Health
    # ----------------------------------------------------------

    @app.get("/api/status")
    async def get_status():
        """Get overall system status."""
        return {
            "status": "running",
            "version": __version__,
            "pid": os.getpid(),
            "sessions": len(state.sessions),
            "memory_chars": state.memory.total_chars(),
            "skills_count": len(state.skills.list_skills()),
            "goals_count": len(state.goals),
            "providers": len(state.router.providers),
            "tools_available": len(state.tools.list_tools()),
            "context_window": state.context_manager.window_size,
            "context_model": state.context_manager.model_name,
            "project": state.current_project.name if state.current_project else None,
            "keys_version": state.key_storage.version,
            "capabilities": len(state.tools.list_tools()),
            "rules_count": state.rules_store.count(),
            "agent_templates": len(state.agent_creator.list_templates()),
            "orchestration_runs": state.orchestrator.count(),
        }

    # ----------------------------------------------------------
    # Suggestions
    # ----------------------------------------------------------

    @app.get("/api/suggestions")
    async def list_suggestions(status: str | None = None):
        """List all user suggestions. Optionally filter by status."""
        store = SuggestionStore()
        suggestions = store.list_all(status=status)
        return {
            "suggestions": [vars(s) for s in suggestions],
            "counts": store.count(),
        }

    @app.post("/api/suggestions")
    async def create_suggestion(body: dict):
        """Submit a new suggestion. Requires name and suggestion text.
        Optionally includes 'biggest_problem' — the user's biggest pain
        point with their current AI agent.

        If SMTP is configured in config.yaml notifications section,
        an email is sent to the configured address.
        """
        name = body.get("name", "").strip()
        suggestion = body.get("suggestion", "").strip()
        biggest_problem = body.get("biggest_problem", "").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Name is required (minimum for credit attribution)")
        if not suggestion:
            raise HTTPException(status_code=400, detail="Suggestion text is required")

        store = SuggestionStore()
        entry = store.add(name=name, suggestion=suggestion, biggest_problem=biggest_problem)

        # Send email notification if SMTP is configured
        notif = state.config.notifications
        if notif.smtp_host and notif.to_address:
            from ..suggestions import send_suggestion_email
            import asyncio
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, lambda: send_suggestion_email(
                suggestion=entry,
                smtp_host=notif.smtp_host,
                smtp_port=notif.smtp_port,
                smtp_user=notif.smtp_user,
                smtp_password=notif.smtp_password,
                from_address=notif.from_address,
                to_address=notif.to_address,
                use_tls=notif.use_tls,
            ))

        return vars(entry)

    @app.patch("/api/suggestions/{suggestion_id}")
    async def update_suggestion(suggestion_id: str, body: dict):
        """Update suggestion status (acknowledge, mark implemented)."""
        new_status = body.get("status", "").strip()
        if new_status not in ("new", "acknowledged", "implemented"):
            raise HTTPException(status_code=400, detail="Status must be new, acknowledged, or implemented")
        store = SuggestionStore()
        entry = store.update_status(suggestion_id, new_status)
        if entry is None:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return vars(entry)

    @app.get("/api/update-check")
    async def check_for_updates():
        """Check GitHub for new releases. Returns changelog highlights for
        every version newer than the installed one, so the UI can show
        what the user would gain by upgrading."""
        import httpx as _httpx
        try:
            client = _httpx.AsyncClient(timeout=5.0)
            # Fetch the latest release for the primary check
            resp = await client.get(
                "https://api.github.com/repos/drc10101/sawyer-agent/releases/latest"
            )
            if resp.status_code != 200:
                return {"current_version": __version__, "latest_version": "unknown", "update_available": False, "error": f"GitHub returned {resp.status_code}"}

            data = resp.json()
            latest = data.get("tag_name", "").lstrip("v")
            current_parts = [int(x) for x in __version__.split(".")]
            latest_parts = [int(x) for x in latest.split(".")] if latest else [0, 0, 0]
            update_available = latest_parts > current_parts

            result = {
                "current_version": __version__,
                "latest_version": latest,
                "update_available": update_available,
                "release_url": data.get("html_url", ""),
                "release_notes": data.get("body", "")[:500] if data.get("body") else "",
            }

            # If an update is available, also fetch all releases to build
            # changelog highlights for every version the user is missing
            if update_available:
                try:
                    all_resp = await client.get(
                        "https://api.github.com/repos/drc10101/sawyer-agent/releases?per_page=10"
                    )
                    if all_resp.status_code == 200:
                        changelog = []
                        for release in all_resp.json():
                            ver = release.get("tag_name", "").lstrip("v")
                            if not ver:
                                continue
                            ver_parts = [int(x) for x in ver.split(".")]
                            if ver_parts > current_parts:
                                body = release.get("body", "") or ""
                                # Extract first line of each bullet as a highlight
                                highlights = []
                                for line in body.split("\n"):
                                    line = line.strip()
                                    if line.startswith("- ") or line.startswith("* "):
                                        highlights.append(line.lstrip("-* ").strip())
                                changelog.append({
                                    "version": ver,
                                    "url": release.get("html_url", ""),
                                    "highlights": highlights[:8],
                                    "date": release.get("published_at", "")[:10] if release.get("published_at") else "",
                                })
                        result["changelog"] = changelog
                except Exception:
                    pass  # Non-critical — changelog is optional

            await client.aclose()
            return result
        except Exception as e:
            return {"current_version": __version__, "latest_version": "unknown", "update_available": False, "error": str(e)}


    # ----------------------------------------------------------
    # Self-upgrade
    # ----------------------------------------------------------

    @app.post("/api/upgrade")
    async def upgrade_sawyer():
        """Upgrade Sawyer Agent to the latest version from GitHub.

        Downloads the new package via pip, then spawns a restart script
        that waits for this process to exit and starts the server again.
        All user data in ~/.sawyer-harness/ (config, memory, skills, keys, cron)
        is preserved -- only the Python package code changes.

        Returns the new version number on success, or an error message.
        The server process exits after responding, so the client should
        show a reconnect overlay while waiting.
        """
        import subprocess
        import sys
        import time
        import platform

        old_version = __version__
        logger.info(f"Starting upgrade from v{old_version}")

        # Step 1: pip install --upgrade
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade",
                 "git+https://github.com/drc10101/sawyer-agent.git", "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"Upgrade pip install failed: {result.stderr}")
                raise HTTPException(
                    status_code=500,
                    detail=f"pip install failed: {result.stderr[:500]}",
                )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="pip install timed out after 120s")

        # Step 2: Verify the new version
        try:
            import importlib
            import sawyer_harness as _sh
            importlib.reload(_sh)
            new_version = _sh.__version__
        except Exception:
            new_version = "unknown"

        logger.info(f"Upgrade complete: v{old_version} -> v{new_version}")

        # Step 3: Write restart script and schedule exit
        is_windows = platform.system() == "Windows"
        my_pid = os.getpid()

        if is_windows:
            script_path = UserData.restart_script.with_suffix(".bat")
            # Windows batch: kill old PID, wait for it to die, restart server
            script_lines = [
                "@echo off",
                "echo Sawyer Agent - Restarting after upgrade...",
                f"echo Waiting for PID {my_pid} to exit...",
                f"taskkill /PID {my_pid} /F >nul 2>&1",
                ":wait",
                f'tasklist /FI "PID eq {my_pid}" 2>nul | find "{my_pid}" >nul',
                "if %errorlevel%==0 (timeout /t 1 /nobreak >nul & goto wait)",
                "echo Starting Sawyer Agent...",
                f'"{sys.executable}" -m sawyer_harness --host 127.0.0.1 --port 8765',
                "pause",
            ]
            script_content = "\n".join(script_lines)
        else:
            script_path = UserData.restart_script.with_suffix(".sh")
            script_lines = [
                "#!/bin/bash",
                "echo 'Sawyer Agent - Restarting after upgrade...'",
                f"echo 'Waiting for PID {my_pid} to exit...'",
                f"kill {my_pid} 2>/dev/null",
                f"while kill -0 {my_pid} 2>/dev/null; do sleep 1; done",
                "echo 'Starting Sawyer Agent...'",
                f"{sys.executable} -m sawyer_harness --host 127.0.0.1 --port 8765",
            ]
            script_content = "\n".join(script_lines)

        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_content, encoding="utf-8")

        if not is_windows:
            script_path.chmod(0o755)

        # Step 4: Launch restart script in background and schedule our exit
        restart_delay = 2  # seconds to let the HTTP response go out first

        def _exit_after_delay():
            time.sleep(restart_delay)
            if is_windows:
                subprocess.Popen(
                    ["cmd", "/c", str(script_path)],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(["bash", str(script_path)],
                    start_new_session=True,
                )
            os._exit(0)  # Hard exit -- uvicorn won't stop gracefully

        import threading
        exit_thread = threading.Thread(target=_exit_after_delay, daemon=True)
        exit_thread.start()

        return {
            "status": "upgrading",
            "old_version": old_version,
            "new_version": new_version,
            "message": f"Upgraded from v{old_version} to v{new_version}. Restarting server.",
        }

    @app.post("/api/restart")
    async def restart_server():
        """Restart the Sawyer server process.

        Writes a restart script that waits for this process to exit,
        then starts the server again with the same arguments.
        The client should show a reconnect overlay while waiting.
        All user data in ~/.sawyer-harness/ is preserved.
        """
        import subprocess
        import sys
        import time
        import platform

        is_windows = platform.system() == "Windows"
        my_pid = os.getpid()

        # Determine the host/port this server is running on
        host = getattr(state, "_host", "127.0.0.1")
        port = getattr(state, "_port", 8765)

        if is_windows:
            script_path = UserData.restart_script.with_suffix(".bat")
            script_lines = [
                "@echo off",
                "echo Sawyer Agent - Restarting...",
                f"echo Waiting for PID {my_pid} to exit...",
                f"taskkill /PID {my_pid} /F >nul 2>&1",
                ":wait",
                f'tasklist /FI "PID eq {my_pid}" 2>nul | find "{my_pid}" >nul',
                "if %errorlevel%==0 (timeout /t 1 /nobreak >nul & goto wait)",
                "echo Starting Sawyer Agent...",
                f'"{sys.executable}" -m sawyer_harness --host {host} --port {port}',
                "pause",
            ]
            script_content = "\n".join(script_lines)
        else:
            script_path = UserData.restart_script.with_suffix(".sh")
            script_lines = [
                "#!/bin/bash",
                "echo 'Sawyer Agent - Restarting...'",
                f"echo 'Waiting for PID {my_pid} to exit...'",
                f"kill {my_pid} 2>/dev/null",
                f"while kill -0 {my_pid} 2>/dev/null; do sleep 1; done",
                "echo 'Starting Sawyer Agent...'",
                f"{sys.executable} -m sawyer_harness --host {host} --port {port}",
            ]
            script_content = "\n".join(script_lines)

        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_content, encoding="utf-8")

        if not is_windows:
            script_path.chmod(0o755)

        restart_delay = 2

        def _exit_after_delay():
            time.sleep(restart_delay)
            if is_windows:
                subprocess.Popen(
                    ["cmd", "/c", str(script_path)],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(["bash", str(script_path)],
                    start_new_session=True,
                )
            os._exit(0)

        import threading
        exit_thread = threading.Thread(target=_exit_after_delay, daemon=True)
        exit_thread.start()

        return {
            "status": "restarting",
            "message": "Server is restarting. Wait for the reconnect prompt.",
        }

    @app.post("/api/open-data-folder")
    async def open_data_folder():
        """Open the user data directory in the OS file explorer.

        Opens ~/.sawyer-harness/user/ which contains config, memory, skills,
        keys, tools, projects — everything that survives uninstall.
        """
        import subprocess
        import platform

        folder = UserData.user_dir
        folder.mkdir(parents=True, exist_ok=True)

        system = platform.system()
        try:
            if system == "Windows":
                subprocess.Popen(["explorer", str(folder)])
            elif system == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return {"status": "opened", "path": str(folder)}
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Could not open folder: {e}",
            )

    @app.get("/api/capabilities")
    async def get_capabilities():
        """Return the agent's capabilities manifest."""
        from pathlib import Path
        import yaml as _yaml
        cap_path = Path(__file__).resolve().parent.parent / "capabilities.yaml"
        if cap_path.exists():
            return _yaml.safe_load(cap_path.read_text())
        return {"agent_handles": [], "requires_user": [], "behavior_rules": []}

    # ----------------------------------------------------------
    # Key Storage
    # ----------------------------------------------------------
    @app.get("/api/keys")
    async def list_keys(category: str | None = None):
        """List all stored keys (masked). Optional ?category= filter."""
        return state.key_storage.list_entries(category=category, masked=True)

    @app.get("/api/keys/{category}/{name}")
    async def get_key(category: str, name: str):
        """Get a single key entry (masked)."""
        entry = state.key_storage.get_entry(category, name)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Entry '{name}' not found in {category}")
        return state.key_storage._mask_entry(entry)

    @app.post("/api/keys/{category}")
    async def add_key(category: str, entry: dict):
        """Add a new key entry. Body should have at least 'name' field."""
        try:
            return state.key_storage.add_entry(category, entry)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/api/keys/{category}/{name}")
    async def update_key(category: str, name: str, updates: dict):
        """Update an existing key entry."""
        try:
            return state.key_storage.update_entry(category, name, updates)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/keys/{category}/{name}")
    async def delete_key(category: str, name: str):
        """Delete a key entry."""
        try:
            return state.key_storage.delete_entry(category, name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/keys/categories")
    async def list_key_categories():
        """List available key categories, permissions, presets, and counts."""
        from ..key_storage import PERMISSIONS, PERMISSION_LABELS, KEY_PRESETS
        return {
            "categories": state.key_storage.categories(),
            "permissions": PERMISSIONS,
            "permission_labels": PERMISSION_LABELS,
            "presets": KEY_PRESETS,
            "counts": state.key_storage.count(),
        }

    # ----------------------------------------------------------
    # Git push
    # ----------------------------------------------------------

    @app.post("/api/git/push")
    async def git_push():
        """Stage Sawyer Harness changes, commit, and push to remote."""
        import subprocess
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        try:
            # Only track changes within the sawyer-harness directory
            status = subprocess.run(
                ["git", "status", "--porcelain", "--", "sawyer_harness/", "tests/", "web/", "key_storage.py"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
            if status.returncode != 0:
                return {"success": False, "error": f"git status failed: {status.stderr}"}

            if not status.stdout.strip():
                return {"success": True, "message": "Nothing to push -- working tree clean.", "committed": False}

            # Stage only project files
            add = subprocess.run(
                ["git", "add", "sawyer_harness/", "tests/", "key_storage.py"],
                cwd=str(repo), capture_output=True, text=True, timeout=15,
            )
            if add.returncode != 0:
                return {"success": False, "error": f"git add failed: {add.stderr}"}

            # Commit with timestamp
            from datetime import datetime
            msg = f"sawyer: auto-push {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            commit = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=str(repo), capture_output=True, text=True, timeout=15,
            )
            if commit.returncode != 0:
                if "nothing to commit" in commit.stdout.lower():
                    return {"success": True, "message": "Nothing to commit.", "committed": False}
                return {"success": False, "error": f"git commit failed: {commit.stderr}"}

            # Push
            push = subprocess.run(
                ["git", "push"],
                cwd=str(repo), capture_output=True, text=True, timeout=60,
            )
            if push.returncode != 0:
                return {"success": False, "error": f"git push failed: {push.stderr}", "committed": True}

            return {"success": True, "message": f"Pushed: {msg}", "committed": True}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Git operation timed out."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @app.get("/health")
    async def health_check():
        """Simple health check endpoint."""
        return {"status": "ok", "version": "0.4.0"}

    # ----------------------------------------------------------
    # Agent Rules
    # ----------------------------------------------------------

    @app.get("/api/rules")
    async def list_rules(scope: str = "", agent: str = "", enabled_only: bool = False):
        """List all agent rules, optionally filtered."""
        rules = state.rules_store.list_rules(
            scope=RuleScope(scope) if scope else None,
            agent=agent or None,
            enabled_only=enabled_only,
        )
        return {"rules": [r.to_dict() for r in rules], "total": len(rules)}

    @app.post("/api/rules/reload")
    async def reload_rules():
        """Reload rules from disk."""
        state.rules_store.reload()
        return {"status": "reloaded", "count": state.rules_store.count()}

    @app.get("/api/rules/prompt")
    async def get_rules_prompt(scope: str = "", agent: str = ""):
        """Get the formatted rules text for injection into system prompt."""
        prompt = state.rules_store.get_rules_prompt(
            scope=RuleScope(scope) if scope else None,
            agent=agent or None,
        )
        return {"prompt": prompt}

    @app.get("/api/rules/{rule_id}")
    async def get_rule(rule_id: str):
        """Get a specific rule."""
        rule = state.rules_store.get_rule(rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        return rule.to_dict()

    @app.post("/api/rules")
    async def create_rule(
        name: str,
        rule: str,
        detail: str = "",
        priority: str = "P2",
        scope: str = "global",
        agent: str = "",
        enabled: bool = True,
    ):
        """Create a new agent rule."""
        try:
            pri = RulePriority(priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {priority}")
        try:
            sc = RuleScope(scope)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid scope: {scope}")

        new_rule = state.rules_store.add_rule(
            name=name, rule=rule, detail=detail,
            priority=pri, scope=sc, agent=agent, enabled=enabled,
        )
        return new_rule.to_dict()

    @app.put("/api/rules/{rule_id}")
    async def update_rule(rule_id: str, updates: dict):
        """Update an existing rule."""
        result = state.rules_store.update_rule(rule_id, **updates)
        if not result:
            raise HTTPException(status_code=404, detail="Rule not found")
        return result.to_dict()

    @app.delete("/api/rules/{rule_id}")
    async def delete_rule(rule_id: str):
        """Delete a rule."""
        result = state.rules_store.delete_rule(rule_id)
        if not result:
            raise HTTPException(status_code=404, detail="Rule not found")
        return {"status": "deleted", "rule_id": rule_id}

    # ----------------------------------------------------------
    # Agent Creator / Templates
    # ----------------------------------------------------------

    @app.get("/api/agents")
    async def list_agents(category: str = ""):
        """List all agent templates."""
        templates = state.agent_creator.list_templates(
            category=category or None,
        )
        return {"templates": [t.to_dict() for t in templates], "total": len(templates)}

    @app.get("/api/agents/categories")
    async def list_agent_categories():
        """Get unique agent categories."""
        return {"categories": state.agent_creator.get_categories()}

    @app.get("/api/agents/{template_id}")
    async def get_agent_template(template_id: str):
        """Get a specific agent template."""
        tpl = state.agent_creator.get_template(template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        return tpl.to_dict()

    @app.post("/api/agents")
    async def create_agent_template(data: dict):
        """Create a new agent template."""
        required = ["name"]
        for field in required:
            if field not in data or not data[field]:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

        tpl = state.agent_creator.create_template(
            name=data["name"],
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            base_url=data.get("base_url", ""),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            rules=data.get("rules", []),
            skills=data.get("skills", []),
            tools_enabled=data.get("tools_enabled", "all"),
            icon=data.get("icon", "bot"),
            category=data.get("category", "general"),
            soul_identity=data.get("soul_identity", ""),
            soul_strengths=data.get("soul_strengths", []),
            soul_personality=data.get("soul_personality", []),
            soul_values=data.get("soul_values", []),
            soul_quirks=data.get("soul_quirks", []),
        )
        return tpl.to_dict()

    @app.put("/api/agents/{template_id}")
    async def update_agent_template(template_id: str, updates: dict):
        """Update an agent template."""
        # Parse soul data if provided
        soul_data = updates.pop("soul", None) or {}
        if isinstance(soul_data, dict):
            from ..agent_creator import AgentSoul
            updates["soul"] = AgentSoul.from_dict(soul_data)
        # Parse individual soul fields if sent flat
        soul_fields = {}
        for key in ["soul_identity", "soul_strengths", "soul_personality", "soul_values", "soul_quirks"]:
            if key in updates:
                soul_fields[key] = updates.pop(key)
        if soul_fields and "soul" not in updates:
            from ..agent_creator import AgentSoul
            updates["soul"] = AgentSoul(
                identity=soul_fields.get("soul_identity", ""),
                strengths=soul_fields.get("soul_strengths", []),
                personality=soul_fields.get("soul_personality", []),
                values=soul_fields.get("soul_values", []),
                quirks=soul_fields.get("soul_quirks", []),
            )
        result = state.agent_creator.update_template(template_id, **updates)
        if not result:
            raise HTTPException(status_code=404, detail="Template not found or is built-in")
        return result.to_dict()

    @app.delete("/api/agents/{template_id}")
    async def delete_agent_template(template_id: str):
        """Delete a user-created agent template (cannot delete built-ins)."""
        result = state.agent_creator.delete_template(template_id)
        if not result:
            raise HTTPException(status_code=400, detail="Cannot delete built-in template or template not found")
        return {"status": "deleted", "template_id": template_id}

    @app.post("/api/agents/{template_id}/spawn")
    async def spawn_agent_from_template(template_id: str):
        """Spawn a new agent session from a template."""
        tpl = state.agent_creator.get_template(template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")

        # Build system prompt from template + rules + soul
        system_parts = [tpl.system_prompt or "You are Sawyer Agent, a secure AI assistant."]

        # Inject soul identity
        if tpl.soul and (tpl.soul.identity or tpl.soul.strengths or tpl.soul.personality):
            soul_prompt = tpl.soul.to_prompt_section()
            if soul_prompt:
                system_parts.append(soul_prompt)

        # Inject template-specific rules
        if tpl.rules:
            system_parts.append("\n## Agent Rules\n")
            for r in tpl.rules:
                pri = r.get("priority", "P2")
                system_parts.append(f"- [{pri}] {r['rule']}")
                if r.get("detail"):
                    system_parts.append(f"  {r['detail']}")
            system_parts.append("")

        # Inject global custom rules
        rules_prompt = state.rules_store.get_rules_prompt()
        if rules_prompt:
            system_parts.append(rules_prompt)

        system_prompt = "\n".join(system_parts)

        # Override model config if template specifies
        config = HarnessConfig()
        if tpl.model:
            config.llm.model = tpl.model
        if tpl.provider:
            config.llm.provider = tpl.provider
        if tpl.base_url:
            config.llm.base_url = tpl.base_url
        config.llm.temperature = tpl.temperature
        config.llm.max_tokens = tpl.max_tokens

        # Create tools registry with template's tool restrictions
        allowed = None if tpl.tools_enabled == "all" else tpl.tools_enabled.split(",")
        tools = create_default_registry(
            allowed_tools=allowed or None,
            denied_paths=config.security.denied_paths,
            permission_mode=config.security.permission_mode,
        )

        llm = LLMClient(config.llm, tool_registry=tools)
        agent = Agent(
            config=config,
            llm=llm,
            memory=state.memory,
            tools=tools,
            system_prompt=system_prompt,
            skills=state.skills,
            context_window=state.context_manager.window_size,
            compaction_policy=CompactionPolicy.from_dict({
                "policy": config.compaction.policy,
                "threshold": config.compaction.threshold,
                "recency_pct": config.compaction.recency_pct,
            }),
        )

        session_id = str(uuid.uuid4())[:8]
        state.sessions[session_id] = agent

        return {
            "session_id": session_id,
            "template": tpl.id,
            "template_name": tpl.name,
            "model": config.llm.model,
            "system_prompt_preview": system_prompt[:500],
        }

    # ----------------------------------------------------------
    # Orchestration Engine
    # ----------------------------------------------------------

    @app.get("/api/orchestrations")
    async def list_orchestrations(status: str | None = None):
        """List orchestration runs, optionally filtered by status."""
        runs = state.orchestrator.list_runs(status=status)
        return [r.to_dict() for r in runs]

    @app.post("/api/orchestrations")
    async def create_orchestration(data: dict):
        """Create a new orchestration run for a goal."""
        goal = data.get("goal", "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail="Goal is required")
        run = state.orchestrator.create_run(goal=goal)
        return run.to_dict()

    @app.get("/api/orchestrations/{run_id}")
    async def get_orchestration(run_id: str):
        """Get details of an orchestration run."""
        run = state.orchestrator.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()

    @app.get("/api/orchestrations/{run_id}/stats")
    async def get_orchestration_stats(run_id: str):
        """Get statistics for an orchestration run."""
        stats = state.orchestrator.run_stats(run_id)
        if not stats:
            raise HTTPException(status_code=404, detail="Run not found")
        return stats

    @app.post("/api/orchestrations/{run_id}/start")
    async def start_orchestration(run_id: str):
        """Mark a run as started."""
        run = state.orchestrator.start_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()

    @app.post("/api/orchestrations/{run_id}/complete")
    async def complete_orchestration(run_id: str):
        """Mark a run as completed."""
        run = state.orchestrator.complete_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict()

    @app.delete("/api/orchestrations/{run_id}")
    async def delete_orchestration(run_id: str):
        """Delete an orchestration run."""
        if not state.orchestrator.delete_run(run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        return {"ok": True}

    @app.post("/api/orchestrations/{run_id}/tasks")
    async def add_orchestration_task(run_id: str, data: dict):
        """Add a task to an orchestration run."""
        goal = data.get("goal", "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail="Goal is required")

        briefing_data = data.get("briefing")
        briefing = None
        if briefing_data:
            briefing = AgentBriefing(
                purpose=briefing_data.get("purpose", ""),
                goal=briefing_data.get("goal", goal),
                rules=briefing_data.get("rules", []),
                permissions=briefing_data.get("permissions", []),
                success_criteria=briefing_data.get("success_criteria", ""),
                context=briefing_data.get("context", ""),
                timeout_seconds=briefing_data.get("timeout_seconds", 300),
                agent_type=briefing_data.get("agent_type", "worker"),
            )
        else:
            # Auto-assemble briefing
            briefing = state.orchestrator.assemble_briefing(
                purpose=f"Subtask of orchestration run",
                goal=goal,
                agent_type=data.get("agent_type", "worker"),
            )

        task = state.orchestrator.add_task(
            run_id=run_id,
            goal=goal,
            agent_type=data.get("agent_type", "worker"),
            priority=TaskPriority(data.get("priority", "P2")),
            parent_task_id=data.get("parent_task_id"),
            briefing=briefing,
        )
        if not task:
            raise HTTPException(status_code=404, detail="Run not found")
        return task.to_dict()

    @app.put("/api/orchestrations/{run_id}/tasks/{task_id}")
    async def update_orchestration_task(run_id: str, task_id: str, data: dict):
        """Update a task's status, result, or improvements."""
        status = None
        if "status" in data:
            status = TaskStatus(data["status"])

        task = state.orchestrator.update_task(
            run_id=run_id,
            task_id=task_id,
            status=status,
            result=data.get("result"),
            error=data.get("error"),
            improvements=data.get("improvements"),
        )
        if not task:
            raise HTTPException(status_code=404, detail="Task or run not found")
        return task.to_dict()

    @app.post("/api/orchestrations/{run_id}/decompose")
    async def decompose_orchestration(run_id: str, data: dict):
        """Decompose a run's goal into subtasks."""
        run = state.orchestrator.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        subtasks = data.get("subtasks", [])
        if not subtasks:
            raise HTTPException(status_code=400, detail="Subtasks are required")

        tasks = state.orchestrator.decompose_goal(
            run_id=run_id,
            goal=run.goal,
            subtasks=subtasks,
        )
        return [t.to_dict() for t in tasks]

    @app.post("/api/orchestrations/{run_id}/evaluate/{task_id}")
    async def evaluate_orchestration_task(run_id: str, task_id: str):
        """Evaluate a completed task for improvement opportunities."""
        task = state.orchestrator.get_task(run_id, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        improvements = state.orchestrator.evaluate_result(task)
        # Update task with improvements
        state.orchestrator.update_task(
            run_id=run_id,
            task_id=task_id,
            improvements=improvements,
        )

        # Update run stats
        run = state.orchestrator.get_run(run_id)
        if run:
            run.improvements_found += len(improvements)

        return {"task_id": task_id, "improvements": improvements}

    @app.get("/api/orchestration/templates")
    async def get_orchestration_templates():
        """Get the orchestrator, creative, and worker agent templates."""
        templates = state.agent_creator.list_templates(category="orchestration")
        worker_templates = state.agent_creator.list_templates(category="execution")
        return {
            "orchestration": [t.to_dict() for t in templates],
            "execution": [t.to_dict() for t in worker_templates],
        }

    # ----------------------------------------------------------
    # Cron Manager (enhanced endpoints)
    # ----------------------------------------------------------

    @app.put("/api/cron/{job_id}")
    async def update_cron_job(job_id: str, updates: dict):
        """Update an existing cron job."""
        result = state.scheduler.update_job(job_id, **updates)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result.to_dict()

    @app.post("/api/cron/{job_id}/run")
    async def run_cron_job_now(job_id: str):
        """Trigger a cron job to run immediately."""
        result = await state.scheduler.run_job_now(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    # ----------------------------------------------------------
    # Static files & index
    # ----------------------------------------------------------

    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    @app.get("/")
    async def serve_index():
        """Serve the web UI. No-cache to ensure updates appear immediately."""
        from starlette.responses import FileResponse
        response = FileResponse(STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    if STATIC_DIR.exists():
        app.mount("/icons", StaticFiles(directory=str(STATIC_DIR / "icons")), name="icons")
        app.mount("/styles", StaticFiles(directory=str(STATIC_DIR / "styles")), name="styles")
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _ensure_shortcuts() -> None:
    """Auto-install desktop/start menu shortcuts on first run.

    Checks if shortcuts already exist. If not, silently creates them
    using the same logic as 'python -m sawyer_harness install-shortcuts'.
    This ensures the Sawyer icon appears on first run without requiring
    the user to run a separate command.
    """
    import platform
    import subprocess

    system = platform.system()

    if system == "Windows":
        # Check if desktop shortcut already exists
        desktop_lnk = Path.home() / "Desktop" / "Sawyer Agent.lnk"
        if desktop_lnk.exists():
            return  # Already installed
    elif system == "Darwin":
        app_bundle = Path.home() / "Applications" / "Sawyer Agent.app"
        if app_bundle.exists():
            return
    else:
        desktop_file = Path.home() / ".local" / "share" / "applications" / "sawyer-agent.desktop"
        if desktop_file.exists():
            return

    # Run install-shortcuts silently
    try:
        from sawyer_harness.cli import _cmd_install_shortcuts
        import argparse
        args = argparse.Namespace()
        _cmd_install_shortcuts(args)
    except Exception as e:
        logger.info(f"Shortcut auto-install skipped: {e}")


def _kill_port_holder(host: str, port: int) -> None:
    """Kill any process already listening on host:port.

    Works cross-platform (Windows and Linux/macOS).  On Windows the
    PID is found via ``netstat -ano`` and terminated with ``taskkill``.
    On POSIX systems ``lsof`` or ``fuser`` is used, falling back to
    ``pkill`` on the Sawyer process name.

    The current process is never killed (we skip our own PID).
    """
    import os
    import signal
    import subprocess
    import platform

    my_pid = os.getpid()

    # ── Fast check: is anything even listening? ──────────────────
    import socket
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass  # something is listening
    except OSError:
        return  # port is free -- nothing to kill

    # ── Find and kill the process holding the port ───────────────
    system = platform.system()

    if system == "Windows":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                # Match lines like:  TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    12345
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[1]
                state = parts[3]
                pid_str = parts[4]
                if f":{port}" in local_addr and state == "LISTENING":
                    try:
                        pid = int(pid_str)
                    except ValueError:
                        continue
                    if pid == my_pid:
                        continue
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=10,
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    else:
        # POSIX: try lsof first, then fuser
        for cmd in [
            ["lsof", "-ti", f":{port}"],
            ["fuser", f"{port}/tcp"],
        ]:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=10,
                )
                for token in result.stdout.split():
                    try:
                        pid = int(token.strip())
                    except ValueError:
                        continue
                    if pid == my_pid:
                        continue
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            # If we found and killed something, stop trying alternatives
            if result.stdout.strip():
                break

    # ── Wait for the port to be released ─────────────────────────
    import time
    for _ in range(20):
        time.sleep(0.25)
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
        except OSError:
            return  # port is now free


def run_server(config: HarnessConfig | None = None, host: str = "0.0.0.0", port: int = 8765):
    """Run the web server.

    If another process is already listening on the same host:port,
    it is terminated first so Sawyer always starts cleanly.
    Then uvicorn binds and, once the server is accepting connections,
    opens the default web browser to the UI.

    On first run, automatically creates desktop/start menu shortcuts
    with the Sawyer icon.
    """
    import uvicorn
    import webbrowser
    import threading
    import platform

    # ── Auto-install shortcuts on first run ───────────────────────
    _ensure_shortcuts()

    # ── Kill any existing process on this port ──────────────────────
    _kill_port_holder(host, port)

    app = create_app(config)

    # Store host/port on state so restart endpoint can use them
    app.state.sawyer._host = host
    app.state.sawyer._port = port

    opened = [False]  # mutable flag in closure

    def open_browser_when_ready():
        """Poll until the server responds, then open the browser once."""
        import socket
        import time
        for _ in range(60):  # up to 60 seconds
            time.sleep(0.5)
            try:
                with socket.create_connection((host, port), timeout=1):
                    pass
            except OSError:
                continue
            # Server is up -- open browser once
            if not opened[0]:
                opened[0] = True
                webbrowser.open(f"http://{host}:{port}")
            return

    thread = threading.Thread(target=open_browser_when_ready, daemon=True)
    thread.start()
    uvicorn.run(app, host=host, port=port)


def main():
    """Entry point for sawyer-web command."""
    import argparse

    parser = argparse.ArgumentParser(description="Sawyer Agent Web UI")
    parser.add_argument("--config", "-c", default=None, help="Config file path (default: Sawyer user data dir)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", "-p", type=int, default=8765, help="Port to bind")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from sawyer_harness.config import DEFAULT_CONFIG_PATH

    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH
    config = HarnessConfig.from_file(config_path)

    # First-run setup: if no config file or no API key, run interactive wizard
    if not config_path.exists() or config.needs_setup():
        from sawyer_harness.config import setup_wizard
        config = setup_wizard(config_path)

    run_server(config, host=args.host, port=args.port)