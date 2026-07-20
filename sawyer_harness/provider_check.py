"""
Provider startup check -- ensure the LLM provider is reachable before the agent starts.

On startup, Sawyer checks if the configured LLM provider is actually listening.
For local providers (localhost), it can auto-start them. For remote providers,
it verifies connectivity and warns if unreachable.

This prevents the common failure mode: agent starts, sends first message,
gets a connection error, and has no idea why.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import time
from urllib.parse import urlparse

logger = logging.getLogger("sawyer-harness.provider_check")

# How long to wait for a local provider to become ready (seconds)
LOCAL_STARTUP_TIMEOUT = 60

# How long to wait between readiness checks (seconds)
POLL_INTERVAL = 1.5

# How many readiness polls before giving up
MAX_POLLS = int(LOCAL_STARTUP_TIMEOUT / POLL_INTERVAL)


def _extract_host_port(base_url: str) -> tuple[str, int]:
    """Extract host and port from a base URL.

    Returns (host, port). Defaults: http=80, https=443.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return host, port


def _is_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_local(host: str) -> bool:
    """Check if a host points to the local machine."""
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _start_ollama() -> bool:
    """Attempt to start Ollama as a background process.

    On Windows, looks for 'ollama' in PATH and starts it.
    On Mac/Linux, uses the 'ollama serve' command.

    Returns True if the start command was issued (doesn't mean it's ready yet).
    """
    system = platform.system()

    if system == "Windows":
        # On Windows, Ollama typically runs as 'ollama app serve' or
        # the Ollama desktop app. Try to start it.
        # First check if it's already a background process
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if "ollama" in result.stdout.lower():
                logger.info("Ollama process found but port not responding -- may be starting up")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try starting Ollama
        try:
            # Use 'start' to launch without blocking. Shell=True needed for 'start'.
            subprocess.Popen(
                ["ollama", "app", "serve"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Started Ollama (Windows)")
            return True
        except FileNotFoundError:
            logger.warning("Ollama not found in PATH. Install from https://ollama.com")
            return False

    else:
        # Mac/Linux -- start 'ollama serve' in background
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info("Started Ollama (Unix)")
            return True
        except FileNotFoundError:
            logger.warning("Ollama not found in PATH. Install from https://ollama.com")
            return False


def _start_local_provider(host: str, port: int) -> bool:
    """Attempt to start a local LLM provider.

    Currently supports:
    - Ollama on port 11434

    Returns True if a start command was issued.
    """
    # Port 11434 = Ollama
    if port == 11434:
        return _start_ollama()

    # Port 8000 = generic local server -- can't auto-start unknown providers
    logger.warning(
        f"Local provider on port {port} is not reachable. "
        f"Auto-start is only supported for Ollama (port 11434). "
        f"Please start your provider manually."
    )
    return False


def check_and_start_provider(base_url: str, provider: str = "") -> dict:
    """Check if the LLM provider is reachable. Auto-start if local and down.

    Args:
        base_url: The LLM provider base URL from config.
        provider: The provider name (ollama, openai, etc.) for logging.

    Returns:
        dict with keys:
        - reachable: bool -- provider is accepting connections
        - started: bool -- we attempted to auto-start it
        - host: str -- the host we checked
        - port: int -- the port we checked
        - message: str -- human-readable status
    """
    host, port = _extract_host_port(base_url)
    local = _is_local(host)
    provider_label = provider.upper() if provider else host

    # Check if already up
    if _is_port_open(host, port):
        return {
            "reachable": True,
            "started": False,
            "host": host,
            "port": port,
            "message": f"{provider_label} is running on {host}:{port}",
        }

    # Not reachable
    if not local:
        return {
            "reachable": False,
            "started": False,
            "host": host,
            "port": port,
            "message": (
                f"{provider_label} at {host}:{port} is not reachable. "
                f"Check your network connection and API key."
            ),
        }

    # Local and down -- try to start it
    logger.info(f"{provider_label} not detected on {host}:{port}. Attempting to start...")
    started = _start_local_provider(host, port)

    if not started:
        return {
            "reachable": False,
            "started": False,
            "host": host,
            "port": port,
            "message": (
                f"{provider_label} on {host}:{port} is down and could not be started. "
                f"Start it manually and try again."
            ),
        }

    # Wait for it to become ready
    logger.info(f"Waiting for {provider_label} to become ready on {host}:{port}...")
    for i in range(MAX_POLLS):
        time.sleep(POLL_INTERVAL)
        if _is_port_open(host, port):
            logger.info(f"{provider_label} is ready after {(i + 1) * POLL_INTERVAL:.1f}s")
            return {
                "reachable": True,
                "started": True,
                "host": host,
                "port": port,
                "message": (
                    f"{provider_label} started and ready on {host}:{port} "
                    f"(took {(i + 1) * POLL_INTERVAL:.1f}s)"
                ),
            }

    # Timed out waiting
    logger.warning(
        f"{provider_label} was started but did not become ready on {host}:{port} "
        f"within {LOCAL_STARTUP_TIMEOUT}s"
    )
    return {
        "reachable": False,
        "started": True,
        "host": host,
        "port": port,
        "message": (
            f"{provider_label} was started but is not responding on {host}:{port} "
            f"after {LOCAL_STARTUP_TIMEOUT}s. It may still be starting up -- "
            f"try again in a moment."
        ),
    }