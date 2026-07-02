# Sawyer Agent Integration

How to configure any agent framework to use Sawyer's distributed MoE inference as its LLM backend.

---

## How It Works

Sawyer is a **router**, not a model runner. It sits between your agent frameworks and the actual inference backends, routing requests intelligently.

```
┌───────────────────────────┐
│  Agent Frameworks          │
│  (Hermes, Cursor, Aider,  │
│   Continue, LangChain...)  │
└────────────┬──────────────┘
             │  OpenAI-compatible API
             │  http://localhost:8000/v1
             ▼
┌────────────────────────────┐
│  Sawyer Client (:8000)     │
│  sawyer chat               │
│                            │
│  Routes to first available:│
│  1. Sawyer network (MoE)   │
│  2. llama.cpp server (:8444)│
│  3. Ollama (:11434)        │
│  4. Error if nothing runs  │
└────────────┬──────────────┘
             │
     ┌───────┼────────┐
     ▼       ▼        ▼
┌────────┐ ┌──────┐ ┌─────────────┐
│Sawyer  │ │llama │ │  Ollama     │
│Network │ │.cpp  │ │  (:11434)   │
│(MoE)   │ │(:8444│ │             │
│        │ │)     │ │  GPU: CUDA  │
│        │ │      │ │  GPU: Vulkan│
│        │ │      │ │  GPU: ROCm  │
│        │ │      │ │  CPU: AVX2  │
└────────┘ └──────┘ └─────────────┘
```

**Key point:** Agent frameworks never talk to Ollama or llama.cpp directly. They talk to Sawyer on port 8000, and Sawyer forwards to whichever backend is running. You don't need to configure Ollama's port or llama.cpp's port in your agent — just point everything at `:8000`.

---

## Quick Reference

| Framework | Config Location | Key Settings |
|-----------|----------------|--------------|
| **Hermes** | `~/.hermes/config.yaml` | `custom_providers.sawyer` section |
| **OpenClaw** | `~/.openclaw/config.json` | `model.baseUrl` + `model.apiKey` |
| **Claude Code** | Environment vars | `OPENAI_BASE_URL` + `OPENAI_API_KEY` |
| **Cursor** | Settings > Models | Override OpenAI Base URL + API Key |
| **Continue** | `~/.continue/config.json` | `provider: "openai"` + `apiBase` |
| **Aider** | Environment vars | `OPENAI_API_BASE` + `OPENAI_API_KEY` |
| **Cline** | VS Code extension settings | API Provider > OpenAI Compatible |
| **LangChain** | Python code | `ChatOpenAI(base_url=...)` |
| **LlamaIndex** | Python code | `OpenAI(api_base=...)` |
| **CrewAI** | Environment vars | `OPENAI_API_BASE` + `OPENAI_API_KEY` |
| **AutoGPT** | `.env` file | `OPENAI_API_BASE_URL` + `OPENAI_API_KEY` |
| **Any OpenAI-compatible** | Provider settings | Base URL: `http://localhost:8000/v1` |

All integrations use the same endpoint: `http://localhost:8000/v1` (local) or `https://sawyer.infill.systems/v1` (remote).

---

## Inference Backends

Sawyer's `LocalInference` class tries backends in priority order. At least one must be running for local inference to work.

### Backend Priority

| Priority | Backend | Default Port | Setup |
|----------|---------|-------------|-------|
| 1 | Sawyer network (MoE) | N/A (distributed) | `sawyer serve` |
| 2 | llama.cpp server | `:8444` | `llama-server --port 8444 -m model.gguf` |
| 3 | Ollama | `:11434` | `ollama serve` |

If none are available, Sawyer returns HTTP 503 with an actionable error message.

### Ollama (Most Common)

Ollama is the easiest backend to set up and supports the widest range of hardware:

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull and run a model
ollama pull llama3
ollama serve    # starts on :11434

