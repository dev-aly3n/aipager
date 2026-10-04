"""Roadmap 8.88 + 8.89: the wizard's group setup, and its default-mode step.

Before this change the wizard's auto-detect returned the OLDEST matching
update on every call (member #2 was never found: "already on the
allow-list. Ask someone else to mention." forever), and while the daemon
ran its long poll took the updates the wizard looked for. There was no way
to add a member to a group, and "Add a group scope" for a group already set
up replaced its whole member list (the operator included). The optional
"deny Write and Edit" rule defaulted to yes and promised an admin override
that does not exist, and the auto-deny notice cited a config key. The
first-run "default mode" question wrote a key nothing read.

Groups cannot be tested live; these tests are the proof. The operator's own
install is one DM: the DM-parity tests pin that nothing else changes there.
"""

from __future__ import annotations

import contextlib
import json
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from aipager import scope as scope_mod
from aipager import team as team_mod
from aipager.bot import new_flow
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession
from aipager.wizard import (
    daemon_io, edit_menu, first_run, scope_flows, scope_io, team_setup,
    telegram_api,
)

GROUP = -1001
NEW_GROUP = -1005
OTHER_GROUP = -1007
DM = 555
ALY, BOB, EVE, CAROL, STRANGER = 1, 2, 5, 3, 9
TOKEN = "123456:FAKE"

POLICY = load_policy(Path("/nonexistent/aipager-policy.yaml"),
                     Path("/nonexistent/aipager-policy.d"))


# ---- builders ----------------------------------------------------------------

def _group(chat_id=GROUP, label="team", deny=()):
    return Scope(chat_id=chat_id, kind="group", label=label,
                 members=(Member(id=ALY, label="aly", role="owner"),
                          Member(id=BOB, label="bob", role="user")),
                 deny_tools=tuple(deny))


def _dm():
    return Scope(chat_id=DM, kind="dm", label="owner DM",
                 members=(Member(id=ALY, label="owner", role="owner"),))


def _cfg() -> Path:
    return scope_mod.CONFIG_PATH


def _write_config(scopes, **kw) -> bytes:
    scope_mod.dump_scopes(scopes, TOKEN, _cfg(), **kw)
    return _cfg().read_bytes()


def _members(chat_id=GROUP):
    scopes, _ = scope_io.read_config()
    s = next(s for s in scopes if s.chat_id == chat_id)
    return [(m.id, m.label, m.role) for m in s.members]


def _msg(uid, username, chat_id, ctype, text="@aipagerbot hi", title=None):
    chat = {"id": chat_id, "type": ctype}
    if title:
        chat["title"] = title
    return {"message": {"from": {"id": uid, "username": username,
                                 "first_name": username},
                        "chat": chat, "text": text}}


def _updates(*msgs):
    return {"ok": True, "result": [
        {"update_id": i, **m} for i, m in enumerate(msgs, start=1)]}


def _serve_updates(monkeypatch, body, code=200):
    calls = []

    def _http(url):
        calls.append(url)
        return body, code, ""
    monkeypatch.setattr(telegram_api, "_http_json", _http)
    return calls


def _no_telegram(monkeypatch):
    def _http(url):
        raise AssertionError("getUpdates must not be called while the "
                             "daemon runs")
    monkeypatch.setattr(telegram_api, "_http_json", _http)


def _daemon(monkeypatch, pid):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: pid)


