"""Create a Windows desktop shortcut for Sawyer Agent."""
import os
import sys

def create_shortcut():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "Sawyer Agent.lnk")

    # Find the sawyer-web.bat launcher
    bat_path = os.path.join(os.path.dirname(__file__), "sawyer-web.bat")
    if not os.path.exists(bat_path):
        # Fallback: use the Python entry point
        bat_path = sys.executable

    icon_path = os.path.join(os.path.dirname(__file__), "web", "static", "sawyer-icon.ico")
    if not os.path.exists(icon_path):
        icon_path = ""

    try:
        import pythoncom
        from win32com.shell import shell

        pythoncom.CoInitialize()
        shortcut = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink,
            None,
            pythoncom.CLSCTX_INPROC_SERVER,
            shell.IID_IShellLink,
        )
        shortcut.SetPath(bat_path)
        shortcut.SetWorkingDirectory(os.path.dirname(bat_path))
        shortcut.SetDescription("Sawyer Agent - Secure, Model-Agnostic, Self-Hosted AI Agent")
        if icon_path:
            shortcut.SetIconLocation(icon_path, 0)

        persist = shortcut.QueryInterface(pythoncom.IID_IPersistFile)
        persist.Save(shortcut_path, 0)
        print(f"Desktop shortcut created: {shortcut_path}")
        return True
    except ImportError:
        # No pywin32 -- create a .bat on desktop instead
        bat_content = f'''@echo off
title Sawyer Agent
echo   Sawyer Agent -- Starting on http://127.0.0.1:8765
echo   Press Ctrl+C to stop.
echo.
python -m sawyer_harness.web.server --host 127.0.0.1 --port 8765
if errorlevel 1 (
    echo.
    echo Sawyer Agent is not installed. Run:
    echo   pip install git+https://github.com/drc10101/sawyer-agent.git
    echo.
    pause
)
'''
        desktop_bat = os.path.join(desktop, "Sawyer Agent.bat")
        with open(desktop_bat, "w") as f:
            f.write(bat_content)
        print(f"Desktop launcher created: {desktop_bat}")
        print("(Install pywin32 for a proper .lnk shortcut with icon: pip install pywin32)")
        return True


if __name__ == "__main__":
    create_shortcut()