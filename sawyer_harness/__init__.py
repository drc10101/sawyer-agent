"""Sawyer Harness -- Secure, model-agnostic, self-hosted AI agent framework."""

__version__ = "0.6.0"

from sawyer_harness.config import HarnessConfig
from sawyer_harness.memory import MemoryStore
from sawyer_harness.tools import ToolRegistry, create_default_registry
from sawyer_harness.llm import LLMClient, Message, ToolCall, LLMResponse
from sawyer_harness.agent import Agent
from sawyer_harness.skills import SkillStore, Skill
from sawyer_harness.scheduler import CronScheduler, CronJob, ScheduleType
from sawyer_harness.routing import ModelRouter, ProviderEndpoint, TaskType, ProviderHealth
from sawyer_harness.telegram_adapter import TelegramAdapter
from sawyer_harness.orchestrator import OrchestratorEngine, OrchestrationRun, OrchestratedTask, TaskStatus, TaskPriority, AgentBriefing
from sawyer_harness.compression import ContextCompressor, LLMCompressor, CompressionResult, Priority
from sawyer_harness.session_engine import SessionEngine, SessionNote, SessionSummary
from sawyer_harness.context_manager import ContextManager, ContextBudget, ModelContextConfig, MODEL_WINDOWS
from sawyer_harness.project import Project, ProjectManager
from sawyer_harness.skill_creator import SkillCreator, SkillCreationSession, SessionPhase
from sawyer_harness.key_storage import KeyStorage, KEY_PRESETS

__all__ = [
    "HarnessConfig",
    "MemoryStore",
    "ToolRegistry",
    "LLMClient",
    "Message",
    "ToolCall",
    "LLMResponse",
    "Agent",
    "SkillStore",
    "Skill",
    "CronScheduler",
    "CronJob",
    "ScheduleType",
    "ModelRouter",
    "ProviderEndpoint",
    "TaskType",
    "ProviderHealth",
    "TelegramAdapter",
    "OrchestratorEngine",
    "OrchestrationRun",
    "OrchestratedTask",
    "TaskStatus",
    "TaskPriority",
    "AgentBriefing",
    "ContextCompressor",
    "LLMCompressor",
    "CompressionResult",
    "Priority",
    "SessionEngine",
    "SessionNote",
    "SessionSummary",
    "ContextManager",
    "ContextBudget",
    "ModelContextConfig",
    "MODEL_WINDOWS",
    "Project",
    "ProjectManager",
    "SkillCreator",
    "SkillCreationSession",
    "SessionPhase",
    "KeyStorage",
    "KEY_PRESETS",
]