"""
Sawyer Agent CLI.

Usage:
    python -m sawyer_harness                      Start the web server
    python -m sawyer_harness web                  Start the web server
    python -m sawyer_harness launch <model>       Self-activating: parse model, configure, start
    python -m sawyer_harness setup                Configure or reconfigure API key
    python -m sawyer_harness sandbox [mode]       Change permission mode (readonly/readwrite/all)
    python -m sawyer_harness install-shortcuts    Create desktop/start menu shortcuts with icon
    python -m sawyer_harness uninstall             Remove Sawyer completely
    python -m sawyer_harness version               Show version

Self-activating launch examples:
    sawyer launch glm-5.1:cloud                Ollama cloud (prompts for key)
    sawyer launch gpt-4o                       OpenAI (prompts for key)
    sawyer launch claude-sonnet-4              Anthropic (prompts for key)
    sawyer launch llama3                        Local Ollama (no key needed)
    sawyer launch --model gpt-4o --key sk-xxx   Explicit, zero prompts

Sandbox examples:
    sawyer sandbox              Interactive menu
    sawyer sandbox readwrite    Set read-write mode (recommended)
    sawyer sandbox all          Set full access

Short form (if on PATH):
    sawyer-web                                  Start the web server
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


def _cmd_launch(args):
    """Self-activating launch: parse model string, resolve config, start server."""
    from sawyer_harness.launch import auto_launch

    auto_launch(
        model=args.model,
        api_key=args.key or "",
        host=args.host,
        port=args.port,
        verbose=args.verbose,
        config_path=args.config,
    )


def _cmd_setup(args):
    """Run the setup wizard to configure or reconfigure Sawyer."""
    from sawyer_harness.config import setup_wizard, DEFAULT_CONFIG_PATH

    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH
    setup_wizard(config_path)
    print("\nConfig saved. Run `python -m sawyer_harness` to start.\n")


def _cmd_sandbox(args):
    """Change the permission mode (sandbox level) without reconfiguring the LLM."""
    from sawyer_harness.config import HarnessConfig, SecurityConfig, DEFAULT_CONFIG_PATH, PERMISSION_MODES

    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH

    if args.mode:
        # Non-interactive: mode given as argument
        mode = args.mode.lower()
        if mode not in PERMISSION_MODES:
            print(f"Invalid mode '{args.mode}'. Choose from: {', '.join(PERMISSION_MODES)}")
            return
    else:
        # Interactive: show menu
        config = HarnessConfig.from_file(config_path) if config_path.exists() else HarnessConfig()
        cur = config.security.permission_mode
        print("\n=== Sawyer Permission Mode ===\n")
        print("  Controls what tools the agent can use.\n")
        print("  1. readonly   — read files, search, browse web (safest)")
        print("  2. readwrite  — edit files, run shell commands (recommended)")
        print("  3. all        — full access, no restrictions")
        mode_default = {"readonly": "1", "readwrite": "2", "all": "3"}.get(cur, "3")
        mode_input = input(f"\nCurrent: {cur}. New permission mode [{mode_default}]: ").strip() or mode_default
        mode = {"1": "readonly", "2": "readwrite", "3": "all"}.get(mode_input, cur)

    # Load existing config, update only security
    config = HarnessConfig.from_file(config_path) if config_path.exists() else HarnessConfig()
    config.security.permission_mode = mode
    config.security.sandbox = mode != "all"
    saved = config.save(config_path)

    labels = {"readonly": "read-only (safest)", "readwrite": "read-write (recommended)", "all": "full access"}
    print(f"\nPermission mode set to: {mode} ({labels.get(mode, mode)})")
    print(f"Config saved to: {saved}\n")


def _cmd_uninstall(args):
    """Remove Sawyer: stop server, delete ephemeral data, uninstall package.

    Preserves the user/ directory (config, memory, skills, keys, suggestions,
    tools, projects, LKG, cron, rules, goal loops, agent templates) so
    reinstalling picks up right where you left off.

    On Windows the running exe cannot delete itself, so we spawn a detached
    cleanup script that waits for us to exit before running pip uninstall.
    """
    from sawyer_harness.paths import UserData

    data_dir = UserData.home
    user_dir = UserData.user_dir
    cache_dir = UserData.cache_dir
    log_dir = UserData.log_dir
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

    # 2. Delete ephemeral data (cache/, logs/, root-level files)
    #    Explicitly PRESERVE user/ — config, memory, skills, keys, tools, etc.
    if data_dir.exists():
        # Wipe cache and logs (safe to delete)
        for ephemeral in (cache_dir, log_dir):
            if ephemeral.exists():
                print(f"Removing {ephemeral}...")
                shutil.rmtree(ephemeral, ignore_errors=True)
                found = True

        # Remove root-level ephemeral files (legacy locations pre-migration)
        ephemeral_root_files = [
            "session-scores",   # directory — moved to cache/ by migration
            "uploads",           # directory — moved to cache/ by migration
        ]
        for name in ephemeral_root_files:
            p = data_dir / name
            if p.exists():
                print(f"Removing {p}...")
                shutil.rmtree(p, ignore_errors=True)
                found = True

        # Remove root-level ephemeral single files (legacy, pre-migration)
        ephemeral_root_single = [
            "_restart",          # restart script
        ]
        for name in ephemeral_root_single:
            p = data_dir / name
            if p.exists():
                p.unlink(missing_ok=True)
                found = True

        # Remove the launch script at root
        launch_script = UserData.launch_script
        if launch_script.exists():
            launch_script.unlink(missing_ok=True)
            found = True

        # Remove migration marker (will re-migrate if user reinstalls and has
        # legacy files, but user/ is already in the right place)
        migration_marker = UserData._migration_marker
        if migration_marker.exists():
            migration_marker.unlink(missing_ok=True)

        # Confirm user/ survives
        if user_dir.exists():
            print(f"\nPreserved {user_dir} (config, memory, skills, keys, tools, projects)")
        else:
            print(f"\nNo user data directory at {user_dir}")

        # Clean up empty parent dirs only if user/ is empty too
        # (if user/ has content, leave the whole .sawyer-harness/ tree intact)
        if user_dir.exists() and any(user_dir.iterdir()):
            print(f"Keeping {data_dir} with your user data intact.")
        else:
            # user/ is empty or gone — safe to remove the whole tree
            print(f"Removing {data_dir} (no user data to preserve)...")
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

        # Remove Start Menu shortcut
        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        start_menu_lnk = start_menu / "Sawyer Agent.lnk"
        if start_menu_lnk.exists():
            print(f"Removing {start_menu_lnk}...")
            start_menu_lnk.unlink()
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

    if user_dir.exists() and any(user_dir.iterdir()):
        print("\nSawyer Agent uninstalled. Your data is preserved at:")
        print(f"  {user_dir}")
        print("Reinstall with: pip install sawyer-agent")
        print("To fully delete all data, remove that directory manually.")
    elif found:
        print("\nSawyer Agent has been removed.")
    else:
        print("\nNothing to remove -- Sawyer was not installed.")


def _cmd_version(args):
    """Print version and exit."""
    from sawyer_harness import __version__
    print(f"Sawyer Agent {__version__}")


def _cmd_install_shortcuts(args):
    """Create desktop and Start Menu shortcuts with the Sawyer icon.

    On Windows: creates .lnk shortcuts using PowerShell.
    On Linux: creates .desktop files.
    On macOS: creates an .app wrapper via AppleScript.
    """
    from sawyer_harness import __version__

    # Find the Sawyer icon in the package
    icon_path = Path(__file__).parent / "web" / "static" / "icons" / "favicon.ico"
    if not icon_path.exists():
        print(f"Warning: Icon not found at {icon_path}")
        icon_path = None

    # Find the sawyer-web entry point
    python_exe = sys.executable

    system = platform.system()

    if system == "Windows":
        desktop = Path.home() / "Desktop"
        start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"

        # Create a local app dir for the launcher script and icon copy
        app_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Sawyer Agent"
        app_dir.mkdir(parents=True, exist_ok=True)

        # Copy icon to app dir (so it persists even if package is updated)
        local_icon = app_dir / "sawyer.ico"
        if icon_path and icon_path.exists():
            shutil.copy2(icon_path, local_icon)
        else:
            local_icon = None

        # Create a launcher batch file
        launcher_bat = app_dir / "Sawyer Agent.bat"
        launcher_bat.write_text(
            f'@echo off\n'
            f'"{python_exe}" -m sawyer_harness %*\n',
            encoding="utf-8",
        )

        # PowerShell script to create .lnk shortcuts
        icon_arg = f'"$s.IconLocation = \'{local_icon}\'"' if local_icon else ""
        ps_script = f'''
$ws = New-Object -ComObject WScript.Shell

# Desktop shortcut
$s = $ws.CreateShortcut('{desktop}\\Sawyer Agent.lnk')
$s.TargetPath = '{launcher_bat}'
$s.Arguments = ''
$s.WorkingDirectory = '{app_dir}'
$s.Description = 'Sawyer Agent v{__version__} - Secure, model-agnostic AI agent'
{icon_arg}
$s.Save()

# Start Menu shortcut
$s2 = $ws.CreateShortcut('{start_menu}\\Sawyer Agent.lnk')
$s2.TargetPath = '{launcher_bat}'
$s2.Arguments = ''
$s2.WorkingDirectory = '{app_dir}'
$s2.Description = 'Sawyer Agent v{__version__} - Secure, model-agnostic AI agent'
{icon_arg}
$s2.Save()

Write-Host "Shortcuts created."
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            print(f"Desktop shortcut: {desktop / 'Sawyer Agent.lnk'}")
            print(f"Start Menu shortcut: {start_menu / 'Sawyer Agent.lnk'}")
            if local_icon:
                print(f"Icon: {local_icon}")
        else:
            print(f"Error creating shortcuts: {result.stderr}")
            # Fallback: create a simple .bat on desktop
            fallback = desktop / "Sawyer Agent.bat"
            fallback.write_text(
                f'@echo off\n'
                f'title Sawyer Agent\n'
                f'"{python_exe}" -m sawyer_harness %*\n'
                f'pause\n',
                encoding="utf-8",
            )
            print(f"Fallback: created {fallback}")

    elif system == "Darwin":
        # macOS: create an .app in /Applications
        app_dir_mac = Path.home() / "Applications"
        app_bundle = app_dir_mac / "Sawyer Agent.app"
        contents = app_bundle / "Contents" / "MacOS"
        contents.mkdir(parents=True, exist_ok=True)

        # Create launcher script
        launcher = contents / "Sawyer Agent"
        launcher.write_text(
            f'#!/bin/bash\n'
            f'exec "{python_exe}" -m sawyer_harness "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)

        # Create Info.plist
        plist = app_bundle / "Contents" / "Info.plist"
        plist.write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0">\n'
            f'<dict>\n'
            f'  <key>CFBundleName</key><string>Sawyer Agent</string>\n'
            f'  <key>CFBundleDisplayName</key><string>Sawyer Agent</string>\n'
            f'  <key>CFBundleVersion</key><string>{__version__}</string>\n'
            f'  <key>CFBundleExecutable</key><string>Sawyer Agent</string>\n'
            f'</dict>\n'
            f'</plist>\n',
            encoding="utf-8",
        )

        print(f"Application: {app_bundle}")

    else:
        # Linux: create .desktop file
        desktop_file = Path.home() / ".local" / "share" / "applications" / "sawyer-agent.desktop"
        desktop_file.parent.mkdir(parents=True, exist_ok=True)

        # Copy icon to a standard location
        local_icon_dir = Path.home() / ".local" / "share" / "icons"
        local_icon_dir.mkdir(parents=True, exist_ok=True)
        local_icon = local_icon_dir / "sawyer-agent.png"
        png_icon = Path(__file__).parent / "web" / "static" / "icons" / "web-app-manifest-192x192.png"
        if png_icon.exists():
            shutil.copy2(png_icon, local_icon)
            icon_line = f"Icon={local_icon}"
        elif icon_path and icon_path.exists():
            shutil.copy2(icon_path, local_icon_dir / "sawyer-agent.ico")
            icon_line = f"Icon={local_icon_dir / 'sawyer-agent.ico'}"
        else:
            icon_line = "Icon=applications-internet"

        desktop_file.write_text(
            f'[Desktop Entry]\n'
            f'Version={__version__}\n'
            f'Type=Application\n'
            f'Name=Sawyer Agent\n'
            f'Comment=Secure, model-agnostic AI agent\n'
            f'Exec={python_exe} -m sawyer_harness\n'
            f'{icon_line}\n'
            f'Terminal=true\n'
            f'Categories=Development;Network;\n',
            encoding="utf-8",
        )
        desktop_file.chmod(0o755)
        print(f"Desktop file: {desktop_file}")

    print("\nShortcuts installed. Double-click to start Sawyer Agent.")


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

    # launch -- self-activating start
    launch_p = subparsers.add_parser(
        "launch",
        help="Self-activating: parse model string, configure, and start",
        description=(
            "Start Sawyer with a model string. Auto-detects provider, base URL, "
            "and key requirements. No setup wizard needed.\n\n"
            "Examples:\n"
            "  sawyer launch glm-5.1:cloud       # Ollama cloud\n"
            "  sawyer launch gpt-4o              # OpenAI\n"
            "  sawyer launch claude-sonnet-4     # Anthropic\n"
            "  sawyer launch llama3              # Local Ollama\n"
            "  sawyer launch --model gpt-4o --key sk-xxx  # Explicit, zero prompts\n\n"
            "Model routing:\n"
            "  :cloud suffix  -> Ollama cloud API\n"
            "  :local suffix  -> local Ollama\n"
            "  gpt-/o1-/o3-   -> OpenAI\n"
            "  claude-        -> Anthropic\n"
            "  deepseek-      -> DeepSeek\n"
            "  gemini-        -> Google Gemini\n"
            "  Anything else  -> local Ollama"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    launch_p.add_argument("model", nargs="?", default="", help="Model string (e.g. glm-5.1:cloud, gpt-4o, llama3)")
    launch_p.add_argument("--model", "-m", dest="model_flag", default="", help="Model string (alternative to positional)")
    launch_p.add_argument("--key", "-k", default="", help="API key (skips prompt if provided)")
    launch_p.add_argument("--host", default="127.0.0.1", help="Host to bind")
    launch_p.add_argument("--port", "-p", type=int, default=8765, help="Port to bind")
    launch_p.add_argument("--config", "-c", default=None, help="Config file path")
    launch_p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    launch_p.set_defaults(func=_cmd_launch)

    # setup
    setup_p = subparsers.add_parser("setup", help="Configure or reconfigure API key and provider")
    setup_p.add_argument("--config", "-c", default=None, help="Config file path")
    setup_p.set_defaults(func=_cmd_setup)

    # sandbox
    sandbox_p = subparsers.add_parser(
        "sandbox",
        help="Change permission mode (sandbox level)",
        description=(
            "Set the agent permission mode without reconfiguring the LLM.\n\n"
            "Modes:\n"
            "  readonly   — read files, search, browse web (safest)\n"
            "  readwrite  — edit files, run shell commands (recommended)\n"
            "  all        — full access, no restrictions\n\n"
            "Run without a mode argument for interactive selection.\n\n"
            "Examples:\n"
            "  sawyer sandbox              # Interactive menu\n"
            "  sawyer sandbox readwrite    # Set directly\n"
            "  sawyer sandbox all          # Full access"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sandbox_p.add_argument("mode", nargs="?", default=None, help="Permission mode: readonly, readwrite, or all")
    sandbox_p.add_argument("--config", "-c", default=None, help="Config file path")
    sandbox_p.set_defaults(func=_cmd_sandbox)

    # uninstall
    uninstall_p = subparsers.add_parser("uninstall", help="Remove Sawyer completely")
    uninstall_p.set_defaults(func=_cmd_uninstall)

    # install-shortcuts
    shortcuts_p = subparsers.add_parser("install-shortcuts", help="Create desktop/start menu shortcuts with Sawyer icon")
    shortcuts_p.set_defaults(func=_cmd_install_shortcuts)

    # version
    version_p = subparsers.add_parser("version", help="Show version")
    version_p.set_defaults(func=_cmd_version)

    args = parser.parse_args()

    # No subcommand: default to web
    if args.command is None:
        _cmd_web(args)
        return

    # Merge positional model with --model flag for launch command
    if args.command == "launch":
        model = args.model or args.model_flag
        if not model:
            launch_p.error("model string is required (e.g. 'glm-5.1:cloud', 'gpt-4o', 'llama3')")
        args.model = model

    args.func(args)


if __name__ == "__main__":
    main()