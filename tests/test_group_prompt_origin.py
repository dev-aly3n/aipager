"""Roadmap 8.74: a Telegram prompt always carries its origin and its
sender's rules.

Group audit A1/A10 (probes p4 and p8): a restricted group member got an
unrestricted turn by sending a slash command (typed raw, no marker), or
any message to a session whose last driver aipager could not resolve,
because the hook reads a governing prompt with no ``[via Telegram`` line
as a terminal prompt. These tests pin the fix on both sides:

- daemon: the marker comes from the message's own sender (unknown gets
  ``[via Telegram]``), every send path makes the sender the driver, and a
  slash command from a role without ``bypass_role_denies`` is refused
  unless it is one of aipager's keyboard commands or a model switch;
- hook: a Claude Code slash-command record is Telegram when the turn's
  snapshot consumed a note that sent that command.

DM parity: one DM scope with an owner (the operator's own install) and
personal mode see exactly what they saw before.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.bot import group_intake, new_flow, reactions
from aipager.bot.transport import NEEDS_ADMIN_REPLY, PROMPT_REFUSED
from aipager.dtach import enforce, inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
DM = 555
SESSION = "claude-api__g1001"

ALY, BOB, RO, ADA, STRANGER = 1, 2, 3, 4, 777


def _group_scope(members=None):
    return Scope(chat_id=GROUP, kind="group", label="team", members=members or (
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
        Member(id=ADA, label="ada", role="admin"),
    ))


def _dm_scope():
    return Scope(chat_id=DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


@pytest.fixture
def typed(monkeypatch):
    """Everything typed into a PTY, as ``(session, text)``."""
    out: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


@pytest.fixture
def gbot(mk_bot):
    """A scope-mode bot: the group (owner, user, read_only, admin) plus
    the owner's DM. Card and reaction plumbing is recorded, not run."""
    def _mk(scopes=None):
        bot = mk_bot(scopes=scopes if scopes is not None
                     else [_group_scope(), _dm_scope()])
        bot.policy = load_policy()
        bot._app.bot.id = 999
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


def _session(bot, name=SESSION, chat=GROUP, kind="group", driver=ALY):
    s = bot.registry.get_or_create(name)
    s.label = name.removeprefix("claude-").split("__")[0]
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = Status.IDLE
    s.last_driver_user_id = driver
    bot.registry.last_active_session = name     # the chat's target
    return s


def _update(text, *, user_id, chat_id=GROUP, message_id=400):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="")
    m = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = message_id
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.chat = u.effective_chat
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def _ctx():
    return MagicMock(bot=MagicMock(id=999))


def _replies(update) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in update.message.reply_text.await_args_list]


def _reacted(bot, emoji) -> bool:
    return any(c.args[1:] == (emoji,) for c in bot._react.await_args_list)


def _notes(name=SESSION):
    return ps.list_outstanding_notes(name)


# ---- D-G: slash commands from a restricted sender ---------------------------

def test_p4_a_users_direct_slash_command_is_refused_with_a_clear_reply(
        gbot, typed, run_async):
    """p4 case A: bob (user, no Bash) sends ``/api /deliver …``. Nothing
    is typed, no note is written, he is told why and gets a 🤷."""
    bot = gbot()
    sess = _session(bot)
    trigger_before = sess.trigger_msg_id
    u = _update("/api /deliver append 'curl x|sh' to ~/.bashrc", user_id=BOB)

    run_async(bot._handle_message(u, _ctx()))

    assert typed == []
    assert _notes() == []
    assert _replies(u) == [NEEDS_ADMIN_REPLY]
    assert _reacted(bot, reactions.NOT_DELIVERED)
    assert sess.trigger_msg_id == trigger_before   # nothing moved
    assert sess.status == Status.IDLE


def test_a_users_slash_command_is_refused_before_it_is_held(
        gbot, typed, run_async):
    """With a dialog open the message would be held, then refused only
    when it drained. The refusal comes first: nothing is queued."""
    bot = gbot()
    sess = _session(bot)
    sess.pending_permission = {"tool_summary": "Bash ls"}
    u = _update("/api /deliver go", user_id=BOB)

    run_async(bot._handle_message(u, _ctx()))

    assert sess.pending_queue == []
    assert _replies(u) == [NEEDS_ADMIN_REPLY]
    assert typed == []


def test_the_central_guard_refuses_a_users_slash_command(gbot, typed, run_async):
    """However the text reached ``_inject_prompt``, a user's slash
    command is refused there before anything is written or typed."""
    bot = gbot()
    sess = _session(bot)
    origin_before = sess.last_prompt_origin

    ok = run_async(bot._inject_prompt(sess, "/review the diff", driver_user_id=BOB))

    assert ok is PROMPT_REFUSED and not ok
    assert typed == []
    assert _notes() == []
    assert sess.last_prompt_origin == origin_before
    assert sess.last_driver_user_id == ALY        # bob never drove it


