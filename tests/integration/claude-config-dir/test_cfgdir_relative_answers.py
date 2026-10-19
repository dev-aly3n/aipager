"""No start-up answers go into a global config whose folder is relative,
in particular a legacy ``.config.json`` inside the session's own folder
(entrypoints ``prepare_session_start``: "Writes nothing (returns ``[]``)
when ``global_relative`` (a whitespace or relative value, or an empty
value whose start home holds the legacy file in use); an empty value
otherwise answers in ``~/.claude.json``"; ``ClaudePaths.global_relative``;
review-1 nit on ``claude_paths.py:196-214``; probe P3).

With ``CLAUDE_CONFIG_DIR=""`` the start home is the launch folder, so the
legacy check looks at ``<launch folder>/.config.json``; the new-style file
for ``""`` is ``~/.claude.json`` (``||`` falls back to the home folder).
"""

from __future__ import annotations

import pytest

from aipager import claude_bootstrap, claude_paths
from aipager.dtach.inject import launch_session

from _cfgdir_kit import KEY, rjson, wjson

CCD = "CLAUDE_CONFIG_DIR"


def _prepare(cc, value, **extra):
    env = {CCD: value, **extra}
    return claude_bootstrap.prepare_session_start(str(cc.work), env, cwd=str(cc.work))


def _legacy(cc):
    return cc.work / ".config.json"


def _nonempty_legacy(cc):
    return wjson(_legacy(cc), {"legacy": True})


def _empty_writable_legacy(cc):
    p = _legacy(cc)
    p.write_bytes(b"")
    p.chmod(0o644)
    return p


# ---- locate: global_relative ----------------------------------------------------

def test_an_empty_value_with_a_legacy_file_in_the_launch_folder_is_global_relative(cc):
    _nonempty_legacy(cc)
    assert claude_paths.locate({CCD: ""}, cwd=str(cc.work)).global_relative is True


def test_an_empty_value_with_a_legacy_file_in_the_launch_folder_uses_it(cc):
    """The same rule as Claude Code (P3, binary ``Y_``): the file is in use."""
    _nonempty_legacy(cc)
    assert claude_paths.locate({CCD: ""}, cwd=str(cc.work)).global_config == _legacy(cc)


def test_an_empty_value_without_a_legacy_file_is_not_global_relative(cc):
    assert claude_paths.locate({CCD: ""}, cwd=str(cc.work)).global_relative is False


def test_an_empty_value_without_a_legacy_file_answers_in_the_home_file(cc):
    assert claude_paths.locate({CCD: ""}, cwd=str(cc.work)).global_config == cc.home_json


@pytest.mark.parametrize("value", ["  ", "relcfg"], ids=["whitespace", "relative"])
def test_a_whitespace_or_relative_value_is_global_relative(cc, value):
    assert claude_paths.locate({CCD: value}, cwd=str(cc.work)).global_relative is True


def test_an_absolute_value_is_not_global_relative(cc):
    assert claude_paths.locate({CCD: str(cc.x)}, cwd=str(cc.work)).global_relative is False


def test_an_absolute_value_with_a_legacy_file_is_not_global_relative(cc):
    wjson(cc.x / ".config.json", {"legacy": True})
    assert claude_paths.locate({CCD: str(cc.x)}, cwd=str(cc.work)).global_relative is False


# ---- prepare_session_start: a legacy file in use in the launch folder ----------------

def test_a_nonempty_legacy_file_in_the_launch_folder_is_left_byte_identical(cc):
    p = _nonempty_legacy(cc)
    before = p.read_bytes()
    _prepare(cc, "", ANTHROPIC_API_KEY=KEY)
    assert p.read_bytes() == before


def test_a_nonempty_legacy_file_in_the_launch_folder_reports_no_change(cc):
    _nonempty_legacy(cc)
    assert _prepare(cc, "", ANTHROPIC_API_KEY=KEY) == []


def test_a_legacy_file_in_use_keeps_the_home_file_unwritten(cc):
    """"Writes nothing": the answers do not fall over to ``~/.claude.json``
    either, a file the session would not read."""
    _nonempty_legacy(cc)
    _prepare(cc, "", ANTHROPIC_API_KEY=KEY)
    assert not cc.home_json.exists()


def test_an_empty_writable_legacy_file_without_a_home_file_stays_empty(cc):
    """The write-bit branch: 0 bytes, writable, ``~/.claude.json`` absent:
    the legacy file is in use, and it is in the session's folder."""
    p = _empty_writable_legacy(cc)
    _prepare(cc, "")
    assert p.read_bytes() == b""


