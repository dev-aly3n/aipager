"""design.md success criteria 3 and 4: bad token 4, network 7, chat not
started / can't initiate / blocked 5 with the exact words, missing deps
3; nothing is written in any of them."""

from __future__ import annotations

import urllib.error

import pytest

from agent_setup_support import BOT, HTTP, NET


START = f"Open t.me/{BOT} and press Start, then run this again."


# ---- getMe ----

@pytest.mark.parametrize("code", [401, 404])
def test_rejected_token_exits_4(env, code):
    env.tg.set("getMe", HTTP(code, "Unauthorized" if code == 401
                             else "Not Found"))
    r = env.setup()
    assert (r.code, r.json["error"]) == (4, "token_rejected")


def test_rejected_token_sends_nothing(env):
    env.tg.bots.clear()
    env.setup()
    assert env.tg.sent == []


def test_rejected_token_writes_nothing(env):
    env.tg.bots.clear()
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_rejected_token_bot_username_is_null(env):
    env.tg.bots.clear()
    assert env.setup().json["bot_username"] is None


@pytest.mark.parametrize("spec", [
    NET(), HTTP(500, "Internal Server Error"), HTTP(502, "Bad Gateway"),
    HTTP(429, "Too Many Requests: retry after 5"),
    urllib.error.URLError(TimeoutError("timed out")),
    TimeoutError("timed out"),
], ids=["dns", "500", "502", "429", "urlerror-timeout", "socket-timeout"])
def test_unreachable_getme_exits_7(env, spec):
    env.tg.set("getMe", spec)
    r = env.setup()
    assert (r.code, r.json["error"]) == (7, "telegram_unreachable")


def test_unreachable_getme_writes_nothing(env):
    env.tg.set("getMe", NET())
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


# ---- sendMessage refusals ----

NOT_STARTED = [
    HTTP(400, "Bad Request: chat not found"),
    HTTP(403, "Forbidden: bot can't initiate conversation with a user"),
]


@pytest.mark.parametrize("spec", NOT_STARTED, ids=["not-found", "initiate"])
def test_chat_not_started_exits_5(env, spec):
    env.tg.set("sendMessage", spec)
    r = env.setup()
    assert (r.code, r.json["error"]) == (5, "chat_not_started")


@pytest.mark.parametrize("spec", NOT_STARTED, ids=["not-found", "initiate"])
def test_chat_not_started_json_says_press_start(env, spec):
    env.tg.set("sendMessage", spec)
    d = env.setup().json
    assert START in f"{d['message']} {d['fix']}"


@pytest.mark.parametrize("spec", NOT_STARTED, ids=["not-found", "initiate"])
def test_chat_not_started_plain_stderr_says_press_start(env, spec):
    env.tg.set("sendMessage", spec)
    r = env.setup(json_out=False)
    assert r.code == 5 and START in " ".join(r.err.split())


@pytest.mark.parametrize("spec", NOT_STARTED, ids=["not-found", "initiate"])
def test_chat_not_started_writes_nothing(env, spec):
    env.tg.set("sendMessage", spec)
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_chat_not_started_reports_test_message_failed(env):
    env.tg.set("sendMessage", NOT_STARTED[0])
    assert env.setup().json["test_message"] == "failed"


BLOCKED = HTTP(403, "Forbidden: bot was blocked by the user")


def test_blocked_exits_5_bot_blocked(env):
    env.tg.set("sendMessage", BLOCKED)
    r = env.setup()
    assert (r.code, r.json["error"]) == (5, "bot_blocked")


def test_blocked_message_says_unblock_and_press_start(env):
    env.tg.set("sendMessage", BLOCKED)
    d = env.setup().json
    text = f"{d['message']} {d['fix']}"
    assert "Unblock" in text and "press Start, then run this again." in text


def test_blocked_plain_stderr_says_unblock(env):
    env.tg.set("sendMessage", BLOCKED)
    r = env.setup(json_out=False)
    err = " ".join(r.err.split())
    assert "Unblock" in err and "press Start, then run this again." in err


def test_blocked_writes_nothing(env):
    env.tg.set("sendMessage", BLOCKED)
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_unreachable_test_send_exits_7(env):
    env.tg.set("sendMessage", NET())
    r = env.setup()
    assert (r.code, r.json["error"]) == (7, "telegram_unreachable")


def test_unreachable_test_send_writes_nothing(env):
    env.tg.set("sendMessage", HTTP(503, "Service Unavailable"))
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_other_test_send_refusal_exits_1(env):
    env.tg.set("sendMessage", HTTP(400, "Bad Request: something else"))
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "test_send_failed")


def test_other_test_send_refusal_writes_nothing(env):
    env.tg.set("sendMessage", HTTP(400, "Bad Request: something else"))
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_ctrl_c_exits_130(env):
    env.tg.set("sendMessage", KeyboardInterrupt())
    r = env.setup()
    assert (r.code, r.json["error"]) == (130, "interrupted")