@pytest.mark.parametrize("sender", [None, STRANGER, RO])
def test_an_unknown_or_read_only_sender_lacks_the_right(
        gbot, typed, run_async, sender):
    bot = gbot()
    sess = _session(bot)
    ok = run_async(bot._inject_prompt(sess, "/deliver x", driver_user_id=sender))
    assert ok is PROMPT_REFUSED
    assert typed == []


@pytest.mark.parametrize("sender", [ALY, ADA])
def test_owners_and_admins_send_any_slash_command_raw(
        gbot, typed, run_async, sender):
    bot = gbot()
    sess = _session(bot)
    ok = run_async(bot._inject_prompt(sess, "/deliver append x", driver_user_id=sender))
    assert ok is True
    assert typed == [(SESSION, "/deliver append x")]      # raw: no marker
    note = _notes()[0]
    assert note["body"] == "/deliver append x"


def test_keyboard_commands_and_model_switches_still_go_through_for_a_user(
        gbot, typed, run_async):
    bot = gbot()
    allowed = ["/compact", *bot._command_map.values(),
               *bot._model_map.values(), "/model claude-sonnet-4-6",
               "/model  some-model[1m]"]
    assert "/compact" in bot._command_map.values()     # the default keyboard
    # The two free-form model switches are on no keyboard.
    assert not {"/model claude-sonnet-4-6", "/model  some-model[1m]"} & set(
        bot._model_map.values())
    for text in allowed:
        sess = _session(bot)
        ok = run_async(bot._inject_prompt(sess, text, driver_user_id=BOB))
        assert ok is True, text
    assert [t for _, t in typed] == allowed


def test_compact_is_allowed_even_when_the_keyboard_has_no_compact(
        gbot, typed, run_async):
    """aipager's own Compact button and the Mini App type ``/compact``
    with no sender; a keyboard.json without Compact must not break them."""
    bot = gbot()
    bot._command_map = {"Clear": "/clear"}
    ok = run_async(bot._inject_prompt(_session(bot), "/compact"))
    assert ok is True
    assert typed == [(SESSION, "/compact")]


@pytest.mark.parametrize("text", [
    "/model", "/model opus now", "/compact everything", "/init extra words",
    "/clear;rm -rf x", " /deliver x", "/model\n/deliver x", "/model\nopus",
])
def test_only_the_exact_keyboard_text_or_one_model_name_is_allowed(
        gbot, typed, run_async, text):
    bot = gbot()
    ok = run_async(bot._inject_prompt(_session(bot), text, driver_user_id=BOB))
    assert ok is PROMPT_REFUSED
    assert typed == []


def test_a_keyboard_command_button_is_sent_raw_for_a_user_and_sets_the_driver(
        gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot)
    label = next(lbl for lbl, cmd in bot._command_map.items() if cmd == "/init")
    u = _update(group_intake.KEYBOARD_MARKER + label, user_id=BOB)

    run_async(bot._handle_message(u, _ctx()))

    assert typed == [(SESSION, "/init")]
    assert sess.last_driver_user_id == BOB
    assert NEEDS_ADMIN_REPLY not in _replies(u)


def test_a_model_button_is_sent_for_a_user(gbot, typed, run_async):
    bot = gbot()
    _session(bot)
    label, cmd = next(iter(bot._model_map.items()))
    u = _update(group_intake.KEYBOARD_MARKER + label, user_id=BOB)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [(SESSION, cmd)]


def test_a_slash_template_is_refused_for_a_user(gbot, typed, run_async):
    """Templates are prompts, not keyboard commands: a slash one is
    refused for a user by the template path itself."""
    bot = gbot()
    sess = _session(bot)
    bot._template_map = {"Review": "/review everything"}
    u = _update(group_intake.KEYBOARD_MARKER + "Review", user_id=BOB)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == []
    assert _replies(u) == [NEEDS_ADMIN_REPLY]
    assert sess.last_prompt != "/review everything"


def test_a_file_caption_slash_command_is_refused_for_a_user(
        gbot, typed, run_async):
    """p4: a caption ``/api /cmd`` gives the prompt ``/cmd <path>``."""
    bot = gbot()
    _session(bot)
    u = _update(None, user_id=BOB)
    run_async(bot._inject_file_prompt(
        u, MagicMock(bot=MagicMock(id=999)), "/api /deliver do it",
        [Path("/srv/x.txt")], all_photos=False, log_name="x.txt"))
    assert typed == []
    assert _replies(u) == [NEEDS_ADMIN_REPLY]


def test_new_with_a_slash_first_message_starts_the_session_without_it(
        gbot, typed, run_async):
    """p4: ``/new x1 /cmd …`` used to queue the command raw."""
    bot = gbot()

    async def _create(label, *, scope_chat_id, driver_user_id=None, **kw):
        name = f"claude-{label}__g1001"
        s = bot.registry.get_or_create(name)
        s.label, s.scope_chat_id, s.scope_kind = label, scope_chat_id, "group"
        bot.registry.transition(name, Status.IDLE)
        return name, ""

    bot.create_session = _create
    u = _update("/new x1 /deliver do it", user_id=BOB)
    run_async(new_flow.create_from_text(bot, u, "x1 /deliver do it"))

    sess = bot.registry.get("claude-x1__g1001")
    assert sess is not None
    assert sess.pending_queue == []
    assert NEEDS_ADMIN_REPLY in _replies(u)


