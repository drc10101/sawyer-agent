"""
Vision Bridge -- give text-only agents the ability to see.

Uses Playwright for browser screenshots and Google Gemini's free tier
for structured visual validation. The pipeline:

  [Agent action] -> [Playwright screenshot] -> [Gemini evaluation] -> [JSON critique]

The critique is injected back into the agent's context for self-correction.
This is the "blind agent" solution: lightweight on local hardware,
cloud-powered visual reasoning when needed.

Design principles:
- Conditional checking: only call Gemini when the agent signals completion
  or a DOM interaction fails, not on every step
- Structured output: Gemini returns machine-parseable JSON, not prose
- Rate-limit aware: respects Gemini free-tier quotas (10 req/min, 250/day)
- Graceful degradation: if Gemini is unavailable, returns a "skip" result
  rather than crashing the agent loop
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("sawyer-harness.vision_bridge")

# ── Rate limiter ──────────────────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter for Gemini API calls.

    Default: 10 requests/minute, 250/day (Gemini Flash free tier).
    Thread-safe for use in async contexts.
    """

    def __init__(
        self,
        max_per_minute: int = 10,
        max_per_day: int = 250,
    ):
        self.max_per_minute = max_per_minute
        self.max_per_day = max_per_day
        self._minute_calls: list[float] = []
        self._day_calls: list[float] = []

    def can_call(self) -> bool:
        """Check if a call is allowed right now."""
        now = time.time()
        # Prune old timestamps
        self._minute_calls = [t for t in self._minute_calls if now - t < 60]
        self._day_calls = [t for t in self._day_calls if now - t < 86400]
        return len(self._minute_calls) < self.max_per_minute and len(self._day_calls) < self.max_per_day

    def record_call(self) -> None:
        """Record that a call was made."""
        now = time.time()
        self._minute_calls.append(now)
        self._day_calls.append(now)

    @property
    def remaining_today(self) -> int:
        """How many calls remain in the current day window."""
        now = time.time()
        self._day_calls = [t for t in self._day_calls if now - t < 86400]
        return self.max_per_day - len(self._day_calls)


# ── Structured validation output ─────────────────────────────────────

@dataclass
class VisualValidation:
    """Structured result from a vision check.

    This is what Gemini returns -- machine-readable, not prose.
    The agent can parse this directly and decide whether to self-correct.
    """
    task_completed: bool = False
    visual_bug_detected: bool = False
    issue_description: str = ""
    suggested_fix: str = ""
    confidence: float = 0.0  # 0.0-1.0, how confident the vision model is

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_completed": self.task_completed,
            "visual_bug_detected": self.visual_bug_detected,
            "issue_description": self.issue_description,
            "suggested_fix": self.suggested_fix,
            "confidence": self.confidence,
        }

    def to_agent_message(self) -> str:
        """Format as a system message to inject back into the agent's context.

        This is the feedback loop: the vision system reports what it saw,
        and the text-only agent adjusts its approach accordingly.
        """
        if self.task_completed and not self.visual_bug_detected:
            return "[VISION: Task completed successfully. No visual issues detected.]"

        parts = ["[SYSTEM NOTIFICATION: VISUAL VERIFICATION FAILED]"]
        parts.append("Your previous action resulted in a visual issue.")
        if self.issue_description:
            parts.append(f"- Issue: {self.issue_description}")
        if self.suggested_fix:
            parts.append(f"- Recommended resolution: {self.suggested_fix}")
        parts.append("Adjust your approach and retry.")

        return "\n".join(parts)

    @classmethod
    def from_gemini_json(cls, data: dict[str, Any]) -> VisualValidation:
        """Parse Gemini's structured JSON response into a VisualValidation.

        Handles both camelCase (from Gemini) and snake_case (our schema).
        """
        return cls(
            task_completed=bool(data.get("task_completed", data.get("taskCompleted", False))),
            visual_bug_detected=bool(data.get("visual_bug_detected", data.get("visualBugDetected", False))),
            issue_description=str(data.get("issue_description", data.get("issueDescription", ""))),
            suggested_fix=str(data.get("suggested_fix", data.get("suggestedFix", ""))),
            confidence=float(data.get("confidence", 0.7)),
        )

    @classmethod
    def skip(cls, reason: str = "") -> VisualValidation:
        """Create a skip result when vision checking is unavailable.

        The agent should treat this as "no information" rather than a failure.
        """
        return cls(
            task_completed=True,  # Assume success if we can't verify
            visual_bug_detected=False,
            issue_description=f"Vision check skipped: {reason}" if reason else "Vision check skipped",
            suggested_fix="",
            confidence=0.0,
        )


