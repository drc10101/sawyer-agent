"""Sawyer Consumer Client — the user-facing inference gateway.

This is what the person with the 8GB laptop runs. It provides:

1. An OpenAI-compatible API at /v1/chat/completions so any existing tool
   (curl, OpenAI SDK, Ollama clients) can use it without modification.

2. A web chat UI at / so users can just open localhost:8000 and start chatting.

3. A local inference fallback — if no Sawyer network is available, it can
   fall back to a local model (llama.cpp, Ollama, LM Studio, vLLM) for
   basic inference.

4. Dynamic model discovery — queries Ollama /api/tags and llama.cpp /v1/models
   at startup and on refresh, so agents always see what's actually available.

The whole point: cheaper inference than OpenAI/Anthropic, using distributed
MoE when available, local fallback when not.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from sawyer.config import SawyerConfig
from sawyer.client.faq_html import FAQ_HTML

logger = logging.getLogger(__name__)


# ── Data Models ─────────────────────────────────────────────────────


@dataclass
class InferenceResult:
    """Result from an inference request."""

    text: str
    thinking: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    finish_reason: str = "stop"
    cost_tokens: int = 0  # tokens deducted from account


@dataclass
class DiscoveredModel:
    """A model discovered from a local backend."""

    id: str
    backend: str  # "ollama", "llama_cpp", "vllm", "lm_studio"
    owned_by: str = "local"
    context_length: int | None = None
    details: dict = field(default_factory=dict)


# ── Local Inference Fallback ───────────────────────────────────────


class LocalInference:
    """Fallback to local inference when no Sawyer network is available.

    Tries, in order of priority:
    1. Sawyer network (distributed MoE) — not yet implemented
    2. Google Gemini API (if API key is configured) — cloud fallback
    3. llama.cpp server / vLLM / LM Studio (OpenAI-compatible on configured port)
    4. Ollama (native API on configured port)

    For Gemini models (names starting with "gemini"), Gemini is tried first.
    When no local models are available and a Gemini API key is configured,
    Gemini is used as a cloud fallback.

    Supports:
    - Dynamic model discovery from each backend (including Gemini)
    - Thinking/reasoning models (extracts content from 'thinking' field)
    - Streaming via SSE for Ollama, OpenAI-compatible, and Gemini backends
    - Configurable backend URLs via SawyerConfig
    - Proper error logging instead of silent swallowing
    """

    # Default model name mappings: Sawyer name -> backend name
    DEFAULT_MODEL_MAP = {
        "mixtral-8x7b": "mixtral",
        "deepseek-v2-lite": "deepseek-v2",
    }

    def __init__(self, config: SawyerConfig | None = None) -> None:
        self.config = config or SawyerConfig()
        # Configurable backend URLs — can be overridden via env vars
        self._ollama_url = (
            os.environ.get("SAWYER_OLLAMA_URL", "")
            or getattr(self.config, "ollama_url", "")
            or "http://localhost:11434"
        )
        self._llama_url = (
            os.environ.get("SAWYER_LLAMA_URL", "")
            or getattr(self.config, "llama_url", "")
            or "http://localhost:8444"
        )
        self._lm_studio_url = (
            os.environ.get("SAWYER_LM_STUDIO_URL", "")
            or getattr(self.config, "lm_studio_url", "")
            or "http://localhost:1234"
        )
        self._vllm_url = (
            os.environ.get("SAWYER_VLLM_URL", "")
            or getattr(self.config, "vllm_url", "")
            or "http://localhost:8001"
        )
        self._gemini_api_key = (
            os.environ.get("SAWYER_GEMINI_API_KEY", "")
            or getattr(self.config, "gemini_api_key", "")
            or ""
        )
        self._gemini_url = "https://generativelanguage.googleapis.com/v1beta"
        self._discovered_models: list[DiscoveredModel] = []
        self._last_discovery_time: float = 0.0
        self._discovery_ttl: float = 300.0  # Refresh every 5 minutes

    # ── Model Discovery ──────────────────────────────────────────

    def discover_models(self, force: bool = False) -> list[DiscoveredModel]:
        """Query all backends for available models.

        Results are cached for 5 minutes. Use force=True to refresh immediately.
        Probes all backends concurrently for fast discovery (~1s vs ~12s sequential).
        """
        now = time.time()
        if (
            not force
            and self._discovered_models
            and (now - self._last_discovery_time) < self._discovery_ttl
        ):
            return self._discovered_models

        models: list[DiscoveredModel] = []
        logger.info("Discovering models from local backends...")

        # Probe all backends concurrently
        from concurrent.futures import ThreadPoolExecutor, as_completed

        backend_tasks = {
            "gemini": (self._discover_gemini_models,),
            "ollama": (self._discover_ollama_models,),
            "llama_cpp": (self._discover_openai_compatible_models, "llama_cpp", self._llama_url),
            "vllm": (self._discover_openai_compatible_models, "vllm", self._vllm_url),
            "lm_studio": (self._discover_openai_compatible_models, "lm_studio", self._lm_studio_url),
        }

        def _run_task(name, task_tuple):
            if name in ("ollama", "gemini"):
                return name, task_tuple[0]()
            else:
                func, bname, url = task_tuple
                return name, func(bname, url)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_run_task, name, task): name
                for name, task in backend_tasks.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    backend_name, discovered = future.result(timeout=5)
                except Exception as exc:
                    logger.debug(f"  {name}: discovery failed ({exc})")
                    continue
                if discovered is not None:
                    models.extend(discovered)
                    logger.info(f"  {backend_name}: {len(discovered)} models found")
                else:
                    logger.info(f"  {name}: not available")

        self._discovered_models = models
        self._last_discovery_time = now
        logger.info(f"Model discovery complete: {len(models)} total models available")
        return models

    def _discover_ollama_models(self) -> list[DiscoveredModel] | None:
        """Query Ollama /api/tags for available models."""
        try:
            import httpx

            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self._ollama_url}/api/tags")
                if resp.status_code != 200:
                    logger.debug(f"Ollama /api/tags returned {resp.status_code}")
                    return None

                data = resp.json()
                models = []
                for model_info in data.get("models", []):
                    name = model_info.get("name", "")
                    if not name:
                        continue
                    details = model_info.get("details", {})
                    models.append(
                        DiscoveredModel(
                            id=name,  # Use full name with tag for routing
                            backend="ollama",
                            owned_by="local",
                            context_length=details.get("context_length"),
                            details=details,
                        )
                    )
                return models if models else None
        except Exception as e:
            logger.debug(f"Ollama discovery failed: {e}")
            return None

    def _discover_openai_compatible_models(
        self, backend_name: str, url: str
    ) -> list[DiscoveredModel] | None:
        """Query OpenAI-compatible /v1/models endpoint (llama.cpp, vLLM, LM Studio)."""
        try:
            import httpx

            with httpx.Client(timeout=httpx.Timeout(1.0, connect=1.0)) as client:
                resp = client.get(f"{url}/v1/models")
                if resp.status_code != 200:
                    return None

                data = resp.json()
                models = []
                for model_info in data.get("data", []):
                    model_id = model_info.get("id", "")
                    if not model_id:
                        continue
                    models.append(
                        DiscoveredModel(
                            id=model_id,
                            backend=backend_name,
                            owned_by="local",
                            context_length=model_info.get("context_length"),
                        )
                    )
                return models if models else None
        except Exception as e:
            logger.debug(f"{backend_name} discovery failed: {e}")
            return None

    def _discover_gemini_models(self) -> list[DiscoveredModel] | None:
        """Query Gemini API for available models.

        Requires a Gemini API key (set via SAWYER_GEMINI_API_KEY env var
        or config.gemini_api_key).
        """
        if not self._gemini_api_key:
            logger.debug("Gemini: no API key configured, skipping discovery")
            return None

        try:
            import httpx

            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = client.get(
                    f"{self._gemini_url}/models",
                    params={"key": self._gemini_api_key},
                )
                if resp.status_code != 200:
                    logger.debug(f"Gemini /models returned {resp.status_code}: {resp.text[:200]}")
                    return None

                data = resp.json()
                models = []
                for model_info in data.get("models", []):
                    model_id = model_info.get("name", "")
                    if not model_id:
                        continue
                    # Gemini model names are like "models/gemini-1.5-pro"
                    # Strip "models/" prefix for display/routing
                    display_id = model_id.split("models/")[-1] if "models/" in model_id else model_id
                    # Only include generative models (skip embedding, etc.)
                    supported_methods = model_info.get("supportedGenerationMethods", [])
                    if "generateContent" not in supported_methods:
                        continue
                    models.append(
                        DiscoveredModel(
                            id=display_id,
                            backend="gemini",
                            owned_by="google",
                            context_length=model_info.get("inputTokenLimit"),
                            details={
                                "output_token_limit": model_info.get("outputTokenLimit"),
                                "supported_methods": supported_methods,
                            },
                        )
                    )
                return models if models else None
        except Exception as e:
            logger.debug(f"Gemini discovery failed: {e}")
            return None

    def _resolve_model(self, model: str) -> tuple[str, str | None]:
        """Resolve a Sawyer model name to (backend_model_name, backend_type).

        Returns (resolved_name, backend_hint).
        If backend_hint is None, try all backends.
        If backend_hint is set, try that backend first.
        """
        # If the model name exactly matches a discovered model, use it directly
        for dm in self._discovered_models:
            if dm.id == model or dm.id.split(":")[0] == model:
                return dm.id, dm.backend

        # Apply the Sawyer name mapping
        resolved = self.DEFAULT_MODEL_MAP.get(model, model)

        # Check if the resolved name matches a discovered model
        for dm in self._discovered_models:
            if dm.id == resolved or dm.id.split(":")[0] == resolved:
                return dm.id, dm.backend

        # If "sawyer" (default) and we have discovered models, pick the first one
        if model in ("sawyer", "") and self._discovered_models:
            first = self._discovered_models[0]
            logger.info(f"Default model 'sawyer' resolved to '{first.id}' on {first.backend}")
            return first.id, first.backend

        # If "llama3" (old fallback) and we have discovered models, try to find it
        if model in ("llama3", "") and self._discovered_models:
            # Look for any llama model
            for dm in self._discovered_models:
                if "llama" in dm.id.lower():
                    return dm.id, dm.backend
            # Fall back to first available
            first = self._discovered_models[0]
            logger.info(
                f"Model '{model}' not found, " f"falling back to '{first.id}' on {first.backend}"
            )
            return first.id, first.backend

        # No match found — return as-is and let the backend try
        return resolved, None

    # ── Inference ─────────────────────────────────────────────────

    def infer(
        self,
        prompt: str,
        model: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> InferenceResult:
        """Try local inference backends in order.

        Model resolution:
        1. If the model name matches a discovered model, route directly
        2. Apply Sawyer name mapping (e.g., "mixtral-8x7b" -> "mixtral")
        3. If "sawyer" or empty, use the first available discovered model
        4. If no match, pass through to backends and let them handle it
        """
        # Ensure models are discovered before routing
        if not self._discovered_models:
            self.discover_models()

        resolved_model, backend_hint = self._resolve_model(model)

        # If we know which backend has this model, try it first
        if backend_hint == "gemini":
            result = self._try_gemini(prompt, resolved_model, max_tokens, temperature)
            if result:
                return result
            raise RuntimeError(
                f"Gemini model '{resolved_model}' failed. "
                "Check your API key in Settings."
            )
        elif backend_hint == "ollama":
            result = self._try_ollama(prompt, resolved_model, max_tokens, temperature)
            if result:
                return result
            # Fall through to other backends
            result = self._try_llama(prompt, resolved_model, max_tokens, temperature, top_p)
            if result:
                return result
        elif backend_hint in ("llama_cpp", "vllm", "lm_studio"):
            result = self._try_llama(prompt, resolved_model, max_tokens, temperature, top_p)
            if result:
                return result
            # Fall through to Ollama
            result = self._try_ollama(prompt, resolved_model, max_tokens, temperature)
            if result:
                return result
        else:
            # No hint — try all backends in priority order
            # If model name starts with "gemini", try Gemini first
            if resolved_model.lower().startswith("gemini"):
                result = self._try_gemini(prompt, resolved_model, max_tokens, temperature)
                if result:
                    return result
                # User explicitly chose a Gemini model — don't silently fall back
                raise RuntimeError(
                    f"Gemini model '{resolved_model}' failed. "
                    "Check your API key in Settings."
                )
            result = self._try_llama(prompt, resolved_model, max_tokens, temperature, top_p)
            if result:
                return result
            result = self._try_ollama(prompt, resolved_model, max_tokens, temperature)
            if result:
                return result
            # Fallback to Gemini if no local backends have the model
            try:
                result = self._try_gemini(prompt, resolved_model, max_tokens, temperature)
                if result:
                    return result
            except RuntimeError:
                pass  # Gemini fallback failed, continue to error

        # Build actionable error message
        available = self.is_available()
        available_backends = [k for k, v in available.items() if v]
        discovered_ids = [m.id for m in self._discovered_models]

        msg = "No inference backend available."
        if discovered_ids:
            msg += f"\n\nAvailable models: {', '.join(discovered_ids[:10])}"
            msg += f"\nRequested model '{model}' resolved to '{resolved_model}'."
        else:
            msg += "\n\nNo models were found on any backend."
            msg += "\n  - Install Ollama: https://ollama.com then run: ollama pull llama3"
            msg += "\n  - Or start llama.cpp: llama-server --port 8444 -m model.gguf"
            msg += "\n  - Or connect to the Sawyer network: sawyer serve"

        if available_backends:
            msg += f"\n\nBackends online but no models loaded: {', '.join(available_backends)}"
            msg += "\nPull a model: ollama pull llama3"

        raise RuntimeError(msg)

    def _try_ollama(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult | None:
        """Try Ollama at the configured URL (default localhost:11434)."""
        try:
            import httpx

            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": temperature,
                        },
                    },
                )
                if resp.status_code == 404:
                    # Model not found — let the caller try other backends
                    logger.info(f"Ollama: model '{model}' not found")
                    return None
                if resp.status_code != 200:
                    logger.warning(f"Ollama returned status {resp.status_code}: {resp.text[:200]}")
                    return None

                data = resp.json()

                # Handle thinking/reasoning models (glm-5.x, qwen3.5, deepseek-r1, etc.)
                # These models put the chain-of-thought in "thinking" and the answer in "content".
                # But sometimes "content" is empty and "thinking" has the actual answer.
                message = data.get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else ""
                thinking = message.get("thinking", "") if isinstance(message, dict) else ""

                # If content is empty but thinking has content, use thinking as the response
                used_thinking_as_content = False
                if not content.strip() and thinking.strip():
                    content = thinking.strip()
                    used_thinking_as_content = True
                    logger.debug(
                        f"Ollama: extracted content from " f"thinking field for model '{model}'"
                    )

                # If both are empty, check for error
                if not content.strip() and not thinking.strip():
                    error = data.get("error", "")
                    if error:
                        logger.warning(f"Ollama returned empty response with error: {error}")
                        return None
                    logger.warning(f"Ollama returned empty response for model '{model}'")

                # Preserve thinking field only if we didn't substitute it as content
                thinking_value = (
                    ""
                    if used_thinking_as_content
                    else (thinking if isinstance(message, dict) else "")
                )

                return InferenceResult(
                    text=content,
                    thinking=thinking_value,
                    input_tokens=data.get("prompt_eval_count", 0),
                    output_tokens=data.get("eval_count", 0),
                    latency_ms=data.get("total_duration", 0) / 1_000_000,
                    model=data.get("model", model),
                    finish_reason="stop" if data.get("done") else "length",
                    cost_tokens=data.get("eval_count", 0),
                )

        except httpx.ConnectError:
            logger.debug(f"Ollama not reachable at {self._ollama_url}")
            return None
        except Exception as e:
            logger.warning(f"Ollama inference error: {e}")
            return None

    def _try_llama(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> InferenceResult | None:
        """Try llama.cpp server / vLLM / LM Studio (all OpenAI-compatible).

        Tries the configured llama.cpp URL first, then vLLM, then LM Studio.
        All speak OpenAI-compatible /v1/chat/completions.
        """
        # Try each OpenAI-compatible backend in order
        for url, backend_name in [
            (self._llama_url, "llama_cpp"),
            (self._vllm_url, "vllm"),
            (self._lm_studio_url, "lm_studio"),
        ]:
            result = self._try_openai_compatible(
                url, backend_name, prompt, model, max_tokens, temperature, top_p
            )
            if result is not None:
                return result
        return None

    def _try_openai_compatible(
        self,
        url: str,
        backend_name: str,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> InferenceResult | None:
        """Try an OpenAI-compatible backend at the given URL."""
        try:
            import httpx

            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{url}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                    },
                )
                if resp.status_code == 404:
                    logger.debug(f"{backend_name}: model '{model}' not found at {url}")
                    return None
                if resp.status_code != 200:
                    logger.debug(f"{backend_name} returned {resp.status_code} at {url}")
                    return None

                data = resp.json()
                choice = data.get("choices", [{}])[0]
                usage = data.get("usage", {})
                message = choice.get("message", {})

                # Handle thinking models — some backends return thinking in content
                content = message.get("content", "") if isinstance(message, dict) else ""

                return InferenceResult(
                    text=content,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    latency_ms=data.get("latency_ms", 0),
                    model=data.get("model", model),
                    finish_reason=choice.get("finish_reason", "stop"),
                    cost_tokens=usage.get("completion_tokens", 0),
                )

        except httpx.ConnectError:
            logger.debug(f"{backend_name} not reachable at {url}")
            return None
        except Exception as e:
            logger.warning(f"{backend_name} inference error: {e}")
            return None

    def _try_gemini(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult | None:
        """Try Google Gemini API for inference.

        Uses the Gemini REST API (generateContent endpoint).
        Requires a Gemini API key (set via SAWYER_GEMINI_API_KEY env var
        or config.gemini_api_key).
        """
        if not self._gemini_api_key:
            return None

        try:
            import httpx

            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{self._gemini_url}/models/{model}:generateContent",
                    params={"key": self._gemini_api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "maxOutputTokens": max_tokens,
                            "temperature": temperature,
                        },
                    },
                )
                if resp.status_code == 404:
                    raise RuntimeError(f"Gemini model '{model}' not found. Check the model name.")
                if resp.status_code != 200:
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        err_msg = resp.text[:200]
                    raise RuntimeError(f"Gemini API error ({resp.status_code}): {err_msg}")

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Gemini returned no candidates (model may be unavailable)")

                candidate = candidates[0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                text_parts = [p.get("text", "") for p in parts if p.get("text")]
                text = "\n".join(text_parts)

                # Handle thinking/reasoning models
                thinking = ""
                for p in parts:
                    if p.get("thought") or p.get("thinking"):
                        thinking += p.get("text", "")

                usage = data.get("usageMetadata", {})

                return InferenceResult(
                    text=text,
                    thinking=thinking,
                    input_tokens=usage.get("promptTokenCount", 0),
                    output_tokens=usage.get("candidatesTokenCount", 0),
                    latency_ms=0,  # Gemini doesn't return latency
                    model=model,
                    finish_reason="stop" if candidate.get("finishReason") == "STOP" else "length",
                    cost_tokens=usage.get("candidatesTokenCount", 0),
                )

        except httpx.ConnectError:
            logger.debug(f"Gemini not reachable at {self._gemini_url}")
            return None
        except Exception as e:
            logger.warning(f"Gemini inference error: {e}")
            return None

    # ── Streaming ─────────────────────────────────────────────────

    def infer_stream(
        self,
        prompt: str,
        model: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        messages: list | None = None,
    ):
        """Stream inference results as SSE chunks.

        Yields dicts in OpenAI streaming format:
        {"id": ..., "object": "chat.completion.chunk", "choices": [{"delta": {"content": "..."}]}

        The first chunk always has {"role": "assistant"} in the delta.
        The last chunk has {"finish_reason": "stop"}.
        """
        chat_id = f"chatcmpl-{int(time.time())}"

        if not self._discovered_models:
            self.discover_models()

        resolved_model, backend_hint = self._resolve_model(model)

        # Yield role chunk first (OpenAI streaming format requires this)
        yield {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": resolved_model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }

        # Try Gemini first if the model is a Gemini model
        if backend_hint == "gemini" or (backend_hint is None and resolved_model.lower().startswith("gemini")):
            streamed = False
            for chunk in self._stream_gemini(
                prompt,
                resolved_model,
                max_tokens,
                temperature,
                chat_id=chat_id,
                messages=messages,
            ):
                yield chunk
                streamed = True
            if streamed:
                return
            # User explicitly chose a Gemini model — don't silently fall back
            raise RuntimeError(
                f"Gemini model '{resolved_model}' failed. "
                "Check your API key in Settings."
            )

        # Try streaming from Ollama first (native streaming support)
        if backend_hint in (None, "ollama"):
            streamed = False
            for chunk in self._stream_ollama(
                prompt,
                resolved_model,
                max_tokens,
                temperature,
                chat_id=chat_id,
                messages=messages,
            ):
                yield chunk
                streamed = True
            if streamed:
                return

        # Try streaming from OpenAI-compatible backends
        if backend_hint in ("llama_cpp", "vllm", "lm_studio", None):
            for url, name in [
                (self._llama_url, "llama_cpp"),
                (self._vllm_url, "vllm"),
                (self._lm_studio_url, "lm_studio"),
            ]:
                chunks = list(
                    self._stream_openai_compatible(
                        url,
                        name,
                        prompt,
                        resolved_model,
                        max_tokens,
                        temperature,
                        top_p,
                        chat_id=chat_id,
                        messages=messages,
                    )
                )
                if chunks:
                    for chunk in chunks:
                        yield chunk
                    return

        # Try Gemini as streaming fallback if no local backends worked
        if backend_hint not in ("ollama", "llama_cpp", "vllm", "lm_studio", "gemini"):
            streamed = False
            for chunk in self._stream_gemini(
                prompt,
                resolved_model,
                max_tokens,
                temperature,
                chat_id=chat_id,
                messages=messages,
            ):
                yield chunk
                streamed = True
            if streamed:
                return

        # No streaming backend available -- fall back to non-streaming
        try:
            result = self.infer(prompt, model, max_tokens, temperature, top_p)
            # Yield the full content as one chunk then the stop
            yield {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": result.model or model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": result.text},
                        "finish_reason": None,
                    }
                ],
            }
            yield {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": result.model or model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": result.finish_reason}],
            }
        except RuntimeError:
            # Re-raise with proper SSE error format
            raise

    def _stream_ollama(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        chat_id: str = "",
        messages: list | None = None,
    ):
        """Stream from Ollama using its native /api/chat streaming."""
        try:
            import httpx

            # Build Ollama messages from conversation history
            ollama_messages = []
            if messages:
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role in ("user", "assistant", "system"):
                        ollama_messages.append({"role": role, "content": content})
            if not ollama_messages:
                ollama_messages = [{"role": "user", "content": prompt}]

            with (
                httpx.Client(timeout=120) as hclient,
                hclient.stream(
                    "POST",
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": ollama_messages,
                        "stream": True,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": temperature,
                        },
                    },
                ) as resp,
            ):
                if resp.status_code != 200:
                    logger.debug(f"Ollama stream returned {resp.status_code}")
                    return

                if not chat_id:
                    chat_id = f"chatcmpl-{int(time.time())}"
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = data.get("message", {})
                    content = message.get("content", "") if isinstance(message, dict) else ""
                    thinking = message.get("thinking", "") if isinstance(message, dict) else ""

                    # Yield thinking as a separate delta (reasoning models)
                    if thinking and isinstance(message, dict):
                        yield {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": data.get("model", model),
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"thinking": thinking},
                                    "finish_reason": None,
                                }
                            ],
                        }

                    # Yield content delta
                    if content:
                        yield {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": data.get("model", model),
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": content},
                                    "finish_reason": None,
                                }
                            ],
                        }

                    if data.get("done"):
                        yield {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": data.get("model", model),
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                        return

        except httpx.ConnectError:
            logger.debug(f"Ollama not reachable for streaming at {self._ollama_url}")
            return
        except Exception as e:
            logger.warning(f"Ollama streaming error: {e}")
            return

    def _stream_openai_compatible(
        self,
        url: str,
        backend_name: str,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        chat_id: str = "",
        messages: list | None = None,
    ):
        """Stream from an OpenAI-compatible backend using SSE."""
        try:
            import httpx

            # Build messages from conversation history
            api_messages = []
            if messages:
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role in ("user", "assistant", "system"):
                        api_messages.append({"role": role, "content": content})
            if not api_messages:
                api_messages = [{"role": "user", "content": prompt}]

            with (
                httpx.Client(timeout=120) as client,
                client.stream(
                    "POST",
                    f"{url}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": api_messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                        "stream": True,
                    },
                ) as resp,
            ):
                if resp.status_code != 200:
                    return

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data_str)
                        yield chunk
                    except json.JSONDecodeError:
                        continue

        except httpx.ConnectError:
            return
        except Exception:
            return

    def _stream_gemini(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        chat_id: str = "",
        messages: list | None = None,
    ):
        """Stream from Google Gemini API using generateContent with SSE.

        Yields dicts in OpenAI streaming format.
        Requires a Gemini API key.
        """
        if not self._gemini_api_key:
            return

        try:
            import httpx

            # Build Gemini conversation contents from messages
            gemini_contents = []
            if messages:
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role == "user":
                        gemini_contents.append({"role": "user", "parts": [{"text": content}]})
                    elif role == "assistant":
                        gemini_contents.append({"role": "model", "parts": [{"text": content}]})
                    elif role == "system":
                        # Gemini uses systemInstruction at top level
                        pass
            if not gemini_contents:
                gemini_contents = [{"role": "user", "parts": [{"text": prompt}]}]

            if not chat_id:
                chat_id = f"chatcmpl-{int(time.time())}"

            with (
                httpx.Client(timeout=120) as client,
                client.stream(
                    "POST",
                    f"{self._gemini_url}/models/{model}:streamGenerateContent",
                    params={"key": self._gemini_api_key, "alt": "sse"},
                    json={
                        "contents": gemini_contents,
                        "generationConfig": {
                            "maxOutputTokens": max_tokens,
                            "temperature": temperature,
                        },
                    },
                ) as resp,
            ):
                if resp.status_code != 200:
                    logger.debug(f"Gemini stream returned {resp.status_code}")
                    return

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        return
                    try:
                        chunk_data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    candidates = chunk_data.get("candidates", [])
                    if not candidates:
                        continue

                    candidate = candidates[0]
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])

                    for p in parts:
                        # Handle thinking parts
                        if p.get("thought") or p.get("thinking"):
                            thinking_text = p.get("text", "")
                            if thinking_text:
                                yield {
                                    "id": chat_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"thinking": thinking_text},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                        elif p.get("text"):
                            yield {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": p["text"]},
                                        "finish_reason": None,
                                    }
                                ],
                            }

                    # Check for completion
                    finish_reason = candidate.get("finishReason")
                    if finish_reason == "STOP":
                        yield {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                        }
                        return

        except httpx.ConnectError:
            logger.debug(f"Gemini not reachable for streaming")
            return
        except Exception as e:
            logger.warning(f"Gemini streaming error: {e}")
            return

    # ── Backend Status ────────────────────────────────────────────

    def is_available(self) -> dict[str, bool]:
        """Check which local backends are reachable (concurrent, fast)."""
        from concurrent.futures import ThreadPoolExecutor

        import httpx

        def _check(url: str, name: str) -> tuple[str, bool]:
            try:
                timeout = httpx.Timeout(2.0, connect=1.0)
                with httpx.Client(timeout=timeout) as client:
                    if name == "llama_cpp":
                        resp = client.get(f"{url}/health")
                    elif name == "ollama":
                        resp = client.get(url)
                    elif name == "gemini":
                        # Gemini is "available" if an API key is configured
                        return name, bool(self._gemini_api_key)
                    else:
                        resp = client.get(f"{url}/v1/models")
                    return name, resp.status_code == 200
            except Exception:
                return name, False

        backends = [
            (self._llama_url, "llama_cpp"),
            (self._ollama_url, "ollama"),
            (self._vllm_url, "vllm"),
            (self._lm_studio_url, "lm_studio"),
            (self._gemini_url, "gemini"),
        ]

        available = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(_check, url, name) for url, name in backends]
            for future in futures:
                try:
                    name, ok = future.result(timeout=3)
                    available[name] = ok
                except Exception:
                    pass

        return available

    def get_models_list(self) -> list[dict]:
        """Get OpenAI-compatible model list from all discovered models."""
        self.discover_models()

        models = []
        seen_ids = set()

        # Always include "sawyer" as the default routing model
        models.append({"id": "sawyer", "object": "model", "owned_by": "sawyer"})
        seen_ids.add("sawyer")

        # Add well-known Gemini models if an API key is configured
        # (even if discovery hasn't found them yet or is cached)
        if self._gemini_api_key:
            gemini_defaults = [
                {"id": "gemini-2.5-pro", "owned_by": "google"},
                {"id": "gemini-2.5-flash", "owned_by": "google"},
                {"id": "gemini-2.0-flash", "owned_by": "google"},
                {"id": "gemini-1.5-pro", "owned_by": "google"},
                {"id": "gemini-1.5-flash", "owned_by": "google"},
            ]
            for gm in gemini_defaults:
                if gm["id"] not in seen_ids:
                    models.append({"id": gm["id"], "object": "model", "owned_by": gm["owned_by"]})
                    seen_ids.add(gm["id"])

        # Add all discovered models
        for dm in self._discovered_models:
            if dm.id not in seen_ids:
                entry = {"id": dm.id, "object": "model", "owned_by": dm.owned_by}
                if dm.context_length:
                    entry["context_length"] = dm.context_length
                if dm.details:
                    # Include useful details like family, parameter_size
                    for key in ("family", "parameter_size", "quantization_level"):
                        if key in dm.details:
                            entry[key] = dm.details[key]
                    # Include Gemini-specific details
                    if dm.backend == "gemini" and "output_token_limit" in dm.details:
                        entry["output_token_limit"] = dm.details["output_token_limit"]
                models.append(entry)
                seen_ids.add(dm.id)
            # Also add the short name (without tag) as an alias
            short = dm.id.split(":")[0] if ":" in dm.id else ""
            if short and short not in seen_ids:
                models.append({"id": short, "object": "model", "owned_by": dm.owned_by})
                seen_ids.add(short)

        return models


# ── Chat UI ─────────────────────────────────────────────────────────


CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sawyer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface-2: #1a1a25;
    --border: #2a2a3a;
    --text: #e4e4ef;
    --text-dim: #8888a0;
    --accent: #12c7ef;
    --accent-dim: #0e9fc3;
    --user-bg: #1a2a35;
    --assistant-bg: #1a1a25;
    --error: #ef4444;
    --thinking-bg: #1a1a2a;
    --gemini: #12c7ef;
    --gemini-dim: #0ea5cc;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
  }
  header h1 {
    font-size: 18px;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.5px;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .header-info {
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    gap: 16px;
    align-items: center;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 4px;
  }
  .status-dot.online { background: #22c55e; }
  .status-dot.offline { background: var(--error); }
  .status-dot.local { background: #f59e0b; }
  .settings-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 6px;
    border-radius: 6px;
    transition: background 0.2s, color 0.2s;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .settings-btn:hover {
    background: var(--surface-2);
    color: var(--text);
  }
  .settings-btn svg {
    width: 20px;
    height: 20px;
    transition: transform 0.3s;
  }
  .settings-btn:hover svg {
    transform: rotate(45deg);
  }

  /* Settings panel overlay */
  .settings-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 998;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.3s;
  }
  .settings-overlay.open {
    opacity: 1;
    pointer-events: auto;
  }

  /* Settings panel */
  .settings-panel {
    position: fixed;
    top: 0;
    right: -380px;
    width: 360px;
    height: 100vh;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 999;
    transition: right 0.3s ease;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }
  .settings-panel.open {
    right: 0;
  }
  .settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 20px 24px 16px;
    border-bottom: 1px solid var(--border);
  }
  .settings-header h2 {
    font-size: 16px;
    font-weight: 600;
    color: var(--text);
  }
  .settings-close {
    background: none;
    border: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px;
    border-radius: 4px;
    transition: color 0.2s;
    font-size: 20px;
    line-height: 1;
  }
  .settings-close:hover { color: var(--text); }
  .settings-body {
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }
  .settings-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .settings-section label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .settings-section .hint {
    font-size: 11px;
    color: var(--text-dim);
    opacity: 0.7;
  }
  .api-key-row {
    display: flex;
    gap: 0;
    align-items: stretch;
  }
  .api-key-input {
    flex: 1;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-right: none;
    border-radius: 8px 0 0 8px;
    color: var(--text);
    font-size: 13px;
    padding: 10px 12px;
    outline: none;
    font-family: monospace;
    transition: border-color 0.2s;
  }
  .api-key-input:focus {
    border-color: var(--accent);
  }
  .api-key-input::placeholder {
    color: var(--text-dim);
    opacity: 0.5;
  }
  .eye-toggle {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 0 8px 8px 0;
    color: var(--text-dim);
    cursor: pointer;
    padding: 0 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.2s, background 0.2s;
  }
  .eye-toggle:hover {
    color: var(--text);
    background: var(--border);
  }
  .eye-toggle svg {
    width: 18px;
    height: 18px;
  }
  .save-btn {
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
    margin-top: 4px;
  }
  .save-btn:hover { opacity: 0.9; }
  .save-btn.saved {
    background: #22c55e;
    color: #000;
  }
  .api-key-status {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 2px;
  }
  .api-key-status.connected { color: #22c55e; }
  .api-key-status.missing { color: var(--text-dim); }

  /* Gemini model selector in settings */
  .gemini-model-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .gemini-model-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .gemini-model-item:hover {
    border-color: var(--accent-dim);
  }
  .gemini-model-item.selected {
    border-color: var(--gemini);
    background: #1a1a2f;
  }
  .gemini-model-item .model-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    flex: 1;
  }
  .gemini-model-item .radio-dot {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid var(--border);
    flex-shrink: 0;
    position: relative;
    transition: border-color 0.2s;
  }
  .gemini-model-item.selected .radio-dot {
    border-color: var(--gemini);
  }
  .gemini-model-item.selected .radio-dot::after {
    content: '';
    position: absolute;
    top: 3px; left: 3px;
    width: 6px; height: 6px;
    background: var(--gemini);
    border-radius: 50%;
  }


  .model-select-wrapper {
    position: relative;
  }
  .model-select {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    padding: 10px 12px;
    font-size: 13px;
    outline: none;
    max-width: 200px;
    appearance: none;
    -webkit-appearance: none;
    padding-right: 28px;
  }
  .model-select:focus {
    border-color: var(--accent);
  }
  .model-select-arrow {
    position: absolute;
    right: 8px;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
    color: var(--text-dim);
  }
  .model-select-arrow svg {
    width: 14px;
    height: 14px;
  }
  .model-badge-container {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .message {
    max-width: 75%;
    padding: 12px 16px;
    border-radius: 12px;
    line-height: 1.5;
    font-size: 14px;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  .message.user {
    align-self: flex-end;
    background: var(--user-bg);
    border: 1px solid var(--accent-dim);
  }
  .message.assistant {
    align-self: flex-start;
    background: var(--assistant-bg);
    border: 1px solid var(--border);
  }
  .message.error {
    background: #2a1515;
    border: 1px solid var(--error);
    color: #fca5a5;
  }
  .message.system {
    align-self: center;
    background: transparent;
    color: var(--text-dim);
    font-size: 12px;
    border: none;
    padding: 4px;
  }
  .thinking {
    background: var(--thinking-bg);
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--text-dim);
    max-height: 200px;
    overflow-y: auto;
    cursor: pointer;
  }
  .thinking.collapsed {
    max-height: 24px;
    overflow: hidden;
  }
  .thinking-label {
    font-weight: 600;
    color: #6a6a9a;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .meta {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 6px;
  }
  #input-area {
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 16px 24px;
    flex-shrink: 0;
  }
  .input-row {
    display: flex;
    gap: 8px;
    align-items: flex-end;
  }
  textarea {
    flex: 1;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 14px;
    padding: 12px;
    resize: none;
    font-family: inherit;
    min-height: 44px;
    max-height: 200px;
    outline: none;
    transition: border-color 0.2s;
  }
  textarea:focus {
    border-color: var(--accent);
  }
  button.send-btn {
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
    white-space: nowrap;
  }
  button.send-btn:hover { opacity: 0.9; }
  button.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .welcome {
    text-align: center;
    padding: 40px 24px 24px;
    color: var(--text-dim);
  }
  .welcome h2 {
    color: var(--text);
    font-size: 24px;
    margin-bottom: 8px;
    font-weight: 600;
  }
  .welcome p {
    font-size: 14px;
    line-height: 1.6;
    max-width: 480px;
    margin: 0 auto 20px;
  }
  .quickstart {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    max-width: 560px;
    margin: 0 auto 20px;
    text-align: left;
  }
  .quickstart-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    transition: border-color 0.2s;
  }
  .quickstart-card:hover {
    border-color: var(--accent);
  }
  .quickstart-card h3 {
    color: var(--accent);
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .quickstart-card p {
    font-size: 13px;
    margin: 0;
    color: var(--text-dim);
    line-height: 1.4;
  }
  .quickstart-card code {
    display: block;
    margin-top: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--text);
    background: var(--bg);
    padding: 6px 8px;
    border-radius: 4px;
    overflow-x: auto;
  }
  .features {
    display: flex;
    justify-content: center;
    gap: 24px;
    max-width: 560px;
    margin: 0 auto;
    flex-wrap: wrap;
  }
  .feature {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--text-dim);
  }
  .feature-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
  }
  .token-info {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 8px;
  }
</style>
</head>
<body>

<header>
  <h1>Sawyer</h1>
  <div class="header-info">
    <span>
      <span class="status-dot local" id="status-dot"></span>
      <span id="status-text">Checking...</span>
    </span>
    <span id="model-display">local</span>
    <button class="settings-btn" id="settings-btn" onclick="toggleSettings()" title="Settings">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
      </svg>
    </button>
  </div>
</header>

<!-- Settings overlay -->
<div class="settings-overlay" id="settings-overlay" onclick="toggleSettings()"></div>

<!-- Settings panel -->
<div class="settings-panel" id="settings-panel">
  <div class="settings-header">
    <h2>Settings</h2>
    <button class="settings-close" onclick="toggleSettings()">&times;</button>
  </div>
  <div class="settings-body">
    <!-- Gemini API Key -->
    <div class="settings-section">
      <label>API Key</label>
      <div class="api-key-row">
        <input type="password" class="api-key-input" id="gemini-key"
               placeholder="Paste your API key"
               autocomplete="off">
        <button class="eye-toggle" id="eye-toggle" onclick="toggleKeyVisibility()" title="Show/hide key">
          <svg id="eye-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
          <svg id="eye-off-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
            <line x1="1" y1="1" x2="23" y2="23"/>
          </svg>
        </button>
      </div>
      <div class="api-key-status" id="key-status"></div>
      <button class="save-btn" id="save-key-btn" onclick="saveGeminiKey()">Save Key</button>
    </div>

    <!-- Gemini Model Selector -->
    <div class="settings-section">
      <label>Cloud Model</label>
      <div class="gemini-model-list" id="gemini-model-list">
        <!-- populated by JS -->
      </div>
    </div>
  </div>
</div>

<div id="messages">
  <div class="welcome">
    <h2>Distributed MoE Inference</h2>
    <p>Split inference across nodes. Pay for what you use. Add a cloud key to start chatting.</p>
    <div class="quickstart">
      <div class="quickstart-card">
        <h3>Install</h3>
        <p>Get Sawyer running in one command:</p>
        <code>pip install sawyer-core</code>
      </div>
      <div class="quickstart-card">
        <h3>Local Model</h3>
        <p>Serve a model from your machine:</p>
        <code>sawyer serve --offline</code>
      </div>
      <div class="quickstart-card">
        <h3>Join Network</h3>
        <p>Connect to the distributed network:</p>
        <code>sawyer serve</code>
      </div>
      <div class="quickstart-card">
        <h3>Cloud</h3>
        <p>Add an API key in Settings to start chatting immediately.</p>
      </div>
    </div>
    <div class="features">
      <span class="feature"><span class="feature-dot"></span>MoE routing</span>
      <span class="feature"><span class="feature-dot"></span>Local + cloud</span>
      <span class="feature"><span class="feature-dot"></span>Pay per token</span>
      <span class="feature"><span class="feature-dot"></span>Open source</span>
    </div>
  </div>
</div>



<div id="input-area">
  <div class="token-info" id="token-info"></div>
  <div class="input-row">
    <div class="model-badge-container" id="model-badge-area">
      <div class="model-select-wrapper">
        <select class="model-select" id="model-select">
          <option value="">auto</option>
        </select>
        <span class="model-select-arrow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </span>
      </div>
    </div>
    <textarea id="prompt" rows="1" placeholder="Ask how to get started..." autofocus></textarea>
    <button class="send-btn" id="send" onclick="sendMessage()">Send</button>
  </div>
</div>

<script>
const messagesDiv = document.getElementById('messages');
const promptInput = document.getElementById('prompt');
const sendBtn = document.getElementById('send');
const modelSelect = document.getElementById('model-select');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const modelDisplay = document.getElementById('model-display');
const tokenInfo = document.getElementById('token-info');
const settingsPanel = document.getElementById('settings-panel');
const settingsOverlay = document.getElementById('settings-overlay');
const geminiKeyInput = document.getElementById('gemini-key');
const keyStatusEl = document.getElementById('key-status');
const saveKeyBtn = document.getElementById('save-key-btn');
const geminiModelList = document.getElementById('gemini-model-list');
const modelBadgeArea = document.getElementById('model-badge-area');

let conversationHistory = [];
let isLoading = false;
let geminiKey = localStorage.getItem('gemini_api_key') || '';
let selectedGeminiModel = localStorage.getItem('gemini_model') || '';

const CLOUD_MODELS = [
  { id: 'gemini-2.5-pro', name: '2.5 Pro' },
  { id: 'gemini-2.5-flash', name: '2.5 Flash' },
  { id: 'gemini-2.0-flash', name: '2.0 Flash' },
  { id: 'gemini-1.5-pro', name: '1.5 Pro' },
  { id: 'gemini-1.5-flash', name: '1.5 Flash' },
];

// --- Settings panel ---
function toggleSettings() {
  const isOpen = settingsPanel.classList.contains('open');
  if (isOpen) {
    settingsPanel.classList.remove('open');
    settingsOverlay.classList.remove('open');
  } else {
    settingsPanel.classList.add('open');
    settingsOverlay.classList.add('open');
    geminiKeyInput.value = geminiKey;
    updateKeyStatus();
  }
}

function toggleKeyVisibility() {
  const isPassword = geminiKeyInput.type === 'password';
  geminiKeyInput.type = isPassword ? 'text' : 'password';
  document.getElementById('eye-icon').style.display = isPassword ? 'none' : 'block';
  document.getElementById('eye-off-icon').style.display = isPassword ? 'block' : 'none';
}

function updateKeyStatus() {
  if (geminiKey) {
    const masked = geminiKey.slice(0, 4) + '...' + geminiKey.slice(-4);
    keyStatusEl.textContent = 'Key saved (' + masked + ')';
    keyStatusEl.className = 'api-key-status connected';
  } else {
    keyStatusEl.textContent = 'No key configured';
    keyStatusEl.className = 'api-key-status missing';
  }
}

async function saveGeminiKey() {
  geminiKey = geminiKeyInput.value.trim();
  localStorage.setItem('gemini_api_key', geminiKey);
  updateKeyStatus();
  renderGeminiModels();
  if (geminiKey) {
    // Sync key to server so cloud backend is available for routing
    saveKeyBtn.textContent = 'Saving...';
    try {
      const resp = await fetch('/v1/gemini-key', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({api_key: geminiKey}),
      });
      if (resp.ok) {
        saveKeyBtn.textContent = 'Saved';
        saveKeyBtn.classList.add('saved');
        // Refresh model list to include cloud models
        checkStatus();
      } else {
        saveKeyBtn.textContent = 'Error';
        const err = await resp.json().catch(() => ({}));
        keyStatusEl.textContent = 'Error: ' + (err.detail || 'Failed');
        keyStatusEl.className = 'api-key-status missing';
      }
    } catch (e) {
      saveKeyBtn.textContent = 'Error';
      keyStatusEl.textContent = 'Connection error';
      keyStatusEl.className = 'api-key-status missing';
    }
  } else {
    // Remove key from server
    try { await fetch('/v1/gemini-key', {method: 'DELETE'}); } catch (e) {}
    saveKeyBtn.textContent = 'Saved';
    saveKeyBtn.classList.add('saved');
    checkStatus();
  }
  setTimeout(() => {
    saveKeyBtn.textContent = 'Save Key';
    saveKeyBtn.classList.remove('saved');
  }, 1500);
}

// --- Gemini model list ---
function renderGeminiModels() {
  geminiModelList.innerHTML = '';
  CLOUD_MODELS.forEach(m => {
    const item = document.createElement('div');
    item.className = 'gemini-model-item' + (selectedGeminiModel === m.id ? ' selected' : '');
    item.innerHTML = '<div class="radio-dot"></div>'
      + '<span class="model-name">' + m.name + '</span>';
    item.addEventListener('click', () => {
      selectedGeminiModel = m.id;
      localStorage.setItem('gemini_model', m.id);
      renderGeminiModels();
      updateModelBadge();
    });
    geminiModelList.appendChild(item);
  });
}

function updateModelBadge() {
  // Cloud model badge removed — no Gemini branding shown
}

// Auto-resize textarea
promptInput.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 200) + 'px';
});

// Enter to send (Shift+Enter for newline)
promptInput.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Escape to close settings
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && settingsPanel.classList.contains('open')) {
    toggleSettings();
  }
});

// Check backend status and populate model list
async function checkStatus() {
  try {
    const resp = await fetch('/v1/models');
    if (resp.ok) {
      const data = await resp.json();
      const models = data.data || [];

      // Update status
      if (models.length > 0) {
        statusDot.className = 'status-dot online';
        statusText.textContent = 'Connected';
        modelDisplay.textContent = models.map(m => m.id)
                    .slice(0, 5).join(', ')
                    + (models.length > 5 ? '...' : '');
      } else {
        statusDot.className = 'status-dot local';
        statusText.textContent = 'No models';
        modelDisplay.textContent = 'No models found';
      }

      // Update model select dropdown
      const currentVal = modelSelect.value;
      // Keep "auto" option, remove rest
      modelSelect.innerHTML = '<option value="">auto</option>';
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        const size = m.parameter_size ? ' (' + m.parameter_size + ')' : '';
        opt.textContent = m.id + size;
        modelSelect.appendChild(opt);
      });
      // Restore selection
      if (currentVal && [...modelSelect.options].some(o => o.value === currentVal)) {
        modelSelect.value = currentVal;
      }
    }
  } catch (e) {
    try {
      const resp = await fetch('/health');
      if (resp.ok) {
        statusDot.className = 'status-dot local';
        statusText.textContent = 'Local';
        modelDisplay.textContent = 'local';
      }
    } catch (e2) {
      statusDot.className = 'status-dot offline';
      statusText.textContent = 'Offline';
    }
  }
}

checkStatus();
setInterval(checkStatus, 30000);

// Fetch token balance
async function updateTokenInfo() {
  try {
    const resp = await fetch('/v1/balance');
    if (resp.ok) {
      const data = await resp.json();
      tokenInfo.textContent = 'Balance: ' + (data.balance || 0) + ' tokens';
    }
  } catch (e) {
    // Token accounting not available
  }
}
updateTokenInfo();

function addMessage(role, content, meta, thinking) {
  const div = document.createElement('div');
  div.className = 'message ' + role;

  if (thinking && thinking.trim()) {
    const thinkDiv = document.createElement('div');
    thinkDiv.className = 'thinking collapsed';
    thinkDiv.innerHTML = '<span class="thinking-label"'
                    + '>Thinking</span><br>'
                    + thinking.replace(/</g, '&lt;').split(String.fromCharCode(10)).join('<br>');
    thinkDiv.addEventListener('click', () => thinkDiv.classList.toggle('collapsed'));
    div.appendChild(thinkDiv);
  }

  const contentDiv = document.createElement('div');
  contentDiv.textContent = content;
  div.appendChild(contentDiv);

  if (meta) {
    const metaDiv = document.createElement('div');
    metaDiv.className = 'meta';
    metaDiv.textContent = meta;
    div.appendChild(metaDiv);
  }

  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  return div;
}

async function sendMessage() {
  const prompt = promptInput.value.trim();
  if (!prompt || isLoading) return;

  isLoading = true;
  sendBtn.disabled = true;
  promptInput.value = '';
  promptInput.style.height = 'auto';

  addMessage('user', prompt);

  const assistantDiv = document.createElement('div');
  assistantDiv.className = 'message assistant';
  assistantDiv.textContent = '';
  messagesDiv.appendChild(assistantDiv);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  conversationHistory.push({role: 'user', content: prompt});

  const startTime = performance.now();
  // If a Gemini model is selected in settings and no local model is chosen, use Gemini
  let selectedModel = modelSelect.value || 'sawyer';
  if (!modelSelect.value && geminiKey && selectedGeminiModel) {
    selectedModel = selectedGeminiModel;
  }

  try {
    // Try streaming first
    const response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: selectedModel,
        messages: conversationHistory,
        max_tokens: 1024,
        temperature: 0.7,
        stream: true,
      }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error?.message || data.detail || 'Request failed');
    }

    const contentType = response.headers.get('content-type') || '';
    let fullContent = '';

    if (contentType.includes('text/event-stream') || contentType.includes('text/plain')) {
      // Stream response
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const {done, value} = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split(String.fromCharCode(10));
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') continue;

          try {
            const chunk = JSON.parse(data);
            const delta = chunk.choices?.[0]?.delta;
            if (delta?.content) {
              fullContent += delta.content;
              assistantDiv.textContent = fullContent;
              messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }
          } catch (e) {
            // Skip malformed chunks
          }
        }
      }
    } else {
      // JSON response (non-streaming)
      const data = await response.json();
      const choice = data.choices?.[0];
      fullContent = choice?.message?.content || '';
      assistantDiv.textContent = fullContent;

      const usage = data.usage || {};
      const elapsed = Math.round(performance.now() - startTime);
      const meta = [
        usage.prompt_tokens ? 'in:' + usage.prompt_tokens : '',
        usage.completion_tokens ? 'out:' + usage.completion_tokens : '',
        elapsed + 'ms',
      ].filter(Boolean).join(' | ');
      if (meta) {
        const metaDiv = document.createElement('div');
        metaDiv.className = 'meta';
        metaDiv.textContent = meta;
        assistantDiv.appendChild(metaDiv);
      }
    }

    conversationHistory.push({role: 'assistant', content: fullContent});
    updateTokenInfo();

  } catch (err) {
    assistantDiv.className = 'message error';
    if (err.message && err.message.includes('503')) {
      assistantDiv.textContent = '';
      const title = document.createElement('div');
      title.textContent = 'No model is running yet.';
      title.style.fontWeight = '600';
      title.style.marginBottom = '8px';
      assistantDiv.appendChild(title);
      const steps = [
        'Install Ollama (https://ollama.com) then run: ollama pull llama3',
        'Or start a local model: sawyer serve --offline --model mixtral-8x7b',
        'Or connect to the Sawyer network: sawyer serve',
      ];
      const list = document.createElement('ul');
      list.style.margin = '0';
      list.style.paddingLeft = '20px';
      steps.forEach(s => {
        const li = document.createElement('li');
        li.textContent = s;
        li.style.marginBottom = '4px';
        list.appendChild(li);
      });
      assistantDiv.appendChild(list);
    } else {
      assistantDiv.textContent = 'Error: ' + err.message;
    }
  }

  isLoading = false;
  sendBtn.disabled = false;
  promptInput.focus();
}

// Initialize settings UI
geminiKeyInput.value = geminiKey;
updateKeyStatus();
renderGeminiModels();
updateModelBadge();

// Sync stored cloud key to server on load
if (geminiKey) {
  fetch('/v1/gemini-key', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_key: geminiKey}),
  }).then(() => {
    // Refresh model list to include cloud models
    checkStatus();
  }).catch(() => {});
}
</script>
</body>
</html>"""


# ── API Server ──────────────────────────────────────────────────────


def create_client_app(config: SawyerConfig | None = None) -> FastAPI:
    """Create the FastAPI app for the consumer client."""
    config = config or SawyerConfig()
    local_inference = LocalInference(config)

    app = FastAPI(
        title="Sawyer Client",
        description="Distributed MoE inference — cheaper than your provider",
        version="0.4.0",
    )

    @app.get("/", response_class=HTMLResponse)
    async def chat_ui():
        """Serve the FAQ onboarding page."""
        return HTMLResponse(content=FAQ_HTML)

    @app.get("/health")
    async def health():
        """Health check."""
        import asyncio

        backends = await asyncio.to_thread(local_inference.is_available)
        models = await asyncio.to_thread(local_inference.discover_models)
        return {
            "status": "ok",
            "backends": backends,
            "models_available": len(models),
            "mode": "available" if any(backends.values()) or models else "local",
        }

    @app.get("/v1/models")
    async def list_models():
        """List available models (OpenAI-compatible).

        Dynamically discovers models from Ollama, llama.cpp, vLLM, and LM Studio.
        """
        import asyncio

        models = await asyncio.to_thread(local_inference.get_models_list)
        return {"object": "list", "data": models}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """OpenAI-compatible chat completions endpoint.

        This is the main entry point for inference. It tries:
        1. Sawyer network (distributed MoE) if available
        2. Local fallback (llama.cpp, Ollama, LM Studio, vLLM) if not

        Supports both streaming and non-streaming responses.
        """
        import asyncio

        body = await request.json()
        messages = body.get("messages", [])
        model = body.get("model", "sawyer")
        max_tokens = body.get("max_tokens", 512)
        temperature = body.get("temperature", 0.7)
        top_p = body.get("top_p", 0.9)
        stream = body.get("stream", False)

        # Extract the last user message for local fallback
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        if not last_user_msg:
            raise HTTPException(status_code=400, detail="No user message found")

        if stream:
            # Return SSE stream — run sync generator in a thread
            # to avoid blocking the event loop
            import queue
            import threading

            def _producer(
                q: queue.Queue,
                prompt: str,
                model: str,
                max_tokens: int,
                temperature: float,
                top_p: float,
                messages: list,
            ):
                """Run infer_stream in a background thread, push chunks to queue."""
                try:
                    for chunk in local_inference.infer_stream(
                        prompt=prompt,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        messages=messages,
                    ):
                        q.put(("chunk", chunk))
                except RuntimeError as e:
                    q.put(("error", str(e)))
                except Exception as e:
                    q.put(("error", f"Inference error: {e}"))
                finally:
                    q.put(None)  # sentinel: done

            q: queue.Queue = queue.Queue()
            thread = threading.Thread(
                target=_producer,
                args=(
                    q,
                    last_user_msg,
                    model,
                    max_tokens,
                    temperature,
                    top_p,
                    messages,
                ),
                daemon=True,
            )
            thread.start()

            async def generate():
                """Pull chunks from queue and yield SSE events."""
                while True:
                    # Use asyncio.to_thread to avoid blocking while
                    # waiting for the queue, with a timeout so we can
                    # check if the thread is still alive
                    item = await asyncio.to_thread(q.get, timeout=30)
                    if item is None:
                        # Producer finished
                        yield "data: [DONE]\n\n"
                        return
                    tag, data = item
                    if tag == "error":
                        error_data = {
                            "error": {
                                "message": data,
                                "type": "server_error",
                                "code": 503,
                            }
                        }
                        yield f"data: {json.dumps(error_data)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    # tag == "chunk"
                    yield f"data: {json.dumps(data)}\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # Non-streaming response
        try:
            result = await asyncio.to_thread(
                local_inference.infer,
                prompt=last_user_msg,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )

            response = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": result.model or model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": result.text,
                        },
                        "finish_reason": result.finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": result.input_tokens,
                    "completion_tokens": result.output_tokens,
                    "total_tokens": result.input_tokens + result.output_tokens,
                },
            }

            # Include thinking in response if available (for debugging/inspection)
            if result.thinking:
                response["choices"][0]["message"]["thinking"] = result.thinking

            return response

        except RuntimeError as e:
            msg = str(e)
            if "No inference backend" in msg or "No models" in msg:
                backends = await asyncio.to_thread(local_inference.is_available)
                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": "No inference backend is running.",
                        "suggestions": [
                            "Install Ollama and run: ollama pull llama3",
                            "Start a local model: sawyer serve --offline --model mixtral-8x7b",
                            "Connect to the Sawyer network: sawyer serve",
                        ],
                        "available_models": [
                            m.id for m in local_inference._discovered_models[:10]
                        ],
                        "backends": backends,
                    },
                ) from None
            raise HTTPException(
                status_code=503,
                detail=str(e),
            ) from None
        except Exception as e:
            logger.exception("Inference error")
            raise HTTPException(
                status_code=500,
                detail=f"Inference error: {e}",
            ) from None

    @app.get("/v1/balance")
    async def get_balance():
        """Get token balance for the current account."""
        return {
            "balance": 0,
            "mode": "local",
            "message": "Token accounting active when connected to Sawyer network",
        }

    # ── Gemini API Key Management ──────────────────────────────────

    @app.get("/v1/gemini-key")
    async def get_gemini_key():
        """Check if a Gemini API key is configured (does NOT return the key)."""
        has_key = bool(local_inference._gemini_api_key)
        return {
            "configured": has_key,
            "message": "Gemini API key is configured" if has_key else "No Gemini API key configured",
        }

    @app.post("/v1/gemini-key")
    async def set_gemini_key(request: Request):
        """Set or update the Gemini API key at runtime.

        Accepts JSON: {"api_key": "AIza..."}
        The key is stored in memory and also persisted to the config
        environment variable for the current session.
        """
        import asyncio

        body = await request.json()
        api_key = body.get("api_key", "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail="api_key is required")

        # Update the local inference instance
        local_inference._gemini_api_key = api_key
        # Also set the env var so it persists for the session
        os.environ["SAWYER_GEMINI_API_KEY"] = api_key

        # Re-discover models to include Gemini models
        await asyncio.to_thread(local_inference.discover_models, force=True)

        return {
            "status": "ok",
            "message": "Gemini API key configured successfully",
            "models_count": len([m for m in local_inference._discovered_models if m.backend == "gemini"]),
        }

    @app.delete("/v1/gemini-key")
    async def delete_gemini_key():
        """Remove the Gemini API key."""
        import asyncio

        local_inference._gemini_api_key = ""
        os.environ.pop("SAWYER_GEMINI_API_KEY", None)

        # Re-discover models without Gemini
        await asyncio.to_thread(local_inference.discover_models, force=True)

        return {
            "status": "ok",
            "message": "Gemini API key removed",
        }

    return app


def serve_client(
    host: str = "localhost",
    port: int = 8000,
    config: SawyerConfig | None = None,
    ollama_bridge: bool = False,
) -> None:
    """Start the consumer client server.

    This is what users run to get cheaper inference. Opens a web UI
    and an OpenAI-compatible API endpoint.

    When ollama_bridge is True, also registers local Ollama as an
    inference provider on the Sawyer network, letting other nodes
    use your GPU through the network.
    """
    app = create_client_app(config)

    if ollama_bridge:
        app.state.ollama_bridge = True

    # Discover models at startup so we know what's available
    local_inference = LocalInference(config)
    models = local_inference.discover_models(force=True)
    print("\n  Sawyer Client v0.5.0")
    print(f"  Chat UI:     http://{host}:{port}")
    print(f"  API:         http://{host}:{port}/v1/chat/completions")
    print(f"  Models:      http://{host}:{port}/v1/models")
    if models:
        print(f"  Available:   {', '.join(m.id for m in models[:8])}")
    else:
        print("  Available:   No local models found")
        print("  Install Ollama: https://ollama.com")
        print("  Then run: ollama pull llama3")
    if ollama_bridge:
        print("  Ollama bridge: serving local Ollama to the network")
    print()
    uvicorn.run(app, host=host, port=port)
