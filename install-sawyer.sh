#!/usr/bin/env bash
# ── Sawyer Agent installer (Linux / macOS) ──────────────────────
#
#  Usage:
#    curl -sL https://raw.githubusercontent.com/drc10101/sawyer-agent/master/install-sawyer.sh | bash
#    or:  bash install-sawyer.sh [command]
#
#  Commands:
#    (none)     Full install: Python check + pip install + setup + shortcut
#    reinstall  Update package + re-run setup + shortcut
#    setup      Reconfigure API key and provider
#    uninstall  Remove Sawyer completely
#    start      Start the server (browser opens automatically)
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "  ${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "  ${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "  ${RED}[ERROR]${NC} $*"; }

COMMAND="${1:-install}"

usage() {
    echo
    echo "  Usage: install-sawyer.sh [command]"
    echo
    echo "  Commands:"
    echo "    (none)      Full install: Python check + pip install + setup + shortcut"
    echo "    reinstall   Update package and reconfigure"
    echo "    setup       Reconfigure API key and provider"
    echo "    uninstall   Remove Sawyer completely"
    echo "    start       Start the server"
    echo
    exit 1
}

case "$COMMAND" in
    install|reinstall|setup|uninstall|start) ;;
    -h|--help|help) usage ;;
    *) error "Unknown command: $COMMAND"; usage ;;
esac

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

PYTHON_CMD=""

find_python() {
    # Prefer python3, fall back to python
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 11 ]]; }; then
                PYTHON_CMD="$cmd"
                return 0
            fi
        fi
    done
    return 1
}

ensure_pip() {
    if "$PYTHON_CMD" -m pip --version &>/dev/null; then
        return 0
    fi
    warn "pip not found. Attempting to bootstrap..."
    "$PYTHON_CMD" -m ensurepip --upgrade 2>/dev/null || {
        error "pip is not available and could not be installed."
        error "Install pip via your package manager, then re-run."
        if [[ "$(uname -s)" == "Linux" ]]; then
            error "  Debian/Ubuntu: sudo apt install python3-pip"
            error "  Fedora:        sudo dnf install python3-pip"
            error "  Arch:          sudo pacman -S python-pip"
        elif [[ "$(uname -s)" == "Darwin" ]]; then
            error "  Reinstall Python from https://python.org or: brew install python3"
        fi
        exit 1
    }
    ok "pip is now available."
}

create_venv() {
    local venv_dir="$HOME/.sawyer-harness/venv"
    if [[ -f "$venv_dir/bin/activate" ]]; then
        ok "Virtual environment already exists at $venv_dir"
    else
        info "Creating virtual environment..."
        "$PYTHON_CMD" -m venv "$venv_dir" || {
            error "Failed to create virtual environment."
            error "Falling back to system-wide install."
            return 1
        }
        ok "Virtual environment created at $venv_dir"
    fi
    source "$venv_dir/bin/activate"
    ok "Virtual environment activated."
}

install_shortcuts() {
    info "Creating desktop shortcut..."
    "$PYTHON_CMD" -m sawyer_harness install-shortcuts 2>/dev/null && \
        ok "Desktop shortcut created." || \
        warn "Could not create desktop shortcut. Run manually: python -m sawyer_harness install-shortcuts"
}

# ────────────────────────────────────────────────────────────────
# Commands
# ────────────────────────────────────────────────────────────────

