"""
Sawyer Harness one-click launcher.

Starts the web server, opens the browser, and waits for Ctrl+C.
"""

import sys
import webbrowser
import threading
import time


def main():
    print()
    print("  ███████╗██████╗ ██╗  ██╗███████╗██████╗  ██████╗ ████████╗")
    print("  ██╔════╝██╔══██╗██║ ██╔╝██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝")
    print("  ███████╗██████╔╝█████╔╝ █████╗  ██████╔╝██║   ██║   ██║   ")
    print("  ╚════██║██╔═══╝ ██╔═██╗ ██╔══╝  ██╔══██╗██║   ██║   ██║   ")
    print("  ███████║██║     ██║  ██╗███████╗██║  ██║╚██████╔╝   ██║   ")
    print("  ╚══════╝╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ")
    print()
    print("  Harness v0.3 -- Secure, Model-Agnostic, Self-Hosted AI Agent")
    print()

    host = "0.0.0.0"
    port = 8765
    url = f"http://localhost:{port}"

    # Open browser after a short delay (let the server start first)
    def open_browser():
        time.sleep(1.5)
        webbrowser.open(url)

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    print(f"  Starting server on {url}")
    print(f"  Press Ctrl+C to stop.")
    print()

    # Import and run the server
    from sawyer_harness.web.server import run_server
    from sawyer_harness.config import HarnessConfig

    config = HarnessConfig()
    run_server(config, host=host, port=port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Sawyer Agent stopped.")
        sys.exit(0)