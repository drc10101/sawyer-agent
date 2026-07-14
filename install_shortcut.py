"""
Create Sawyer Harness desktop shortcut for one-click launch.
Run this script once to install the shortcut.
"""

import os
import sys

try:
    from win32com.client import Dispatch
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


def create_shortcut_win32():
    """Create shortcut using win32com (most reliable on Windows)."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "Sawyer Harness.lnk")

    python_exe = sys.executable
    project_dir = os.path.join(os.path.expanduser("~"), "development", "sawyer-harness")
    launcher = os.path.join(project_dir, "sawyer_harness", "launcher.py")
    icon_path = os.path.join(project_dir, "sawyer_harness", "web", "static", "sawyer.ico")

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.Targetpath = python_exe
    shortcut.Arguments = f'"{launcher}"'
    shortcut.WorkingDirectory = project_dir
    shortcut.Description = "Sawyer Harness -- AI Agent Framework"
    shortcut.IconLocation = icon_path
    shortcut.save()

    print(f"  Shortcut created: {shortcut_path}")
    print(f"  Target: {python_exe} {launcher}")
    print(f"  Icon: {icon_path}")


def create_shortcut_powershell():
    """Create shortcut using PowerShell (fallback if win32com unavailable)."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "Sawyer Harness.lnk")

    python_exe = sys.executable
    project_dir = os.path.join(os.path.expanduser("~"), "development", "sawyer-harness")
    launcher = os.path.join(project_dir, "sawyer_harness", "launcher.py")
    icon_path = os.path.join(project_dir, "sawyer_harness", "web", "static", "sawyer.ico")

    # PowerShell script to create the shortcut
    ps_script = (
        f"$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut('{shortcut_path}')\n"
        f"$shortcut.TargetPath = '{python_exe}'\n"
        f"$shortcut.Arguments = '\"{launcher}\"'\n"
        f"$shortcut.WorkingDirectory = '{project_dir}'\n"
        f"$shortcut.Description = 'Sawyer Harness -- AI Agent Framework'\n"
        f"$shortcut.IconLocation = '{icon_path}'\n"
        f"$shortcut.Save()\n"
        f"Write-Host 'Shortcut created: {shortcut_path}'\n"
    )

    import subprocess
    result = subprocess.run(
        ["powershell", "-Command", ps_script],
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"  Error: {result.stderr.strip()}")
        print("  Trying alternative method...")

        # Fallback: create a .url file instead
        url_path = os.path.join(desktop, "Sawyer Harness.url")
        bat_path = os.path.join(project_dir, "start-sawyer.bat")
        with open(url_path, "w") as f:
            f.write("[InternetShortcut]\n")
            f.write(f"URL=file:///{bat_path.replace(os.sep, '/')}\n")
            f.write(f"IconFile={icon_path}\n")
            f.write("IconIndex=0\n")
        print(f"  URL shortcut created: {url_path}")


def main():
    print()
    print("  Sawyer Harness -- Desktop Shortcut Installer")
    print()

    if HAS_WIN32:
        print("  Using win32com to create shortcut...")
        create_shortcut_win32()
    else:
        print("  win32com not available, using PowerShell...")
        create_shortcut_powershell()

    print()
    print("  Done! Double-click 'Sawyer Harness' on your desktop to launch.")
    print("  The server will start and your browser will open to http://localhost:8765")
    print()


if __name__ == "__main__":
    main()