def _answers(monkeypatch, module, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            pytest.fail("ran out of canned answers")
    monkeypatch.setattr(module, "_ask", _ask)


def _quiet(monkeypatch, module):
    """Record hint() lines; no spinner."""
    lines: list[str] = []
    monkeypatch.setattr(module, "hint", lambda s, *a, **k: lines.append(s))
    monkeypatch.setattr(module, "_spin", lambda msg: contextlib.nullcontext())
    return lines


@pytest.fixture(autouse=True)
def _fresh_unauthorized():
    team_mod.reset_unauthorized_seen()
    yield
    team_mod.reset_unauthorized_seen()


# =============================================================================
# (a) auto-detect: newest first, captured ids skipped, the daemon's records
# =============================================================================

def test_get_updates_is_read_newest_first(monkeypatch):
    _serve_updates(monkeypatch, _updates(
        _msg(ALY, "aly", ALY, "private", "/start"),
        _msg(ALY, "aly", OTHER_GROUP, "supergroup", "/start", title="old"),
        _msg(BOB, "bob", GROUP, "supergroup", title="team"),
        _msg(EVE, "eve", EVE, "private", "/start"),
    ))
    assert telegram_api._fetch_id_from_updates(TOKEN, want="user")[:2] == (
        EVE, "eve")
    assert telegram_api._fetch_id_from_updates(TOKEN, want="group")[:2] == (
        GROUP, "team")
    assert telegram_api._fetch_id_from_updates(TOKEN, want="dm")[0] == EVE


def test_get_updates_skips_the_ids_already_captured(monkeypatch):
    _serve_updates(monkeypatch, _updates(
        _msg(BOB, "bob", GROUP, "supergroup"),
        _msg(ALY, "aly", GROUP, "supergroup", "/start"),
    ))
    assert telegram_api._fetch_id_from_updates(
        TOKEN, want="user", exclude={ALY})[:2] == (BOB, "bob")
    assert telegram_api._fetch_id_from_updates(
        TOKEN, want="user", exclude={ALY, BOB}) == (
        None, None, telegram_api.ONLY_KNOWN_SENDERS_ADVISORY)
    assert telegram_api._fetch_id_from_updates(
        TOKEN, want="group", exclude={GROUP})[0] is None


def test_get_updates_conflict_says_another_program_reads_the_bot(monkeypatch):
    """A 409 (a daemon this wizard could not see) used to read as "nothing
    detected"."""
    _serve_updates(monkeypatch, {"ok": False, "description": "Conflict"}, 409)
    assert telegram_api._fetch_id_from_updates(TOKEN, want="user") == (
        None, None, telegram_api.UPDATES_CONFLICT_ADVISORY)


def _pending(*records):
    """Records as the daemon writes them, oldest first."""
    for r in records:
        team_mod.record_pending_user(*r[:4], **(r[4] if len(r) > 4 else {}))


def _group_note(title, ctype="supergroup"):
    return {"chat_title": title, "chat_type": ctype}


def test_the_daemons_records_are_read_newest_first(monkeypatch):
    _no_telegram(monkeypatch)
    _pending(
        (STRANGER, "old", "Old One", OTHER_GROUP, _group_note("old group")),
        (CAROL, "carol", "Carol", GROUP),                    # configured chat
        (BOB, "bob", "Bob B", NEW_GROUP, _group_note("new team")),
        (EVE, "", "Eve E", EVE),                             # no handle
    )
    assert telegram_api._fetch_id_from_pending(want="user")[:2] == (EVE, "Eve E")
    assert telegram_api._fetch_id_from_pending(
        want="user", exclude={EVE})[:2] == (BOB, "bob")
    # Groups: only records the daemon wrote for a group it does not serve.
    assert telegram_api._fetch_id_from_pending(want="group") == (
        NEW_GROUP, "new team", None)
    assert telegram_api._fetch_id_from_pending(
        want="group", exclude={NEW_GROUP})[:2] == (OTHER_GROUP, "old group")
    assert telegram_api._fetch_id_from_pending(
        want="user", exclude={EVE, BOB, CAROL, STRANGER}) == (
        None, None, telegram_api.ONLY_KNOWN_SENDERS_ADVISORY)
    # A DM from someone not set up is never written down: say so.
    assert telegram_api._fetch_id_from_pending(want="dm") == (
        None, None, telegram_api.DAEMON_DM_ADVISORY)


def test_a_private_chat_record_is_never_a_group(monkeypatch):
    _no_telegram(monkeypatch)
    _pending((BOB, "bob", "", 77, {"chat_title": "", "chat_type": "private"}))
    assert telegram_api._fetch_id_from_pending(want="group") == (
        None, None, telegram_api.DAEMON_NO_GROUP_ADVISORY)


def test_an_empty_record_file_says_what_the_daemon_notes(monkeypatch):
    _no_telegram(monkeypatch)
    assert telegram_api._fetch_id_from_pending(want="user") == (
        None, None, telegram_api.DAEMON_NO_USER_ADVISORY)
    assert telegram_api._fetch_id_from_pending(want="group") == (
        None, None, telegram_api.DAEMON_NO_GROUP_ADVISORY)


def test_detect_id_reads_the_source_it_is_given(monkeypatch):
    _pending((BOB, "bob", "", NEW_GROUP, _group_note("new team")))
    calls = _serve_updates(monkeypatch, _updates(
        _msg(EVE, "eve", GROUP, "supergroup")))
    assert telegram_api._detect_id(TOKEN, want="user", source="daemon")[0] == BOB
    assert calls == []
    assert telegram_api._detect_id(TOKEN, want="user", source="telegram")[0] == EVE
    assert len(calls) == 1


@pytest.mark.parametrize("pid,source", [
    (4242, "daemon"), (-1, "daemon"), (None, "telegram")])
def test_watch_source_follows_the_daemon(monkeypatch, pid, source):
    _daemon(monkeypatch, pid)
    assert telegram_api._watch_source() == source


def test_watch_source_falls_back_to_telegram_when_the_probe_fails(monkeypatch):
    def _boom():
        raise OSError("probe failed")
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _boom)
    assert telegram_api._watch_source() == "telegram"


# ---- the capture loop ----------------------------------------------------------

def test_member_two_is_found_after_member_one(monkeypatch):
    """Probe C12 (C9 in the probe file): alice's DM /start, alice's group
    /start, then bob's mention. Member #1 (alice) is captured; member #2
    used to come back as alice on every attempt."""
    _serve_updates(monkeypatch, _updates(
        _msg(ALY, "aly", ALY, "private", "/start"),
        _msg(ALY, "aly", GROUP, "supergroup", "/start"),
        _msg(BOB, "bob", GROUP, "supergroup"),
        _msg(ALY, "aly", GROUP, "supergroup", "thanks"),   # newest: alice again
    ))
    _quiet(monkeypatch, team_setup)
    _answers(monkeypatch, team_setup, [
        "auto", True,       # method, "They've sent something"
        True,               # "Found @bob (id 2). Is this the person to add?"
        "",                 # label: the suggestion
    ])
    out = team_setup._capture_user_identity(
        2, existing_ids={ALY}, existing_labels={"aly"}, token=TOKEN)
    assert out == {"id": BOB, "label": "bob"}


def test_the_found_person_is_confirmed_before_use(monkeypatch):
    """Every prompt is answered by its kind, so a missing question shows
    up as a missing prompt, not as a shifted answer."""
    _serve_updates(monkeypatch, _updates(_msg(BOB, "bob", GROUP, "supergroup")))
    _quiet(monkeypatch, team_setup)
    asked = []

    def _prompt(kind):
        def _make(text, *a, **k):
            asked.append((kind, text))
            return kind
        return _make
    for kind in ("select", "confirm", "text"):
        monkeypatch.setattr(team_setup.questionary, kind, _prompt(kind))
    monkeypatch.setattr(team_setup, "_ask", lambda q: {
        "select": "auto", "confirm": True, "text": ""}[q])
    out = team_setup._capture_user_identity(
        2, existing_ids={ALY}, existing_labels={"aly"}, token=TOKEN)
    assert out == {"id": BOB, "label": "bob"}
    assert ("confirm", "Found @bob (id 2). Is this the person to add?") in asked


