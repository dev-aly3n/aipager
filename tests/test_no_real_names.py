"""No real person's name, handle, machine or session name in the repo.

The repo is public. Examples use neutral names (``@alice``, ``bob``,
sessions ``frontend`` / ``backend``, ``/home/alice``), and a name copied
from a live install into a test or a comment kept coming back. This test
reads every file git tracks or would track (not ignored), and its path,
and fails on any word whose SHA-256 is in :data:`BANNED`
(``tests/neutral_names.py``). Only the hashes are stored, so neither file
spells a name out.

A word is a run of ASCII letters and digits, also split at a lower to
upper case change, so ``-home-x-``, ``X_DM``, ``@x`` and ``xDm`` all count
as the word ``x``, compared case-insensitively. A name made of two words
is checked as the pair joined with ``-``: written ``a-b``, ``a_b``,
``a.b`` or ``aB``, also inside a longer run (``x_aB``, ``a-bY``), and as
``ab``, whose hash is banned too. Two words with a space or a doubled
separator (``a--b``) between them are not paired. A longer word that only starts with a
name (the author handle in the git metadata, say) is a different word and
passes.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.neutral_names import BANNED, as_renamed, owner_handle, sha

ROOT = Path(__file__).resolve().parent.parent

#: A run of letters and digits with single "-", "_" or "." joins inside it.
_RUN = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
#: The words of one letters-and-digits piece, split at a case change.
_WORD = re.compile(r"[A-Z]+(?![a-z])[0-9]*|[A-Z]?[a-z]+[0-9]*|[0-9]+")

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
              ".ruff_cache", "build", "dist"}


def _hash(word: str) -> str:
    return sha(word)


@functools.lru_cache(maxsize=None)
def _is_banned(word: str) -> bool:
    return _hash(word) in BANNED


def _words(run: str) -> list[str]:
    """Every word of one run (see the module docstring), as written."""
    pieces = run.replace("_", "-").replace(".", "-").split("-")
    words = list(pieces)
    flat: list[str] = []  # the run's words in order, each piece split at case changes
    for piece in pieces:
        if piece[1:].islower() or piece.isupper():
            flat.append(piece)
        else:
            split = _WORD.findall(piece)
            words.extend(split)
            flat.extend(split)
    words.extend(f"{a}-{b}" for a, b in zip(pieces, pieces[1:]))
    words.extend(f"{a}-{b}" for a, b in zip(flat, flat[1:]))
    return words


def _banned_words(run: str) -> list[str]:
    return [w for w in _words(run) if _is_banned(w.lower())]


def _tracked_files(root: Path = ROOT) -> list[str]:
    """Tracked paths relative to ``root``; a walk of the tree without git."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard"], cwd=root, check=True,
                             capture_output=True, timeout=30).stdout
        files = [f for f in out.decode().split("\0") if f]
        if files:
            return files
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for top, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in names:
            found.append(Path(top, name).relative_to(root).as_posix())
    return found


#: Bytes that can be part of a run map to themselves, every other to a space.
_KEEP = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
_TO_SPACE = bytes(c if c in _KEEP else 32 for c in range(256))


def _runs(data: bytes) -> set[str]:
    """The unique runs in ``data`` (what ``_RUN`` finds, fast: a C-level
    translate and split instead of a regex over 35 MB)."""
    chunks = set(data.translate(_TO_SPACE).split())
    return set(_RUN.findall(b" ".join(chunks).decode("ascii")))


def offenders(root: Path, files: list[str]) -> list[str]:
    """``path:line: run`` for every banned word in these files' paths and
    text (one pass collecting unique runs, then a line pass for the hits)."""
    datas = {}
    for rel in files:
        try:
            data = (root / rel).read_bytes()
            data.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary file, or one deleted from the work tree
        datas[rel] = data
    runs = _runs(b"\n".join(["\n".join(files).encode(), *datas.values()]))
    words: set[str] = set()
    for run in runs:
        words.update(_words(run))
    if not any(_is_banned(w) for w in {w.lower() for w in words}):
        return []
    bad = {run for run in runs if _banned_words(run)}
    hits = []
    for rel in files:
        if any(run in bad for run in _RUN.findall(rel)):
            hits.append(f"{rel}: (the path)")
        if rel not in datas or not (_runs(datas[rel]) & bad):
            continue
        for n, line in enumerate(datas[rel].decode("utf-8").splitlines(), 1):
            hits.extend(f"{rel}:{n}: {run}" for run in _RUN.findall(line)
                        if run in bad)
    # Fail safe: the first pass found a banned word the line pass missed.
    return hits or ["(a banned word was found but could not be placed)"]


