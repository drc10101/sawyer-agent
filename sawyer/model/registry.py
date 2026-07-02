"""Sawyer model registry — supported MoE models and expert layouts.

Models are tagged by use case so users can find what they need:
  chat  — conversation, Q&A, general assistance
  code  — programming, debugging, code generation
  both  — strong at both chat and code
"""


from dataclasses import dataclass, field


@dataclass
class ExpertLayout:
    """Description of a single expert within a MoE model."""

    expert_id: int
    param_count: float  # billions
    size_gb_q4: float  # size in GB at Q4_K_M quantization
    layers: int


@dataclass
class MoEModel:
    """A supported Mixture-of-Experts model."""

    name: str
    display_name: str
    total_params_b: float  # total parameters in billions
    num_experts: int  # total number of experts
    active_experts: int  # experts activated per token (gating sparsity)
    model_size_gb_q4: float  # total Q4_K_M size
    expert_size_gb_q4: float  # per-expert Q4_K_M size
    context_length: int  # maximum context window
    gating_type: str  # "top_k", "top_n", "shared"
    hf_repo: str  # HuggingFace repo ID
    tags: list[str] = field(default_factory=list)  # use-case tags: "chat", "code", "both"
    description: str = ""  # one-line human-readable description
    experts: list[ExpertLayout] | None = None  # detailed expert layout (if available)

    @property
    def active_params_b(self) -> float:
        """Effective parameters per token (only active experts count)."""
        # Rough estimate: shared params + active expert params
        shared_fraction = 0.3  # attention and embedding params are shared
        shared_params = self.total_params_b * shared_fraction
        expert_params = self.expert_size_gb_q4 * self.active_experts * 2.5  # rough GB->B
        return shared_params + expert_params

    @property
    def min_vram_gb(self) -> float:
        """Minimum VRAM to run the full model (Q4_K_M)."""
        return self.model_size_gb_q4 + 2.0  # 2GB for KV cache overhead

    @property
    def min_vram_per_expert_gb(self) -> float:
        """Minimum VRAM to host one expert shard."""
        return self.expert_size_gb_q4 + 1.0  # 1GB headroom

    def supports_use(self, use: str) -> bool:
        """Check if this model is suitable for a use case."""
        if use in self.tags:
            return True
        if "both" in self.tags and use in ("chat", "code"):
            return True
        return False