@pytest.mark.parametrize("source", ["daemon", "telegram"])
def test_a_name_without_a_username_is_never_shown_as_a_handle(
        monkeypatch, source):
    """Anyone can call themselves "bob": only a real username gets the @."""
    _daemon(monkeypatch, 4242 if source == "daemon" else None)
    if source == "daemon":
        _no_telegram(monkeypatch)
        _pending((STRANGER, "", "bob", NEW_GROUP, _group_note("new team")))
    else:
        _serve_updates(monkeypatch, {"ok": True, "result": [{"message": {
            "from": {"id": STRANGER, "first_name": "bob"},
            "chat": {"id": GROUP, "type": "supergroup"}, "text": "hi"}}]})
    _quiet(monkeypatch, team_setup)
    asked = []
    monkeypatch.setattr(team_setup.questionary, "confirm",
                        lambda text, **k: asked.append(text) or "confirm")
    monkeypatch.setattr(team_setup.questionary, "select",
                        lambda text, **k: "select")
    monkeypatch.setattr(team_setup.questionary, "text",
                        lambda text, **k: "text")
    monkeypatch.setattr(team_setup, "_ask", lambda q: {
        "select": "auto", "confirm": True, "text": ""}[q])
    out = team_setup._capture_user_identity(
        2, existing_ids={ALY}, existing_labels={"aly"}, token=TOKEN)
    assert out == {"id": STRANGER, "label": "bob"}
    assert "Found bob (no username) (id 9). Is this the person to add?" in asked
    assert not any("@bob" in a for a in asked)


def test_edit_menu_carries_on_after_a_broken_config(monkeypatch):
    """aipager.yaml broken by hand mid-session: the action says why and the
    menu stays up."""
    _write_config([_dm(), _group()])
    _run_menu(monkeypatch, ["edit_scope", "add_member", "exit"])
    monkeypatch.setattr(edit_menu, "_pick_scope",
                        lambda scopes, *a, **k: next(
                            s for s in scopes if s.chat_id == GROUP))
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": EVE, "label": "eve"})
    monkeypatch.setattr(edit_menu, "_pick_role", lambda *a, **k: "user")

    def _broken(*a, **k):
        raise scope_mod.ScopeConfigError("aipager.yaml: scopes[1] is broken")
    monkeypatch.setattr(edit_menu, "append_member", _broken)
    errors = []
    monkeypatch.setattr(edit_menu, "friendly_error",
                        lambda msg: errors.append(msg))
    try:
        rc = edit_menu._edit_flow()
    except scope_mod.ScopeConfigError:
        pytest.fail("the edit menu died on a broken aipager.yaml")
    assert rc == 0
    assert errors == ["aipager.yaml: scopes[1] is broken"]


def test_a_person_turned_down_is_looked_past(monkeypatch):
    """A stale record must not come back on every attempt either."""
    seen_excludes = []
    found = iter([(STRANGER, "stranger", None), (BOB, "bob", None)])

    def _detect(token, *, want, exclude=frozenset(), source="telegram"):
        seen_excludes.append(set(exclude))
        return next(found)
    monkeypatch.setattr(team_setup, "_detect_id", _detect)
    _quiet(monkeypatch, team_setup)
    _answers(monkeypatch, team_setup, [
        "auto", True,
        False,              # not the stranger
        True,               # continue
        True,               # bob, yes
        "",
    ])
    out = team_setup._capture_user_identity(
        2, existing_ids={ALY}, existing_labels={"aly"}, token=TOKEN)
    assert out == {"id": BOB, "label": "bob"}
    assert seen_excludes == [{ALY}, {ALY, STRANGER}]


def test_member_capture_reads_the_daemons_records_while_it_runs(monkeypatch):
    _daemon(monkeypatch, 4242)
    _no_telegram(monkeypatch)
    _pending((ALY, "aly", "", NEW_GROUP, _group_note("new team")),
             (BOB, "bob", "Bob", NEW_GROUP, _group_note("new team")),
             (ALY, "aly", "", NEW_GROUP, _group_note("new team")))
    lines = _quiet(monkeypatch, team_setup)
    _answers(monkeypatch, team_setup, ["auto", True, True, ""])
    out = team_setup._capture_user_identity(
        2, existing_ids={ALY}, existing_labels={"aly"}, token=TOKEN)
    assert out == {"id": BOB, "label": "bob"}
    assert telegram_api.WATCH_SOURCE_LINES["daemon"] in lines
    assert telegram_api.WATCH_SOURCE_LINES["telegram"] not in lines
    assert lines[-1] == telegram_api.WATCH_SOURCE_LINES["daemon"]
    assert telegram_api.WATCH_SOURCE_LINES["daemon"].startswith(
        "Watching through the running daemon")


def test_member_capture_watches_telegram_when_the_daemon_is_stopped(monkeypatch):
    _daemon(monkeypatch, None)
    calls = _serve_updates(monkeypatch, _updates(
        _msg(BOB, "bob", GROUP, "supergroup")))
    lines = _quiet(monkeypatch, team_setup)
    _answers(monkeypatch, team_setup, ["auto", True, True, ""])
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token=TOKEN)
    assert out == {"id": BOB, "label": "bob"}
    assert len(calls) == 1
    assert telegram_api.WATCH_SOURCE_LINES["telegram"] in lines
    assert telegram_api.WATCH_SOURCE_LINES["daemon"] not in lines
    assert telegram_api.WATCH_SOURCE_LINES["telegram"].startswith(
        "Watching Telegram directly")


