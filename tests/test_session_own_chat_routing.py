"""Roadmap 8.81: a session's sends, edits and deletes go to its own chat.

With more than one chat in aipager.yaml, ``config.CHAT_ID`` is the first
GROUP scope's id. Every site below used to address ``CHAT_ID``, so on a
DM + group install a DM session's audit lines, diff previews and
auto-deny notices were posted into the team group, its card edits went to
the group (the DM card froze on "Working", a two-question
AskUserQuestion never showed question 2), and Retry/Compact deleted the
group's message with the same id.

Every test runs twice: on a DM + group install (``CHAT_ID`` is the group,
the session lives in the DM) and on a one-DM install (the operator's own,
where nothing may change). In both, every call must address the DM.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession

DM = 111
GROUP = -100222
OWNER = 111

_DM_SCOPE = Scope(chat_id=DM, kind="dm", label="owner DM",
                  members=(Member(id=OWNER, label="owner", role="owner"),))
_GROUP_SCOPE = Scope(chat_id=GROUP, kind="group", label="team",
                     members=(Member(id=OWNER, label="owner", role="owner"),
                              Member(id=333, label="bob", role="user")))


@pytest.fixture(params=["dm+group", "dm-only"])
def install(request, monkeypatch):
    """The scopes of the install, with ``config.CHAT_ID`` as config.py
    computes it for them: the first group's id when there is one."""
    if request.param == "dm+group":
        monkeypatch.setattr("aipager.config.CHAT_ID", str(GROUP))
        return [_DM_SCOPE, _GROUP_SCOPE]
    monkeypatch.setattr("aipager.config.CHAT_ID", str(DM))
    return [_DM_SCOPE]


@pytest.fixture(autouse=True)
def _no_pty(monkeypatch):
    for name in ("send_keys", "is_alive", "discard_queued_input",
                 "send_text_and_enter"):
        monkeypatch.setattr(f"aipager.dtach.inject.{name}",
                            AsyncMock(return_value=True))


def _bot(mk_bot, scopes):
    bot = mk_bot(scopes=scopes)
    for meth in ("send_message", "edit_message_text", "delete_message"):
        setattr(bot._app.bot, meth, AsyncMock(return_value=MagicMock(message_id=9001)))
    bot._start_animation = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def _dm_session(bot, *, busy=None, status=Status.BUSY):
    s = TrackedSession(name="claude-jim__d111", label="jim", status=status)
    s.scope_chat_id, s.scope_kind = DM, "dm"
    s.last_driver_user_id = OWNER
    s.busy_msg_id = busy
    bot.registry._sessions[s.name] = s
    return s


def _tap(bot, sess, verb, *, chat=DM, user=OWNER, message_id=None,
         edit_fails=False):
    from aipager.bot import session_parity
    query = MagicMock()
    query.data = session_parity.session_cb(bot, chat, sess, verb)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock(
        side_effect=RuntimeError("edit failed") if edit_fails else None)
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.chat.id = chat
    query.message.chat_id = chat
    query.message.message_id = message_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat.id = chat
    update.message = None
    return update, query


def _chat_of_send(call):
    return call.kwargs.get("chat_id", call.args[0] if call.args else None)


def _sent_chats(bot):
    return [_chat_of_send(c) for c in bot._app.bot.send_message.await_args_list]


def _edited_chats(bot):
    return [c.kwargs["chat_id"] for c in bot._app.bot.edit_message_text.await_args_list]


def _deleted_chats(bot):
    return [c.kwargs["chat_id"] for c in bot._app.bot.delete_message.await_args_list]


def _same_chat(a, b):
    return str(a) == str(b)


# ── sends ────────────────────────────────────────────────────────────────────

def test_auto_deny_notice_goes_to_the_sessions_chat(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.INTERACTIVE)
    run_async(bot._auto_deny(s, {"name": "Write", "summary": "Write ~/secret/plan.md"}, None))
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


def test_diff_preview_goes_to_the_sessions_chat(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot)
    run_async(bot._send_diff_preview(
        s, "Write", {"file_path": "/home/op/.env", "content": "API_KEY=sk-1\n"}))
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