# Registry of supported models
MODELS: dict[str, MoEModel] = {
    "mixtral-8x7b": MoEModel(
        name="mixtral-8x7b",
        display_name="Mixtral 8x7B",
        total_params_b=46.7,
        num_experts=8,
        active_experts=2,
        model_size_gb_q4=24.0,
        expert_size_gb_q4=1.5,
        context_length=32768,
        gating_type="top_k",
        hf_repo="TheBloke/Mixtral-8x7B-v0.1-GGUF",
        tags=["both"],
        description="Best all-rounder. Strong at chat and code. 8 experts, 2 active per token.",
    ),
    "deepseek-v2-lite": MoEModel(
        name="deepseek-v2-lite",
        display_name="DeepSeek-V2 Lite",
        total_params_b=15.7,
        num_experts=64,
        active_experts=6,
        model_size_gb_q4=9.0,
        expert_size_gb_q4=0.8,
        context_length=131072,
        gating_type="shared",
        hf_repo="deepseek-ai/DeepSeek-V2-Lite-Chat-GGUF",
        tags=["chat", "code"],
        description="64 tiny experts. Great for distributed serving. 128K context.",
    ),
    "qwen1.5-moe-a2.7b": MoEModel(
        name="qwen1.5-moe-a2.7b",
        display_name="Qwen1.5-MoE A2.7B",
        total_params_b=14.3,
        num_experts=60,
        active_experts=4,
        model_size_gb_q4=7.0,
        expert_size_gb_q4=0.5,
        context_length=32768,
        gating_type="top_k",
        hf_repo="Qwen/Qwen1.5-MoE-A2.7B-GGUF",
        tags=["chat"],
        description="Lightweight chat model. 60 small experts, fits on modest hardware.",
    ),
    "dbrx": MoEModel(
        name="dbrx",
        display_name="DBRX Instruct",
        total_params_b=132.0,
        num_experts=16,
        active_experts=4,
        model_size_gb_q4=65.0,
        expert_size_gb_q4=2.5,
        context_length=32768,
        gating_type="top_k",
        hf_repo="databricks/dbrx-instruct-GGUF",
        tags=["code"],
        description="Databricks code specialist. 16 large experts, 4 active. Needs serious hardware.",
    ),
    # --- Frontier models (archived GGUF, served via local inference or Sawyer network) ---
    "gemma3-27b": MoEModel(
        name="gemma3-27b",
        display_name="Gemma 3 27B",
        total_params_b=27.0,
        num_experts=1,  # dense model, no expert routing
        active_experts=1,
        model_size_gb_q4=16.0,
        expert_size_gb_q4=16.0,
        context_length=131072,
        gating_type="none",
        hf_repo="unsloth/gemma-3-27b-it-GGUF",
        tags=["both"],
        description="Google Gemma 3 27B dense. Strong general-purpose model, 128K context. Not MoE.",
    ),
    "glm-5.1": MoEModel(
        name="glm-5.1",
        display_name="GLM-5.1",
        total_params_b=744.0,
        num_experts=256,  # estimated, MoE with shared experts
        active_experts=40,  # ~40B active per token
        model_size_gb_q4=434.0,
        expert_size_gb_q4=1.5,
        context_length=131072,
        gating_type="shared",
        hf_repo="unsloth/GLM-5.1-GGUF",
        tags=["both"],
        description="744B MoE, ~40B active. Frontier chat and code. Being retired from Ollama Cloud July 15.",
    ),
    "deepseek-v4-flash": MoEModel(
        name="deepseek-v4-flash",
        display_name="DeepSeek-V4-Flash",
        total_params_b=284.0,
        num_experts=256,  # estimated, MoE with shared experts
        active_experts=21,
        model_size_gb_q4=163.0,
        expert_size_gb_q4=0.6,
        context_length=1048576,
        gating_type="shared",
        hf_repo="teamblobfish/DeepSeek-V4-Flash-GGUF",
        tags=["code", "chat"],
        description="284B MoE, 21B active. Agentic with tool-use. 1M context. Fast inference.",
    ),
    "deepseek-v4-pro": MoEModel(
        name="deepseek-v4-pro",
        display_name="DeepSeek-V4-Pro",
        total_params_b=1600.0,
        num_experts=256,  # estimated, MoE with shared experts
        active_experts=49,
        model_size_gb_q4=889.0,
        expert_size_gb_q4=3.5,
        context_length=1048576,
        gating_type="shared",
        hf_repo="teamblobfish/DeepSeek-V4-Pro-GGUF",
        tags=["code", "chat"],
        description="1.6T MoE, 49B active. Frontier coding and agentic. 1M context. The big one.",
    ),
    "qwen3-coder-480b": MoEModel(
        name="qwen3-coder-480b",
        display_name="Qwen3-Coder 480B",
        total_params_b=480.0,
        num_experts=128,  # estimated
        active_experts=35,
        model_size_gb_q4=257.0,
        expert_size_gb_q4=1.0,
        context_length=262144,
        gating_type="top_k",
        hf_repo="unsloth/Qwen3-Coder-480B-A35B-Instruct-GGUF",
        tags=["code"],
        description="480B MoE, 35B active. Specialized coding model. 262K context.",
    ),
    "qwen3-coder-next": MoEModel(
        name="qwen3-coder-next",
        display_name="Qwen3-Coder Next",
        total_params_b=15.0,
        num_experts=1,  # dense or light MoE
        active_experts=15,
        model_size_gb_q4=45.0,
        expert_size_gb_q4=45.0,
        context_length=262144,
        gating_type="none",
        hf_repo="Qwen/Qwen3-Coder-Next-GGUF",
        tags=["code"],
        description="Next-gen Qwen coding model. Agentic, tool-using. Moderate size.",
    ),
    "devstral-small": MoEModel(
        name="devstral-small",
        display_name="Devstral-Small 2507",
        total_params_b=24.0,
        num_experts=1,  # dense model
        active_experts=1,
        model_size_gb_q4=13.3,
        expert_size_gb_q4=13.3,
        context_length=131072,
        gating_type="none",
        hf_repo="mistralai/Devstral-Small-2507_gguf",
        tags=["code"],
        description="24B dense agentic coding model. Mistral. Fits on any GPU.",
    ),
    "qwen3-coder-30b": MoEModel(
        name="qwen3-coder-30b",
        display_name="Qwen3-Coder 30B A3B",
        total_params_b=30.0,
        num_experts=128,  # estimated
        active_experts=3,
        model_size_gb_q4=17.3,
        expert_size_gb_q4=0.3,
        context_length=262144,
        gating_type="top_k",
        hf_repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        tags=["code"],
        description="30B MoE, 3B active. Very efficient coding model. Fits on any GPU.",
    ),
}


def get_model(name: str) -> MoEModel:
    """Look up a model by name."""
    if name not in MODELS:
        raise ValueError(f"Unknown model: {name}. Available: {list(MODELS.keys())}")
    return MODELS[name]


def list_models(use: str | None = None) -> list[MoEModel]:
    """Return supported models, optionally filtered by use case.

    Args:
        use: Filter by use case ('chat', 'code', or None for all)
    """
    models = list(MODELS.values())
    if use:
        models = [m for m in models if m.supports_use(use)]
    return models


def can_host_expert(model: MoEModel, available_vram_gb: float) -> bool:
    """Check if a node has enough VRAM to host an expert for this model."""
    return available_vram_gb >= model.min_vram_per_expert_gb