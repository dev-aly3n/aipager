# Contributing to aipager

## Local development

```sh
git clone https://github.com/dev-aly3n/aipager && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check aipager tests
```

### Running the daemon during development

After `pip install -e .`, four console scripts are on your PATH:

| Script | What it does |
|---|---|
| `aipager` | the CLI dispatcher (`start`, `config`, `version`) |
| `aipager-hook` | Claude Code hook handler — invoked by Claude per event |
| `aipager-statusline` | Claude Code statusLine — invoked on every tick |
| `claude-dtach` | launches a Claude Code session under `dtach` |

Tweak code, then `aipager start` runs the daemon with your changes
(editable install means no reinstall needed for `.py` edits).

### `dtach` during development

`dtach-bin` is a runtime dependency. If it's not yet on PyPI (or you're
testing changes to it), install from a local checkout:

```sh
pip install /path/to/dtach-bin
```

Otherwise `pip install -e '.[dev]'` will pull the published version
from PyPI.

## Release process

Releases are tag-driven. Tagging a commit on `main` triggers
`.github/workflows/publish.yml`, which builds `sdist` + `wheel` and
uploads via PyPI Trusted Publisher (OIDC — no stored API token).

### Cutting a release

1. Bump `version` in `pyproject.toml`
2. Add a `[X.Y.Z]` section at the top of `CHANGELOG.md`
3. Commit: `git commit -m "bump version to X.Y.Z"`
4. Tag: `git tag vX.Y.Z && git push origin main --tags`
5. CI builds and publishes within ~2 minutes

### First-time PyPI Trusted Publisher setup (one-time)

Before the first OIDC release, you need:

1. A PyPI account at https://pypi.org/account/register/
2. The first upload done manually with an API token:
   ```sh
   pip install build twine
   python -m build
   twine upload dist/*
   ```
3. Register a Trusted Publisher on PyPI for the project:
   - Project settings → Publishing → Add a trusted publisher
   - Provider: GitHub Actions
   - Owner: `dev-aly3n`
   - Repository: `aipager`
   - Workflow file: `publish.yml`
   - Environment: (leave blank)

After this, all future releases use OIDC. No tokens are stored anywhere.

## Style / linting

```sh
ruff check aipager tests
ruff format aipager tests  # if you want auto-formatting
```

The CI matrix runs Python 3.10 through 3.13 — keep the codebase free of
3.11+ syntax (no `Self`, no `TypeVarTuple` etc.). Use
`from __future__ import annotations` for new files that need modern
typing.

## Commit style

One-liner subject, lowercase imperative mood, ≤72 chars. No commit
body. Examples:

- `fix transcript path scan to handle multi-cwd setups`
- `add aipager service subcommand for systemd-user installer`

Squash-merge PRs that have noisy intermediate commits — the main branch
log should be a clean reading order.