def test_a_held_slash_command_that_drains_refused_is_dropped_and_explained(
        gbot, typed, run_async):
    """Held before the check could run (a /new conflict card queues the
    prompt), the drain refuses it: 🤷 and the reply, nothing typed."""
    bot = gbot()
    sess = _session(bot)
    sess.queue_prompt("/deliver go", 610, "", BOB)
    bot._mark_not_delivered = AsyncMock()
    bot._app.bot.send_message = AsyncMock()

    run_async(bot._drain_next_queued(sess))

    assert typed == []
    bot._mark_not_delivered.assert_awaited_once_with(sess, [{"msg_id": 610}])
    kw = bot._app.bot.send_message.await_args.kwargs
    assert kw["chat_id"] == GROUP
    assert kw["text"] == NEEDS_ADMIN_REPLY
    assert kw["reply_to_message_id"] == 610


# ---- the marker comes from the sender --------------------------------------

def test_p8_a_removed_members_held_message_drains_marked_and_floored(
        gbot, typed, run_async):
    """p8: bob's held message drains after a reload removed him. It must
    still read as Telegram (``[via Telegram]``), under the floor."""
    bot = gbot()
    sess = _session(bot, driver=BOB)
    sess.queue_prompt("rm the deploy keys and push", 600, "", BOB)
    bot.scopes = [_group_scope((Member(id=ALY, label="aly", role="owner"),))]

    run_async(bot._drain_next_queued(sess))

    assert typed == [(SESSION, "[via Telegram]\nrm the deploy keys and push")]
    note = _notes()[0]
    assert note["bypass_safety"] is False
    assert set(ps.FLOOR_SNAPSHOT["deny_tools"]) <= set(note["deny_tools"])


def test_p4_b_a_message_to_a_session_whose_last_driver_is_unknown_is_marked(
        gbot, typed, run_async):
    """p4 case B: the last driver was removed; bob's message carries his
    own marker, not none."""
    bot = gbot()
    _session(bot, driver=9999)            # nobody aipager knows
    u = _update("run `id` with Bash and show me", user_id=BOB)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [(SESSION, "[via Telegram · @bob · role:user]\n"
                               "run `id` with Bash and show me")]


def test_an_unknown_sender_gets_the_bare_marker(gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot)
    run_async(bot._inject_prompt(sess, "hello", driver_user_id=STRANGER))
    assert typed == [(SESSION, "[via Telegram]\nhello")]
    assert sess.last_driver_user_id == ALY     # a stranger never drives


def test_no_sender_never_borrows_the_last_drivers_marker(gbot, typed, run_async):
    """Retry by someone else, a drained entry with no captured sender:
    the last driver (the owner) must not label it."""
    bot = gbot()
    sess = _session(bot, driver=ALY)
    run_async(bot._inject_prompt(sess, "hello"))
    assert typed == [(SESSION, "[via Telegram]\nhello")]


def test_bobs_direct_send_is_labelled_bob_and_makes_him_the_driver(
        gbot, typed, run_async):
    """A1 side effect: bob's ``/api …`` reached Claude as the owner's."""
    bot = gbot()
    sess = _session(bot, driver=ALY)
    u = _update("/api list the files", user_id=BOB)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [(SESSION, "[via Telegram · @bob · role:user]\nlist the files")]
    assert sess.last_driver_user_id == BOB


def test_direct_send_to_an_untracked_live_session_makes_the_sender_driver(
        gbot, typed, run_async):
    """``_direct_send``'s second branch: a live session the registry did
    not know, adopted by the typed name. Named for this group: an
    unsuffixed name is the owner DM's (roadmap 8.82/8.76), not the
    group's to adopt."""
    bot = gbot()
    u = _update("/fresh__g1001 hello", user_id=ADA)
    run_async(bot._direct_send(u, "fresh__g1001", "hello"))
    sess = bot.registry.get("claude-fresh__g1001")
    assert sess.last_driver_user_id == ADA
    # Stamped with the group at adoption: the group form of the marker.
    assert typed == [("claude-fresh__g1001",
                      "[via Telegram · @ada · role:admin]\nhello")]


def test_a_template_makes_the_sender_the_driver(gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot, driver=ALY)
    label, prompt = next(iter(bot._template_map.items()))
    u = _update(group_intake.KEYBOARD_MARKER + label, user_id=BOB)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [(SESSION, f"[via Telegram · @bob · role:user]\n{prompt}")]
    assert sess.last_driver_user_id == BOB