def _stub_test_send(monkeypatch, sent):
    def _send(token, cid):
        sent.append(cid)
        return True, ""
    monkeypatch.setattr(first_run, "_test_send", _send)


def test_group_detect_reads_the_daemons_records_while_it_runs(monkeypatch):
    _daemon(monkeypatch, 4242)
    _no_telegram(monkeypatch)
    _pending((ALY, "aly", "", OTHER_GROUP, _group_note("old group")),
             (BOB, "bob", "", NEW_GROUP, _group_note("new team")))
    lines = _quiet(monkeypatch, first_run)
    sent = []
    _stub_test_send(monkeypatch, sent)
    _answers(monkeypatch, first_run, ["auto", True, True])
    assert first_run._step_chat_id(TOKEN, "aipagerbot", mode="team") == NEW_GROUP
    assert sent == [NEW_GROUP]
    assert telegram_api.WATCH_SOURCE_LINES["daemon"] in lines


def test_a_group_whose_test_did_not_arrive_is_looked_past(monkeypatch):
    _daemon(monkeypatch, None)
    _serve_updates(monkeypatch, _updates(
        _msg(ALY, "aly", NEW_GROUP, "supergroup", "/start"),
        _msg(ALY, "aly", OTHER_GROUP, "supergroup", "/start"),
    ))
    lines = _quiet(monkeypatch, first_run)
    sent = []
    _stub_test_send(monkeypatch, sent)
    _answers(monkeypatch, first_run, [
        "auto", True, False,     # OTHER_GROUP (newest): it did not arrive
        "auto", True, True,      # NEW_GROUP
    ])
    assert first_run._step_chat_id(TOKEN, "aipagerbot", mode="team") == NEW_GROUP
    assert sent == [OTHER_GROUP, NEW_GROUP]
    assert lines.count(telegram_api.WATCH_SOURCE_LINES["telegram"]) == 2


def test_group_detect_looks_past_groups_already_set_up(monkeypatch):
    """A stale record for a working group, newer than the new group's, must
    not get a test message posted into the working group."""
    _write_config([_dm(), _group()])
    _daemon(monkeypatch, 4242)
    _no_telegram(monkeypatch)
    _pending((BOB, "bob", "", NEW_GROUP, _group_note("new team")),
             (ALY, "aly", "", GROUP, _group_note("team")))
    _quiet(monkeypatch, first_run)
    sent = []
    _stub_test_send(monkeypatch, sent)
    _answers(monkeypatch, first_run, ["auto", True, True])
    assert first_run._step_chat_id(TOKEN, "aipagerbot", mode="team") == NEW_GROUP
    assert sent == [NEW_GROUP]


def test_group_detect_says_when_only_groups_already_set_up_were_seen(
        monkeypatch):
    _write_config([_dm(), _group(chat_id=NEW_GROUP)])
    with _cfg().open("a") as f:
        f.write(f"chat_migrations:\n  {GROUP}: {NEW_GROUP}\n")
    _daemon(monkeypatch, None)
    _serve_updates(monkeypatch, _updates(
        _msg(ALY, "aly", GROUP, "group", "/start"),          # its old id
        _msg(ALY, "aly", NEW_GROUP, "supergroup", "/start"),
    ))
    _quiet(monkeypatch, first_run)
    printed = []
    monkeypatch.setattr(first_run.err_console, "print",
                        lambda s, *a, **k: printed.append(s))
    sent = []
    _stub_test_send(monkeypatch, sent)
    _answers(monkeypatch, first_run, ["auto", True, "manual", str(OTHER_GROUP),
                                      True])
    assert first_run._step_chat_id(TOKEN, "aipagerbot", mode="team") == \
        OTHER_GROUP
    assert sent == [OTHER_GROUP]
    assert printed[0] == \
        f"  [err]{telegram_api.ONLY_KNOWN_GROUPS_ADVISORY}[/err]"


def test_the_daemons_records_say_when_only_known_groups_were_seen(monkeypatch):
    _no_telegram(monkeypatch)
    _pending((BOB, "bob", "", GROUP, _group_note("team")))
    assert telegram_api._fetch_id_from_pending(
        want="group", exclude={GROUP}) == (
        None, None, telegram_api.ONLY_KNOWN_GROUPS_ADVISORY)


def test_dm_parity_first_run_dm_detect_reads_telegram_and_says_nothing_new(
        monkeypatch):
    """The operator's own first run: getUpdates as before, no source line,
    never the daemon's records (even if a daemon answers the probe)."""
    _daemon(monkeypatch, 4242)
    calls = _serve_updates(monkeypatch, _updates(
        _msg(ALY, "aly", ALY, "private", "/start")))
    lines = _quiet(monkeypatch, first_run)
    sent = []
    _stub_test_send(monkeypatch, sent)
    _answers(monkeypatch, first_run, ["auto", True, True])
    assert first_run._step_chat_id(TOKEN, "aipagerbot", mode="personal") == ALY
    assert len(calls) == 1 and sent == [ALY]
    assert not any(line.startswith("Watching") for line in lines)


# =============================================================================
# (a) the daemon notes senders in groups it does not serve, without replying
# =============================================================================

def _gbot(mk_bot, scopes=None):
    bot = mk_bot(scopes=scopes if scopes is not None else [_group(), _dm()])
    bot.policy = POLICY
    return bot


def _update(*, user_id, chat_id, ctype, title=None, username="bob",
            first="Bob", last="B", text="/start"):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id, type=ctype, title=title)
    u.effective_user = MagicMock(id=user_id, username=username,
                                 first_name=first, last_name=last,
                                 is_bot=False)
    m = MagicMock()
    m.text = text
    m.chat = u.effective_chat
    m.sender_chat = None
    m.from_user = u.effective_user
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    return u