def _shortest_banned_word() -> str:
    """A banned word recovered from its hash, so the tests below can use
    one without this file naming anyone."""
    return owner_handle()


def _caught(text: str) -> bool:
    return any(_banned_words(run) for run in _RUN.findall(text))


def test_no_tracked_file_names_a_real_person():
    hits = offenders(ROOT, _tracked_files())
    assert hits == [], ("a real name is back; use a neutral one (@alice, "
                        "frontend, /home/alice):\n  " + "\n  ".join(hits[:50]))


def test_the_scan_reads_the_whole_tree():
    """The guard is only as good as its file list: it must see the package,
    the tests, the docs and the changelog, not an empty or partial list."""
    files = set(_tracked_files())
    for must in ("aipager/config.py", "tests/conftest.py", "CHANGELOG.md",
                 "tools/i18n/source.jsonl", "docs/hooks.md", "README.md",
                 "tests/test_no_real_names.py"):
        assert must in files
    assert len(files) > 1000


def test_every_disguise_of_a_name_is_caught():
    w = _shortest_banned_word()
    for text in (w, w.upper(), w.title(), f"@{w}", f"/home/{w}/x",
                 f"-home-{w}-repo", f"{w.upper()}_DM", f"_{w}_dm", f"{w}Dm",
                 f"{w.title()}'s", f'label="{w} DM"'):
        assert _caught(text), text


def test_two_word_and_digit_names_are_caught(monkeypatch, request):
    """Synthetic names (hashed here) stand in for the real two-word and
    letters-and-digits ones."""
    monkeypatch.setattr(sys.modules[__name__], "BANNED",
                        BANNED | {_hash("foo-bar"), _hash("foobar"), _hash("baz-qux"),
                                  _hash("qx7")})
    _is_banned.cache_clear()
    request.addfinalizer(_is_banned.cache_clear)
    for text in ("foo-bar", "foo_bar", "FOO_BAR", "claude-foo_bar", "a foo-bar b",
                 "foo.bar", "FooBar", "fooBar", "x_fooBar_y", "foobar", "FOOBAR",
                 "BazQux", "bazQux", "BAZ_QUX", "myBaz-qux", "baz-quxX", "baz_QuxDm",
                 "x.bazQux.y", "qx7", "QX7", "on qx7's DM",
                 "test_replay_qx7.py"):
        assert _caught(text), text
    for text in ("foo", "bar", "foo-baz", "foobaz", "foo bar", "bazqux", "qx70", "qx"):
        assert not _caught(text), text


def test_a_longer_word_that_contains_a_name_passes():
    w = _shortest_banned_word()
    for text in (f"{w}3n", f"dev.{w}3n@example.com", f"dev-{w}3n", f"an{w}sis",
                 f"{w}x", "frontend"):
        assert not _caught(text), text


def test_offenders_names_the_file_line_and_path(tmp_path):
    w = _shortest_banned_word()
    (tmp_path / "ok.py").write_text("OWNER = 'alice'\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(f"x = 1\nOWNER = '{w}'\n", encoding="utf-8")
    (tmp_path / f"test_{w}.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe" + w.encode())
    files = ["ok.py", "bad.py", f"test_{w}.py", "blob.bin"]
    assert offenders(tmp_path, files) == ["bad.py:2: " + w,
                                          f"test_{w}.py: (the path)"]
    assert offenders(tmp_path, ["ok.py"]) == []


def test_as_renamed_renames_the_old_handle_and_nothing_else():
    w = _shortest_banned_word()
    old = f'"[via Telegram · @{w}]" **{w}** -home-{w}- {w}3n an{w}sis'.encode()
    assert as_renamed(old) == (
        f'"[via Telegram · @alice]" **alice** -home-alice- {w}3n an{w}sis').encode()
    assert as_renamed(b"nothing to rename") == b"nothing to rename"


def test_a_hit_the_line_pass_cannot_place_still_fails(tmp_path, monkeypatch):
    """The fail safe: when the first pass finds a banned word and the line
    pass finds none (the two passes disagree), the scan still reports."""
    w = _shortest_banned_word()
    (tmp_path / "bad.py").write_text(f"OWNER = '{w}'\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_banned_words", lambda run: [])
    assert offenders(tmp_path, ["bad.py"]) == [
        "(a banned word was found but could not be placed)"]