def test_allow_audit_line_and_card_edit_stay_in_the_dm(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=500, status=Status.INTERACTIVE)
    s.pending_permission = {"tool_summary": "Bash: cat ~/private/notes.txt",
                            "tool_info": {"name": "Bash"}, "wait_started_at": 0}
    update, _q = _tap(bot, s, "allow", message_id=500)
    run_async(bot._handle_callback(update, MagicMock()))
    sends = bot._app.bot.send_message.await_args_list
    assert any("Allowed" in str(c) for c in sends), sends
    assert all(_same_chat(_chat_of_send(c), DM) for c in sends), sends
    assert _edited_chats(bot) and all(_same_chat(c, DM) for c in _edited_chats(bot))


def _two_questions(s, *, multi=False):
    qs = [{"question": "Which DB?", "multiSelect": multi,
           "options": [{"label": "pg"}, {"label": "sqlite"}]},
          {"question": "Drop old tables?",
           "options": [{"label": "yes"}, {"label": "no"}]}]
    s.pending_permission = {"ask_question": True, "question": qs[0]["question"],
                            "options": qs[0]["options"], "questions": qs,
                            "current_idx": 0, "multi_select": multi,
                            "cursor_pos": 0, "selected": set(),
                            "tool_info": {"name": "AskUserQuestion"},
                            "wait_started_at": 0}


def test_two_question_card_advances_in_the_dm(install, mk_bot, run_async):
    """Question 2's buttons replace question 1's on the DM card, so a
    second tap on question 1 cannot answer question 2 blind."""
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=700, status=Status.INTERACTIVE)
    _two_questions(s)
    update, _q = _tap(bot, s, "opt0", message_id=700)
    run_async(bot._handle_callback(update, MagicMock()))
    assert s.pending_permission["question"] == "Drop old tables?"
    edits = bot._app.bot.edit_message_text.await_args_list
    assert edits, "the card was never edited"
    assert all(_same_chat(c.kwargs["chat_id"], DM) and c.kwargs["message_id"] == 700
               for c in edits), edits
    assert "Drop old tables" in edits[-1].args[0]
    assert all(_same_chat(c, DM) for c in _sent_chats(bot))


def test_last_question_answer_restores_working_card_in_the_dm(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=700, status=Status.INTERACTIVE)
    _two_questions(s)
    s.pending_permission["current_idx"] = 1
    update, _q = _tap(bot, s, "opt0", message_id=700)
    run_async(bot._handle_callback(update, MagicMock()))
    assert s.pending_permission is None
    edits = _edited_chats(bot)
    assert edits and all(_same_chat(c, DM) for c in edits), edits


def test_multi_select_toggle_submit_and_advance_stay_in_the_dm(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=700, status=Status.INTERACTIVE)
    _two_questions(s, multi=True)
    update, _q = _tap(bot, s, "opt1", message_id=700)
    run_async(bot._handle_callback(update, MagicMock()))
    toggled = _edited_chats(bot)
    assert toggled and all(_same_chat(c, DM) for c in toggled), toggled
    bot._app.bot.edit_message_text.reset_mock()

    update, _q = _tap(bot, s, "submit", message_id=700)
    run_async(bot._handle_callback(update, MagicMock()))
    assert s.pending_permission["question"] == "Drop old tables?"
    sends = bot._app.bot.send_message.await_args_list
    assert any("Answered" in str(c) for c in sends), sends
    assert all(_same_chat(_chat_of_send(c), DM) for c in sends), sends
    advanced = _edited_chats(bot)
    assert advanced and all(_same_chat(c, DM) for c in advanced), advanced


def test_multi_select_last_submit_restores_working_card_in_the_dm(
        install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=700, status=Status.INTERACTIVE)
    _two_questions(s, multi=True)
    s.pending_permission["current_idx"] = 1
    s.pending_permission["multi_select"] = True
    update, _q = _tap(bot, s, "submit", message_id=700)
    run_async(bot._handle_callback(update, MagicMock()))
    assert s.pending_permission is None
    edits = _edited_chats(bot)
    assert edits and all(_same_chat(c, DM) for c in edits), edits


# ── the card's last word ─────────────────────────────────────────────────────

