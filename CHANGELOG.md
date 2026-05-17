# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