def _records():
    path = team_mod.PENDING_USERS_PATH
    return json.loads(path.read_text()) if path.exists() else []


def test_the_daemon_notes_a_sender_in_an_unknown_group_without_replying(
        mk_bot, run_async):
    bot = _gbot(mk_bot)
    up = _update(user_id=EVE, chat_id=NEW_GROUP, ctype="supergroup",
                 title="new team", username="eve", first="Eve", last="")
    assert run_async(bot._authorize(up)) is False
    up.message.reply_text.assert_not_called()
    bot._app.bot.send_message.assert_not_called()
    recs = _records()
    assert len(recs) == 1
    rec = recs[0]
    assert {k: rec[k] for k in ("user_id", "username", "display_name",
                                "chat_id", "chat_title", "chat_type")} == {
        "user_id": EVE, "username": "eve", "display_name": "Eve",
        "chat_id": NEW_GROUP, "chat_title": "new team",
        "chat_type": "supergroup"}


def test_a_basic_group_is_noted_too(mk_bot, run_async):
    bot = _gbot(mk_bot)
    up = _update(user_id=EVE, chat_id=-77, ctype="group", title="small")
    assert run_async(bot._authorize(up)) is False
    up.message.reply_text.assert_not_called()
    assert _records()[0]["chat_type"] == "group"


def test_dm_parity_an_unknown_dm_is_answered_and_not_noted(mk_bot, run_async):
    bot = _gbot(mk_bot)
    up = _update(user_id=EVE, chat_id=EVE, ctype="private")
    assert run_async(bot._authorize(up)) is False
    up.message.reply_text.assert_awaited_once()
    assert "isn't configured to talk to you" in \
        up.message.reply_text.await_args.args[0]
    assert _records() == []


def test_a_configured_groups_non_member_is_unchanged(mk_bot, run_async):
    bot = _gbot(mk_bot)
    up = _update(user_id=EVE, chat_id=GROUP, ctype="supergroup", title="team",
                 username="eve", first="Eve", last="")
    assert run_async(bot._authorize(up)) is False
    up.message.reply_text.assert_awaited_once()
    assert "not on this scope's allow-list" in \
        up.message.reply_text.await_args.args[0]
    [rec] = _records()
    assert rec["user_id"] == EVE and rec["chat_id"] == GROUP
    assert "chat_title" not in rec and "chat_type" not in rec


def test_a_configured_groups_member_is_not_noted(mk_bot, run_async):
    bot = _gbot(mk_bot)
    up = _update(user_id=BOB, chat_id=GROUP, ctype="supergroup")
    assert run_async(bot._authorize(up)) is True
    up.message.reply_text.assert_not_called()
    assert _records() == []


def test_a_mention_in_an_unknown_group_is_noted(mk_bot, run_async):
    """The message chat gate drops it before any other handler."""
    bot = _gbot(mk_bot)
    up = _update(user_id=EVE, chat_id=NEW_GROUP, ctype="supergroup",
                 title="new team", text="@aipagerbot hi")
    run_async(bot._note_unknown_group_message(up))
    up.message.reply_text.assert_not_called()
    assert [(r["user_id"], r["chat_id"], r["chat_title"]) for r in _records()] \
        == [(EVE, NEW_GROUP, "new team")]


@pytest.mark.parametrize("case", ["configured", "private", "anonymous",
                                  "personal", "bot"])
def test_only_an_unknown_groups_person_is_noted(mk_bot, run_async, case):
    bot = _gbot(mk_bot)
    if case == "configured":
        up = _update(user_id=EVE, chat_id=GROUP, ctype="supergroup")
    elif case == "private":
        up = _update(user_id=EVE, chat_id=EVE, ctype="private")
    else:
        up = _update(user_id=EVE, chat_id=NEW_GROUP, ctype="supergroup")
    if case == "anonymous":
        up.message.sender_chat = MagicMock(id=NEW_GROUP)
    if case == "personal":
        bot.scopes = None
    if case == "bot":
        up.effective_user.is_bot = True
    run_async(bot._note_unknown_group_message(up))
    up.message.reply_text.assert_not_called()
    assert _records() == []


def _start_with_recorded_handlers(bot, run_async):
    handlers: list = []
    app = MagicMock()
    app.add_handler = lambda h, group=0: handlers.append((group, h))
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.updater.start_polling = AsyncMock()
    app.bot = bot._app.bot
    builder = MagicMock()
    builder.build.return_value = app
    bot._make_builder = lambda: builder
    bot._update_bot_commands = AsyncMock()
    bot._probe_group_chats = AsyncMock()
    try:
        run_async(bot.start())
    finally:
        del bot._make_builder
        del bot._update_bot_commands
        del bot._probe_group_chats
    return handlers


def _ptb_text(chat_id, ctype, text="@aipagerbot hi"):
    import datetime as _dt
    from telegram import Chat, Message, Update, User
    chat = Chat(id=chat_id, type=ctype, title="t" if chat_id < 0 else None)
    msg = Message(message_id=1, date=_dt.datetime.now(_dt.timezone.utc),
                  chat=chat, text=text,
                  from_user=User(id=EVE, first_name="Eve", is_bot=False))
    return Update(update_id=1, message=msg)


def test_the_daemon_routes_only_unknown_group_messages_to_the_note(
        mk_bot, run_async):
    from telegram.ext import MessageHandler
    bot = _gbot(mk_bot)
    _write_config([_group(), _dm()])
    handlers = _start_with_recorded_handlers(bot, run_async)

    def _takers(update):
        return [h.callback for g, h in handlers
                if g == 0 and isinstance(h, MessageHandler)
                and h.check_update(update)]
    assert _takers(_ptb_text(NEW_GROUP, "supergroup")) == [
        bot._note_unknown_group_message]
    assert bot._note_unknown_group_message not in _takers(
        _ptb_text(GROUP, "supergroup"))
    assert bot._note_unknown_group_message not in _takers(
        _ptb_text(EVE, "private"))
    # It is the last message handler: a configured chat's message is
    # taken by the handlers before it.
    msg_handlers = [h for g, h in handlers
                    if g == 0 and isinstance(h, MessageHandler)]
    assert msg_handlers[-1].callback == bot._note_unknown_group_message


