"""In a group set to Persian, a member whose own chat is Russian gets the
pop-ups of every phase 3a tap in Russian, while the cards and replies the
group sees stay Persian (multilingual design 6, phase 3a).

One case per toast family (sessions, /new, retry, compact, the mode
switch, send-now, someone else's card, the tap gate). The model-switch
refusal's family is ``tests/test_model_refusal_keys.py``."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from aipager import preferences
from tests import i18n_surfaces_p3a as p3a
from tests.i18n_surfaces import Env

CYRILLIC = re.compile("[Ѐ-ӿ]")
PERSIAN = re.compile("[؀-ۿ]")
GROUP, BOB, OWNER, DM = p3a.GROUP, p3a.BOB, p3a.OWNER, p3a.DM


@pytest.fixture
def env(mk_bot, run_async, monkeypatch):
    """A Persian group (and aly's Persian DM); bob's own chat is Russian."""
    e = Env(mk_bot, run_async, monkeypatch, "fa")
    preferences.set_ui_language(BOB, "ru")
    return e


class Taps:
    """Taps as bob in the group: the pop-ups apart from everything else."""

    def __init__(self, env, bot, tg):
        self.env, self.bot, self.tg = env, bot, tg
        self.toasts: list[str] = []

    def __call__(self, data, **kw):
        kw.setdefault("chat", GROUP)
        kw.setdefault("user", BOB)
        q = p3a.tap(self.env, self.bot, self.tg, data, **kw)
        for call in q.answer.await_args_list:
            text = call.args[0] if call.args else call.kwargs.get("text")
            if text:
                self.toasts.append(str(text))
        return q

    def check(self):
        """Every pop-up Russian; whatever else went out, Persian."""
        assert self.toasts, "no pop-up"
        for toast in self.toasts:
            assert CYRILLIC.search(toast) and not PERSIAN.search(toast), toast
        rest = list(self.tg.out)
        for toast in self.toasts:
            rest.remove(toast)
        for text in rest:
            assert not CYRILLIC.search(text), text


def test_session_taps(env):
    bot, tg = p3a._sess_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    from aipager.state import Status
    gone = p3a.session(bot, "x2", chat=GROUP, status=Status.GONE)
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(gone, "talk"))
    taps(p3a._sess_cb(x1, f"endok{x1.turn_key + 3}"))
    bot._can_prompt_user = lambda *a, **k: False
    for verb in ("talk", "rename", "delete", "restart"):
        taps(p3a._sess_cb(x1, verb))
    taps.check()
    assert len(taps.toasts) >= 6


def test_new_session_taps(env):
    bot, tg = p3a._new_world(env)
    p3a._open_name_card(env, bot, tg, chat=GROUP)          # aly's card: 7001
    taps = Taps(env, bot, tg)
    start = len(tg.out)
    taps("_:nw:cancel", message_id=7001)
    p3a._open_name_card(env, bot, tg, chat=GROUP, user=BOB)
    card = bot._new_wizard_pending[(GROUP, BOB)]["msg_id"]
    taps("_:nw:mode:auto", message_id=card)
    taps("_:nw:model:99", message_id=card)
    taps("_:nw:mode:ask", message_id=12345)
    tg.out[:start] = []
    taps.check()
    assert any(PERSIAN.search(t) for t in tg.out)        # bob's own Name card


def test_the_name_card_uses_the_group_language(env):
    """A card the group shares reads in the group's language, whoever
    opened it."""
    bot, tg = p3a._new_world(env)
    p3a._open_name_card(env, bot, tg, chat=GROUP, user=BOB)
    assert tg.out
    assert all(not CYRILLIC.search(t) for t in tg.out)
    assert PERSIAN.search(tg.out[0])


def test_retry_taps(env):
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP, name="x1")
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(x1, "retry"))
    x1.last_prompt = "x1"
    p3a.alive(env, False)
    taps(p3a._sess_cb(x1, "retry"))
    p3a.alive(env, True)
    bot._inject_prompt = AsyncMock(return_value=False)
    taps(p3a._sess_cb(x1, "retry"))
    taps.check()


def test_compact_taps(env):
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP, name="x1")
    taps = Taps(env, bot, tg)
    taps("claude-zz:compact")
    bot._inject_prompt = AsyncMock(return_value=True)
    taps(p3a._sess_cb(x1, "compact"))
    taps.check()


def test_mode_switch_taps(env):
    bot, tg = p3a._perms_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(x1, f"modefull{x1.turn_key}"))
    x1.relaunch_in_flight = True                       # a switch running: its toast
    bot._perms_pending[x1.name] = {"target_mode": "ask", "msg_id": 42,
                                   "label": "x1", "turn": x1.turn_key}
    taps(p3a._sess_cb(x1, "perms_confirm"))
    x1.relaunch_in_flight = False
    bot._perms_pending.pop(x1.name, None)               # no record: out of date
    taps(p3a._sess_cb(x1, "perms_confirm"))
    taps.check()
    assert len(taps.toasts) == 3


def test_send_now_taps(env):
    from aipager.bot import send_now
    bot, tg = p3a.world(env)
    x1 = p3a._queued(bot, chat=GROUP)
    taps = Taps(env, bot, tg)
    for result in ("taken", "typing", "failed"):
        bot._send_now_core = AsyncMock(return_value=send_now.SendNowOutcome(result, "x1"))
        taps(p3a._sess_cb(x1, "now:900"))
    taps.check()


def test_someone_elses_card(env):
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    card = tg._next + 1
    p3a.cmd(env, bot, tg, "_handle_kill_cmd", "/kill x1", chat=GROUP)
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(x1, f"endok{x1.turn_key}"), message_id=card)
    taps.check()
    assert any(PERSIAN.search(t) for t in tg.out)        # the confirm itself


def test_tap_gate(env):
    from aipager.bot import tap_gate
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    d1 = p3a.session(bot, "x2", chat=DM)
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(d1, "menu"))
    env.mp.setattr(tap_gate, "required_capability", lambda *a, **k: tap_gate.MANAGE)
    bot._member_can = lambda *a, **k: False
    taps(p3a._sess_cb(x1, "menu"))
    taps.check()
    assert len(taps.toasts) == 2


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_whoami_keeps_config_names(mk_bot, run_async, monkeypatch, code):
    """Setting names, tool names and role names are the config's own words:
    shown as they are in every language."""
    from aipager import policy_snapshot
    e = Env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a.world(e)
    monkeypatch.setattr(policy_snapshot, "resolve_snapshot", lambda role, scope, member: {
        "deny_tools": ["Bash", "WebFetch"], "allow_tools": ["Read", "Grep"],
        "bypass_safety": False, "bypass_role_denies": False, "can_manage": True})
    p3a.cmd(e, bot, tg, "_handle_whoami", "/whoami", chat=GROUP, user=BOB)
    text = "\n".join(tg.out)
    for word in ("deny_tools", "allow_tools", "bypass_safety", "Bash", "WebFetch",
                 "Read", "Grep", "user", "bob", "team"):
        assert word in text, word
    script = PERSIAN if code == "fa" else CYRILLIC
    assert script.search(text)


def test_the_member_with_no_choice_reads_the_group(env):
    """With no language of their own, bob's pop-ups follow the group."""
    preferences.set_ui_language(BOB, "")
    bot, tg = p3a._sess_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    bot._can_prompt_user = lambda *a, **k: False
    q = p3a.tap(env, bot, tg, p3a._sess_cb(x1, "rename"), chat=GROUP, user=BOB)
    toast = q.answer.await_args.args[0]
    assert PERSIAN.search(toast)


def test_a_stop_on_a_finished_tasks_card(env):
    """Stop on the card of a task that already finished: the pop-up is the
    tapper's (Russian), the group sees nothing, and nothing is stopped."""
    from aipager import i18n
    from aipager.bot.session_ops import TOAST_STOP_STALE
    from aipager.dtach import inject
    from aipager.state import Status
    bot, tg = p3a.world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP, status=Status.BUSY)
    x1.busy_msg_id = 500
    taps = Taps(env, bot, tg)
    taps(p3a._sess_cb(x1, "stop"), message_id=100)
    taps.check()
    assert taps.toasts == [i18n.get("ru").text(TOAST_STOP_STALE)]
    assert tg.out == taps.toasts
    inject.send_keys.assert_not_awaited()


@pytest.mark.parametrize("data", ["kill-cancel", "_:pick:cancel", "resume_mode_cancel"])
def test_a_cancelled_card_stays_in_the_groups_language(env, data):
    """bob (Russian) cancels a card the group shares: the edited card reads
    in the group's language (Persian), not bob's."""
    from aipager import i18n
    from aipager.bot import callbacks
    bot, tg = p3a._sess_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    msg = {"kill-cancel": callbacks.KILL_CANCELLED, "_:pick:cancel": callbacks.PICK_CANCELLED,
           "resume_mode_cancel": callbacks.CANCELLED}[data]
    q = p3a.tap(env, bot, tg, data if data.startswith("_:") else p3a._sess_cb(x1, data),
                chat=GROUP, user=BOB)
    assert q.edit_message_text.await_count == 1
    assert tg.out == [i18n.get("fa").text(msg)]
    assert PERSIAN.search(tg.out[0]) and not CYRILLIC.search(tg.out[0])
