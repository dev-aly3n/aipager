"""``aipager doctor``'s ``settings_json`` row never raises on odd file
content (entrypoints ``check_settings_json``: FAIL when missing, not
readable (a folder in its place, not UTF-8, nested too deep), invalid
JSON or valid JSON that is not an object; WARN when no aipager-hook or no
statusLine, "hook entries of the wrong shape are skipped, never raise";
test-report-1 tester-iter1-001).

The same content is also fed to the daemon's ``ensure_session_hooks``
("Never raises") and must never be written over (design success
criterion 5).
"""

from __future__ import annotations

import os

import pytest

from aipager import claude_bootstrap, claude_paths, doctor

from _cfgdir_kit import rjson, wire, wjson

CCD = "CLAUDE_CONFIG_DIR"

DEPTH = 100_000
ODD = {
    "list": b"[1]",
    "empty-list": b"[]",
    "number": b"42",
    "float": b"1.5e3",
    "string": b'"settings"',
    "null": b"null",
    "true": b"true",
    "empty-file": b"",
    "whitespace-only": b"  \n\t",
    "nested-list-too-deep": b"[" * DEPTH + b"]" * DEPTH,
    "nested-object-too-deep": b'{"a":' * DEPTH + b"1" + b"}" * DEPTH,
    "binary-garbage": bytes(range(256)) * 4,
    "not-utf8-in-a-string": b'{"model": "\xff\xfe"}',
    "utf16-bom": '{"model": "opus"}'.encode("utf-16"),
    "nul-bytes": b"\x00" * 64,
    "truncated": b'{"hooks": {"Stop": [',
}
ODD_IDS = list(ODD)


def _row():
    return doctor.check_settings_json()


@pytest.fixture(params=["config-dir", "default"])
def settings_file(request, cc):
    """The effective file, either ``$CLAUDE_CONFIG_DIR/settings.json`` (from
    daemon.env) or the default ``~/.claude/settings.json``."""
    if request.param == "config-dir":
        cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
        return cc.x / "settings.json"
    return cc.home_settings


# ---- content that is not a settings object: FAIL, no exception ------------------------

@pytest.mark.parametrize("name", ODD_IDS, ids=ODD_IDS)
def test_odd_content_fails_the_row(settings_file, name):
    settings_file.write_bytes(ODD[name])
    assert _row().status == doctor.FAIL


@pytest.mark.parametrize("name", ODD_IDS, ids=ODD_IDS)
def test_odd_content_fail_has_a_fix(settings_file, name):
    settings_file.write_bytes(ODD[name])
    assert (_row().fix or "").strip()


def test_a_folder_in_place_of_the_file_fails(settings_file):
    settings_file.mkdir(parents=True)
    assert _row().status == doctor.FAIL


def test_an_unreadable_file_fails(settings_file):
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    wire(settings_file)
    settings_file.chmod(0o000)
    try:
        status = _row().status
    finally:
        settings_file.chmod(0o600)
    assert status == doctor.FAIL


def test_a_dangling_symlink_fails(settings_file):
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.symlink_to(settings_file.parent / "gone.json")
    assert _row().status == doctor.FAIL


def test_a_symlink_loop_fails(settings_file):
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    other = settings_file.parent / "loop.json"
    other.symlink_to(settings_file)
    settings_file.symlink_to(other)
    assert _row().status == doctor.FAIL


@pytest.mark.parametrize("name", ["list", "nested-list-too-deep", "binary-garbage"],
                         ids=["list", "nested-list-too-deep", "binary-garbage"])
def test_doctor_never_writes_over_odd_content(settings_file, name):
    settings_file.write_bytes(ODD[name])
    _row()
    assert settings_file.read_bytes() == ODD[name]


# ---- hook entries of the wrong shape: WARN (no aipager-hook), no exception --------------