def test_ctrl_c_writes_nothing(env):
    env.tg.set("sendMessage", KeyboardInterrupt())
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


# ---- deps ----

@pytest.mark.parametrize("dep", ["dtach", "claude", "aipager-hook",
                                 "aipager-statusline"])
def test_missing_dep_exits_3(env, dep):
    env.missing.add(dep)
    r = env.setup()
    assert (r.code, r.json["error"]) == (3, "deps_missing")


@pytest.mark.parametrize("dep", ["dtach", "claude", "aipager-hook",
                                 "aipager-statusline"])
def test_missing_dep_writes_nothing(env, dep):
    env.missing.add(dep)
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


@pytest.mark.parametrize("dep", ["dtach", "claude", "aipager-hook",
                                 "aipager-statusline"])
def test_missing_dep_sends_nothing(env, dep):
    env.missing.add(dep)
    env.setup()
    assert env.tg.sent == []


@pytest.mark.parametrize("dep", ["dtach", "claude", "aipager-hook",
                                 "aipager-statusline"])
def test_missing_dep_is_reported_not_found(env, dep):
    env.missing.add(dep)
    items = env.setup().json["deps"]["items"]
    assert [i["found"] for i in items if i["name"] == dep] == [False]


@pytest.mark.parametrize("dep", ["dtach", "claude", "aipager-hook",
                                 "aipager-statusline"])
def test_missing_dep_fix_holds_its_item_fix(env, dep):
    env.missing.add(dep)
    d = env.setup().json
    item_fix = [i["fix"] for i in d["deps"]["items"] if i["name"] == dep][0]
    assert item_fix and item_fix in (d["fix"] or "")


def test_missing_hook_fix_names_the_reinstall_command(env):
    env.missing.add("aipager-hook")
    assert "uv tool install --reinstall aipager" in (env.setup().json["fix"]
                                                     or "")


def test_missing_deps_fix_holds_every_missing_fix(env):
    env.missing.update({"dtach", "aipager-statusline"})
    d = env.setup().json
    fixes = [i["fix"] for i in d["deps"]["items"] if not i["found"]]
    assert len(fixes) == 2 and all(f in d["fix"] for f in fixes)


def test_missing_deps_plain_mode_prints_the_fix(env):
    env.missing.add("aipager-hook")
    r = env.setup(json_out=False)
    assert r.code == 3 and "uv tool install --reinstall aipager" in (
        r.out + r.err)


def test_deps_ok_true_when_all_present(env):
    assert env.setup().json["deps"]["ok"] is True


def test_deps_ok_false_when_one_missing(env):
    env.missing.add("dtach")
    assert env.setup().json["deps"]["ok"] is False


# ---- exit priority ----

def test_malformed_config_beats_missing_deps(env):
    env.config_path.write_text("scopes: [[[\n")
    env.missing.add("dtach")
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "config_malformed")


def test_existing_install_beats_missing_deps(env):
    env.setup()
    env.missing.add("dtach")
    r = env.setup(chat=777)
    assert (r.code, r.json["error"]) == (6, "existing_install")


def test_settings_invalid_beats_missing_deps(env):
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text("{not json")
    env.missing.add("dtach")
    r = env.setup()
    assert (r.code, r.json["error"]) == (1, "settings_invalid")


def test_existing_install_beats_settings_invalid(env):
    env.setup()
    env.settings_path.write_text("{not json")
    r = env.setup(chat=777)
    assert (r.code, r.json["error"]) == (6, "existing_install")


# ---- local failures write nothing ----

def test_malformed_config_writes_nothing(env):
    env.config_path.write_text("scopes: [[[\n")
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_malformed_config_is_not_overwritten_with_force(env):
    env.config_path.write_text("scopes: [[[\n")
    before = env.snapshot()
    r = env.setup("--force")
    assert r.code == 1 and env.snapshot() == before


def test_malformed_config_sends_nothing(env):
    env.config_path.write_text("scopes: [[[\n")
    env.setup("--force")
    assert env.tg.sent == []


def test_invalid_settings_json_writes_no_yaml(env):
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text("{not json")
    env.setup()
    assert not env.config_path.exists()


def test_invalid_settings_json_is_left_untouched(env):
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text("{not json")
    before = env.snapshot()
    env.setup()
    assert env.snapshot() == before


def test_unwritable_config_dir_is_write_failed(env, monkeypatch, tmp_path):
    """Error guessing: a partial-write failure is reported, scrubbed."""
    import os

    import aipager.scope as scope_mod
    ro = tmp_path / "ro"
    ro.mkdir()
    monkeypatch.setattr(scope_mod, "CONFIG_PATH", ro / "aipager.yaml")
    os.chmod(ro, 0o500)
    try:
        r = env.setup()
    finally:
        os.chmod(ro, 0o700)
    assert (r.code, r.json["error"]) == (1, "write_failed")