# =============================================================================
# (b) Edit a scope → Add a member; "Add a group scope" never replaces members
# =============================================================================

def test_edit_scope_offers_add_a_member_for_groups_only(monkeypatch):
    offered = {}

    def _select(prompt, choices, **k):
        offered[prompt] = [c.value for c in choices]
        return object()
    monkeypatch.setattr(edit_menu.questionary, "select", _select)
    monkeypatch.setattr(edit_menu, "_ask", lambda q: "cancel")
    edit_menu._edit_scope(_group(), TOKEN)
    edit_menu._edit_scope(_dm(), TOKEN)
    group_choices, dm_choices = offered.values()
    assert group_choices[0] == "add_member"
    assert "add_member" not in dm_choices


def _run_menu(monkeypatch, answers):
    """The real edit menu with canned answers; returns the reload hints."""
    queue = iter(answers)
    monkeypatch.setattr(edit_menu, "_ask",
                        lambda q: next(queue, None) or pytest.fail(
                            "ran out of canned answers"))
    monkeypatch.setattr(edit_menu.questionary, "select",
                        lambda *a, **k: object())
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    monkeypatch.setattr(edit_menu, "_bot_username", lambda t: "aipagerbot")


def test_add_a_member_appends_and_reloads_live(monkeypatch):
    _write_config([_dm(), _group(deny=("Bash",))])
    _run_menu(monkeypatch, ["edit_scope", "add_member", "exit"])
    monkeypatch.setattr(edit_menu, "_pick_scope",
                        lambda scopes, *a, **k: next(
                            s for s in scopes if s.chat_id == GROUP))
    captured = []

    def _capture(idx, *, existing_ids, existing_labels, token):
        captured.append((idx, set(existing_ids), set(existing_labels)))
        return {"id": EVE, "label": "eve"}
    monkeypatch.setattr(team_setup, "_capture_user_identity", _capture)
    monkeypatch.setattr(edit_menu, "_pick_role", lambda *a, **k: "user")
    _daemon(monkeypatch, 4242)
    signals = []
    monkeypatch.setattr(daemon_io.os, "kill",
                        lambda pid, sig: signals.append((pid, sig)))

    assert edit_menu._edit_flow() == 0
    assert captured == [(3, {ALY, BOB}, {"aly", "bob"})]
    assert _members() == [(ALY, "aly", "owner"), (BOB, "bob", "user"),
                          (EVE, "eve", "user")]
    scopes, token = scope_io.read_config()
    assert token == TOKEN
    assert [s.chat_id for s in scopes] == [DM, GROUP]       # order kept
    assert next(s for s in scopes if s.chat_id == GROUP).deny_tools == ("Bash",)
    assert signals == [(4242, signal.SIGUSR1)]


def test_add_a_member_cancelled_changes_nothing(monkeypatch):
    before = _write_config([_dm(), _group()])
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: None)
    assert edit_menu._add_member(_group(), TOKEN) is False
    assert _cfg().read_bytes() == before


def test_add_a_member_reads_the_group_as_it_is_now(monkeypatch):
    """Someone was added (or the file edited) since the menu read it: they
    are kept, and the same person is never added twice."""
    _write_config([_dm(), _group()])
    stale = _group()
    scope_io.append_member(GROUP, Member(id=CAROL, label="carol",
                                         role="read_only"), TOKEN)
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": EVE, "label": "eve"})
    monkeypatch.setattr(edit_menu, "_pick_role", lambda *a, **k: "admin")
    assert edit_menu._add_member(stale, TOKEN) is True
    assert _members() == [(ALY, "aly", "owner"), (BOB, "bob", "user"),
                          (CAROL, "carol", "read_only"), (EVE, "eve", "admin")]


@pytest.mark.parametrize("member,why", [
    (Member(id=BOB, label="robert", role="user"), "already a member"),
    (Member(id=EVE, label="bob", role="user"), "already used"),
])
def test_append_member_refuses_a_duplicate(member, why):
    before = _write_config([_dm(), _group()])
    problem = scope_io.append_member(GROUP, member, TOKEN)
    assert problem is not None and why in problem
    assert _cfg().read_bytes() == before


def test_append_member_refuses_a_missing_scope():
    before = _write_config([_dm()])
    assert scope_io.append_member(GROUP, Member(id=EVE, label="eve",
                                                role="user"), TOKEN)
    assert _cfg().read_bytes() == before


def test_add_new_scope_never_replaces_a_scope():
    before = _write_config([_dm(), _group()])
    assert scope_io.add_new_scope(
        Scope(chat_id=GROUP, kind="group", label="again",
              members=(Member(id=EVE, label="eve", role="user"),)),
        TOKEN) is False
    assert _cfg().read_bytes() == before
    assert scope_io.add_new_scope(_group(chat_id=NEW_GROUP), TOKEN) is True
    assert _members(GROUP) == [(ALY, "aly", "owner"), (BOB, "bob", "user")]
    assert _members(NEW_GROUP) == [(ALY, "aly", "owner"), (BOB, "bob", "user")]


def test_add_a_member_is_not_offered_for_a_dm(monkeypatch):
    before = _write_config([_dm()])
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: pytest.fail("must not capture"))
    assert edit_menu._add_member(_dm(), TOKEN) is False
    assert _cfg().read_bytes() == before