# Verify
curl http://localhost:11434/api/tags
```

**Hardware acceleration:**
- **NVIDIA GPU (CUDA):** Ollama auto-detects and uses CUDA. No extra config needed.
- **AMD GPU (ROCm):** Ollama auto-detects ROCm on Linux. On Windows, set `OLLAMA_LLM_LIBRARY=rocm`.
- **Apple Metal (M-series):** Ollama uses Metal automatically on macOS.
- **Vulkan (Intel Arc, older AMD, mobile GPUs):** Set `OLLAMA_LLM_LIBRARY=vulkan` to force Vulkan rendering. Works on Windows and Linux.
- **CPU only:** Ollama falls back to CPU if no GPU is detected. Works but slow for large models.

**Vulkan setup:**
```bash
# Force Vulkan rendering (for Intel Arc, AMD without ROCm, etc.)
set OLLAMA_LLM_LIBRARY=vulkan   # Windows
export OLLAMA_LLM_LIBRARY=vulkan  # Linux/macOS

# Then start Ollama normally
ollama serve
```

**Windows GPU notes:**
- CUDA: Install NVIDIA CUDA toolkit, Ollama detects automatically
- Vulkan: Works with Intel Arc, AMD GPUs that lack ROCm support
- DirectML: Not supported by Ollama; use Vulkan instead

### llama.cpp Server

For direct llama.cpp usage without Ollama:

```bash
# Build llama.cpp with GPU support (CUDA example)
cmake -B build -DLLAMA_CUDA=ON && cmake --build build --config Release

# Start the server on port 8444
./build/bin/llama-server --port 8444 -m ~/models/mixtral-8x7b-instruct-q4_K_M.gguf

# Vulkan build (for Intel Arc, AMD, etc.)
cmake -B build -DLLAMA_VULKAN=ON && cmake --build build --config Release
./build/bin/llama-server --port 8444 -m model.gguf

# Metal (macOS — built by default on Apple Silicon)
cmake -B build -DLLAMA_METAL=ON && cmake --build build --config Release
./build/bin/llama-server --port 8444 -m model.gguf
```

Sawyer will try llama.cpp on `:8444` before falling back to Ollama on `:11434`.

### LM Studio

LM Studio runs a local model server with an OpenAI-compatible endpoint. To use it with Sawyer:

**Option A: Sawyer as proxy (recommended)** — Start LM Studio on its default port, then Sawyer routes to it:

1. Start LM Studio, load a model, start the local server (default `:1234`)
2. Sawyer's LocalInference doesn't auto-detect `:1234`, so set the llama.cpp URL:
   ```
   # Set environment variable to point Sawyer at LM Studio
   SAWYER_LLAMA_URL=http://localhost:1234/v1 sawyer chat
   ```

**Option B: Bypass Sawyer** — Point your agent directly at LM Studio:
```
http://localhost:1234/v1
```
This works but you lose Sawyer's network routing and model management.

### vLLM

For production-grade inference on NVIDIA GPUs:

```bash
# Install vLLM
pip install vllm

# Start vLLM server on port 8444
python -m vllm.entrypoints.openai.api_server \
  --model mistralai/Mixtral-8x7B-Instruct-v0.1 \
  --port 8444 \
  --tensor-parallel-size 2  # for multi-GPU
```

Sawyer will detect vLLM on `:8444` the same as llama.cpp (both speak OpenAI-compatible API).

**Note:** vLLM requires NVIDIA GPUs with CUDA. It does not support Vulkan, ROCm, or CPU-only inference.

### Inference Backend Config

Sawyer's `SawyerConfig` supports an `inference_backend` setting:

```python
# In sawyer/config.py or environment
inference_backend = "auto"    # auto, vllm, llama_cpp
# auto = try llama.cpp then Ollama (current default)
# vllm = only try vLLM on :8444
# llama_cpp = only try llama.cpp on :8444
```

To override via CLI:
```bash
sawyer serve --backend vllm       # Force vLLM backend
sawyer serve --backend llama_cpp  # Force llama.cpp backend
sawyer serve --backend auto       # Try all (default)
```

---

## Starting Sawyer

Before configuring agents, start the Sawyer client server:

```bash
# Start the consumer client (web UI + OpenAI API)
sawyer chat
# → Web UI at http://localhost:8000
# → OpenAI API at http://localhost:8000/v1/chat/completions

# Start with Ollama bridge (serve your local GPU to the network)
sawyer chat --ollama-bridge

# Start the provider node (serve expert inference to the network)
sawyer serve

# Start with a specific model (provider mode)
sawyer serve --offline --model mixtral-8x7b

