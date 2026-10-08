# Packaging manifests

Build recipes for distribution channels that don't fit in the main
repo:

| Channel | Path | Status |
|---|---|---|
| Arch User Repository | [`aur/`](aur/) | Not published yet: the release workflow pushes to `aur.archlinux.org/aipager.git` once the maintainer's AUR key is set up; until then it skips |
| Snap Store | [`snap/`](snap/) | Strict confinement, bundles python + node + `claude` + `dtach` |

PyPI, Docker, Homebrew, and Nix don't live here - those are wired
directly into their respective ecosystems (`pyproject.toml` →
GitHub Actions → PyPI, `Dockerfile` → ghcr.io, `flake.nix` →
`nix run github:dev-aly3n/aipager`, the [dev-aly3n/homebrew-tap](https://github.com/dev-aly3n/homebrew-tap)
repo for brew).

Each subdirectory has its own README with the per-release ritual.