def test_stop_settles_the_dm_card(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, busy=600, status=Status.BUSY)
    out = run_async(bot._stop_session_core(s))
    assert out.ok
    edits = bot._app.bot.edit_message_text.await_args_list
    assert any("Stopped" in c.args[0] for c in edits), edits
    assert all(_same_chat(c.kwargs["chat_id"], DM) and c.kwargs["message_id"] == 600
               for c in edits), edits
    assert s.busy_msg_id is None


def test_settle_card_text_takes_no_chat_and_edits_the_sessions(install, mk_bot, run_async):
    import inspect
    bot = _bot(mk_bot, install)
    assert "chat_id" not in inspect.signature(bot._settle_card_text).parameters
    s = _dm_session(bot, busy=650)
    assert run_async(bot._settle_card_text(s, "done")) is True
    assert [str(c) for c in _edited_chats(bot)] == [str(DM)]


# ── Retry / Compact ──────────────────────────────────────────────────────────

def test_retry_deletes_in_the_tapped_chat_and_notes_it(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.IDLE)
    s.last_prompt, s.trigger_msg_id = "deploy it", 42
    s.last_prompt_driver_user_id = OWNER
    bot._card_for_injected = AsyncMock()
    bot._inject_prompt = AsyncMock(return_value=True)
    update, _q = _tap(bot, s, "retry", message_id=800)
    run_async(bot._handle_callback(update, MagicMock()))
    assert bot._inject_prompt.await_args.kwargs["chat_id"] == DM
    calls = bot._app.bot.delete_message.await_args_list
    assert [(c.kwargs["chat_id"], c.kwargs["message_id"]) for c in calls] == [(DM, 800)]


def test_compact_deletes_in_the_tapped_chat(install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.IDLE)
    bot._inject_prompt = AsyncMock(return_value=True)
    update, _q = _tap(bot, s, "compact", message_id=801)
    run_async(bot._handle_callback(update, MagicMock()))
    calls = bot._app.bot.delete_message.await_args_list
    assert [(c.kwargs["chat_id"], c.kwargs["message_id"]) for c in calls] == [(DM, 801)]


# ── resume / mode-switch fallbacks (the card edit failed) ───────────────────

def _long_tap(name, verb, *, chat=DM, user=OWNER, message_id=900):
    query = MagicMock()
    query.data = f"{name}:{verb}"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("edit failed"))
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.chat.id = chat
    query.message.chat_id = chat
    query.message.message_id = message_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat.id = chat
    update.message = None
    return update, query


def _replying_resume(bot):
    async def _do_resume(*, reply_fn, **_kw):
        await reply_fn("resumed")
    bot._do_resume = AsyncMock(side_effect=_do_resume)


def test_resume_mode_picker_fallback_replies_in_the_tapped_chat(
        install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.GONE)
    _replying_resume(bot)
    update, _q = _long_tap(s.name, "resume_mode_ask")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


def test_new_conflict_resume_fallback_replies_in_the_tapped_chat(
        install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.GONE)
    _replying_resume(bot)
    update, _q = _long_tap(s.name, "new_resume")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


def test_mode_switch_fallback_message_goes_to_the_tapped_chat(
        install, mk_bot, run_async):
    bot = _bot(mk_bot, install)
    s = _dm_session(bot, status=Status.IDLE)
    bot._perms_pending[s.name] = {"msg_id": 900, "target_skip_perms": True,
                                  "label": "jim"}

    async def _switch(sess, target, edit, **_kw):
        await edit("switching")
    bot._do_perms_switch_via_fn = AsyncMock(side_effect=_switch)
    update, _q = _long_tap(s.name, "perms_confirm")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_perms_switch_via_fn.assert_awaited_once()
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


# ── no CHAT_ID for session traffic (static) ─────────────────────────────────

_BOT_DIR = Path(__file__).resolve().parent.parent / "aipager" / "bot"

# Every CHAT_ID name a module may still load, as (function, why). Anything
# else is a session send, edit or delete aimed at the install's default chat.
_ALLOWED_CHAT_ID_USES = {
    "callbacks.py": set(),
    "animation.py": set(),
    # personal mode's operator check: the operator IS the one DM's peer.
    "auth.py": {"_is_personal_mode_operator"},
    # the pinned bar: a legacy personal install's one chat, and the same
    # unstamped-session fallback as resolve_chat_id.
    "dashboard.py": {"_pinned_chats", "_pinned_chat_of"},
}