def _retry_tap(bot, run_async, user_id):
    query = MagicMock()
    query.data = f"{SESSION}:retry"
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=42, text="❌ failed")
    query.message.chat = MagicMock(id=GROUP)
    query.message.chat_id = GROUP
    query.from_user = MagicMock(id=user_id)
    u = MagicMock(callback_query=query, effective_user=query.from_user,
                  effective_chat=query.message.chat, message=None)
    bot._app.bot.delete_message = AsyncMock()
    run_async(bot._handle_callback(u, MagicMock()))
    return query


def test_retry_by_its_author_runs_as_and_makes_the_author_the_driver(
        gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot, driver=ALY)
    sess.last_prompt = "again please"
    sess.last_prompt_driver_user_id = BOB
    _retry_tap(bot, run_async, BOB)
    assert typed == [(SESSION, "[via Telegram · @bob · role:user]\nagain please")]
    assert sess.last_driver_user_id == BOB


def test_retry_by_someone_else_keeps_the_driver_and_is_unattributed(
        gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot, driver=ALY)
    sess.last_prompt = "again please"
    sess.last_prompt_driver_user_id = BOB
    _retry_tap(bot, run_async, ADA)
    assert typed == [(SESSION, "[via Telegram]\nagain please")]
    assert sess.last_driver_user_id == ALY


# ---- DM parity and personal mode --------------------------------------------

def test_dm_parity_owner_marker_and_slash_commands_unchanged(
        gbot, typed, run_async):
    """The operator's install: one DM scope, an owner. Same marker bytes
    as before, any slash command typed raw, the owner's rights on the
    note."""
    bot = gbot(scopes=[_dm_scope()])
    name = "claude-dev__d555"
    sess = _session(bot, name=name, chat=DM, kind="dm", driver=ALY)

    assert run_async(bot._inject_prompt(sess, "fix it", driver_user_id=ALY))
    assert run_async(bot._inject_prompt(sess, "/deliver ship it", driver_user_id=ALY))
    assert run_async(bot._inject_prompt(sess, "/security-review", driver_user_id=ALY))

    assert typed == [(name, "[via Telegram · @aly]\nfix it"),
                     (name, "/deliver ship it"),
                     (name, "/security-review")]
    assert all(n["bypass_safety"] is True for n in _notes(name))
    assert sess.last_driver_user_id == ALY


def test_dm_parity_owner_direct_send_of_a_slash_command(gbot, typed, run_async):
    bot = gbot(scopes=[_dm_scope()])
    sess = _session(bot, name="claude-dev__d555", chat=DM, kind="dm")
    u = _update("/dev /deliver ship it", user_id=ALY, chat_id=DM)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [("claude-dev__d555", "/deliver ship it")]
    assert NEEDS_ADMIN_REPLY not in _replies(u)
    assert sess.status == Status.BUSY


def test_personal_mode_is_unchanged(mk_bot, typed, run_async):
    bot = mk_bot()                     # scopes=None, team=None
    sess = bot.registry.get_or_create("claude-dev")
    sess.status = Status.IDLE
    assert run_async(bot._inject_prompt(sess, "hello", driver_user_id=12345))
    assert run_async(bot._inject_prompt(sess, "/deliver x", driver_user_id=12345))
    assert run_async(bot._inject_prompt(sess, "/whatever"))
    assert typed == [("claude-dev", "hello"), ("claude-dev", "/deliver x"),
                     ("claude-dev", "/whatever")]
    assert sess.last_driver_user_id is None


# ---- hook side: a slash-command record's origin ----------------------------

def _command_record(name: str, args: str = "") -> dict:
    """A slash command as Claude Code writes it: the record line, then the
    expanded body as an ``isMeta`` entry."""
    text = (f"<command-message>{name.lstrip('/')}</command-message>\n"
            f"<command-name>{name}</command-name>")
    if args:
        text += f"\n<command-args>{args}</command-args>"
    return {"parentUuid": "p1", "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": text},
            "uuid": "u1", "timestamp": "2026-10-04T10:00:00.000Z"}


def _meta_body() -> dict:
    return {"parentUuid": "u1", "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": [
                {"type": "text", "text": "# Deliver\nRun the pipeline..."}]},
            "isMeta": True, "uuid": "u2"}


def _write_transcript(tmp_path, *entries) -> str:
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n"
                         for e in entries), encoding="utf-8")
    return str(p)


def _role_snapshot(role_name: str, member: Member, bodies) -> dict:
    snap = ps.resolve_snapshot(load_policy().get_role(role_name),
                               _group_scope(), member)
    snap["note_bodies"] = list(bodies)
    snap["scope_mode"] = True
    return snap


def _decide(monkeypatch, snap, transcript, tool, tool_input):
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: dict(snap))
    return enforce.decide({
        "hook_event_name": "PreToolUse", "tool_name": tool,
        "tool_input": tool_input, "session": SESSION,
        "transcript_path": transcript, "cwd": "/srv/team/api",
    })


PROTECTED_WRITE = ("Write", {"file_path": f"/tmp/claude-policy-{SESSION}.json",
                             "content": '{"bypass_safety": true}'})