# Custom host/port
sawyer chat --host 0.0.0.0 --port 8080
sawyer serve --port 9443
```

**Which command to use:**
- `sawyer chat` — Consumer mode. You want to use inference. Starts the web UI and OpenAI-compatible API at `:8000`.
- `sawyer serve` — Provider mode. You want to contribute GPU to the network and earn tokens.

**Verify it's running:**

```bash
curl http://localhost:8000/v1/models
# Returns: {"object":"list","data":[{"id":"sawyer",...},...]}

# Check which backends are detected
curl http://localhost:8000/v1/backends
# Returns: {"llama_cpp": false, "ollama": true, "mode": "available"}
```

---

## Model Name Mapping

Sawyer accepts these model IDs at the `/v1/chat/completions` endpoint:

| Model ID | Backend | Description |
|----------|---------|-------------|
| `sawyer` | Auto | Default routing — network first, local fallback |
| `mixtral-8x7b` | Ollama | Mixtral 8x7B MoE |
| `deepseek-v2-lite` | Ollama | DeepSeek V2 Lite |

Any model ID not in this list is passed through to the local inference backend (Ollama) as-is. If you have additional Ollama models installed, reference them by their Ollama model name:

```bash
# Works if you've pulled the model
ollama pull codellama
# Then in your agent:
model: "codellama"    # Sawyer passes "codellama" to Ollama
```

---

## Hermes Configuration

### Option 1: Config File (Recommended)

Edit `~/.hermes/config.yaml` (or the active profile's config):

```yaml
model:
  default: sawyer
  provider: custom:sawyer
  base_url: http://localhost:8000/v1
  api_key: sawyer-local   # Any non-empty string works for local Sawyer
  max_tokens: 16000

custom_providers:
  sawyer:
    name: Sawyer Distributed MoE
    base_url: http://localhost:8000/v1
    api_key: sawyer-local
    models:
      - id: sawyer
        name: Sawyer Default
        context_length: 32768
      - id: mixtral-8x7b
        name: Mixtral 8x7B
        context_length: 32768
      - id: deepseek-v2-lite
        name: DeepSeek V2 Lite
        context_length: 32768
```

Then restart Hermes (`/reset` in CLI, or `hermes` for a fresh session).

### Option 2: Environment Variables

```bash
export HERMES_MODEL_DEFAULT=sawyer
export HERMES_MODEL_PROVIDER=custom:sawyer
export HERMES_MODEL_BASE_URL=http://localhost:8000/v1
export HERMES_MODEL_API_KEY=sawyer-local
```

### Option 3: CLI Flag

```bash
hermes chat -m sawyer --provider custom:sawyer
```

### Option 4: Remote Sawyer Instance

For a Sawyer server running on a remote machine (e.g., InFill production):

```yaml
model:
  default: sawyer
  provider: custom:sawyer
  base_url: https://sawyer.infill.systems/v1
  api_key: ${SAWYER_API_KEY}  # Set in .env
  max_tokens: 16000

custom_providers:
  sawyer:
    name: Sawyer Distributed MoE
    base_url: https://sawyer.infill.systems/v1
    api_key: ${SAWYER_API_KEY}
    models:
      - id: sawyer
        name: Sawyer Default
        context_length: 32768
```

### Switching Back to Cloud Providers

Hermes can switch providers mid-conversation:

```
/model anthropic/claude-sonnet-4    # Switch to Anthropic
/model sawyer                       # Switch back to Sawyer
```

Or use the interactive model picker:

```
hermes model
```

---

## OpenClaw

OpenClaw uses an OpenAI-compatible interface. Configure Sawyer as a custom provider.

### Local Instance

In `~/.openclaw/config.json`:

```json
{
  "model": {
    "provider": "sawyer",
    "model": "sawyer",
    "baseUrl": "http://localhost:8000/v1",
    "apiKey": "sawyer-local"
  }
}
```

### Remote Instance

```json
{
  "model": {
    "provider": "sawyer",
    "model": "sawyer",
    "baseUrl": "https://sawyer.infill.systems/v1",
    "apiKey": "YOUR_SAWYER_API_KEY"
  }
}
```

### Environment Variable

```bash
export OPENCLAW_MODEL_BASE_URL=http://localhost:8000/v1
export OPENCLAW_MODEL=sawyer
export OPENCLAW_API_KEY=sawyer-local
```

---

## Claude Code (Anthropic CLI)

Claude Code supports custom OpenAI-compatible endpoints via environment variables.

### Configuration

```bash
# Point Claude Code at Sawyer
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=sawyer-local

