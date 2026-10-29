"""No real person's name, handle, machine or session name, and no real
chat id, in the repo.

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

A chat id is any whole run of digits inside a run (``d…`` and ``g…`` scope
suffixes, ``name__d…`` labels, ``group-…`` folders, ``-…`` group ids),
failing when the SHA-256 of its digits, or of what follows a leading
``100`` (a group's ``-100…`` supergroup form), is in :data:`BANNED_IDS`.
Any other longer or shorter number that holds those digits is a different
number and passes, and so do digits split by a separator (``1_000``):
a cut-down copy of a real id is not caught, so review fixtures by hand.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.neutral_names import BANNED, BANNED_IDS, as_renamed, id_forms, owner_handle, sha

ROOT = Path(__file__).resolve().parent.parent

#: A run of letters and digits with single "-", "_" or "." joins inside it.
_RUN = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
#: The words of one letters-and-digits piece, split at a case change.
_WORD = re.compile(r"[A-Z]+(?![a-z])[0-9]*|[A-Z]?[a-z]+[0-9]*|[0-9]+")
#: Every whole run of digits in a run: the chat id candidates.
_DIGITS = re.compile(r"[0-9]+")

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
              ".ruff_cache", "build", "dist"}


def _hash(word: str) -> str:
    return sha(word)


@functools.lru_cache(maxsize=None)
def _is_banned(word: str) -> bool:
    if word.isdigit():
        return any(_hash(d) in BANNED_IDS for d in id_forms(word))
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
    words.extend(_DIGITS.findall(run))
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
    assert hits == [], ("a real name or chat id is back; use a neutral one "
                        "(@alice, frontend, /home/alice, 222333444, -4100000001):"
                        "\n  " + "\n  ".join(hits[:50]))


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


@pytest.fixture
def standin_ids(monkeypatch, request):
    """Arm the scan with stand-in chat ids (hashed here) in place of the
    real ones, a DM id and a group id of the real ones' digit counts."""
    monkeypatch.setattr(sys.modules[__name__], "BANNED_IDS",
                        frozenset({_hash("123456789"), _hash("4000000999")}))
    _is_banned.cache_clear()
    request.addfinalizer(_is_banned.cache_clear)


def test_a_chat_id_is_caught_in_every_form(standin_ids):
    for text in ("123456789", "chat 123456789.", "CHAT_ID = '123456789'",
                 "d123456789", "claude-api__d123456789", "api__d123456789",
                 "-4000000999", "g4000000999", "group-4000000999",
                 "claude-api__g4000000999", '{"chat_id": -4000000999}',
                 "x123456789y", "/tmp/d123456789/x", "-1004000000999",
                 "100123456789"):
        assert _caught(text), text
    for text in ("1234567890", "0123456789", "12345678", "23456789",
                 "123_456_789", "-40000009990", "222333444", "-4100000001",
                 "1014000000999", "2004000000999"):
        assert not _caught(text), text


def test_offenders_places_a_chat_id(tmp_path, standin_ids):
    (tmp_path / "ok.py").write_text("CHAT = 222333444\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text('x = 1\nNAME = "api__g4000000999"\n',
                                     encoding="utf-8")
    assert offenders(tmp_path, ["ok.py", "bad.py"]) == ["bad.py:2: g4000000999"]
    assert offenders(tmp_path, ["ok.py"]) == []


def test_the_real_ids_are_armed_and_the_fakes_pass():
    """Five id hashes, none of them a fake the tree uses instead."""
    assert len(BANNED_IDS) == 5
    for fake in ("222333444", "4100000001", "333444555", "3334445556", "111333555",
                 "900000001", "4000000001", "1004000000001"):
        assert not _is_banned(fake), fake


#: The last commit with the operator's ids in clear text.
_BEFORE = "76b3684e"


def _at_commit(rel: str) -> str:
    try:
        res = subprocess.run(["git", "show", f"{_BEFORE}:{rel}"], cwd=ROOT,
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available")
    if res.returncode != 0:
        pytest.skip(f"{_BEFORE} is not in this checkout")
    return res.stdout.decode()


def test_the_operator_ids_hash_to_the_ones_armed():
    """A wrong hash would quietly disarm the scan and the harness tripwire:
    rebuild both from the commit before they left the tree."""
    from tests.neutral_names import OPERATOR_IDS
    dm = re.search(r"^REAL_CHAT_ID = ([0-9]+)$",
                   _at_commit("tests/e2e/fake_telegram/instance.py"), re.M)
    group = re.search(r"group -([0-9]+) \u2192", _at_commit("aipager/scope.py"))
    assert dm and group
    assert (sha(dm.group(1)), sha(group.group(1))) == OPERATOR_IDS


def test_as_renamed_renames_a_chat_id_as_a_whole_number(monkeypatch):
    from tests import neutral_names
    monkeypatch.setattr(neutral_names, "_ID_RENAMES",
                        {_hash("123456789"): b"999888777"})
    old = b'"x__d123456789" -123456789 1234567890 0123456789 123456789'
    assert as_renamed(old) == (
        b'"x__d999888777" -999888777 1234567890 0123456789 999888777')