def test_an_empty_writable_legacy_file_without_a_home_file_reports_no_change(cc):
    _empty_writable_legacy(cc)
    assert _prepare(cc, "") == []


# ---- boundaries: a legacy file that is NOT in use -----------------------------------

def test_an_empty_writable_legacy_file_with_a_home_file_is_not_used(cc):
    """The write-bit branch needs the new-style file to be absent."""
    _empty_writable_legacy(cc)
    wjson(cc.home_json, {})
    _prepare(cc, "")
    assert rjson(cc.home_json).get("projects")


def test_an_empty_writable_legacy_file_with_a_home_file_stays_empty(cc):
    p = _empty_writable_legacy(cc)
    wjson(cc.home_json, {})
    _prepare(cc, "")
    assert p.read_bytes() == b""


def test_an_empty_read_only_legacy_file_is_not_used(cc):
    p = _legacy(cc)
    p.write_bytes(b"")
    p.chmod(0o444)
    _prepare(cc, "")
    assert rjson(cc.home_json).get("projects")


def test_a_folder_named_like_the_legacy_file_is_not_used(cc):
    _legacy(cc).mkdir()
    _prepare(cc, "")
    assert rjson(cc.home_json).get("projects")


def test_an_empty_value_without_a_legacy_file_still_answers_in_the_home_file(cc):
    assert "trusted" in _prepare(cc, "")


# ---- whitespace and relative values -------------------------------------------------

@pytest.mark.parametrize("value", ["  ", "relcfg"], ids=["whitespace", "relative"])
def test_a_whitespace_or_relative_value_reports_no_change(cc, value):
    assert _prepare(cc, value, ANTHROPIC_API_KEY=KEY) == []


@pytest.mark.parametrize("value", ["  ", "relcfg"], ids=["whitespace", "relative"])
def test_a_whitespace_or_relative_value_creates_no_global_config(cc, value):
    _prepare(cc, value, ANTHROPIC_API_KEY=KEY)
    assert not (cc.work / value / ".claude.json").exists()


@pytest.mark.parametrize("value", ["  ", "relcfg"], ids=["whitespace", "relative"])
def test_a_whitespace_or_relative_value_never_writes_the_home_file(cc, value):
    _prepare(cc, value, ANTHROPIC_API_KEY=KEY)
    assert not cc.home_json.exists()


def test_a_relative_values_legacy_file_is_left_byte_identical(cc):
    p = wjson(cc.work / "relcfg" / ".config.json", {"legacy": True})
    before = p.read_bytes()
    _prepare(cc, "relcfg", ANTHROPIC_API_KEY=KEY)
    assert p.read_bytes() == before


def test_a_relative_values_existing_global_config_is_left_byte_identical(cc):
    p = wjson(cc.work / "relcfg" / ".claude.json", {"mine": True})
    before = p.read_bytes()
    _prepare(cc, "relcfg", ANTHROPIC_API_KEY=KEY)
    assert p.read_bytes() == before


# ---- through launch_session (the env a real launch gets) ---------------------------

class _FakeProc:
    returncode = 1

    async def communicate(self):
        return b"", b"fake dtach: not run"

    async def wait(self):
        return 1


@pytest.fixture
def spawned(monkeypatch):
    seen: list[str] = []

    async def _fake(*args, **kwargs):
        seen.append(" ".join(str(a) for a in args))
        return _FakeProc()

    monkeypatch.setattr("aipager.dtach.inject._create_subprocess_exec", _fake)
    return seen


def test_a_launch_with_an_empty_value_leaves_the_folders_legacy_file_alone(
        cc, run_async, spawned, monkeypatch):
    p = _nonempty_legacy(cc)
    before = p.read_bytes()
    monkeypatch.setenv(CCD, "")
    run_async(launch_session("cfgdirrel", mode="ask", cwd=str(cc.work)))
    assert p.read_bytes() == before


def test_a_launch_with_an_empty_value_writes_no_settings_into_the_folder(
        cc, run_async, spawned, monkeypatch):
    monkeypatch.setenv(CCD, "")
    run_async(launch_session("cfgdirrel", mode="ask", cwd=str(cc.work)))
    assert not (cc.work / "settings.json").exists()


def test_a_launch_with_an_empty_value_reaches_the_spawn(cc, run_async, spawned, monkeypatch):
    """Guards the two launch tests above: they assert after a real spawn
    attempt, not an early refusal."""
    _nonempty_legacy(cc)
    monkeypatch.setenv(CCD, "")
    run_async(launch_session("cfgdirrel", mode="ask", cwd=str(cc.work)))
    assert spawned
