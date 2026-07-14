"""
Sawyer Agent CLI.

Usage:
    python -m sawyer_harness              Start the web server
    python -m sawyer_harness web           Start the web server
    python -m sawyer_harness setup         Configure or reconfigure API key
    python -m sawyer_harness uninstall     Remove Sawyer completely
    python -m sawyer_harness version       Show version

Short form (if on PATH):
    sawyer-web                            Start the web server
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _cmd_web(args):
    """Start the Sawyer web server."""
    from sawyer_harness.web.server import run_server
    from sawyer_harness.config import HarnessConfig, DEFAULT_CONFIG_PATH, setup_wizard
    import logging

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH
    config = HarnessConfig.from_file(config_path)

    if not config_path.exists() or config.needs_setup():
        from sawyer_harness.config import setup_wizard
        config = setup_wizard(config_path)

    run_server(config, host=args.host, port=args.port)


def _cmd_setup(args):
    """Run the setup wizard to configure or reconfigure Sawyer."""
    from sawyer_harness.config import setup_wizard, DEFAULT_CONFIG_PATH

    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH
    setup_wizard(config_path)
    print(f"\nConfig saved. Run `python -m sawyer_harness` to start.\n")


def _cmd_uninstall(args):
    """Remove Sawyer: stop server, delete data, uninstall package.

    On Windows the running exe cannot delete itself, so we spawn a detached
    cleanup script that waits for us to exit before running pip uninstall.
    """
    data_dir = Path.home() / ".sawyer-harness"
    found = False

    # 1. Stop running server
    print("Stopping Sawyer Agent...")
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if ":8765" in line and "LISTEN" in line:
                    pid = line.split()[-1]
                    if pid != str(os.getpid()):
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    else:
        subprocess.run(["pkill", "-f", "sawyer_harness.web.server"],
                       capture_output=True, timeout=10)

    # 2. Delete config, memory, skills, keys
    if data_dir.exists():
        print(f"Removing {data_dir}...")
        shutil.rmtree(data_dir, ignore_errors=True)
        found = True
    else:
        print(f"No data directory at {data_dir}")

    # 3. Remove desktop shortcut and launcher (all platforms)
    for name in ("Sawyer Agent.lnk", "Sawyer Agent.bat"):
        shortcut = Path.home() / "Desktop" / name
        if shortcut.exists():
            print(f"Removing {shortcut}...")
            shortcut.unlink()
            found = True

    if platform.system() == "Windows":
        launcher_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Sawyer Agent"
        if launcher_dir.exists():
            print(f"Removing {launcher_dir}...")
            shutil.rmtree(launcher_dir, ignore_errors=True)
            found = True

    # 4. Uninstall pip package
    #    On Windows: spawn detached script that waits for this process to exit
    #    On Linux/macOS: pip uninstall directly (no exe lock)
    print("Uninstalling sawyer-agent package...")
    my_pid = str(os.getpid())

    if platform.system() == "Windows":
        cleanup = Path.home() / "AppData" / "Local" / "Temp" / "sawyer_cleanup.bat"
        cleanup.write_text(
            f"@echo off\n"
            f"echo Waiting for Sawyer to exit...\n"
            f":wait\n"
            f"tasklist /FI \"PID eq {my_pid}\" 2>nul | find \"{my_pid}\" >nul\n"
            f"if not errorlevel 1 (\n"
            f"    timeout /t 1 /nobreak >nul\n"
            f"    goto wait\n"
            f")\n"
            f"echo Uninstalling sawyer-agent...\n"
            f"\"{sys.executable}\" -m pip uninstall sawyer-agent -y\n"
            f"del \"%~f0\"\n",
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd", "/c", str(cleanup)],
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        print("Uninstall will complete after this window closes.")
    else:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "sawyer-agent", "-y"],
            capture_output=True, text=True, timeout=60,
        )
        if "Successfully uninstalled" in result.stdout:
            print("Package uninstalled.")
        else:
            print(result.stdout.strip() or "Done.")

    if found:
        print("\nSawyer Agent has been removed.")
    else:
        print("\nNothing to remove -- Sawyer was not installed.")


def _cmd_version(args):
    """Print version and exit."""
    from sawyer_harness import __version__
    print(f"Sawyer Agent {__version__}")


def main():
    """Entry point for python -m sawyer_harness and sawyer-web."""
    parser = argparse.ArgumentParser(
        prog="sawyer_harness",
        description="Sawyer Agent -- Secure, model-agnostic, self-hosted AI agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # No subcommand = web (backward compatible with sawyer-web)
    parser.add_argument("--config", "-c", default=None, help="Config file path")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=8765, help="Port to bind (default: 8765)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # web
    web_p = subparsers.add_parser("web", help="Start the web server")
    web_p.add_argument("--config", "-c", default=None, help="Config file path")
    web_p.add_argument("--host", default="127.0.0.1", help="Host to bind")
    web_p.add_argument("--port", "-p", type=int, default=8765, help="Port to bind")
    web_p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    web_p.set_defaults(func=_cmd_web)

    # setup
    setup_p = subparsers.add_parser("setup", help="Configure or reconfigure API key and provider")
    setup_p.add_argument("--config", "-c", default=None, help="Config file path")
    setup_p.set_defaults(func=_cmd_setup)

    # uninstall
    uninstall_p = subparsers.add_parser("uninstall", help="Remove Sawyer completely")
    uninstall_p.set_defaults(func=_cmd_uninstall)

    # version
    version_p = subparsers.add_parser("version", help="Show version")
    version_p.set_defaults(func=_cmd_version)

    args = parser.parse_args()

    # No subcommand: default to web
    if args.command is None:
        _cmd_web(args)
        return

    args.func(args)


if __name__ == "__main__":
    main()