# For remote Sawyer
# export OPENAI_BASE_URL=https://sawyer.infill.systems/v1
# export OPENAI_API_KEY=$SAWYER_API_KEY
```

Then run Claude Code normally. When the `OPENAI_BASE_URL` is set, Claude Code routes OpenAI-format requests through Sawyer instead of OpenAI's servers.

### Per-Project Configuration

Create `.env` in your project root:

```
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sawyer-local
```

---

## Cursor IDE

Cursor supports custom OpenAI-compatible models in Settings.

### Configuration

1. Open Cursor Settings (Ctrl+, / Cmd+,)
2. Navigate to **Models** section
3. Under **OpenAI API Key**, enter `sawyer-local` (or your Sawyer API key for remote)
4. Click **Add model** and enter `sawyer` (or `mixtral-8x7b`, `deepseek-v2-lite`)
5. Under **Override OpenAI Base URL**, enter `http://localhost:8000/v1`

For remote Sawyer, use `https://sawyer.infill.systems/v1`.

### Via settings.json

Edit `~/.cursor/settings.json`:

```json
{
  "cursor.general.openaiApiKey": "sawyer-local",
  "cursor.general.openaiBaseUrl": "http://localhost:8000/v1",
  "cursor.general.models": [
    {
      "id": "sawyer",
      "name": "Sawyer Default"
    },
    {
      "id": "mixtral-8x7b",
      "name": "Mixtral 8x7B"
    }
  ]
}
```

---

## Continue (VS Code / JetBrains Extension)

Continue is an open-source AI code assistant that supports custom model providers.

### Configuration

Edit `~/.continue/config.json` (or `~/.continue/config.yaml`):

```json
{
  "models": [
    {
      "title": "Sawyer (Local)",
      "provider": "openai",
      "model": "sawyer",
      "apiBase": "http://localhost:8000/v1",
      "apiKey": "sawyer-local"
    },
    {
      "title": "Sawyer Mixtral",
      "provider": "openai",
      "model": "mixtral-8x7b",
      "apiBase": "http://localhost:8000/v1",
      "apiKey": "sawyer-local"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Sawyer Autocomplete",
    "provider": "openai",
    "model": "sawyer",
    "apiBase": "http://localhost:8000/v1",
    "apiKey": "sawyer-local"
  },
  "embeddingsProvider": {
    "provider": "openai",
    "model": "sawyer",
    "apiBase": "http://localhost:8000/v1",
    "apiKey": "sawyer-local"
  }
}
```

For remote Sawyer, replace `apiBase` with `https://sawyer.infill.systems/v1` and `apiKey` with your Sawyer API key.

---

## Aider

Aider is a CLI-based AI pair programmer that supports any OpenAI-compatible endpoint.

### Configuration

```bash
# Local Sawyer
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=sawyer-local

# Run aider with Sawyer
aider --model sawyer

# Or specify a different Sawyer model
aider --model mixtral-8x7b

# Remote Sawyer
# export OPENAI_API_BASE=https://sawyer.infill.systems/v1
# export OPENAI_API_KEY=$SAWYER_API_KEY
```

### Persistent Configuration

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias aider-sawyer='OPENAI_API_BASE=http://localhost:8000/v1 OPENAI_API_KEY=sawyer-local aider --model sawyer'
```

---

## Cline (VS Code Extension)

Cline supports custom OpenAI-compatible endpoints.

### Configuration

1. Open Cline sidebar in VS Code
2. Click the gear icon (Settings)
3. Set **API Provider** to "OpenAI Compatible"
4. Set **Base URL** to `http://localhost:8000/v1`
5. Set **API Key** to `sawyer-local` (or your Sawyer API key)
6. Set **Model ID** to `sawyer` (or `mixtral-8x7b`, `deepseek-v2-lite`)

For remote Sawyer, use `https://sawyer.infill.systems/v1` as the Base URL.

---

## Copilot Alternatives (Supermaven, Tabnine, etc.)

Any tool that accepts a custom OpenAI API endpoint can use Sawyer. The general pattern:

1. Find the tool's model/provider settings
2. Set the **API Base URL** / **Endpoint** to `http://localhost:8000/v1`
3. Set the **API Key** to any non-empty string for local (`sawyer-local`)
4. Set the **Model** to `sawyer`, `mixtral-8x7b`, or `deepseek-v2-lite`

### Supermaven

Supermaven Pro supports custom OpenAI endpoints. In Settings, set the OpenAI-compatible base URL to `http://localhost:8000/v1`.

### Tabnine

Tabnine Enterprise supports custom model endpoints. Contact Tabnine for configuration details; the endpoint format is the same OpenAI-compatible interface Sawyer provides.

---

## LangChain / LlamaIndex (Python SDK)

For developers building agent pipelines with LangChain or LlamaIndex, Sawyer integrates directly.

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="sawyer",
    base_url="http://localhost:8000/v1",
    api_key="sawyer-local",
    # For remote: base_url="https://sawyer.infill.systems/v1",
    #              api_key=os.environ["SAWYER_API_KEY"],
)

response = llm.invoke("What is 2+2?")
print(response.content)
```

### LlamaIndex

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    model="sawyer",
    api_base="http://localhost:8000/v1",
    api_key="sawyer-local",
)

response = llm.complete("What is 2+2?")
print(response.text)
```

### With Tool Calling

```python
from langchain_openai import ChatOpenAI
from langchain.tools import tool

@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

llm = ChatOpenAI(
    model="sawyer",
    base_url="http://localhost:8000/v1",
    api_key="sawyer-local",
).bind_tools([add])

response = llm.invoke("What is 2+2?")
# Tool call support depends on the underlying model
```

---

## AutoGPT / CrewAI / Agent Frameworks

Any Python agent framework that uses OpenAI-compatible APIs can use Sawyer.

### AutoGPT

In `.env`:

```bash
OPENAI_API_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sawyer-local
```

### CrewAI

```python
from crewai import Agent, Task, Crew

# CrewAI respects OPENAI_API_BASE environment variable
import os
os.environ["OPENAI_API_BASE"] = "http://localhost:8000/v1"
os.environ["OPENAI_API_KEY"] = "sawyer-local"

agent = Agent(
    role="Analyst",
    goal="Analyze data",
    backstory="Expert analyst",
    llm="sawyer",  # Model name
)
```

---

## Streaming Support

Sawyer's `/v1/chat/completions` endpoint supports streaming (`"stream": true`). All frameworks listed above (Hermes, OpenClaw, Claude Code, Cursor, Continue, Aider, Cline, LangChain, LlamaIndex, CrewAI, AutoGPT) handle Server-Sent Events (SSE) streaming natively when using OpenAI-compatible endpoints, so streaming responses work automatically.

```json
{
  "model": "sawyer",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

---

## Token Balance

Sawyer provides a `/v1/balance` endpoint for token accounting:

```bash
curl http://localhost:8000/v1/balance
# {"balance": 0, "mode": "local", "message": "Token accounting active when connected to Sawyer network"}
```

When connected to the Sawyer network, this reflects your subscription token balance.

---

## Tool Calls (Function Calling)

The `/v1/chat/completions` endpoint passes through tool call requests to the underlying model. Tool call support depends on the model backend:

- **Ollama models**: Some models support tool/function calling (check Ollama docs for model-specific support)
- **Sawyer network**: Supports tool calling for compatible models

Agent frameworks send tool schemas as part of the chat completion request, and Sawyer routes them through to the model. If the model doesn't support tool calling, it will respond with regular text instead. This applies to all integrations — Hermes, OpenClaw, LangChain, CrewAI, and any other framework that uses function calling.

---

## Fallback Behavior

### When Sawyer Is Down

Any framework configured to use Sawyer will receive a connection error. Mitigation options:

1. **Restart Sawyer**: `sawyer serve` to restart the local server
2. **Switch providers**: Most frameworks allow switching models/providers at runtime (Hermes: `/model`, Aider: `--model`, Cursor: Settings)
3. **Configure fallback**: Some frameworks support fallback providers (Hermes delegation fallback, Continue config fallback models)

### When No Inference Backend Is Running

Sawyer returns HTTP 503 with a helpful message:

```json
{
  "detail": {
    "message": "No inference backend is running.",
    "suggestions": [
      "Install Ollama and run: ollama run llama3",
      "Start a local model: sawyer serve --offline --model mixtral-8x7b",
      "Connect to the Sawyer network: sawyer serve"
    ]
  }
}
```

### Recommended Fallback Configuration (Hermes)

```yaml
# In config.yaml
model:
  default: sawyer
  provider: custom:sawyer

