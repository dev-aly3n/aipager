# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
