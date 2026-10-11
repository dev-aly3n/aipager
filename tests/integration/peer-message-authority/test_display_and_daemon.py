"""What people see, and the daemon's side: the busy card's sender line,
the stop notice in both chats (only where roles allow), the turn hold of
a peer-started turn, the ``peer_resolved`` event, C13's compact
attribution and the doctor check."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, peer_auth
from aipager import policy_snapshot as ps
from aipager.bot import peer_notice
from aipager.state import TURN_SENDER_TERMINAL, Status, TrackedSession, turn_sender_from_report

EN = i18n.get("en")
VERIFIED = {"status": "verified", "sender_session": "claude-morgh",
            "driver": {"class": "owner", "labels": ["@mohamad"]}}


# ---- the card line -------------------------------------------------------------------

def test_card_line_names_the_sender_and_who_drove_it():
    assert peer_notice.card_line(EN, VERIFIED) == "📨 from **morgh** (@mohamad)"


def test_card_line_for_a_terminal_sender():
    pf = {**VERIFIED, "driver": {"class": "terminal", "labels": []}}
    assert "typed in its terminal" in peer_notice.card_line(EN, pf)


def test_card_line_when_unverified_says_so():
    line = peer_notice.card_line(EN, {"status": "unverified"})
    assert "not verified" in line


@pytest.mark.parametrize("pf", [None, {}, {"status": "pending"}])
def test_no_card_line_before_the_check(pf):
    assert peer_notice.card_line(EN, pf) == ""


@pytest.mark.parametrize("code", ["fa", "ar", "zh-Hans", "de", "ru"])
def test_card_line_in_other_languages(code):
    line = peer_notice.card_line(i18n.get(code), VERIFIED)
    assert "morgh" in line and "@mohamad" in line and line != peer_notice.card_line(EN, VERIFIED)


def test_the_busy_card_starts_with_the_sender_line():
    from aipager.bot.animation import build_stream_card_ex
    sess = TrackedSession(name="claude-mohandes", label="mohandes", status=Status.BUSY)
    sess.peer_from = dict(VERIFIED)
    card, _dropped = build_stream_card_ex(sess, "Working")
    assert card.startswith("📨 from **morgh** (@mohamad)")
    sess.peer_from = None
    card2, _ = build_stream_card_ex(sess, "Working")
    assert "📨" not in card2


# ---- the stop notice -----------------------------------------------------------------

def test_halt_suffix_names_the_sender():
    s = peer_notice.halt_suffix(EN, VERIFIED, False)
    assert "This task came from <b>morgh</b>." == s


def test_halt_suffix_unverified_and_narrowed():
    s = peer_notice.halt_suffix(EN, {"status": "unverified"}, True)
    assert "could not verify" in s and "narrowed" in s
    assert peer_notice.halt_suffix(EN, None, False) == ""


def _bot_with(mk_bot, monkeypatch, receiver_chat, sender_chat):
    bot = mk_bot()
    recv = TrackedSession(name="claude-mohandes", label="mohandes", status=Status.BUSY)
    recv.scope_chat_id = receiver_chat
    send = TrackedSession(name="claude-morgh", label="morgh", status=Status.IDLE)
    send.scope_chat_id = sender_chat
    bot.registry._sessions.update({recv.name: recv, send.name: send})
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    sent: list = []

    async def fake_send(_bot, chat, text, **kw):
        sent.append((chat, text))
        return MagicMock(message_id=1)
    monkeypatch.setattr("aipager.bot.session_ops.send_text", fake_send)
    return bot, recv, sent


def test_the_sending_chat_hears_when_its_task_was_stopped(mk_bot, run_async, monkeypatch):
    bot, recv, sent = _bot_with(mk_bot, monkeypatch, -5, -5)
    recv.peer_from = dict(VERIFIED)
    run_async(bot._tell_peer_sender(recv, recv.peer_from,
                                    "SendMessage is in this role's deny_tools"))
    assert sent and sent[0][0] == -5
    assert "<b>mohandes</b> did not carry out the message <b>morgh</b>" in sent[0][1]


def test_another_chat_hears_nothing_the_owner_hears_in_private(mk_bot, run_async, monkeypatch):
    """C6: across chats only the owner's private chat is told, and only
    about an owner-driven (or terminal) send; never the sending chat,
    which may be a group."""
    bot, recv, sent = _bot_with(mk_bot, monkeypatch, -5, -777)
    monkeypatch.setattr("aipager.bot.report_flow.resolve_owner", lambda _b: 4242)
    member = {**VERIFIED, "driver": {"class": "member", "labels": ["@bob"]}}
    run_async(bot._tell_peer_sender(recv, member, "x"))
    assert sent == []  # roles and names stay in their chat
    run_async(bot._tell_peer_sender(recv, dict(VERIFIED), "x"))
    assert [c for c, _ in sent] == [4242]


def test_no_owner_no_cross_chat_notice(mk_bot, run_async, monkeypatch):
    bot, recv, sent = _bot_with(mk_bot, monkeypatch, -5, -777)
    monkeypatch.setattr("aipager.bot.report_flow.resolve_owner", lambda _b: None)
    run_async(bot._tell_peer_sender(recv, dict(VERIFIED), "x"))
    assert sent == []


def test_an_unverified_sender_is_never_told(mk_bot, run_async, monkeypatch):
    bot, recv, sent = _bot_with(mk_bot, monkeypatch, -5, -5)
    run_async(bot._tell_peer_sender(recv, {"status": "unverified",
                                           "sender_session": "claude-morgh"}, "x"))
    assert sent == []


# ---- the daemon's turn hold and event --------------------------------------------

def test_a_peer_turn_holds_other_senders_like_a_terminal_turn():
    assert turn_sender_from_report({"origin": "peer", "authors": []}) == TURN_SENDER_TERMINAL


def test_peer_resolved_records_the_sender_on_the_session(monkeypatch):
    from aipager.dtach.hook_receiver import HookReceiver
    from aipager.state import SessionRegistry
    reg = SessionRegistry()
    sess = TrackedSession(name="claude-mohandes", label="mohandes", status=Status.BUSY)
    reg._sessions[sess.name] = sess
    seen = []

    async def notify(s, event, ctx):
        seen.append((event, ctx))
    rec = HookReceiver(reg, notify)
    import json
    asyncio.run(rec._on_datagram(json.dumps({
        "hook_event_name": "peer_resolved", "session": "claude-mohandes",
        "status": "verified", "sender_session": "claude-morgh",
        "driver": {"class": "owner", "labels": ["@m"]}}).encode()))
    assert sess.peer_from["status"] == "verified"
    assert sess.peer_from["sender_session"] == "claude-morgh"
    assert seen and seen[-1][0] == "peer_resolved"


# ---- C13: who asked for /compact ------------------------------------------------

def test_the_mini_app_compact_passes_its_caller(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-j", label="j", status=Status.IDLE)
    calls = []

    async def inject(s, text, *a, **kw):
        calls.append((text, kw.get("driver_user_id")))
        return True
    bot._inject_prompt = inject
    run_async(bot._compact_session_core(sess, driver_user_id=42))
    assert calls == [("/compact", 42)]


def test_the_mini_app_compact_queues_with_its_caller(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-j", label="j", status=Status.BUSY)
    run_async(bot._compact_session_core(sess, driver_user_id=42))
    assert sess.pending_queue[-1][0] == "/compact"
    assert sess.pending_queue[-1][4] == 42


def test_an_injected_compact_note_is_marked_ran(mk_bot, tmp_path):
    bot = mk_bot()
    sess = TrackedSession(name="claude-j", label="j", status=Status.IDLE)
    ps.snapshot_path(sess.name).parent.mkdir(parents=True, exist_ok=True)
    path = ps.write_note(sess.name, None, None, None, msg_id=None, chat_id=None,
                         sender_key=None, author_user_id=None,
                         body="/compact", raw_text="/compact")
    bot._mark_injected_command_ran(sess, "/compact")
    assert path.with_suffix(".ran").exists()


# ---- doctor -----------------------------------------------------------------------------

def test_doctor_reports_the_receipt_folder(monkeypatch):
    from aipager import doctor
    monkeypatch.setattr("aipager.config.SCOPES", None)
    r = doctor.check_peer_messages()
    assert r.status == doctor.OK
    assert any("receipts" in d for d in r.detail)


def test_doctor_warns_about_a_shared_receipt_folder(monkeypatch):
    from aipager import doctor
    monkeypatch.setattr("aipager.config.SCOPES", None)
    store = peer_auth.store_dir()
    store.mkdir(parents=True)
    os.chmod(store, 0o777)
    r = doctor.check_peer_messages()
    assert r.status == doctor.WARN


def test_the_compact_button_sends_with_its_tapper_and_marks_it_ran(
        mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._app.bot.delete_message = AsyncMock()
    calls = []

    async def inject(s, text, *a, **kw):
        calls.append((text, kw.get("driver_user_id")))
        return True
    bot._inject_prompt = inject
    marked = []
    bot._mark_injected_command_ran = lambda s, c: marked.append(c)
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    assert calls and calls[0][0] == "/compact"
    assert calls[0][1] == update.effective_user.id
    assert marked == ["/compact"]


# ---- I5 / I6 and the translated reason ------------------------------------------

def test_restricted_roles_still_cannot_send():
    from aipager.safety import BUILTIN_ROLE_DEFAULTS
    for name in ("user", "read_only"):
        assert "SendMessage" in BUILTIN_ROLE_DEFAULTS[name]["deny_tools"]


@pytest.mark.parametrize("call", [
    ("Read", {"file_path": "~/.local/state/aipager/peer/x.json"}),
    ("Write", {"file_path": "~/.local/state/aipager/peer/x.json", "content": "{}"}),
    ("Bash", {"command": "cat ~/.local/state/aipager/peer/x.json"}),
])
def test_no_turn_without_the_owner_rights_can_reach_the_receipts(call, tmp_path):
    """I6: the receipt folder is under the protected state folder, for an
    admin's turn (which has Bash) as for anyone else's."""
    from aipager.dtach import enforce
    from aipager.safety import BUILTIN_ROLE_DEFAULTS
    from aipager.policy import Role
    d = BUILTIN_ROLE_DEFAULTS["admin"]
    role = Role(name="admin", **{k: (tuple(v) if isinstance(v, (list, tuple)) else v)
                                 for k, v in d.items()})
    ps.snapshot_path("claude-r").parent.mkdir(parents=True, exist_ok=True)
    ps.write_merged_snapshot("claude-r", {**ps.resolve_snapshot(role, None, None),
                                         "turn_origin": "telegram"})
    t = tmp_path / "t.jsonl"
    t.write_text('{"type":"user","message":{"role":"user","content":"[via Telegram · @a]\\nx"}}\n')
    tool, inp = call
    assert enforce.decide({"hook_event_name": "PreToolUse", "session": "claude-r",
                           "tool_name": tool, "tool_input": inp,
                           "transcript_path": str(t), "cwd": "/srv/p",
                           "session_id": "s"}) is not None


def test_the_new_reason_is_translated():
    from aipager import safety
    from aipager.bot import safety_notice
    fa = safety_notice.reason_text(i18n.get("fa"), safety.SEND_UNMANAGED_REASON)
    assert fa != safety.SEND_UNMANAGED_REASON and "SendMessage" in fa