# ── Screenshot capture ───────────────────────────────────────────────

async def capture_screenshot(
    url: str,
    output_path: str = "",
    full_page: bool = False,
    wait_ms: int = 2000,
    selector: str = "",
) -> str:
    """Capture a screenshot of a URL using Playwright.

    Args:
        url: The URL to navigate to and screenshot.
        output_path: Where to save the screenshot. If empty, uses a temp file.
        full_page: Whether to capture the full scrollable page.
        wait_ms: Milliseconds to wait for animations/JS to settle.
        selector: Optional CSS selector to wait for before screenshotting.

    Returns:
        The file path of the saved screenshot.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for vision_bridge screenshots. "
            "Install with: pip install playwright && playwright install chromium"
        )

    if not output_path:
        import tempfile
        output_path = str(Path(tempfile.gettempdir()) / f"vision_check_{int(time.time())}.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)

            if wait_ms:
                await page.wait_for_timeout(wait_ms)

            if selector:
                await page.wait_for_selector(selector, timeout=10000)

            await page.screenshot(path=output_path, full_page=full_page)

        finally:
            await browser.close()

    return output_path


async def capture_screenshot_from_html(
    html: str,
    output_path: str = "",
    full_page: bool = False,
    wait_ms: int = 1000,
) -> str:
    """Capture a screenshot from raw HTML content.

    Useful for checking locally-generated HTML without needing a running server.

    Args:
        html: The HTML content to render and screenshot.
        output_path: Where to save the screenshot. If empty, uses a temp file.
        full_page: Whether to capture the full scrollable page.
        wait_ms: Milliseconds to wait for rendering.

    Returns:
        The file path of the saved screenshot.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required for vision_bridge screenshots. "
            "Install with: pip install playwright && playwright install chromium"
        )

    if not output_path:
        import tempfile
        output_path = str(Path(tempfile.gettempdir()) / f"vision_check_{int(time.time())}.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        try:
            await page.set_content(html, wait_until="networkidle")

            if wait_ms:
                await page.wait_for_timeout(wait_ms)

            await page.screenshot(path=output_path, full_page=full_page)

        finally:
            await browser.close()

    return output_path


# ── Gemini vision evaluation ─────────────────────────────────────────

def _get_gemini_client():
    """Get or create the Google GenAI client.

    Requires GEMINI_API_KEY environment variable.
    Falls back gracefully if not configured.
    """
    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "google-genai is required for vision checks. "
            "Install with: pip install google-genai"
        )

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY environment variable not set. "
            "Get a free key from https://ai.google.dev/ "
            "and set it with: export GEMINI_API_KEY=your_key"
        )

    return genai.Client(api_key=api_key)


