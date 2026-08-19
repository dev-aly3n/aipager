"""What a plain `pip install aipager` actually gets you.

Read from `pyproject.toml`, deliberately, NOT from
`importlib.metadata.requires("aipager")`: the dev venv here carries
metadata for a *different, older* aipager (0.4.4 at the time of
writing) than the source tree under test, so a metadata-based assertion
would quietly describe something else entirely — the exact "passes for
reasons unrelated to its name" trap this project has hit repeatedly.
pyproject.toml is the source of truth for what a fresh install resolves.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_aiohttp_is_a_base_dependency():
    """The Mini App must work from a plain install.

    As an opt-in extra, `pip install aipager` + `aipager miniapp enable`
    produced a Mini App that could never start, with the real reason
    only in a daemon log. It caught the person who wrote the surrounding
    code, which is the strongest argument that the extra was a trap.
    """
    deps = " ".join(_project()["dependencies"])
    assert "aiohttp" in deps, f"aiohttp is not a base dependency: {deps}"


def test_the_miniapp_extra_still_exists_and_is_empty():
    """`aipager[miniapp]` appears in this repo's docs and CHANGELOG, in
    released error messages, and in users' shell history. Deleting the
    extra would make every one of those fail with "unknown extra", so it
    stays as an empty compatibility alias that installs the same thing.
    """
    extras = _project()["optional-dependencies"]
    assert "miniapp" in extras, (
        "removing the extra breaks every documented `aipager[miniapp]` "
        "install command for no benefit")
    assert extras["miniapp"] == [], (
        f"the alias must resolve to nothing now that aiohttp is a base "
        f"dependency, or it will pin a second copy: {extras['miniapp']}")


def test_the_voice_extra_is_untouched():
    """Voice stays opt-in — faster-whisper drags in ~200 MB, which is a
    genuinely different order of cost from aiohttp's ~10 MB."""
    extras = _project()["optional-dependencies"]
    assert any("faster-whisper" in d for d in extras.get("voice", [])), (
        "the voice extra should still carry faster-whisper")


# ---- downstream packaging must not drift from pyproject ------------------
#
# `packaging/aur/PKGBUILD` and `flake.nix` hand-maintain their own dependency
# lists. Nothing links them to pyproject.toml, so a dependency added there is
# simply absent for Arch and Nix users — which is how moving aiohttp into the
# base deps would have shipped an aipager whose Mini App silently cannot start
# for exactly those users. Found in review, not by any test.

PKGBUILD = PYPROJECT.parent / "packaging" / "aur" / "PKGBUILD"
FLAKE = PYPROJECT.parent / "flake.nix"

# pyproject name -> (name in PKGBUILD, name in flake.nix)
_DOWNSTREAM_NAMES = {
    "python-telegram-bot": ("python-telegram-bot", "python-telegram-bot"),
    "httpx": ("python-httpx", "httpx"),
    "dtach-bin": ("dtach", None),          # nix wires dtach separately
    "rich": ("python-rich", "rich"),
    "questionary": ("python-questionary", "questionary"),
    "PyYAML": ("python-yaml", "pyyaml"),
    "aiohttp": ("python-aiohttp", "aiohttp"),
}

# Dependencies knowingly absent downstream today. Each entry is a real gap,
# recorded rather than hidden so the drift test can still protect everything
# else. Shrink this list; do not grow it.
_KNOWN_GAPS = {
    ("PKGBUILD", "python-httpx"),   # pulled transitively by python-telegram-bot
    ("PKGBUILD", "python-yaml"),    # genuinely missing — see roadmap
    ("flake.nix", "httpx"),         # pulled transitively by python-telegram-bot
}


def _base_names() -> list[str]:
    out = []
    for spec in _project()["dependencies"]:
        name = spec.split(">=")[0].split("[")[0].split("<")[0].strip()
        out.append(name)
    return out


def test_every_base_dependency_appears_in_the_aur_pkgbuild():
    text = PKGBUILD.read_text(encoding="utf-8")
    missing = []
    for name in _base_names():
        arch = _DOWNSTREAM_NAMES.get(name, (None, None))[0]
        if arch is None or ("PKGBUILD", arch) in _KNOWN_GAPS:
            continue
        if arch not in text:
            missing.append(f"{name} -> {arch}")
    assert not missing, (
        "PKGBUILD's hand-maintained depends=() has drifted from "
        f"pyproject.toml; Arch users would not get: {missing}")


def _flake_runtime_deps() -> str:
    """Just the `dependencies = with python.pkgs; [ ... ]` block.

    Deliberately NOT a whole-file search. flake.nix lists most of the
    same packages twice — once as the application's runtime
    dependencies and once in the devShell — so matching anywhere in the
    file means dropping a package from the runtime list still "passes"
    on the strength of the devShell copy. Proved in review by doing
    exactly that.
    """
    text = FLAKE.read_text(encoding="utf-8")
    marker = "dependencies = with python.pkgs; ["
    start = text.index(marker) + len(marker)
    return text[start:text.index("]", start)]


def test_every_base_dependency_appears_in_the_nix_flake():
    text = _flake_runtime_deps()
    missing = []
    for name in _base_names():
        nix = _DOWNSTREAM_NAMES.get(name, (None, None))[1]
        if nix is None or ("flake.nix", nix) in _KNOWN_GAPS:
            continue
        if nix not in text:
            missing.append(f"{name} -> {nix}")
    assert not missing, (
        "flake.nix's hand-maintained dependency list has drifted from "
        f"pyproject.toml; Nix users would not get: {missing}")


def test_every_base_dependency_has_a_downstream_mapping():
    """A new dependency with no entry here would slip past both checks
    above without anyone noticing."""
    unmapped = [n for n in _base_names() if n not in _DOWNSTREAM_NAMES]
    assert not unmapped, (
        f"add these to _DOWNSTREAM_NAMES (and to PKGBUILD/flake.nix): {unmapped}")
