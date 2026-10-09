# Contributing to aipager

## Local development

```sh
git clone https://github.com/dev-aly3n/aipager && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

Run the tests under a memory cap as above (on macOS, without
`systemd-run`): a runaway test can otherwise take the machine's memory.

### Running the daemon during development

After `pip install -e .`, three console scripts are on your PATH:

| Script | What it does |
|---|---|
| `aipager` | the CLI (`aipager --help`; sessions start with `aipager session <name>`) |
| `aipager-hook` | Claude Code hook handler - invoked by Claude per event |
| `aipager-statusline` | Claude Code statusLine - invoked on every redraw |

Tweak code, then `aipager start` runs the daemon with your changes
(editable install means no reinstall needed for `.py` edits).

### `dtach` during development

`dtach-bin` is a runtime dependency, so `pip install -e '.[dev]'`
pulls the published version from PyPI. To test changes to `dtach-bin`
itself, install it from a local checkout:

```sh
pip install /path/to/dtach-bin
```

## Release process

Releases are tag-driven. Tagging a commit on `main` triggers
`.github/workflows/publish.yml`, which builds `sdist` + `wheel` and
uploads via PyPI Trusted Publisher (OIDC - no stored API token).

### Cutting a release

1. Bump the version in `pyproject.toml`, `flake.nix` (`version =`),
   `packaging/snap/snapcraft.yaml` (the snap job refuses a tag that does
   not match it) and the README's Docker `Tags:` line.
2. In `CHANGELOG.md`, turn `## [Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD`
   and start a new empty `## [Unreleased]` above it.
3. Commit: `git commit -m "release X.Y.Z"`
4. Tag: `git tag vX.Y.Z && git push origin main --tags`
5. CI builds and publishes to PyPI within a few minutes.
6. Create the GitHub Release by hand (CI does not).

The tag also runs `docker.yml` (the ghcr.io image for amd64 and arm64,
tagged `X.Y.Z` and `X.Y`), `publish.yml`'s `bump-tap` job (regenerates
the Homebrew formula in dev-aly3n/homebrew-tap; needs the `TAP_TOKEN`
secret), `snap.yml` (publishes to the Snap Store once
`SNAPCRAFT_STORE_CREDENTIALS` exists) and `aur.yml` (publishes to the
AUR once `AUR_SSH_PRIVATE_KEY` exists).

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

The CI matrix runs Python 3.10 through 3.13 - keep the codebase free of
3.11+ syntax (no `Self`, no `TypeVarTuple` etc.). Use
`from __future__ import annotations` for new files that need modern
typing.

## Commit style

One-liner subject, lowercase imperative mood, ≤72 chars. No commit
body. Examples:

- `fix transcript path scan to handle multi-cwd setups`
- `add aipager service subcommand for systemd-user installer`

Squash-merge PRs that have noisy intermediate commits - the main branch
log should be a clean reading order.