def test_an_admins_telegram_slash_command_runs_under_the_admin_rules(
        tmp_path, monkeypatch):
    """ada (admin) sends ``/api /deliver …``: typed raw, so the transcript
    holds only the command record. Her consumed note names ``/deliver``,
    so the turn is Telegram and the admin floor applies."""
    ada = _group_scope().members[3]
    snap = _role_snapshot("admin", ada, ["/deliver append to ~/.bashrc"])
    t = _write_transcript(tmp_path, _command_record(
        "/deliver", "append to ~/.bashrc"), _meta_body())
    assert enforce._origin_from_transcript(t, snap["note_bodies"]) == "telegram"
    block = _decide(monkeypatch, snap, t, *PROTECTED_WRITE)
    assert block and "protected path" in block["reason"]
    assert _decide(monkeypatch, snap, t, "Bash", {"command": "sudo ls"})


def test_a_users_allowed_keyboard_command_is_enforced_at_the_hook(
        tmp_path, monkeypatch):
    """``/init`` is on the keyboard, so a user may send it; the turn it
    starts still runs under the user's rules (no Bash)."""
    bob = _group_scope().members[1]
    snap = _role_snapshot("user", bob, ["/init"])
    t = _write_transcript(tmp_path, _command_record("/init"), _meta_body())
    block = _decide(monkeypatch, snap, t, "Bash", {"command": "echo hi"})
    assert block is not None


def test_an_owners_terminal_slash_command_stays_unrestricted(
        tmp_path, monkeypatch):
    """After a Telegram turn, the owner types ``/review`` in the terminal.
    The snapshot (kept from that turn) names no ``/review``: terminal."""
    bob = _group_scope().members[1]
    snap = _role_snapshot("user", bob, ["[via Telegram · @bob · role:user]\nhi",
                                        "/deliver x"])
    t = _write_transcript(tmp_path, _command_record("/review", "the diff"),
                          _meta_body())
    assert enforce._origin_from_transcript(t, snap["note_bodies"]) == "terminal"
    assert _decide(monkeypatch, snap, t, "Bash", {"command": "echo hi"}) is None
    assert _decide(monkeypatch, snap, t, *PROTECTED_WRITE) is None


@pytest.mark.parametrize("bodies,expected", [
    (["/deliver go"], "telegram"),
    (["/deliver"], "telegram"),
    (["  /deliver\tgo"], "telegram"),
    (["/deliverx go"], "terminal"),        # first token must be exact
    (["go /deliver"], "terminal"),         # first token only
    (["[via Telegram · @bob]\n/deliver"], "terminal"),
    ([], "terminal"),
    ([None, 3, ""], "terminal"),           # malformed entries ignored
    ("/deliver go", "terminal"),           # not a list
    (None, "terminal"),
    ({"/deliver go": 1}, "terminal"),      # a mapping is no body list
])
def test_origin_of_a_command_record_reads_only_the_first_token(
        tmp_path, bodies, expected):
    t = _write_transcript(tmp_path, _command_record("/deliver", "go"))
    assert enforce._origin_from_transcript(t, bodies) == expected


def test_a_namespaced_command_matches_its_full_name(tmp_path):
    t = _write_transcript(tmp_path, _command_record("/plug:ship", "x"))
    assert enforce._origin_from_transcript(t, ["/plug:ship x"]) == "telegram"
    assert enforce._origin_from_transcript(t, ["/ship x"]) == "terminal"


def test_the_marker_still_wins_and_unreadable_still_fails_closed(tmp_path):
    t = _write_transcript(tmp_path, {"type": "user", "message": {
        "role": "user", "content": "[via Telegram]\nhello"}})
    assert enforce._origin_from_transcript(t, ()) == "telegram"
    assert enforce._origin_from_transcript(str(tmp_path / "missing.jsonl"),
                                           ()) == "telegram"
    assert enforce._origin_from_transcript(None, ["/x"]) == "telegram"


def test_the_scan_prefilter_parses_a_command_record_line(tmp_path):
    """``_needs_parse`` must never skip the record (scan-cost guarantee):
    skipped, the scan would walk past it to an older prompt."""
    raw = json.dumps(_command_record("/deliver", "go"),
                     separators=(",", ":")).encode()
    assert enforce._needs_parse(raw, sticky=False) is True
    assert enforce._needs_parse(raw, sticky=True) is True
    # An older Telegram prompt before it must not govern the turn.
    t = _write_transcript(
        tmp_path,
        {"type": "user", "message": {"role": "user",
                                     "content": "[via Telegram · @aly]\nolder"}},
        _command_record("/review"), _meta_body())
    assert enforce._origin_from_transcript(t, ()) == "terminal"


# ---- the remaining callers --------------------------------------------------

def test_a_users_slash_command_to_an_untracked_live_session_adopts_nothing(
        gbot, typed, run_async):
    """``_direct_send``'s second branch refuses before adopting the
    typed name, so a refused command leaves no registry entry behind."""
    bot = gbot()
    u = _update("/fresh__g1001 /deliver x", user_id=BOB)
    run_async(bot._direct_send(u, "fresh__g1001", "/deliver x"))
    assert typed == []
    assert bot.registry.get("claude-fresh__g1001") is None
    assert _replies(u) == [NEEDS_ADMIN_REPLY]


