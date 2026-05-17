# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- `aipager config` final-step hint no longer mentions
  `aipager service install` as a secondary option — only
  `aipager start` is shown to keep the new-user path simple. The
  service flow is still documented in the README.

## [0.3.7] - 2026-05-17

### Added
- `/start` and `/help` commands in the Telegram bot now return a
  friendly welcome message with the list of tracked sessions and
  usage hints, instead of the previous `⚠️ Unknown session: start`.
- `aipager config` now verifies the bot can actually reach your chat
  by sending a test message. If Telegram replies with "chat not
  found" (the user hasn't tapped Start on the bot yet), the wizard
  prints a precise instruction and waits while the user opens the
  bot in Telegram, then retries automatically.

### Changed
- When the daemon fails to send to the configured chat with "chat
  not found", the log now points at the fix
  (`open https://t.me/<bot>`) instead of dumping a 30-line traceback.

## [0.3.6] - 2026-05-17

### Fixed
- Bumped HTTP connect timeouts (10 s → 30 s) so the daemon's initial
  `getMe` call survives slow TLS handshakes through HTTPS proxies or
  VPN tunnels. Affected users got an unhelpful `httpx.ConnectTimeout`
  during `aipager start` on networks where curl with 30 s succeeded.
- `aipager config`'s urllib timeout also bumped 10 s → 30 s for the
  same reason — the wizard's `getMe` could spuriously fail on first
  attempt and report "Token invalid or Telegram unreachable" when the
  real issue was proxy latency.

## [0.3.5] - 2026-05-17

### Fixed
- `aipager config` no longer falsely reports `✗ dtach not on PATH` in
  pipx / uv-tool / brew-venv layouts where the bundled binary lives
  inside the venv but isn't on the shell's PATH. The check now uses
  `dtach_bin.path()` (which knows about the venv layout) before
  falling back to PATH.
- `aipager config` final-step hint corrected: was `claude-dtach dev`,
  now says `aipager session dev`. The `claude-dtach` console script was
  removed in 0.3.2 and the stale hint slipped through.

## [0.3.4] - 2026-05-17

### Added
- Pre-flight checks for `aipager start`, `aipager session`, and
  `aipager service install`. Subcommands now fail fast with friendly
  multi-line error messages (exit code 2) when:
  - Telegram bot token or chat ID is missing
    → `aipager config`
  - The `claude` binary isn't on PATH
    → install Claude Code
  - The aipager daemon isn't running (for `session` only)
    → `aipager start` or `aipager service start`
- New module `aipager.preflight` (with tests in `tests/test_preflight.py`)
  hosts the checks so adding new ones in the future is a one-liner.

## [0.3.3] - 2026-05-17

### Changed
- Renamed `aipager new <name>` to `aipager session <name>`. The behavior
  is the same (open the session, creating if it doesn't exist), but the
  verb no longer falsely implies "always create new" — the command
  reattaches transparently when the dtach session is alive.

### Added
- `aipager session <name> --resume` — when creating a fresh dtach
  session, also pass `--continue` to claude so it loads the most recent
  saved conversation in the current cwd. A no-op when reattaching to an
  existing dtach session (claude is already running there).

## [0.3.2] - 2026-05-17

### Added
- `aipager new <name>` subcommand that creates or reattaches a Claude
  Code session under dtach. Replaces the `claude-dtach` console script.

### Changed
- `claude-dtach` console script removed from `[project.scripts]` —
  the same functionality is now `aipager new` so the user-facing CLI is
  unified under a single `aipager` entry point.
- `dtach` binary discovery now goes through `dtach_bin.path()` first,
  which checks `<sys.prefix>/bin/dtach` before falling back to a PATH
  lookup. This makes `uv tool install aipager` / `pipx install aipager`
  installs work out of the box even though those layouts don't put the
  tool's private `bin/` on the shell's PATH.

### Dependencies
- Bumped `dtach-bin` floor to `>=0.9.1` for the new `path()` semantics.

## [0.3.1] - 2026-05-17

### Changed
- `install.sh` now prefers `uv tool install` over Homebrew, and
  bootstraps `uv` via Astral's installer if no Python tool manager is
  found locally. uv bundles its own Python (python-build-standalone) so
  the install path is immune to Homebrew Python bottle bugs (notably the
  `libexpat _XML_SetAllocTrackerActivationThreshold` symbol mismatch on
  macOS Tahoe).
- README now leads with the `curl … | sh` one-liner and the `uv`
  path; the Homebrew tap is documented as a secondary option with a
  call-out about the Tahoe issue.

## [0.3.0] - 2026-05-17

### Added
- `aipager service` subcommand for cross-platform service management.
  Installs aipager as a systemd-user unit on Linux or a launchd plist on
  macOS, so the daemon survives logout. Subcommands: `install`, `start`,
  `stop`, `status`, `logs`, `uninstall`. Unit/plist always references
  the absolute path of `aipager` resolved via `shutil.which`, so it
  works whether aipager came from pipx, brew, or an editable install.
- `install.sh` one-line installer script. Detects the available installer
  (Homebrew on macOS → pipx → uv tool) and uses whichever is present.
  Available via:
  `curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh`.

### Removed
- `scripts/aipager.service.example` (replaced by the template inside
  `aipager.service`, written by `aipager service install`).

## [0.2.1] - 2026-05-17

### Changed
- README now documents the live Homebrew tap install path
  (`brew install dev-aly3n/tap/aipager`). Replaces the
  earlier "coming in v0.3" placeholder.

## [0.2.0] - 2026-05-16

### Added
- Depend on [`dtach-bin`](https://pypi.org/project/dtach-bin/) so
  `pipx install aipager` pulls in a precompiled `dtach` binary for
  Linux x86_64/aarch64 and macOS x86_64/arm64. No manual system
  package install needed.
- GitHub Actions workflows: `test.yml` (ruff + pytest on Python
  3.10–3.13) and `publish.yml` (build + Trusted Publisher OIDC upload
  on tag push).
- `CONTRIBUTING.md` documenting the local dev setup and release flow.

### Changed
- README leads with `pipx install aipager` as the primary install
  path; `pip install -e .` demoted to a "Developing locally" section.

## [0.1.0] - 2026-05-16

### Added
- `pyproject.toml` with hatchling backend
- MIT license
- README and Changelog
- Console script entry points: `aipager`, `aipager-hook`,
  `aipager-statusline`, `claude-dtach`
- `aipager` CLI with `start`, `config`, `version` subcommands
- `aipager config` — interactive setup wizard that patches
  `~/.claude/settings.json` and writes `~/.config/aipager/config.env`
- XDG-compliant config path (`~/.config/aipager/config.env`) with cwd
  `.env` fallback
- Pure-Python port of the `claude-dtach` session launcher
- Test suite for state machine, markdown→HTML converter, and config loader

### Fixed
- Removed hardcoded transcript directory path that worked on only one
  machine; transcript discovery now scans all project subdirs under
  `~/.claude/projects/`.