do_install() {
    echo
    echo "  ============================================"
    echo "   Sawyer Agent - Install"
    echo "  ============================================"
    echo

    # Check Python
    if ! find_python; then
        error "Python 3.11+ not found."
        echo
        echo "  Sawyer requires Python 3.11 or later."
        if [[ "$(uname -s)" == "Darwin" ]]; then
            echo "  Download from: https://python.org"
            echo "  Or: brew install python3"
        else
            echo "  Install via your package manager or download from https://python.org"
        fi
        exit 1
    fi
    ok "Python found: $($PYTHON_CMD --version 2>&1)"

    # Check pip
    ensure_pip

    # Ask about venv (non-interactive mode skips the prompt)
    if [[ -t 0 ]]; then
        echo
        read -r -p "  Install in virtual environment? [Y/n] " use_venv
        use_venv="${use_venv:-Y}"
    else
        use_venv="Y"
    fi

    if [[ "${use_venv,,}" != "n" && "${use_venv,,}" != "no" ]]; then
        create_venv || true  # continue even if venv fails
    fi

    # Install
    echo
    info "Installing Sawyer Agent..."
    "$PYTHON_CMD" -m pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade || {
        error "Installation failed."
        error "Check your internet connection and try again."
        error "If this persists, try: pip install --no-cache-dir git+https://github.com/drc10101/sawyer-agent.git"
        exit 1
    }
    ok "Package installed."

    # Setup wizard
    echo
    info "Running setup wizard..."
    echo
    "$PYTHON_CMD" -m sawyer_harness setup || {
        warn "Setup did not complete. Run manually: python -m sawyer_harness setup"
    }

    # Desktop shortcut
    install_shortcuts

    echo
    echo "  ============================================"
    echo "   Done! Sawyer Agent is ready."
    echo
    echo "   Run 'sawyer-web' or 'python -m sawyer_harness'"
    echo "   to start. The browser opens automatically."
    echo
    echo "   Other commands:"
    echo "     install-sawyer.sh reinstall   Update and reconfigure"
    echo "     install-sawyer.sh setup       Reconfigure API key only"
    echo "     install-sawyer.sh uninstall   Remove everything"
    echo "  ============================================"
    echo
}

do_reinstall() {
    echo
    echo "  ============================================"
    echo "   Sawyer Agent - Reinstall"
    echo "  ============================================"
    echo

    if ! find_python; then
        error "Python 3.11+ not found."
        exit 1
    fi

    # Activate venv if it exists
    if [[ -f "$HOME/.sawyer-harness/venv/bin/activate" ]]; then
        source "$HOME/.sawyer-harness/venv/bin/activate"
    fi

    info "Updating Sawyer Agent..."
    "$PYTHON_CMD" -m pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade --force-reinstall --no-deps || {
        error "Update failed."
        exit 1
    }
    ok "Package updated."

    echo
    "$PYTHON_CMD" -m sawyer_harness setup
    install_shortcuts

    echo
    echo "  Reinstall complete."
}

do_setup() {
    echo
    find_python || { error "Python not found."; exit 1; }
    [[ -f "$HOME/.sawyer-harness/venv/bin/activate" ]] && source "$HOME/.sawyer-harness/venv/bin/activate"
    "$PYTHON_CMD" -m sawyer_harness setup
}

do_uninstall() {
    echo
    echo "  ============================================"
    echo "   Sawyer Agent - Uninstall"
    echo "  ============================================"
    echo

    find_python || PYTHON_CMD="python3"
    [[ -f "$HOME/.sawyer-harness/venv/bin/activate" ]] && source "$HOME/.sawyer-harness/venv/bin/activate"
    "$PYTHON_CMD" -m sawyer_harness uninstall
}

do_start() {
    echo
    echo "  Starting Sawyer Agent..."
    echo "  Browser will open automatically when ready."
    echo

    find_python || { error "Python not found."; exit 1; }
    [[ -f "$HOME/.sawyer-harness/venv/bin/activate" ]] && source "$HOME/.sawyer-harness/venv/bin/activate"
    "$PYTHON_CMD" -m sawyer_harness
}

# ────────────────────────────────────────────────────────────────
# Main dispatch
# ────────────────────────────────────────────────────────────────

case "$COMMAND" in
    install)    do_install ;;
    reinstall)  do_reinstall ;;
    setup)      do_setup ;;
    uninstall)  do_uninstall ;;
    start)      do_start ;;
esac