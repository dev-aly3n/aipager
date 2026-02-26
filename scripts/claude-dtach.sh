#!/bin/bash
# claude-dtach — run Claude Code inside a named dtach session
#
# Usage:
#   claude-dtach dev              → dtach session "claude-dev", runs: claude
#   claude-dtach -y dev           → same but with --dangerously-skip-permissions
#   claude-dtach auth --resume    → dtach session "claude-auth", runs: claude --resume
#   claude-dtach dev -p "fix X"   → dtach session "claude-dev", runs: claude -p "fix X"
#
# dtach passes ALL terminal bytes transparently — mouse, clipboard, scroll
# all work natively. No tmux mouse-capture issues.
#
# The daemon injects keystrokes via: echo "text" | dtach -p <socket>

set -euo pipefail

# Parse -y flag (must come before session name)
SKIP_PERMS=""
if [ "${1:-}" = "-y" ]; then
    SKIP_PERMS="--dangerously-skip-permissions"
    shift
fi

if [ $# -lt 1 ]; then
    echo "Usage: claude-dtach [-y] <name> [claude args...]"
    echo ""
    echo "Options:"
    echo "  -y  Pass --dangerously-skip-permissions to claude"
    echo ""
    echo "Examples:"
    echo "  claude-dtach dev              # start claude in dtach session 'claude-dev'"
    echo "  claude-dtach -y dev           # start with skip-permissions"
    echo "  claude-dtach auth --resume    # resume session in 'claude-auth'"
    echo "  claude-dtach test -p 'hello'  # run with prompt in 'claude-test'"
    exit 1
fi

NAME="$1"
shift
SESSION="claude-${NAME}"
SOCK="/tmp/claude-dtach-${NAME}.sock"

# Check if dtach is installed
if ! command -v dtach &>/dev/null; then
    echo "Error: dtach not installed. Run: sudo apt install dtach"
    exit 1
fi

# Keep terminal tab title set to session name.
# Claude Code overrides the title continuously, so we fight back with a loop.
# Runs in background; killed when dtach exits.
_keep_title() {
    while sleep 3; do
        printf '\033]0;%s\007' "$1" 2>/dev/null || break
    done
}

# Force Claude Code to redraw after reattach (dtach has no screen buffer).
# Bounces the PTY window size (rows-1 then restore) to trigger genuine
# SIGWINCH signals that defeat the kernel/Node.js/Ink same-size guards.
_force_redraw() {
    sleep 0.8
    python3 "$(dirname "$(readlink -f "$0")")/dtach-redraw.py" "$1" 2>/dev/null
}

# If socket exists and session is alive, attach
if [ -S "$SOCK" ]; then
    echo "dtach session '$SESSION' exists — attaching..."
    printf '\033]0;%s\007' "$NAME"
    _keep_title "$NAME" &
    TITLE_PID=$!
    _force_redraw "$NAME" &
    dtach -a "$SOCK" -r winch -E
    kill $TITLE_PID 2>/dev/null
    exit 0
fi

# Start new detached session, then attach
# -n: create without attaching
# -E: disable detach character (no accidental detach)
# -z: pass Ctrl-Z through to claude
# CLAUDE_DTACH_SESSION env var lets notify_hook.py identify the session
echo "Starting Claude in dtach session '$SESSION'..."
dtach -n "$SOCK" -Ez bash -c "export CLAUDE_DTACH_SESSION=$SESSION; claude $SKIP_PERMS --append-system-prompt 'Your session name is \"$NAME\". When users address you by this name, respond naturally — it is your name in this session.' $*"
sleep 0.3
printf '\033]0;%s\007' "$NAME"
_keep_title "$NAME" &
TITLE_PID=$!
dtach -a "$SOCK" -r winch -E
kill $TITLE_PID 2>/dev/null
