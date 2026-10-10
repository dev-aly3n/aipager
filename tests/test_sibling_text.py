"""Texts phase 3b owns that phase 3a shows (the needs-admin reply, the
"Auto needs an admin." reason, the retry-others pop-up) are plain English
here and keys once 3b is merged. ``sibling_text.shown`` renders either:
a stand-in Msg (a key with fa and ru translations) must come out in the
right language at every call site (pop-ups the tapper's, replies the
chat's), and the constant as it is today must come out as ``shown`` says
(its English while it is a str, its translation once it is a Msg)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from aipager import i18n, preferences
from aipager.bot import callbacks, notify, session_ops
from aipager.bot.sibling_text import shown
from aipager.bot.transport import PROMPT_REFUSED
from aipager.i18n import Msg, Safe
from tests import i18n_surfaces_p3a as p3a
from tests.i18n_surfaces import Env

CYRILLIC = re.compile("[Ѐ-ӿ]")
PERSIAN = re.compile("[؀-ۿ]")
GROUP, BOB = p3a.GROUP, p3a.BOB
RLM = "\N{RIGHT-TO-LEFT MARK}"
#: A Msg with fa and ru translations, standing in for 3b's keys.
STANDIN = callbacks.KILL_CANCELLED


def test_a_msg_is_rendered_and_a_str_passes_through():
    fa = i18n.get("fa")
    assert shown(fa, STANDIN) == fa.text(STANDIN) != str(STANDIN)
    html = shown(fa, STANDIN, "html")
    assert isinstance(html, Safe) and RLM + html == fa.html(STANDIN)
    assert shown(i18n.get("ru"), STANDIN, "html") == i18n.get("ru").html(STANDIN)
    plain = "That command needs an admin."
    assert shown(fa, plain) is plain and shown(fa, plain, "html") is plain
    assert isinstance(STANDIN, Msg)


def _as_today(tr, constant):
    """What a constant reads as for *tr*'s reader today: its English while
    it is a str (before 3b's merge), its translation once it is a Msg."""
    return tr.text(constant) if isinstance(constant, Msg) else constant


@pytest.fixture
def env(mk_bot, run_async, monkeypatch):
    """A Persian group; bob's own chat is Russian."""
    e = Env(mk_bot, run_async, monkeypatch, "fa")
    preferences.set_ui_language(BOB, "ru")
    return e


def _toasts(q):
    return [str(c.args[0] if c.args else c.kwargs.get("text"))
            for c in q.answer.await_args_list]


def _refused_world(env, needs_admin):
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP, name="x1")
    x1.last_prompt = "/x"
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    bot._command_needs_admin = lambda *a, **k: needs_admin
    return bot, tg, x1


@pytest.mark.parametrize("verb,needs_admin,name", [
    ("retry", True, "NEEDS_ADMIN_REPLY"),
    ("retry", False, "RETRY_OTHERS_COMMAND_REPLY"),
    ("compact", True, "NEEDS_ADMIN_REPLY"),
])
def test_a_refused_tap_pops_up_in_the_tappers_language(env, verb, needs_admin, name):
    today = _as_today(i18n.get("ru"), getattr(callbacks, name))
    bot, tg, x1 = _refused_world(env, needs_admin)
    q = p3a.tap(env, bot, tg, p3a._sess_cb(x1, verb), chat=GROUP, user=BOB)
    assert _toasts(q) == [today]
    env.mp.setattr(callbacks, name, STANDIN)
    q = p3a.tap(env, bot, tg, p3a._sess_cb(x1, verb), chat=GROUP, user=BOB)
    assert _toasts(q) == [i18n.get("ru").text(STANDIN)]


def test_the_needs_admin_reply_is_in_the_chats_language(env):
    fa = i18n.get("fa")
    today = _as_today(fa, session_ops.NEEDS_ADMIN_REPLY)
    bot, tg = p3a.world(env)
    update = p3a.msg(tg, "/x", chat=GROUP, user=BOB)
    env.run(bot._reply_needs_admin(update))
    env.mp.setattr(session_ops, "NEEDS_ADMIN_REPLY", STANDIN)
    env.run(bot._reply_needs_admin(update))
    assert tg.out == [today, fa.text(STANDIN)]


def test_a_held_commands_needs_admin_reply_is_in_the_chats_language(env):
    fa = i18n.get("fa")
    today = _as_today(fa, notify.NEEDS_ADMIN_REPLY)
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    env.run(bot._reply_needs_admin_queued(x1, 900))
    env.mp.setattr(notify, "NEEDS_ADMIN_REPLY", STANDIN)
    env.run(bot._reply_needs_admin_queued(x1, 900))
    assert tg.out == [today, fa.text(STANDIN)]


def test_resumed_in_ask_gives_the_reason_in_the_chats_language(env):
    from types import SimpleNamespace

    from aipager.state import Status
    bot, tg = p3a._sess_world(env)
    bot._build_session_dashboard = lambda sess, *a, **k: "📊"
    bot._is_admin_user = lambda *a, **k: False
    x1 = p3a.session(bot, "x1", chat=GROUP, status=Status.GONE)
    x1.claude_session_id = "abc"
    x1.skip_perms = True
    x1.transcript_path = ""
    bot._do_resume_core = AsyncMock(return_value=SimpleNamespace(ok=True, reason="resumed",
                                                                 err=""))
    fa = i18n.get("fa")

    def header(reason):
        return fa.html(session_ops.RESUMED_IN_ASK, session="x1", reason=reason)
    today = session_ops.RESUME_AUTO_NEEDS_ADMIN
    if isinstance(today, Msg):
        today = Safe(fa.html(today).removeprefix(RLM))
    p3a.cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=GROUP, user=BOB)
    assert tg.out[-1].startswith(header(today)), tg.out[-1]
    env.mp.setattr(session_ops, "RESUME_AUTO_NEEDS_ADMIN", STANDIN)
    p3a.cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=GROUP, user=BOB)
    assert tg.out[-1].startswith(header(Safe(fa.html(STANDIN).removeprefix(RLM)))), tg.out[-1]
    assert PERSIAN.search(tg.out[-1]) and not CYRILLIC.search(tg.out[-1])
    assert RLM * 2 not in tg.out[-1]
