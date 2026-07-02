# Sawyer Model Library

Models available for Sawyer network serving. Raw GGUF from HuggingFace -- works with any inference engine (llama.cpp, Ollama, vLLM, Sawyer).

## Locally Archived

Models stored on the FreeAgent HDD (D:\models\).

| Model | Params | Active/Token | Quant | Size | Status | Local Path |
|-------|--------|-------------|-------|------|--------|-----------|
| gemma3-27b | 27B | 27B | UD-Q4_K_XL | 16 GB | Complete | `D:\models\gemma3-27b\gemma-3-27b-it-UD-Q4_K_XL.gguf` |
| GLM-5.1 | 744B | ~40B | UD-Q4_K_XL | 434 GB | Downloading | `D:\models\glm-5.1\UD-Q4_K_XL\` |
| DeepSeek-V4-Flash | 284B | 21B | Q4_K_M-XL | 163 GB | Queued | `D:\models\deepseek-v4-flash\Q4_K_M-XL\` |

**Drive:** 1.4 TB FreeAgent. ~1.2 TB free after current downloads finish.

## Download Queue

| Priority | Model | Quant | Size | HF Repo | Download Command |
|----------|-------|-------|------|---------|------------------|
| 1 | GLM-5.1 | UD-Q4_K_XL | 434 GB | `unsloth/GLM-5.1-GGUF` | `hf download unsloth/GLM-5.1-GGUF --include "UD-Q4_K_XL/*" --local-dir D:\models\glm-5.1` |
| 2 | DeepSeek-V4-Flash | Q4_K_M-XL | 163 GB | `teamblobfish/DeepSeek-V4-Flash-GGUF` | `hf download teamblobfish/DeepSeek-V4-Flash-GGUF --include "Q4_K_M-XL/*" --local-dir D:\models\deepseek-v4-flash` |
| 3 | Qwen3-Coder-480B | UD-Q4_K_XL | 257 GB | `unsloth/Qwen3-Coder-480B-A35B-Instruct-GGUF` | `hf download unsloth/Qwen3-Coder-480B-A35B-Instruct-GGUF --include "UD-Q4_K_XL/*" --local-dir D:\models\qwen3-coder-480b` |
| 4 | Qwen3-Coder-Next | Q4_K_M | 45 GB | `Qwen/Qwen3-Coder-Next-GGUF` | `hf download Qwen/Qwen3-Coder-Next-GGUF --include "Q4_K_M/*" --local-dir D:\models\qwen3-coder-next` |
| 5 | Devstral-Small-2507 | Q4_K_M | 13 GB | `mistralai/Devstral-Small-2507_gguf` | `hf download mistralai/Devstral-Small-2507_gguf --include "Devstral-Small-2507-Q4_K_M.gguf" --local-dir D:\models\devstral-small-2507` |
| 6 | Qwen3-Coder-30B-A3B | Q4_K_M | 17 GB | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | `hf download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include "Q4_K_M/*" --local-dir D:\models\qwen3-coder-30b-a3b` |

**Total queue: 929 GB.** With gemma3-27b (16 GB) and current downloads, total archive is ~945 GB on a 1.4 TB drive.

## Full Model Reference

All models in the Sawyer registry with download commands for self-hosting.

### gemma3-27b-it (27B dense)

General-purpose chat and code. Not MoE, but strong baseline.

```bash
hf download unsloth/gemma-3-27b-it-GGUF --include "UD-Q4_K_XL/*" --local-dir D:\models\gemma3-27b
```

| Quant | Size | Quality |
|-------|------|---------|
| UD-Q4_K_XL | 16 GB | Good (recommended) |
| UD-Q5_K_XL | 18 GB | Better |
| Q8_0 | 27 GB | Lossless |

### GLM-5.1 (744B MoE, ~40B active)

Frontier MoE model. Being retired from Ollama Cloud July 15, 2026. This is the priority archive.

```bash
hf download unsloth/GLM-5.1-GGUF --include "UD-Q4_K_XL/*" --local-dir D:\models\glm-5.1
```

| Quant | Size | Shards | Quality |
|-------|------|--------|---------|
| UD-Q4_K_XL | 434 GB | 11 | Good (recommended) |
| UD-Q3_K_XL | 317 GB | 8 | Acceptable |
| UD-Q2_K_XL | 235 GB | 7 | Budget only |

### DeepSeek-V4-Flash (284B MoE, 21B active)

Agentic MoE with tool-use template. 1M context. Smaller and faster than V4-Pro.

```bash
hf download teamblobfish/DeepSeek-V4-Flash-GGUF --include "Q4_K_M-XL/*" --local-dir D:\models\deepseek-v4-flash
```

| Quant | Size | Shards | Quality |
|-------|------|--------|---------|
| Q4_K_M-XL | 163 GB | 4 | Good (recommended) |
| Q2_K-XL | 100 GB | 3 | Acceptable |

### DeepSeek-V4-Pro (1.6T MoE, 49B active)

The frontier. Strongest coding/agentic model available in GGUF. 1M context. Needs dedicated drive or second HDD.

```bash
hf download teamblobfish/DeepSeek-V4-Pro-GGUF --include "Q4_K_M-XL/*" --local-dir D:\models\deepseek-v4-pro
```

| Quant | Size | Shards | Quality |
|-------|------|--------|---------|
| Q4_K_M-XL | 889 GB | 21 | Good (recommended) |
| Q2_K-XL | 535 GB | 13 | Acceptable, quality loss on 1.6T |

### Qwen3-Coder-480B-A35B (480B MoE, 35B active)

Specialized coding MoE. 262K context. Apache-2.0 license.

```bash
hf download unsloth/Qwen3-Coder-480B-A35B-Instruct-GGUF --include "UD-Q4_K_XL/*" --local-dir D:\models\qwen3-coder-480b
```

| Quant | Size | Shards | Quality |
|-------|------|--------|---------|
| UD-Q4_K_XL | 257 GB | 6 | Good (recommended) |
| Q4_K_M | 270 GB | 6 | Good |
| Q2_K | 163 GB | 4 | Acceptable |

### Qwen3-Coder-Next (~15B)

Next-gen agentic coding model. Moderate size, strong capabilities.

```bash
hf download Qwen/Qwen3-Coder-Next-GGUF --include "Q4_K_M/*" --local-dir D:\models\qwen3-coder-next
```

| Quant | Size | Shards | Quality |
|-------|------|--------|---------|
| Q4_K_M | 45 GB | 4 | Good (recommended) |
| Q6_K | 61 GB | 4 | Near-lossless |

### Devstral-Small-2507 (24B dense)

Mistral agentic coding model. Fits on any GPU. Single file, fast download.

```bash
hf download mistralai/Devstral-Small-2507_gguf --include "Devstral-Small-2507-Q4_K_M.gguf" --local-dir D:\models\devstral-small-2507
```

| Quant | Size | Quality |
|-------|------|---------|
| Q4_K_M | 13 GB | Good (recommended) |
| Q5_K_M | 16 GB | Better |

### Qwen3-Coder-30B-A3B (30B MoE, 3B active)

Very efficient coding MoE. 3B active params means it runs on anything.

```bash
hf download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include "Q4_K_M/*" --local-dir D:\models\qwen3-coder-30b-a3b
```

| Quant | Size | Quality |
|-------|------|---------|
| Q4_K_M | 17 GB | Good (recommended) |
| Q5_K_M | 20 GB | Better |

## Running Models

### With Ollama (local testing)

```powershell
# Set model directory to external HDD
$env:OLLAMA_MODELS = "D:\ollama-models"

# Pull small models directly
ollama pull gemma3:27b

# For large GGUF, create a Modelfile pointing to the local file
# (Ollama can import raw GGUF)
```

### With llama.cpp (direct)

```bash
# Single-file models
llama-cli -m D:\models\gemma3-27b\gemma-3-27b-it-UD-Q4_K_XL.gguf

# Multi-shard models (point to first shard)
llama-cli -m D:\models\glm-5.1\UD-Q4_K_XL\GLM-5.1-UD-Q4_K_XL-00001-of-00011.gguf
```

### With Sawyer (network serving)

```bash
# Start a node hosting a specific model
python -m sawyer serve --model glm-5.1 --weights D:\models\glm-5.1\UD-Q4_K_XL

# Start the consumer client
python -m sawyer chat
```

## Notes

- **Use `hf download`, not `huggingface-cli download`** (deprecated as of huggingface_hub 1.21+).
- **Use `--include`** to download only the quantization you need. Full repos are multiple TB.
- **Raw GGUF only.** Ollama blob format locks you into Ollama's runtime. Sawyer needs portable files.
- **SHA-256 verification** is automatic with `hf download`.
- **Resumable.** If a download is interrupted, re-run the same command to resume.