def _chat_id_uses(tree):
    """(enclosing function or '<module>', kind) for every CHAT_ID import
    and every load of the name ``CHAT_ID`` or attribute ``.CHAT_ID``."""
    found = []

    def visit(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.ImportFrom) and any(
                a.name == "CHAT_ID" for a in node.names):
            found.append((func, "import"))
        elif isinstance(node, ast.Name) and node.id == "CHAT_ID":
            found.append((func, "name"))
        elif isinstance(node, ast.Attribute) and node.attr == "CHAT_ID":
            found.append((func, "attr"))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, "<module>")
    return found


@pytest.mark.parametrize("module", sorted(_ALLOWED_CHAT_ID_USES))
def test_no_chat_id_for_session_traffic(module):
    tree = ast.parse((_BOT_DIR / module).read_text())
    allowed = _ALLOWED_CHAT_ID_USES[module]
    uses = _chat_id_uses(tree)
    stray = [(f, k) for f, k in uses
             if f not in allowed and not (f == "<module>" and k == "import" and allowed)]
    assert not stray, f"{module} uses CHAT_ID outside {sorted(allowed)}: {stray}"
    if not allowed:
        assert not uses, f"{module} must not import CHAT_ID: {uses}"


def test_the_static_sweep_catches_an_import_and_a_use():
    """The sweep itself fires on the shapes it exists to forbid."""
    src = ("from aipager.config import (\n    CHAT_ID,\n)\n"
           "async def f(bot):\n    await bot.send_message(CHAT_ID, 'x')\n"
           "def g():\n    from aipager import config\n    return config.CHAT_ID\n")
    uses = _chat_id_uses(ast.parse(src))
    assert ("<module>", "import") in uses
    assert ("f", "name") in uses
    assert ("g", "attr") in uses


@pytest.mark.parametrize("verb", ["resume_mode_ask", "new_resume", "perms_confirm"])
def test_fallback_with_no_tapped_chat_uses_the_sessions_chat(
        verb, mk_bot, run_async, monkeypatch):
    """When the tap's chat cannot be read, the fallback message goes to
    the session's own chat, never to the install's default (a group)."""
    monkeypatch.setattr("aipager.config.CHAT_ID", str(GROUP))
    bot = _bot(mk_bot, None)   # personal mode: an unknown chat may tap
    s = _dm_session(bot, status=Status.GONE if verb != "perms_confirm" else Status.IDLE)
    _replying_resume(bot)
    bot._perms_pending[s.name] = {"msg_id": 900, "target_skip_perms": True,
                                  "label": "jim"}

    async def _switch(sess, target, edit, **_kw):
        await edit("switching")
    bot._do_perms_switch_via_fn = AsyncMock(side_effect=_switch)
    update, query = _long_tap(s.name, verb)
    update.effective_chat = None
    query.message.chat = None
    query.message.chat_id = None
    run_async(bot._handle_callback(update, MagicMock()))
    chats = _sent_chats(bot)
    assert chats and all(_same_chat(c, DM) for c in chats), chats


@pytest.mark.parametrize("verb", ["resume_mode_ask", "new_resume"])
def test_fallback_with_no_chat_at_all_sends_nothing_and_does_not_raise(
        verb, mk_bot, run_async, monkeypatch):
    """No tapped chat and no session chat (the picker's session is not
    tracked, or is unstamped on an install with no default chat): the
    fallback sends nothing rather than calling Telegram with no chat."""
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    bot = _bot(mk_bot, None)
    name = "claude-ghost"
    if verb == "new_resume":
        s = TrackedSession(name=name, label="ghost", status=Status.GONE)
        bot.registry._sessions[name] = s        # unstamped: no chat
    _replying_resume(bot)
    update, query = _long_tap(name, verb)
    update.effective_chat = None
    query.message.chat = None
    query.message.chat_id = None
    try:
        run_async(bot._handle_callback(update, MagicMock()))
    except Exception as exc:          # the guard's whole job
        pytest.fail(f"the fallback raised {exc!r}")
    bot._do_resume.assert_awaited_once()
    bot._app.bot.send_message.assert_not_awaited()
