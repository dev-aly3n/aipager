"""design.md success criterion 11: Bot API answers map to warnings, never
to the exit code; a network error stops the remaining calls; nothing is
ever sent to the group (no sendMessage)."""

from __future__ import annotations

import pytest

from setup_group_support import (
    BOT, BOT_ID, GROUP, HTTP, NET, OWNER, admin_member, dm_scope,
    group_scope, m, ok,
)

ARGS = (m(OWNER, "alice", "owner"),)


@pytest.fixture
def inst(env):
    env.install()
    return env


def _member(status, **kw):
    return ok({"status": status, "user": {"id": BOT_ID, "is_bot": True},
               **kw})


CASES = [
    # (id, method, spec, expected warning)
    ("chat-not-found", "getChat", HTTP(400, "Bad Request: chat not found"),
     "group_not_found"),
    ("chat-forbidden", "getChat",
     HTTP(403, "Forbidden: bot is not a member of the supergroup chat"),
     "bot_not_in_group"),
    ("upgraded", "getChat",
     HTTP(400, "Bad Request: group chat was upgraded to a supergroup chat",
          {"migrate_to_chat_id": -1009999}), "group_upgraded"),
    ("private", "getChat", ok({"id": GROUP, "type": "private"}),
     "chat_not_a_group"),
    ("channel", "getChat", ok({"id": GROUP, "type": "channel",
                               "title": "News"}), "chat_not_a_group"),
    ("left", "getChatMember", _member("left"), "bot_not_in_group"),
    ("kicked", "getChatMember", _member("kicked"), "bot_not_in_group"),
    ("member", "getChatMember", _member("member"), "bot_not_admin"),
    ("restricted", "getChatMember", _member("restricted"), "bot_not_admin"),
    ("admin-no-pin", "getChatMember", admin_member(False), "bot_cannot_pin"),
    ("getme-401", "getMe", HTTP(401, "Unauthorized"), "token_rejected"),
    ("getme-404", "getMe", HTTP(404, "Not Found"), "token_rejected"),
    ("getme-net", "getMe", NET(), "telegram_checks_skipped"),
    ("getchat-net", "getChat", NET(), "telegram_checks_skipped"),
    ("member-net", "getChatMember", NET(), "telegram_checks_skipped"),
    ("getchat-429", "getChat",
     HTTP(429, "Too Many Requests: retry after 5", {"retry_after": 5}),
     "telegram_checks_skipped"),
    ("getme-500", "getMe", HTTP(500, "Internal Server Error"),
     "telegram_checks_skipped"),
    ("getchat-502", "getChat", HTTP(502, "Bad Gateway"),
     "telegram_checks_skipped"),
    ("member-unreadable", "getChatMember", {"ok": True},
     "telegram_checks_skipped"),
    ("member-result-null", "getChatMember", ok(None),
     "telegram_checks_skipped"),
    ("getchat-unreadable", "getChat", {"ok": True},
     "telegram_checks_skipped"),
    ("getme-unreadable", "getMe", {"ok": True}, "telegram_checks_skipped"),
    ("getchat-not-json", "getChat", b"<html>502 Bad Gateway</html>",
     "telegram_checks_skipped"),
    ("getme-timeout", "getMe", TimeoutError("timed out"),
     "telegram_checks_skipped"),
]
IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("cid,method,spec,warning", CASES, ids=IDS)
def test_answer_maps_to_warning(inst, cid, method, spec, warning):
    inst.tg.set(method, spec)
    r = inst.group_cmd(*ARGS)
    assert warning in r.warning_codes()


@pytest.mark.parametrize("cid,method,spec,warning", CASES, ids=IDS)
def test_answer_never_changes_exit_code(inst, cid, method, spec, warning):
    inst.tg.set(method, spec)
    r = inst.group_cmd(*ARGS)
    assert (r.code, r.json["status"]) == (0, "created")


@pytest.mark.parametrize("cid,method,spec,warning", CASES, ids=IDS)
def test_answer_still_writes_the_group(inst, cid, method, spec, warning):
    inst.tg.set(method, spec)
    inst.group_cmd(*ARGS)
    assert inst.group() is not None


@pytest.mark.parametrize("cid,method,spec,warning", CASES, ids=IDS)
def test_answer_never_sends_a_message(inst, cid, method, spec, warning):
    inst.tg.set(method, spec)
    inst.group_cmd(*ARGS)
    assert inst.tg.calls_to("sendMessage") == []


@pytest.mark.parametrize("cid,method,spec,warning", CASES, ids=IDS)
def test_answer_needs_force_stays_6(env, cid, method, spec, warning):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    env.tg.set(method, spec)
    assert env.group_cmd(m(222, "bob", "admin")).code == 6


def test_upgraded_warning_names_the_new_id(inst):
    inst.tg.set("getChat", HTTP(
        400, "Bad Request: group chat was upgraded to a supergroup chat",
        {"migrate_to_chat_id": -1009999}))
    r = inst.group_cmd(*ARGS)
    w = next(w for w in r.json["warnings"] if w["code"] == "group_upgraded")
    assert "-1009999" in w["message"]


def test_network_error_on_getme_makes_no_further_call(inst):
    inst.tg.set("getMe", NET())
    inst.group_cmd(*ARGS)
    assert inst.tg.methods == ["getMe"]


def test_network_error_on_getchat_makes_no_member_call(inst):
    inst.tg.set("getChat", NET())
    inst.group_cmd(*ARGS)
    assert "getChatMember" not in inst.tg.methods