def test_a_spoken_slash_command_from_a_user_is_refused_before_anything_moves(
        gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot)
    trigger_before = sess.trigger_msg_id
    u = _update(None, user_id=BOB)
    run_async(bot._dispatch_voice_transcript(u, "/deliver go", _ctx()))
    assert typed == []
    assert _replies(u) == [NEEDS_ADMIN_REPLY]
    assert sess.trigger_msg_id == trigger_before
    assert sess.last_prompt != "/deliver go"


def test_a_file_from_a_user_refused_moves_no_trigger(gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot)
    trigger_before = sess.trigger_msg_id
    u = _update(None, user_id=BOB, message_id=432)
    run_async(bot._inject_file_prompt(
        u, _ctx(), "/api /deliver do it", [Path("/srv/x.txt")],
        all_photos=False, log_name="x.txt"))
    assert sess.trigger_msg_id == trigger_before
    assert not (sess.last_prompt or "").startswith("/deliver")


def test_retry_of_an_owners_slash_command_by_a_user_says_why(
        gbot, typed, run_async):
    """Retry runs someone else's text with no sender, which may send no
    slash command: the tap is told why instead of "Failed to retry"."""
    bot = gbot()
    sess = _session(bot)
    sess.last_prompt = "/deliver ship it"
    sess.last_prompt_driver_user_id = ALY
    query = _retry_tap(bot, run_async, BOB)
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert NEEDS_ADMIN_REPLY in answers
    assert typed == []


def test_new_with_a_slash_first_message_for_a_taken_name_asks_without_it(
        gbot, typed, run_async):
    """The name-conflict card must not carry a refused command to a
    Resume or Replace: it is refused before the card is shown."""
    bot = gbot()
    _session(bot, name="claude-x1__g1001")
    bot._send_new_conflict_prompt = AsyncMock()
    u = _update("/new x1 /deliver do it", user_id=BOB)
    run_async(new_flow.create_from_text(bot, u, "x1 /deliver do it"))
    assert NEEDS_ADMIN_REPLY in _replies(u)
    assert bot._send_new_conflict_prompt.await_args.kwargs["prompt"] == ""


def test_a_slash_command_is_typed_without_leading_whitespace(
        gbot, typed, run_async):
    """Typed as checked: with leading whitespace Claude Code could take
    it as a plain prompt, which carries no marker."""
    bot = gbot()
    sess = _session(bot)
    assert run_async(bot._inject_prompt(sess, "  /deliver x", driver_user_id=ALY))
    assert typed == [(SESSION, "/deliver x")]


def test_notes_say_whether_they_come_from_scope_mode(
        gbot, mk_bot, typed, run_async):
    bot = gbot()
    run_async(bot._inject_prompt(_session(bot), "/init", driver_user_id=BOB))
    assert _notes()[0]["scope_mode"] is True
    personal = mk_bot()
    psess = personal.registry.get_or_create("claude-dev")
    psess.status = Status.IDLE
    run_async(personal._inject_prompt(psess, "/init", driver_user_id=5))
    assert _notes("claude-dev")[0]["scope_mode"] is False


# ---- review rev-iter1-001: a note left ahead of the command -----------------

def _outstanding(body, ran=False):
    return {"body": body, "raw_text": body, "queued_at": 1.0, "ran": ran}


def test_the_command_note_is_consumed_past_a_lingering_note_ahead_of_it():
    model = _outstanding("/model sonnet")
    init = _outstanding("/init")
    assert ps.match_notes_prefix_run([model, init], "/init") == []
    assert ps.match_notes_for_prompt([model, init], "/init") == [init]


@pytest.mark.parametrize("prompt,expected", [
    ("/init", ["/init"]),
    ("/review the diff", []),              # no note sent /review
    ("hello", []),                         # plain prompts: prefix run only
    ("/ini", []),                          # first token must be exact
    ("/review /init", []),                 # another command naming it
])
def test_the_extra_command_match_is_narrow(prompt, expected):
    notes = [_outstanding("/model sonnet"), _outstanding("/init"),
             _outstanding("/init")]
    got = ps.match_notes_for_prompt(notes, prompt)
    assert [n["body"] for n in got] == expected
    if expected:
        assert got[0] is notes[1]          # the oldest


