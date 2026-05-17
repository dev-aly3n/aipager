#!/bin/sh
# aipager — one-line installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
#
# This script:
#   1. Detects which Python installer you already have (uv → pipx → brew).
#   2. If none is present, bootstraps uv via Astral's official installer.
#   3. Installs aipager (which transitively pulls dtach-bin so the dtach
#      binary lands on PATH automatically).
#   4. Prints the next-step commands.
#
# uv is preferred because it bundles its own Python interpreter
# (python-build-standalone) and is therefore immune to system-Python
# bugs (e.g. the libexpat symbol mismatch on Homebrew Python on macOS
# Tahoe 26.x).
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

case "$OS" in
    Linux|Darwin) ;;
    *) fatal "Unsupported OS: $OS (aipager supports Linux and macOS only)" ;;
esac

# ── Priority 1: uv ────────────────────────────────────────────────
# uv bundles its own Python — no host Python or Homebrew Python
# needed, no brittle system-library dependencies. This is the most
# reliable path on any OS / version.
if cmd_exists uv; then
    info "→ Installing via uv tool..."
    uv tool install aipager
    INSTALLED=1
fi

# ── Priority 2: pipx (already-installed) ──────────────────────────
if [ -z "${INSTALLED:-}" ] && cmd_exists pipx; then
    info "→ Installing via pipx..."
    pipx install aipager
    INSTALLED=1
fi

# ── Priority 3: Homebrew tap ──────────────────────────────────────
# Last resort because Homebrew Python on some macOS versions has
# libexpat symbol issues unrelated to aipager.
if [ -z "${INSTALLED:-}" ] && [ "$OS" = "Darwin" ] && cmd_exists brew; then
    info "→ Installing via Homebrew (dev-aly3n/tap/aipager)..."
    info "  Note: if this fails with a libexpat / pyexpat error on macOS Tahoe,"
    info "  re-run this script after installing uv — it bypasses Homebrew Python."
    brew install dev-aly3n/tap/aipager
    INSTALLED=1
fi

# ── Bootstrap uv if nothing else is available ─────────────────────
if [ -z "${INSTALLED:-}" ]; then
    info "→ Bootstrapping uv (Astral's Python tool manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # uv's installer puts the binary in ~/.local/bin or ~/.cargo/bin
    # depending on version, and adds it to PATH for *new* shells. For
    # this script, add it now so we can run it.
    if ! cmd_exists uv; then
        for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
            [ -x "$d/uv" ] && PATH="$d:$PATH" && export PATH
        done
    fi

    if ! cmd_exists uv; then
        fatal "uv bootstrap failed — see https://docs.astral.sh/uv/getting-started/installation/"
    fi

    info "→ Installing aipager via uv tool..."
    uv tool install aipager
    INSTALLED=1
fi

# Ensure new console scripts are on PATH for this shell session
hash -r 2>/dev/null || true

if ! cmd_exists aipager; then
    cat <<'EOF'

! aipager was installed but is not on your current shell's PATH.
  A new shell session will pick it up. To make it available now:

      # For uv:    export PATH="$HOME/.local/bin:$PATH"
      # For pipx:  pipx ensurepath        (then open a new shell)
      # For brew:  already on PATH; check `which aipager`

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