@pytest.mark.parametrize("spec", [HTTP(429, "Too Many Requests"),
                                  HTTP(500, "x"), {"nonsense": 1}])
def test_unusable_getme_makes_no_further_call(inst, spec):
    inst.tg.set("getMe", spec)
    inst.group_cmd(*ARGS)
    assert inst.tg.methods == ["getMe"]


def test_skipped_warning_appears_once(inst):
    inst.tg.set("getMe", NET())
    r = inst.group_cmd(*ARGS)
    assert r.warning_codes().count("telegram_checks_skipped") == 1


def test_skipped_telegram_doc_checked_false(inst):
    inst.tg.set("getMe", NET())
    t = inst.group_cmd(*ARGS).json["telegram"]
    assert t["checked"] is False


def test_skipped_telegram_doc_fields_null(inst):
    inst.tg.set("getMe", NET())
    t = inst.group_cmd(*ARGS).json["telegram"]
    assert {k: v for k, v in t.items() if k != "checked"} == {
        k: None for k in t if k != "checked"}


def test_member_call_uses_the_bot_id_from_getme(inst):
    inst.group_cmd(*ARGS)
    c = inst.tg.calls_to("getChatMember")[0]
    assert c.params == {"chat_id": str(GROUP), "user_id": str(BOT_ID)}


def test_getchat_query_is_the_chat_id(inst):
    inst.group_cmd(*ARGS)
    assert inst.tg.calls_to("getChat")[0].params == {"chat_id": str(GROUP)}


def test_only_documented_methods_are_called(inst):
    inst.group_cmd(*ARGS)
    assert set(inst.tg.methods) <= {"getMe", "getChat", "getChatMember"}


def test_calls_carry_no_body(inst):
    """Read-only GETs: no form data."""
    inst.group_cmd(*ARGS)
    assert [c.form for c in inst.tg.calls] == [{}] * len(inst.tg.calls)


def test_healthy_group_has_no_telegram_warnings(inst):
    r = inst.group_cmd(*ARGS)
    tg_codes = {"group_not_found", "bot_not_in_group", "bot_not_admin",
                "bot_cannot_pin", "group_upgraded", "chat_not_a_group",
                "token_rejected", "telegram_checks_skipped"}
    assert set(r.warning_codes()) & tg_codes == set()


def test_creator_has_no_telegram_warning(inst):
    inst.tg.set("getChatMember", _member("creator"))
    r = inst.group_cmd(*ARGS)
    assert not {"bot_not_admin", "bot_cannot_pin"} & set(r.warning_codes())


def test_healthy_telegram_doc(inst):
    t = inst.group_cmd(*ARGS).json["telegram"]
    assert (t["checked"], t["chat_type"], t["chat_title"], t["bot_status"],
            t["bot_can_pin"]) == (True, "supergroup", "Team",
                                  "administrator", True)


def test_bot_username_from_getme(inst):
    assert inst.group_cmd(*ARGS).json["bot_username"] == BOT


@pytest.mark.parametrize("read_all,privacy", [(False, True), (True, False)])
def test_privacy_mode_reported(inst, read_all, privacy):
    inst.tg.set("getMe", ok({"id": BOT_ID, "is_bot": True,
                             "username": BOT,
                             "can_read_all_group_messages": read_all}))
    assert inst.group_cmd(*ARGS).json["telegram"]["privacy_mode"] is privacy


def test_privacy_mode_is_never_a_warning(inst):
    r = inst.group_cmd(*ARGS)
    assert not any("privacy" in w["code"] for w in r.json["warnings"])


@pytest.mark.parametrize("method,spec", [
    ("getChatMember", _member("member")),
    ("getChatMember", admin_member(False)),
    ("getChatMember", _member("left")),
    ("getChat", HTTP(400, "Bad Request: chat not found")),
])
def test_next_step_tells_the_person_only_step(inst, method, spec):
    inst.tg.set(method, spec)
    r = inst.group_cmd(*ARGS)
    assert "admin" in r.json["next_step"].lower()


def test_checks_run_on_unchanged(env):
    env.install(scopes=[dm_scope(), group_scope([(OWNER, "alice", "owner")])])
    env.tg.set("getChatMember", _member("member"))
    r = env.group_cmd(*ARGS)
    assert (r.json["status"], "bot_not_admin" in r.warning_codes()) == (
        "unchanged", True)


def test_plain_warnings_go_to_stderr(inst):
    inst.tg.set("getChatMember", _member("member"))
    r = inst.group_cmd(*ARGS, json_out=False)
    assert r.err.lstrip().startswith("!") and "!" not in r.out.split("\n")[0]


def test_warning_entries_have_code_and_message(inst):
    inst.tg.set("getChatMember", _member("member"))
    ws = inst.group_cmd(*ARGS).json["warnings"]
    assert all(set(w) >= {"code", "message"} and w["message"] for w in ws)


def test_detect_group_never_sends_a_message(inst):
    inst.run("setup", "detect-group", "--timeout", "0", "--json")
    assert inst.tg.calls_to("sendMessage") == []


def test_refusal_paths_never_send_a_message(env):
    env.install(scopes=[dm_scope(), group_scope([(222, "bob", "user")])])
    for args in ((m(222, "bob", "admin"),), (m(5, "x", "nope"),),
                 ("--remove-member", "222")):
        env.group_cmd(*args)
    assert env.tg.calls_to("sendMessage") == []