def test_a_users_init_after_a_model_tap_is_enforced_end_to_end(
        gbot, typed, run_async, tmp_path):
    """Reproduces rev-iter1-001: bob taps a model, then Init. The model
    note is still outstanding (ran, inside its grace) when Init's
    UserPromptSubmit fires; the turn must still be bob's."""
    from aipager.dtach import notify_hook
    bot = gbot()
    sess = _session(bot)
    assert run_async(bot._inject_prompt(sess, "/model sonnet", driver_user_id=BOB))
    ps.mark_command_notes_ran(SESSION, ps.list_outstanding_notes(SESSION))
    assert run_async(bot._inject_prompt(sess, "/init", driver_user_id=BOB))

    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    consumed, _ = notify_hook._match_and_promote(SESSION, "/init")
    assert [n["body"] for n in consumed] == ["/init"]

    t = _write_transcript(tmp_path, _command_record("/init"), _meta_body())
    block = enforce.decide({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "echo pwned >> ~/.bashrc"},
        "session": SESSION, "transcript_path": t, "cwd": "/srv/team/api"})
    assert block is not None


# ---- review rev-iter1-002: personal mode at the hook -------------------------

def test_personal_mode_telegram_slash_command_stays_unrestricted_at_the_hook(
        mk_bot, typed, run_async, tmp_path):
    """Personal mode writes floor notes; they never applied to a
    markerless prompt, and a slash command must not change that."""
    from aipager.dtach import notify_hook
    bot = mk_bot()
    name = "claude-dev"
    sess = bot.registry.get_or_create(name)
    sess.status = Status.IDLE
    assert run_async(bot._inject_prompt(sess, "/security-review",
                                        driver_user_id=5))
    ps.snapshot_path(name).parent.mkdir(parents=True, exist_ok=True)
    notify_hook._match_and_promote(name, "/security-review")
    snap = ps.read_snapshot(name)
    assert snap["note_bodies"] == ["/security-review"]
    assert snap["scope_mode"] is False
    t = _write_transcript(tmp_path, _command_record("/security-review"),
                          _meta_body())
    assert enforce.decide({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "git diff"}, "session": name,
        "transcript_path": t, "cwd": "/srv/dev"}) is None


def test_scope_mode_survives_a_prompt_no_note_accounts_for():
    """A terminal prompt queued behind a Telegram turn keeps the turn's
    snapshot, scope_mode included."""
    current = {"bypass_safety": False, "deny_tools": [], "allow_tools": [],
               "deny_paths_no_access": [], "deny_paths_no_write": [],
               "deny_bash_patterns": [], "note_bodies": ["/init"],
               "scope_mode": True}
    kept = ps.snapshot_for_unattributed_prompt(current, [], "typed here")
    assert kept["scope_mode"] is True
    assert kept["note_bodies"] == ["/init"]


# ---- review rev-iter1-006: slash text Claude Code took as a plain prompt -----

def test_a_plain_prompt_equal_to_a_consumed_body_is_telegram(tmp_path):
    t = _write_transcript(tmp_path, {"type": "user", "message": {
        "role": "user", "content": "/etc/hosts what is this"}})
    assert enforce._origin_from_transcript(
        t, ["/etc/hosts what is this"]) == "telegram"
    assert enforce._origin_from_transcript(t, ["/etc/hosts"]) == "terminal"


def test_the_extra_command_match_needs_the_whole_body_in_the_prompt():
    """Same command, other arguments: that note did not send this."""
    notes = [_outstanding("/model sonnet"), _outstanding("/deliver ship it")]
    assert ps.match_notes_for_prompt(notes, "/deliver other thing") == []
    assert ps.match_notes_for_prompt(notes, "/deliver ship it") == [notes[1]]


# ---- review rev-iter2-001: later pick-ups while a command's turn runs --------