delegation:
  # Fallback for subagent tasks when Sawyer is unavailable
  provider: openrouter
  model: anthropic/claude-sonnet-4

# For auxiliary tasks (vision, compression)
auxiliary:
  vision:
    provider: openrouter
    model: google/gemini-2.0-flash
  compression:
    provider: openrouter
    model: anthropic/claude-haiku-4
```

This ensures:
- Main agent uses Sawyer for inference (cost savings)
- Subagents fall back to OpenRouter when Sawyer is unavailable
- Auxiliary tasks (vision, compression) use fast cloud models regardless

---

## Verification

After configuration, verify the integration works:

### Direct API Test (All Frameworks)

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sawyer",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "max_tokens": 50
  }'
```

Expected response:
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "sawyer",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "4"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
}
```

### Check Available Backends

```bash
curl http://localhost:8000/v1/backends
# {"llama_cpp": false, "ollama": true, "mode": "available"}
```

This tells you which inference backends Sawyer has detected. If both are `false`, start one:
```bash
ollama serve    # Starts Ollama on :11434
# or
llama-server --port 8444 -m model.gguf    # Starts llama.cpp on :8444
```

### Hermes

```bash
sawyer chat &
hermes chat -m sawyer -q "What is 2+2?"
hermes status
```

### OpenClaw

```bash
sawyer chat &
openclaw "What is 2+2?"
```

### Claude Code

```bash
sawyer chat &
OPENAI_BASE_URL=http://localhost:8000/v1 OPENAI_API_KEY=sawyer-local claude
```

### Cursor

1. Start Sawyer (`sawyer chat`)
2. Configure in Settings as described above
3. Open a new chat and type "What is 2+2?"

### Aider

```bash
sawyer chat &
OPENAI_API_BASE=http://localhost:8000/v1 OPENAI_API_KEY=sawyer-local aider --model sawyer
```

### Continue

1. Start Sawyer (`sawyer chat`)
2. Add Sawyer to `~/.continue/config.json` as described above
3. Open Continue sidebar in VS Code and select "Sawyer (Local)"

### LangChain (Python)

```bash
sawyer chat &
python -c "from langchain_openai import ChatOpenAI; print(ChatOpenAI(model='sawyer', base_url='http://localhost:8000/v1', api_key='sawyer-local').invoke('2+2=?'))"
```

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|---------|
| Connection refused on `:8000` | Sawyer client not running | `sawyer chat` to start the consumer server |
| 503 "No inference backend" | No model loaded | `ollama run llama3` or `sawyer serve --offline --model mixtral-8x7b` |
| 401 Unauthorized | API key mismatch | Use any non-empty string for local, real key for remote |
| Slow first response | Model loading on first request | First inference is slow (loading weights); subsequent requests are faster |
| Ollama not detected | Ollama not running on `:11434` | `ollama serve` to start Ollama daemon |
| llama.cpp not detected | llama-server not on `:8444` | Start with `llama-server --port 8444 -m model.gguf` |
| Vulkan errors in Ollama | Wrong rendering backend | Set `OLLAMA_LLM_LIBRARY=vulkan` for Intel Arc / AMD without ROCm |
| CUDA not used | Missing NVIDIA drivers | Install CUDA toolkit; Ollama auto-detects on Linux and Windows |
| Tool calls ignored | Model doesn't support function calling | Use a model that supports tool calls (check Ollama docs) |
| Context length exceeded | Request too long for model | Reduce input length or use a model with larger context |

---

## Cost Comparison

| Provider | Model | Cost per 1M tokens (approx) |
|----------|-------|---------------------------|
| Sawyer (local) | Any | Electricity only (~$0.00) |
| Sawyer (network) | Any | Subscriber pool rate |
| OpenRouter | Claude Sonnet 4 | ~$3/$15 |
| Anthropic | Claude Sonnet 4 | ~$3/$15 |
| OpenAI | GPT-4o | ~$2.50/$10 |

Using Sawyer for development and routine tasks can reduce API costs by 90%+ while maintaining quality through the distributed MoE network.