TOP_BAD = {
    "hooks-number": 5,
    "hooks-string": "aipager-hook",
    "hooks-list": ["aipager-hook"],
    "hooks-null": None,
}
TOP_IDS = list(TOP_BAD)

BAD_HOOKS = {
    "event-number": {"Stop": 5},
    "event-string": {"Stop": "aipager-hook"},
    "event-object": {"Stop": {"hooks": []}},
    "matcher-number": {"Stop": [5]},
    "matcher-string": {"Stop": ["aipager-hook"]},
    "matcher-null": {"Stop": [None]},
    "inner-hooks-number": {"Stop": [{"hooks": 5}]},
    "inner-hooks-string": {"Stop": [{"hooks": "aipager-hook"}]},
    "inner-entry-number": {"Stop": [{"hooks": [5]}]},
    "inner-entry-list": {"Stop": [{"hooks": [["aipager-hook"]]}]},
    "command-number": {"Stop": [{"hooks": [{"type": "command", "command": 5}]}]},
    "command-list": {"Stop": [{"hooks": [{"type": "command", "command": ["aipager-hook"]}]}]},
    "command-null": {"Stop": [{"hooks": [{"type": "command", "command": None}]}]},
}
BAD_IDS = list(BAD_HOOKS)
_STATUS = {"type": "command", "command": "aipager-statusline"}


@pytest.mark.parametrize("name", TOP_IDS, ids=TOP_IDS)
def test_a_hooks_value_that_is_not_an_object_fails(settings_file, name):
    """The file has the wrong shape (as ``plan_settings`` calls it): a FAIL
    row with a fix, never an exception."""
    wjson(settings_file, {"hooks": TOP_BAD[name], "statusLine": _STATUS})
    assert _row().status == doctor.FAIL


@pytest.mark.parametrize("name", TOP_IDS, ids=TOP_IDS)
def test_a_hooks_value_that_is_not_an_object_has_a_fix(settings_file, name):
    wjson(settings_file, {"hooks": TOP_BAD[name], "statusLine": _STATUS})
    assert (_row().fix or "").strip()


@pytest.mark.parametrize("name", BAD_IDS, ids=BAD_IDS)
def test_wrong_shaped_hook_entries_warn(settings_file, name):
    """Hook entries of the wrong shape are skipped: no aipager-hook -> WARN."""
    wjson(settings_file, {"hooks": BAD_HOOKS[name], "statusLine": _STATUS})
    assert _row().status == doctor.WARN


@pytest.mark.parametrize("name", BAD_IDS, ids=BAD_IDS)
def test_wrong_shaped_entries_beside_aipagers_are_skipped(settings_file, name):
    """A wired file with junk entries added to its hooks is still wired."""
    wire(settings_file)
    data = rjson(settings_file)
    for event, entries in BAD_HOOKS[name].items():
        data["hooks"]["Junk" + event] = entries
    wjson(settings_file, data)
    assert _row().status == doctor.OK


@pytest.mark.parametrize("value", [5, "aipager-statusline", ["x"], None, {"command": 5}],
                         ids=["number", "string", "list", "null", "command-number"])
def test_a_wrong_shaped_status_line_never_raises(settings_file, value):
    wire(settings_file)
    data = rjson(settings_file)
    data["statusLine"] = value
    wjson(settings_file, data)
    assert _row().status in (doctor.OK, doctor.WARN)


@pytest.mark.parametrize("value", [5, "x", ["a"], None, {"CLAUDE_CONFIG_DIR": 7}],
                         ids=["number", "string", "list", "null", "non-text-value"])
def test_a_wrong_shaped_env_block_never_raises(cc, value):
    wire(cc.home_settings)
    data = rjson(cc.home_settings)
    data["env"] = value
    wjson(cc.home_settings, data)
    assert _row().status == doctor.OK


# ---- odd content in files that only feed the resolver ----------------------------------