def _never_capture(monkeypatch):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: pytest.fail("must not capture"))
    monkeypatch.setattr(team_setup, "_collect_deny_tools",
                        lambda: pytest.fail("must not ask"))


def test_add_a_group_scope_for_a_configured_group_refuses(monkeypatch):
    """Probe C13 (C10): this used to leave the group with only eve."""
    before = _write_config([_dm(), _group()])
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: GROUP)
    warned = []
    monkeypatch.setattr(scope_flows, "friendly_warn",
                        lambda *a: warned.append(a))
    _never_capture(monkeypatch)
    monkeypatch.setattr(scope_flows, "_ask",
                        lambda q: pytest.fail("must not ask anything"))
    assert scope_flows.add_group_scope(TOKEN, "aipagerbot") is False
    assert warned == [(scope_flows.GROUP_ALREADY_SET_UP,)]
    assert scope_flows.GROUP_ALREADY_SET_UP == (
        "This group is already set up. Use Edit a scope → Add a member.")
    assert _cfg().read_bytes() == before
    from aipager.wizard.draft import load_draft
    assert not load_draft()


def test_add_a_group_scope_refuses_a_groups_old_id(monkeypatch):
    """A group Telegram upgraded (8.87) is the same group under its old id."""
    _write_config([_dm(), _group(chat_id=NEW_GROUP)])
    with _cfg().open("a") as f:
        f.write(f"chat_migrations:\n  {GROUP}: {NEW_GROUP}\n")
    before = _cfg().read_bytes()
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: GROUP)
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)
    _never_capture(monkeypatch)
    monkeypatch.setattr(scope_flows, "_ask",
                        lambda q: pytest.fail("must not ask anything"))
    assert scope_flows.add_group_scope(TOKEN, "aipagerbot") is False
    assert _cfg().read_bytes() == before


def test_add_a_group_scope_refuses_a_groups_new_id_while_the_file_has_the_old(
        monkeypatch):
    """The other direction: the scope still has the old id (a hand edit),
    the migration record names the new one, and the new id is detected."""
    _write_config([_dm(), _group()])
    with _cfg().open("a") as f:
        f.write(f"chat_migrations:\n  {GROUP}: {NEW_GROUP}\n")
    before = _cfg().read_bytes()
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: NEW_GROUP)
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)
    _never_capture(monkeypatch)
    monkeypatch.setattr(scope_flows, "_ask",
                        lambda q: pytest.fail("must not ask anything"))
    assert scope_flows.add_group_scope(TOKEN, "aipagerbot") is False
    assert _cfg().read_bytes() == before


def test_a_draft_for_a_configured_group_is_not_committed(monkeypatch):
    from aipager.wizard.draft import load_draft, save_draft
    before = _write_config([_dm(), _group()])
    save_draft({"kind": "group", "chat_id": GROUP, "label": "team",
                "members": [{"id": EVE, "label": "eve", "role": "user"}]})
    warned = []
    monkeypatch.setattr(scope_flows, "friendly_warn",
                        lambda *a: warned.append(a[0]))
    _never_capture(monkeypatch)
    assert scope_flows.add_group_scope(
        TOKEN, "aipagerbot", resume=load_draft()) is False
    assert warned == [scope_flows.GROUP_ALREADY_SET_UP]
    assert _cfg().read_bytes() == before
    assert not load_draft()


def test_a_group_set_up_meanwhile_is_not_replaced_at_commit(monkeypatch):
    """The check at the start is not enough: the commit itself never
    replaces a scope."""
    _write_config([_dm()])
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: GROUP)
    monkeypatch.setattr(scope_flows, "_operator_member", lambda: None)
    answers = iter(["team", False])
    monkeypatch.setattr(scope_flows, "_ask", lambda q: next(answers))
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)

    def _capture(*a, **k):
        # Meanwhile, someone sets the group up.
        scope_mod.dump_scopes([_dm(), _group()], TOKEN, _cfg())
        return {"id": EVE, "label": "eve"}
    monkeypatch.setattr(team_setup, "_capture_user_identity", _capture)
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    assert scope_flows.add_group_scope(TOKEN, "aipagerbot") is False
    assert _members() == [(ALY, "aly", "owner"), (BOB, "bob", "user")]


def test_add_a_group_scope_for_a_new_group_still_adds_it(monkeypatch):
    _write_config([_dm(), _group()])
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: NEW_GROUP)
    monkeypatch.setattr(scope_flows, "_operator_member", lambda: None)
    answers = iter(["new team", False])
    monkeypatch.setattr(scope_flows, "_ask", lambda q: next(answers))
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": EVE, "label": "eve"})
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    assert scope_flows.add_group_scope(TOKEN, "aipagerbot") is True
    assert _members(NEW_GROUP) == [(EVE, "eve", "user")]
    assert _members(GROUP) == [(ALY, "aly", "owner"), (BOB, "bob", "user")]


# =============================================================================
# (c) the optional rule, and the auto-deny notice
# =============================================================================

@pytest.mark.parametrize("answer,tools", [(False, []), (True, ["Write", "Edit"])])
def test_the_edit_rule_defaults_to_no_in_plain_words(monkeypatch, answer, tools):
    asked = []

    def _confirm(text, **k):
        asked.append((text, k.get("default")))
        return object()
    monkeypatch.setattr(team_setup.questionary, "confirm", _confirm)
    monkeypatch.setattr(team_setup, "_ask", lambda q: answer)
    printed = []
    monkeypatch.setattr(team_setup.console, "print",
                        lambda *a, **k: printed.append(" ".join(map(str, a))))
    assert team_setup._collect_deny_tools() == tools
    assert asked == [(
        "Block file edits (Write, Edit) for `user` members in this group? "
        "Admins and the owner are not affected.", False)]
    shown = " ".join(printed + [asked[0][0]])
    assert "override" not in shown and "deny_tools" not in shown