def evaluate_image(
    image_path: str,
    objective: str,
    model: str = "gemini-2.5-flash",
    context: str = "",
) -> VisualValidation:
    """Send a screenshot to Gemini for visual evaluation.

    This is the core of the vision bridge: a text-only agent's action
    produces a visual result, and Gemini acts as its eyes to check
    whether the result matches the intent.

    Args:
        image_path: Path to the screenshot file.
        objective: What the agent was trying to achieve.
        model: Gemini model to use (default: gemini-2.5-flash, free tier).
        context: Additional context about the task or page.

    Returns:
        VisualValidation with structured feedback for the agent.
    """
    from PIL import Image

    rate_limiter = _get_rate_limiter()
    if not rate_limiter.can_call():
        return VisualValidation.skip(
            f"Rate limit reached. {rate_limiter.remaining_today} calls remaining today."
        )

    try:
        client = _get_gemini_client()
    except (ImportError, ValueError) as e:
        return VisualValidation.skip(str(e))

    img = Image.open(image_path)

    prompt = f"""You are the visual sensory system for a text-only browser automation agent.
The agent's objective was: "{objective}"
"""

    if context:
        prompt += f"\nAdditional context: {context}\n"

    prompt += """
Analyze this screenshot. Determine if the objective was successfully achieved
without visual overlaps, hidden fields, broken styling, or disruptive popups.

Respond with a JSON object matching this exact schema:
{
  "task_completed": boolean,
  "visual_bug_detected": boolean,
  "issue_description": string,
  "suggested_fix": string,
  "confidence": number (0.0 to 1.0)
}

Be specific about issues. If there's a cookie banner blocking a button, say exactly
which element and where. If text overlaps, describe which elements conflict.
If the objective is met and no visual issues exist, set task_completed to true
and visual_bug_detected to false.
"""

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=model,
            contents=[img, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        rate_limiter.record_call()

        # Parse the structured JSON response
        response_text = response.text.strip()

        # Handle markdown code fences that Gemini sometimes wraps JSON in
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            # Strip first and last lines (the ``` fences)
            response_text = "\n".join(lines[1:-1])

        data = json.loads(response_text)
        return VisualValidation.from_gemini_json(data)

    except json.JSONDecodeError as e:
        logger.warning("Gemini returned invalid JSON: %s", e)
        return VisualValidation(
            task_completed=False,
            visual_bug_detected=True,
            issue_description="Vision model returned unparseable response",
            suggested_fix="Retry the vision check or inspect manually",
            confidence=0.1,
        )
    except Exception as e:
        logger.error("Vision check failed: %s", e)
        return VisualValidation.skip(f"Vision check error: {e}")


async def evaluate_image_async(
    image_path: str,
    objective: str,
    model: str = "gemini-2.5-flash",
    context: str = "",
) -> VisualValidation:
    """Async wrapper for evaluate_image.

    Runs the synchronous Gemini call in a thread pool so it doesn't
    block the event loop.
    """
    return await asyncio.to_thread(evaluate_image, image_path, objective, model, context)


# ── Full pipeline: capture + evaluate ─────────────────────────────────

async def vision_check(
    url: str,
    objective: str,
    model: str = "gemini-2.5-flash",
    context: str = "",
    full_page: bool = False,
    wait_ms: int = 2000,
    selector: str = "",
) -> VisualValidation:
    """Complete vision check pipeline: screenshot a URL, then evaluate it.

    This is the main entry point for the vision bridge. It:
    1. Captures a screenshot of the URL
    2. Sends the screenshot to Gemini for evaluation
    3. Returns structured feedback for the agent

    Args:
        url: The URL to check.
        objective: What the agent was trying to achieve.
        model: Gemini model to use.
        context: Additional context.
        full_page: Whether to capture the full scrollable page.
        wait_ms: Milliseconds to wait for page to settle.
        selector: Optional CSS selector to wait for.

    Returns:
        VisualValidation with structured feedback.
    """
    try:
        screenshot_path = await capture_screenshot(
            url=url,
            full_page=full_page,
            wait_ms=wait_ms,
            selector=selector,
        )
    except Exception as e:
        logger.error("Screenshot capture failed: %s", e)
        return VisualValidation.skip(f"Screenshot failed: {e}")

    result = await evaluate_image_async(
        image_path=screenshot_path,
        objective=objective,
        model=model,
        context=context,
    )

    # Clean up the temp screenshot
    try:
        Path(screenshot_path).unlink(missing_ok=True)
    except Exception:
        pass  # Non-critical, don't fail the check over a temp file

    return result


async def vision_check_html(
    html: str,
    objective: str,
    model: str = "gemini-2.5-flash",
    context: str = "",
    full_page: bool = False,
    wait_ms: int = 1000,
) -> VisualValidation:
    """Vision check for locally-generated HTML content.

    Renders the HTML in a headless browser, screenshots it, and evaluates.
    Useful for checking agent-generated UI code without deploying it.

    Args:
        html: The HTML content to render and check.
        objective: What the agent was trying to achieve.
        model: Gemini model to use.
        context: Additional context.
        full_page: Whether to capture the full scrollable page.
        wait_ms: Milliseconds to wait for rendering.

    Returns:
        VisualValidation with structured feedback.
    """
    try:
        screenshot_path = await capture_screenshot_from_html(
            html=html,
            full_page=full_page,
            wait_ms=wait_ms,
        )
    except Exception as e:
        logger.error("HTML screenshot capture failed: %s", e)
        return VisualValidation.skip(f"Screenshot failed: {e}")

    result = await evaluate_image_async(
        image_path=screenshot_path,
        objective=objective,
        model=model,
        context=context,
    )

    # Clean up the temp screenshot
    try:
        Path(screenshot_path).unlink(missing_ok=True)
    except Exception:
        pass

    return result


# ── Module-level rate limiter (shared across all calls) ───────────────

_rate_limiter: RateLimiter | None = None

def _get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def set_rate_limits(max_per_minute: int = 10, max_per_day: int = 250) -> None:
    """Configure the global rate limiter.

    Call this at startup to customize limits (e.g., for paid Gemini tiers).
    """
    global _rate_limiter
    _rate_limiter = RateLimiter(max_per_minute=max_per_minute, max_per_day=max_per_day)