@pytest.mark.parametrize("name", ["list", "number", "nested-object-too-deep",
                                  "binary-garbage", "empty-file"],
                         ids=["list", "number", "nested-object-too-deep",
                              "binary-garbage", "empty-file"])
def test_odd_managed_settings_contribute_nothing(cc, name):
    wire(cc.home_settings)
    cc.managed.mkdir(parents=True, exist_ok=True)
    (cc.managed / "managed-settings.json").write_bytes(ODD[name])
    assert _row().status == doctor.OK


@pytest.mark.parametrize("name", ["list", "nested-object-too-deep", "binary-garbage"],
                         ids=["list", "nested-object-too-deep", "binary-garbage"])
def test_an_odd_config_dir_file_fails_despite_a_wired_default_file(cc, name):
    """daemon.env's home is the effective one; the wired default file does not count."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    (cc.x / "settings.json").write_bytes(ODD[name])
    wire(cc.home_settings)
    assert _row().status == doctor.FAIL


@pytest.mark.parametrize("name", ["list", "nested-object-too-deep", "binary-garbage"],
                         ids=["list", "nested-object-too-deep", "binary-garbage"])
def test_an_odd_moved_file_fails(cc, name):
    """A settings ``env`` move to a file with odd content."""
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    (cc.x / "settings.json").write_bytes(ODD[name])
    assert _row().status == doctor.FAIL


def test_an_odd_daemon_env_file_never_raises(cc):
    cc.daemon_env.parent.mkdir(parents=True, exist_ok=True)
    cc.daemon_env.write_bytes(b"\xff\xfe\x00CLAUDE_CONFIG_DIR\x00=\n" + bytes(range(256)))
    os.chmod(cc.daemon_env, 0o600)
    wire(cc.home_settings)
    assert _row().status in (doctor.OK, doctor.WARN, doctor.FAIL)


# ---- the daemon side: the same content never raises and is never written over ----------

@pytest.mark.parametrize("name", ODD_IDS, ids=ODD_IDS)
def test_ensure_session_hooks_reports_false_for_odd_content(cc, name):
    (cc.x / "settings.json").write_bytes(ODD[name])
    assert claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: str(cc.x)})) is False


@pytest.mark.parametrize("name", ODD_IDS, ids=ODD_IDS)
def test_ensure_session_hooks_never_writes_over_odd_content(cc, name):
    (cc.x / "settings.json").write_bytes(ODD[name])
    claude_bootstrap.ensure_session_hooks(claude_paths.locate({CCD: str(cc.x)}))
    assert (cc.x / "settings.json").read_bytes() == ODD[name]


@pytest.mark.parametrize("name", ODD_IDS, ids=ODD_IDS)
def test_hooks_wired_is_false_for_odd_content(cc, name):
    (cc.x / "settings.json").write_bytes(ODD[name])
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is False


@pytest.mark.parametrize("name", TOP_IDS + BAD_IDS, ids=TOP_IDS + BAD_IDS)
def test_hooks_wired_is_false_for_wrong_shaped_hooks(cc, name):
    wjson(cc.x / "settings.json", {"hooks": {**TOP_BAD, **BAD_HOOKS}[name]})
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is False


@pytest.mark.parametrize("name", ["list", "nested-object-too-deep", "binary-garbage"],
                         ids=["list", "nested-object-too-deep", "binary-garbage"])
def test_locate_never_raises_on_an_odd_start_file(cc, name):
    cc.home_settings.write_bytes(ODD[name])
    assert claude_paths.locate({}).config_home == cc.dot_claude


@pytest.mark.parametrize("name", ["list", "nested-object-too-deep", "binary-garbage"],
                         ids=["list", "nested-object-too-deep", "binary-garbage"])
def test_read_settings_gives_an_empty_object_for_odd_content(cc, name):
    (cc.x / "settings.json").write_bytes(ODD[name])
    assert claude_paths.read_settings(cc.x / "settings.json") == {}