def _deny_bot(mk_bot, monkeypatch):
    bot = _gbot(mk_bot, scopes=[_group(deny=("Edit",)), _dm()])
    # (asyncio.sleep is never patched through a module path: the one
    # 0.1 s pause between the two keys is real.)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    sess = TrackedSession(name="claude-x1__g1001", label="x1",
                          status=Status.INTERACTIVE)
    sess.scope_chat_id, sess.scope_kind = GROUP, "group"
    sess.last_driver_user_id = BOB
    bot.registry._sessions[sess.name] = sess
    return bot, sess


def test_the_auto_deny_notice_says_who_and_which_role(mk_bot, run_async,
                                                       monkeypatch):
    bot, sess = _deny_bot(mk_bot, monkeypatch)
    audits = []
    monkeypatch.setattr("aipager.audit.append",
                        lambda **k: audits.append(k))
    assert bot._tool_auto_denied(sess, "Edit") is True
    run_async(bot._auto_deny(sess, {"name": "Edit", "summary": "a.py"},
                             bot._driver_user(sess)))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text == "⛔ <b>x1</b> · Edit blocked for @bob (role user)\n<i>a.py</i>"
    assert "rules" not in text and "—" not in text
    [rec] = audits
    assert rec["action"] == "Auto-denied" and rec["reason"] == "deny_tools"
    assert rec["denied"] is True and rec["tool"] == "Edit"
    assert rec["user_id"] == BOB and rec["username"] == "bob"
    assert rec["scope_chat_id"] == GROUP


def test_the_auto_deny_notice_without_a_known_driver(mk_bot, run_async,
                                                     monkeypatch):
    bot, sess = _deny_bot(mk_bot, monkeypatch)
    run_async(bot._auto_deny(sess, {"name": "Edit", "summary": "a.py"}, None))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text == "⛔ <b>x1</b> · Edit blocked\n<i>a.py</i>"


# =============================================================================
# 8.89: no default-mode question; the leftover key still round-trips
# =============================================================================

def test_the_default_mode_step_is_gone():
    assert not hasattr(first_run, "_step_default_mode")
    assert not hasattr(first_run, "_commit_default_mode")
    from aipager import config
    assert not hasattr(config, "DEFAULT_MODE")


def test_the_edit_menu_has_no_default_mode_entry():
    choices = edit_menu._menu_choices(has_error=False)
    assert "default_mode" not in [c.value for c in choices]
    assert not any("default mode" in str(c.title).lower() for c in choices)


def _stub_first_run(monkeypatch):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: (TOKEN, "aipagerbot"))
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: DM)
    monkeypatch.setattr(first_run, "_grant_owner_step", lambda *a, **k: "owner")
    steps = []
    monkeypatch.setattr(first_run, "_step_deps",
                        lambda step_label: steps.append(step_label) or True)
    monkeypatch.setattr(first_run, "_step_settings",
                        lambda step_label: steps.append(step_label))
    monkeypatch.setattr("aipager.wizard.scope_flows.offer_expansion",
                        lambda *a, **k: None)
    return steps


def test_first_run_asks_no_mode_and_ends_with_the_new_sessions_line(
        monkeypatch):
    steps = _stub_first_run(monkeypatch)
    monkeypatch.setattr(first_run, "_ask",
                        lambda q: pytest.fail("first run asked something"))
    printed = []
    monkeypatch.setattr(first_run.console, "print",
                        lambda *a, **k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(type(first_run.console), "is_terminal",
                        property(lambda self: False))
    assert first_run._first_run_flow() == 0
    assert steps == ["[4/4]", "[4/4]"]
    assert "default_mode" not in yaml.safe_load(_cfg().read_text())
    line = ("New sessions start in Auto for owners and admins; change it in "
            "Telegram under /settings → New sessions.")
    assert first_run.NEW_SESSIONS_LINE == line
    assert printed[-1].strip() == line


def test_the_new_sessions_line_tells_the_truth(mk_bot):
    """Auto for owners and admins, Ask for the others, whatever an old
    aipager.yaml's default_mode says."""
    _write_config([_dm()], default_mode="ask")
    group = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=EVE, label="eve", role="admin")))
    bot = _gbot(mk_bot, scopes=[group, _dm()])
    for uid, chat, auto in [(ALY, DM, True), (ALY, GROUP, True),
                            (EVE, GROUP, True), (BOB, GROUP, False)]:
        st = new_flow.resolve_new_session_settings(bot, chat, uid)
        assert st["skip_perms"] is auto, (uid, chat)


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_an_old_default_mode_key_loads_and_round_trips(mode):
    _write_config([_dm(), _group()], default_mode=mode)
    loaded = scope_mod.load_scopes(_cfg())
    assert loaded is not None and len(loaded[0]) == 2
    assert scope_io.append_member(
        GROUP, Member(id=EVE, label="eve", role="user"), TOKEN) is None
    scope_io.commit_scope(_dm(), TOKEN)
    assert yaml.safe_load(_cfg().read_text()).get("default_mode") == mode


def test_a_file_without_the_key_stays_without_it():
    _write_config([_dm()])
    scope_io.commit_scope(_group(), TOKEN)
    assert "default_mode" not in yaml.safe_load(_cfg().read_text())


def test_the_edit_flow_never_writes_a_default_mode(monkeypatch):
    before = _write_config([_dm()])
    _run_menu(monkeypatch, ["default_mode", "exit"])
    assert edit_menu._edit_flow() == 0
    assert _cfg().read_bytes() == before