def _bash_decision(t, cmd="echo pwned >> ~/.bashrc"):
    return enforce.decide({
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session": SESSION, "transcript_path": t, "cwd": "/srv/team/api"})


def test_a_message_queued_during_a_users_command_keeps_the_command_enforced(
        gbot, typed, run_async, tmp_path):
    """UserPromptSubmit fires at enqueue: bob's "also hi", sent while his
    ``/init`` runs, replaces the snapshot. ``/init``'s record still
    governs the turn and must still read as Telegram."""
    from aipager.dtach import notify_hook
    bot = gbot()
    sess = _session(bot)
    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    assert run_async(bot._inject_prompt(sess, "/init", driver_user_id=BOB))
    notify_hook._match_and_promote(SESSION, "/init")
    t = _write_transcript(tmp_path, _command_record("/init"), _meta_body())
    assert _bash_decision(t) is not None

    sess.status = Status.BUSY
    assert run_async(bot._inject_prompt(sess, "also hi", driver_user_id=BOB))
    body = ps.list_outstanding_notes(SESSION)[0]["body"]
    consumed, _ = notify_hook._match_and_promote(SESSION, body)
    assert consumed
    assert _bash_decision(t) is not None


def test_two_queued_commands_both_stay_enforced(gbot, typed, run_async, tmp_path):
    """Both queue-time pick-ups fire before ``/init`` runs."""
    from aipager.dtach import notify_hook
    bot = gbot()
    sess = _session(bot)
    sess.status = Status.BUSY
    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    assert run_async(bot._inject_prompt(sess, "/init", driver_user_id=BOB))
    assert run_async(bot._inject_prompt(sess, "/security-review", driver_user_id=BOB))
    notify_hook._match_and_promote(SESSION, "/init")
    notify_hook._match_and_promote(SESSION, "/security-review")
    for name in ("/init", "/security-review"):
        t = _write_transcript(tmp_path, _command_record(name), _meta_body())
        assert _bash_decision(t) is not None, name


def test_carried_command_bodies_are_slash_only_unique_and_capped():
    current = {"note_bodies": [
        "/a", "[via Telegram]\nhi", "/b x", "/a", 3, None,
        *[f"/c{i}" for i in range(40)]]}
    out = ps._carried_command_bodies(current, ["/b x"])
    assert out[:2] == ["/a", "/c0"]
    assert "/b x" not in out and "[via Telegram]\nhi" not in out
    assert len(out) == ps.CARRIED_COMMAND_BODIES
    assert ps._carried_command_bodies(None, []) == []
    assert ps._carried_command_bodies({"note_bodies": "/a"}, []) == []


def test_carried_bodies_do_not_make_personal_mode_scoped(tmp_path):
    """A personal-mode snapshot carrying command bodies stays unscoped."""
    ps.snapshot_path("claude-dev").parent.mkdir(parents=True, exist_ok=True)
    ps.write_merged_snapshot("claude-dev", {"note_bodies": ["/init"],
                                            "scope_mode": False})
    merged = ps.snapshot_for_prompt(
        "claude-dev", "hello", [], [{"body": "hello", "scope_mode": False}])
    assert merged["note_bodies"] == ["hello", "/init"]
    assert merged["scope_mode"] is False


# ---- review rev-iter2-003: an owner retrying a member's command ------------

def test_an_owner_retrying_a_members_slash_command_is_told_why(
        gbot, typed, run_async):
    bot = gbot()
    sess = _session(bot)
    sess.last_prompt = "/deliver ship it"
    sess.last_prompt_driver_user_id = BOB
    query = _retry_tap(bot, run_async, ALY)
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert "Only its sender can retry that command." in answers
    assert NEEDS_ADMIN_REPLY not in answers
    assert typed == []


def test_an_owner_retrying_their_own_slash_command_resends_it(
        gbot, typed, run_async):
    """DM parity for Retry: the author's own command goes through raw."""
    bot = gbot()
    sess = _session(bot)
    sess.last_prompt = "/deliver ship it"
    sess.last_prompt_driver_user_id = ALY
    _retry_tap(bot, run_async, ALY)
    assert typed == [(SESSION, "/deliver ship it")]


# ---- review rev-iter3-001 / rev-iter4-001: what a terminal prompt keeps ------

def test_a_second_delivery_of_a_running_command_keeps_it_enforced(
        gbot, typed, run_async, tmp_path):
    """After a compact Claude Code can deliver a message a second time,
    with no note left to match (8.49). For a running Telegram ``/init``
    that must not make the rest of its turn a terminal one."""
    from aipager.dtach import notify_hook
    bot = gbot()
    sess = _session(bot)
    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    assert run_async(bot._inject_prompt(sess, "/init", driver_user_id=BOB))
    notify_hook._match_and_promote(SESSION, "/init")
    sess.status = Status.BUSY
    assert run_async(bot._inject_prompt(sess, "also hi", driver_user_id=BOB))
    notify_hook._match_and_promote(SESSION, ps.list_outstanding_notes(SESSION)[0]["body"])
    t = _write_transcript(tmp_path, _command_record("/init"), _meta_body())

    for _ in range(2):                         # redelivered, twice for luck
        consumed, _ = notify_hook._match_and_promote(SESSION, "/init")
        assert consumed == []
        assert _bash_decision(t) is not None


def test_a_terminal_command_telegram_never_sent_stays_unrestricted(
        gbot, typed, run_async, tmp_path):
    """bob's Telegram ``/init`` turn ended; the operator types
    ``/review`` in the terminal. No body names it: terminal."""
    from aipager.dtach import notify_hook
    bot = gbot()
    sess = _session(bot)
    ps.snapshot_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    assert run_async(bot._inject_prompt(sess, "/init", driver_user_id=BOB))
    notify_hook._match_and_promote(SESSION, "/init")
    notify_hook._match_and_promote(SESSION, "/review the diff")   # typed locally
    t = _write_transcript(tmp_path, _command_record("/review", "the diff"),
                          _meta_body())
    assert _bash_decision(t, "git diff") is None


def test_unmatched_telegram_text_keeps_the_running_commands_named(tmp_path):
    """The all-outstanding fallback (Telegram text no note matched) must
    not drop the commands still queued or running."""
    name = "claude-x__g1"
    ps.snapshot_path(name).parent.mkdir(parents=True, exist_ok=True)
    ps.write_merged_snapshot(name, {"note_bodies": ["/init", "hi"],
                                    "scope_mode": True})
    merged = ps.snapshot_for_prompt(name, "[via Telegram · @bob]\nlost", [], [])
    assert merged["note_bodies"] == ["/init"]
    assert merged["scope_mode"] is True
    assert merged["bypass_safety"] is False
