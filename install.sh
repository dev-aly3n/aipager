#!/bin/sh
# aipager — one-line installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
#
# This script:
#   1. Detects your OS and shell environment.
#   2. Picks the most appropriate installer that's already present
#      (Homebrew on macOS → pipx → uv tool).
#   3. Installs aipager (which transitively pulls dtach-bin so the dtach
#      binary lands on PATH automatically).
#   4. Prints the next-step commands.
#
# After install, run `aipager config` to set up the Telegram bot, then
# `aipager start` to run the daemon. See:
#   https://github.com/dev-aly3n/aipager

set -eu

OS="$(uname -s)"
ARCH="$(uname -m)"

cmd_exists() { command -v "$1" >/dev/null 2>&1; }

info()  { printf "%s\n" "$*"; }
fatal() { printf "✗ %s\n" "$*" >&2; exit 1; }

info "→ Detecting environment ($OS $ARCH)..."

# Prefer Homebrew on macOS — pulls dtach via the system formula, no need
# for dtach-bin's Python wheel, and works on both Intel and Apple Silicon.
if [ "$OS" = "Darwin" ] && cmd_exists brew; then
    info "→ Installing via Homebrew (dev-aly3n/tap/aipager)..."
    brew install dev-aly3n/tap/aipager
    INSTALLED=1
fi

# Otherwise: pipx, then uv tool.
if [ -z "${INSTALLED:-}" ]; then
    if cmd_exists pipx; then
        info "→ Installing via pipx..."
        pipx install aipager
        INSTALLED=1
    elif cmd_exists uv; then
        info "→ Installing via uv tool..."
        uv tool install aipager
        INSTALLED=1
    fi
fi

if [ -z "${INSTALLED:-}" ]; then
    cat >&2 <<'EOF'
✗ Neither brew, pipx, nor uv is available on PATH.

Install one of them, then re-run this script:

  pipx (recommended)
    Debian / Ubuntu:  sudo apt install pipx
    Fedora:           sudo dnf install pipx
    macOS:            brew install pipx

  uv (alternative)
    curl -LsSf https://astral.sh/uv/install.sh | sh

  Homebrew (macOS)
    https://brew.sh

Or do a manual venv install:
    python3 -m venv ~/.aipager-venv
    ~/.aipager-venv/bin/pip install aipager
    ~/.aipager-venv/bin/aipager config
    ~/.aipager-venv/bin/aipager start
EOF
    exit 1
fi

# Sanity: confirm the binary is on PATH now (pipx ensurepath / hash bash).
hash -r 2>/dev/null || true
if ! cmd_exists aipager; then
    cat <<'EOF'

! aipager was installed but is not on your current shell's PATH.
  This usually means a new shell session is needed, OR `pipx ensurepath`
  hasn't been run. Try:

      pipx ensurepath        # then open a new shell
      # or, for this shell only:
      export PATH="$HOME/.local/bin:$PATH"

EOF
    exit 0
fi

info ""
info "✓ aipager installed: $(command -v aipager)"
info ""
info "Next steps:"
info "  aipager config     # interactive setup (Telegram bot token + chat ID)"
info "  aipager start      # run the daemon (Ctrl-C to stop)"
info ""
info "To survive logout (Linux systemd-user / macOS launchd):"
info "  aipager service install"
info ""
info "Docs: https://github.com/dev-aly3n/aipager"
