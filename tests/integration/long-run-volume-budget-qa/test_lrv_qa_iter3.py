"""Iteration-3 black-box rows for 8.30.

1. **A ban lands while ESSENTIAL calls are queued** (R7: zero requests into
   a ban). The ban can arrive on any call: the typing bubble, a card edit,
   an answer, the pinned dashboard, or a mute armed from anywhere
   (``MUTE.mute``). Calls already queued in the limiter at that moment must
   never reach the wire once the mute is armed. PTB callers get
   ``FloodMuted`` and an answer is held.
2. **The pinned dashboard**: a session status change, or a session
   appearing or going, refreshes it at once. Hook-only changes (the header
   flipping between the sessions that notify) wait the idle interval,
   or the busy interval while a session is BUSY.
3. **Two sessions stream for 2 h with the dashboard live**: the bubble
   never goes dark, the chat never enters minimal mode, and every rolling
   hour stays within 1080 ornament calls.

Everything runs on the harness's virtual loop. PTB calls go through the
real limiter (a ``_GatedBot`` subclass that also records message ids).
Rich calls go through the REAL ``rich_message._post`` over an
``httpx.MockTransport``. Nothing in ``asyncio`` is patched.

The pinned dashboard exists only on a legacy single-chat install, so these
rows set ``aipager.bot.dashboard.CHAT_ID``. That is configuration, not a
mock of the code under test.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys

import httpx
import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import HELD
from aipager.state import Status

_h = sys.modules["_lrv_qa_harness"]

CHAT = 256113222
PINNED = 9999
BAN = 25429
MIN = 60.0
HOUR = 3600.0
EPS = 1e-3
SLOW = config.TYPING_AGE_TIER2_INTERVAL                       # 15
SHARE = config.FLOOD_HOURLY_MAX - config.FLOOD_HOURLY_ESSENTIAL_RESERVE  # 1080
IDLE_GAP = config.PINNED_REFRESH_INTERVAL                    # 60
BUSY_GAP = config.PINNED_REFRESH_BUSY_INTERVAL               # 600
ANSWER = "queued answer the ban must not eat"


# ── transports ───────────────────────────────────────────────────────────────

class _Bot(_h._GatedBot):
    """The harness's gated PTB double, plus:

    * ``log``: ``(method, chat, message_id, t)`` for every call that REACHED
      the wire (its callback ran);
    * ``slow_ban``: ``endpoint -> seconds``. The first such call is
      admitted, stays in flight for *seconds*, and is then answered with
      vm3's 25,429 s ban. ``ban_at`` records when that answer came back.
    """

    def __init__(self, limiter, clock):
        super().__init__(limiter, clock)
        self.log: list[tuple] = []
        self.slow_ban: dict[str, float] = {}
        self.ban_at: float | None = None

    def __getattr__(self, name):
        if name not in self._ENDPOINTS:
            raise AttributeError(name)
        endpoint, position = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]
            msg = kwargs.get("message_id")
            if msg is None and endpoint == "editMessageText" and len(args) > 2:
                msg = args[2]

            async def _call():
                t = self._clock()
                self.calls.append((endpoint, chat_id, t))
                self.log.append((endpoint, chat_id, msg, t))
                delay = self.slow_ban.pop(endpoint, None)
                if delay is not None:
                    await asyncio.sleep(delay)
                    self.ban_at = self._clock()
                    raise RetryAfter(BAN)
                return _h._Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args)

        return _method

    def dashboard(self, chat=CHAT) -> list[float]:
        return [t for e, c, m, t in self.log
                if e == "editMessageText" and c == chat and m == PINNED]


class _Http:
    """The rich path's wire. ``slow_ban``: ``endpoint -> seconds``, as on
    :class:`_Bot`, but answered as Telegram's HTTP 429 body."""

    def __init__(self, clock):
        self.clock = clock
        self.requests: list[tuple] = []          # (endpoint, chat, t, payload)
        self.slow_ban: dict[str, float] = {}
        self.ban_at: float | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.requests.append((endpoint, payload.get("chat_id"),
                              self.clock(), payload))
        delay = self.slow_ban.pop(endpoint, None)
        if delay is not None:
            await asyncio.sleep(delay)
            self.ban_at = self.clock()
            return httpx.Response(200, json={
                "ok": False, "error_code": 429,
                "description": f"Too Many Requests: retry after {BAN}",
                "parameters": {"retry_after": BAN}})
        return httpx.Response(200, json={
            "ok": True, "result": {"message_id": payload.get("message_id", 4242)}})


@pytest.fixture
def http(vloop, monkeypatch):
    rec = _Http(vloop.time)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(rm, "_post", _h._REAL_POST)
    monkeypatch.setattr(rm, "_client", httpx.AsyncClient(
        transport=httpx.MockTransport(rec.handler)))
    yield rec
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def pbot(vloop, vlimiter):
    return _Bot(vlimiter, vloop.time)


@pytest.fixture
def dash_on(monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))


def _bot(mk_bot, pbot):
    bot = mk_bot()
    bot._app.bot = pbot
    bot.registry.pinned_msg_id = PINNED
    return bot


def _session(bot, vloop, label, *, age=0.0, msg_id=71, status=Status.BUSY):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = 0.0
    return sess


def _wire(pbot, http, chat=CHAT) -> list[float]:
    out = [t for _e, c, _m, t in pbot.log if c == chat]
    out += [t for _e, c, t, _p in http.requests if c == chat]
    return sorted(out)


def _tool(n):
    return {"tool_summary": f"Bash: step {n}", "tool_name": "Bash"}


# ── 1. a ban lands while ESSENTIAL calls are queued ──────────────────────────

CARRIERS = ["typing", "card_edit", "answer", "dashboard", "mute_armed"]


def _queued_ban(mk_bot, pbot, http, vloop, carrier, *, lift=False):
    """A DM with two BUSY sessions. The chosen *carrier* goes out and stays
    in flight for a second. While it is in flight, four ESSENTIAL PTB
    ``sendMessage`` calls and session b's answer (the real ``notify`` over
    the real rich ``_post``) are started, and most of them queue in the
    limiter. Then Telegram answers the carrier with a 25,429 s ban (or,
    for ``mute_armed``, ``MUTE.mute`` is called directly)."""
    bot = _bot(mk_bot, pbot)
    a = _session(bot, vloop, "a", msg_id=71)
    b = _session(bot, vloop, "b", msg_id=72)
    out: dict = {"refused": 0, "sent": 0, "other": []}

    async def _message(i):
        try:
            await pbot.send_message(chat_id=CHAT, text=f"essential {i}")
            out["sent"] += 1
        except FloodMuted:
            out["refused"] += 1
        except Exception as exc:                 # noqa: BLE001
            out["other"].append(type(exc).__name__)

    async def _answer():
        b.status = Status.IDLE
        bot._stop_animation(b)
        await bot.notify(b, "idle_prompt", {"summary": ANSWER})

    async def _carrier():
        if carrier == "typing":
            pbot.slow_ban["sendChatAction"] = 1.0
            return asyncio.ensure_future(bot._animate_typing(a))
        if carrier == "card_edit":
            http.slow_ban["editMessageText"] = 1.0
            return asyncio.ensure_future(bot._animate_busy(a))
        if carrier == "answer":
            http.slow_ban["sendRichMessage"] = 1.0

            async def _a_answers():
                a.status = Status.IDLE
                await bot.notify(a, "idle_prompt", {"summary": "a's answer"})
            return asyncio.ensure_future(_a_answers())
        if carrier == "dashboard":
            pbot.slow_ban["editMessageText"] = 1.0
            return asyncio.ensure_future(bot.notify(a, "tool_use", _tool(1)))
        # mute_armed: some other call site learnt of a ban.
        async def _arm():
            await asyncio.sleep(1.0)
            MUTE.mute(CHAT, BAN)
            out["mute_at"] = vloop.time()
        return asyncio.ensure_future(_arm())

    def _in_flight():
        if carrier == "mute_armed":
            return True
        return not pbot.slow_ban and not http.slow_ban

    async def main():
        start = vloop.time()
        task = await _carrier()
        # Queue the moment the carrier is on the wire (a card's first edit
        # may come a second or two after its loop starts).
        waited = 0.0
        while not _in_flight() and waited < 30.0:
            await asyncio.sleep(0.02)
            waited += 0.02
        out["carrier_on_wire"] = _in_flight() and bool(_wire(pbot, http))
        queued = [asyncio.ensure_future(_message(i)) for i in range(4)]
        queued.append(asyncio.ensure_future(_answer()))
        await asyncio.sleep(0.4)
        out["pending"] = sum(not q.done() for q in queued)
        await asyncio.sleep(1.0)                 # the ban answer is back
        ban_at = out.get("mute_at") or pbot.ban_at or http.ban_at
        out["ban_at"] = ban_at
        out["muted"] = MUTE.is_muted(CHAT)
        await asyncio.gather(*queued, return_exceptions=True)
        await asyncio.sleep(120.0)               # far past any floor-rate wait
        a.status = Status.IDLE
        bot._stop_animation(a)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        out["held"] = HELD.count(CHAT)
        out["into_ban"] = [t for t in _wire(pbot, http)
                           if ban_at is not None and t > ban_at]
        out["start"] = start
        if lift:
            await asyncio.sleep(BAN + 5.0 - (vloop.time() - ban_at))
            await bot.notify(b, "held_answer_flush", {})
            await asyncio.sleep(1.0)
            out["answers_after"] = [
                p for (e, c, _t, p) in http.requests
                if c == CHAT and ANSWER in json.dumps(p)]

    vloop.run_until_complete(main())
    return out


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_ban_carrier_really_went_out_before_the_queue(
    mk_bot, pbot, http, vloop, vlimiter, carrier,
):
    """Setup check: something was on the wire before the queue started
    (for ``mute_armed``, which sends nothing, this is vacuous and
    skipped)."""
    if carrier == "mute_armed":
        pytest.skip("no carrier call for a directly armed mute")
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert out["carrier_on_wire"] is True


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_calls_were_really_queued_when_the_ban_landed(
    mk_bot, pbot, http, vloop, vlimiter, carrier,
):
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert out["pending"] >= 2, out["pending"]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_ban_arms_the_mute(mk_bot, pbot, http, vloop, vlimiter, carrier):
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert out["muted"] is True


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_zero_wire_calls_after_the_mute(
    mk_bot, pbot, http, vloop, vlimiter, carrier,
):
    """The row the operator asked for: whatever the ban arrived on, no
    queued call (PTB or rich, essential or not) reaches Telegram after
    it."""
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert out["into_ban"] == [], [t - out["ban_at"] for t in out["into_ban"]]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_queued_ptb_essentials_are_refused_with_flood_muted(
    mk_bot, pbot, http, vloop, vlimiter, carrier,
):
    """Every PTB ``sendMessage`` that was still queued ends in
    ``FloodMuted``: none is sent and none fails some other way."""
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert (out["sent"] + out["refused"] == 4 and out["refused"] >= 1
            and out["other"] == []), out


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_queued_answer_is_held(mk_bot, pbot, http, vloop, vlimiter,
                                     carrier):
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier)
    assert out["held"] >= 1


@pytest.mark.parametrize("carrier", ["card_edit", "dashboard"])
def test_q_the_queued_answer_is_delivered_once_after_the_lift(
    mk_bot, pbot, http, vloop, vlimiter, carrier,
):
    out = _queued_ban(mk_bot, pbot, http, vloop, carrier, lift=True)
    assert len(out["answers_after"]) == 1, len(out["answers_after"])


def test_q_a_mute_before_any_queue_refuses_an_essential_at_the_gate(
    vloop, vlimiter, pbot,
):
    """Boundary: the classic case (the mute is armed BEFORE the call). It
    still gets ``FloodMuted`` and makes no wire call."""
    MUTE.mute(CHAT, BAN)

    async def _go():
        with pytest.raises(FloodMuted):
            await pbot.send_message(chat_id=CHAT, text="late")
    vloop.run_until_complete(_go())
    assert pbot.log == []


def test_q_a_ban_in_one_chat_does_not_refuse_anothers_queue(
    vloop, vlimiter, pbot,
):
    """Error guessing: the take-time mute check must be per chat. Calls
    queued for another chat are still sent when this one is muted."""
    other = 111222333
    sent: list = []

    async def _one():
        await pbot.send_message(chat_id=other, text="x")
        sent.append(1)

    async def _go():
        tasks = [asyncio.ensure_future(_one()) for _ in range(5)]
        await asyncio.sleep(0.5)
        MUTE.mute(CHAT, BAN)
        await asyncio.gather(*tasks, return_exceptions=True)
    vloop.run_until_complete(_go())
    assert len(sent) == 5


# ── 2. the pinned dashboard: state at once, hooks wait ───────────────────────

def _dash(mk_bot, pbot, vloop, *, status=Status.BUSY):
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, vloop, "s0", msg_id=71, status=status)
    s1 = _session(bot, vloop, "s1", msg_id=72, status=status)
    return bot, s0, s1


def _steps(vloop, steps):
    """Run ``(delay, coroutine factory)`` steps; return each step's start
    time."""
    marks: list[float] = []

    async def main():
        for delay, step in steps:
            await asyncio.sleep(delay)
            marks.append(vloop.time())
            await step()
    vloop.run_until_complete(main())
    return marks


def _flips(bot, s0, s1, n, every):
    """*n* ``tool_use`` hooks alternating s0/s1, *every* seconds apart."""
    return [(every, (lambda s=(s0, s1)[i % 2], i=i: bot.notify(s, "tool_use",
                                                               _tool(i))))
            for i in range(n)]


def test_d_the_first_hook_shows_the_dashboard(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Setup check: the dashboard is live in these rows."""
    bot, s0, _s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))])
    assert len(pbot.dashboard()) == 1


def test_d_busy_header_flips_do_not_refresh_inside_the_busy_interval(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Both sessions BUSY. After the first refresh, 118 hooks alternate
    s0/s1 every 5 s (590 s). Only the header flips, so nothing goes
    out."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
           + _flips(bot, s0, s1, 118, 5.0))
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def test_d_a_busy_header_flip_after_the_busy_interval_refreshes(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0))),
                   (BUSY_GAP + 1.0, lambda: bot.notify(s1, "tool_use",
                                                       _tool(1)))])
    assert len(pbot.dashboard()) == 2, pbot.dashboard()


def test_d_busy_refreshes_are_never_closer_than_the_busy_interval(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """30 min of alternating hooks every 3 s with both BUSY: every gap
    between dashboard refreshes is at least 600 s."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, _flips(bot, s0, s1, 600, 3.0))
    stamps = pbot.dashboard()
    gaps = [y - x for x, y in zip(stamps, stamps[1:])]
    assert len(stamps) >= 2 and min(gaps) >= BUSY_GAP - EPS, gaps


def test_d_idle_header_flips_wait_the_idle_interval(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """No session BUSY. Hooks alternate every 5 s for 55 s after the first
    refresh; no refresh goes out."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop, status=Status.INTERACTIVE)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
           + _flips(bot, s0, s1, 11, 5.0))
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def test_d_an_idle_header_flip_after_the_idle_interval_refreshes(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    bot, s0, s1 = _dash(mk_bot, pbot, vloop, status=Status.INTERACTIVE)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0))),
                   (IDLE_GAP + 1.0, lambda: bot.notify(s1, "tool_use",
                                                       _tool(1)))])
    assert len(pbot.dashboard()) == 2, pbot.dashboard()


def test_d_phantom_subagent_stops_do_not_refresh(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Error guessing: phantom SubagentStops (empty type, unknown id)
    arrive constantly. Every second for 5 min, from both BUSY sessions,
    they refresh nothing after the first."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    phantom = {"agent_type": "", "agent_id": "unknown", "duration": 0.0}
    steps = [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
    steps += [(1.0, (lambda s=(s0, s1)[i % 2]: bot.notify(
        s, "subagent_stop", dict(phantom)))) for i in range(300)]
    _steps(vloop, steps)
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def _status_change_then_hook(mk_bot, pbot, vloop, change):
    """Both BUSY. A first refresh at 0; 30 s of flips; at 30 s *change*
    is applied; then the sessions keep notifying, one ``tool_use`` a
    second for 5 s (a skip-kind refresh may lose one slot to the card
    edit its own hook caused). Returns ``(dashboard stamps, time of the
    change)``."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)

    async def _change():
        change(bot, s0, s1, vloop)

    marks = _steps(vloop,
                   [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
                   + _flips(bot, s0, s1, 6, 5.0)
                   + [(0.0, _change)]
                   + [(1.0, (lambda i=i: bot.notify(s0 if i % 2 else s1,
                                                    "tool_use", _tool(90 + i))))
                      for i in range(5)])
    return pbot.dashboard(), marks[-6]


def _s1_idle(bot, s0, s1, vloop):
    s1.status = Status.IDLE
    bot._stop_animation(s1)


def _s1_interactive(bot, s0, s1, vloop):
    s1.status = Status.INTERACTIVE


def _s1_gone(bot, s0, s1, vloop):
    s1.status = Status.GONE


def _s2_arrives(bot, s0, s1, vloop):
    _session(bot, vloop, "s2", msg_id=73, status=Status.IDLE)


def _s1_removed(bot, s0, s1, vloop):
    bot.registry.remove("claude-s1")


@pytest.mark.parametrize("change", [_s1_idle, _s1_interactive, _s1_gone,
                                    _s2_arrives, _s1_removed],
                         ids=["busy-to-idle", "busy-to-interactive",
                              "busy-to-gone", "session-appears",
                              "session-removed"])
def test_d_a_state_change_refreshes_at_once(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on, change,
):
    """Inside the 600 s busy wait, a status change (or a session appearing
    or going) is shown at once: within the 5 s of hooks that follow it,
    not 600 s later."""
    stamps, change_at = _status_change_then_hook(mk_bot, pbot, vloop, change)
    assert [t for t in stamps if change_at <= t <= change_at + 5.0 + EPS], \
        [t - change_at for t in stamps]


def test_d_a_state_change_is_shown_once_not_on_every_later_hook(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """After the state change is shown, the header flips go back to
    waiting: 2 min of further hooks add nothing."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    steps = [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
    steps += [(10.0, lambda: _as_coro(_s1_interactive(bot, s0, s1, vloop)))]
    steps += _flips(bot, s0, s1, 25, 5.0)
    _steps(vloop, steps)
    assert len(pbot.dashboard()) == 2, pbot.dashboard()


async def _noop():
    return None


def _as_coro(_value):
    return _noop()


def test_d_a_status_change_goes_out_even_with_the_hour_near_the_shed(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """The routine refresh yields to the hourly budget; a status change
    does not. The chat's hour stands at 700 ornament calls (between the
    60 % resume and 75 % shed marks)."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)

    async def _fill():
        vlimiter.restore([{"chat_id": CHAT,
                           "hourly": [[vloop.wall() - 60.0, 700, 0]]}])

    marks = _steps(vloop, [
        (0.0, lambda: bot.notify(s0, "tool_use", _tool(0))),
        (1.0, _fill),
        (20.0, lambda: _as_coro(_s1_interactive(bot, s0, s1, vloop)))]
        + [(1.0, (lambda i=i: bot.notify(s0 if i % 2 else s1, "tool_use",
                                         _tool(10 + i)))) for i in range(5)])
    assert [t for t in pbot.dashboard() if marks[2] <= t <= marks[2] + 5.0 + EPS]


# ── 3. two sessions stream for 2 h with the dashboard live ───────────────────

def _two_hour_dash(mk_bot, pbot, vloop, vlimiter, rich_http):
    """Two sessions, both BUSY from turn start, in one DM with the pinned
    dashboard live. Each gets a ``tool_use`` hook every 2 to 8 s (seeded).
    The cards and the chat's one bubble run for two hours. Sampled once a
    minute: ``minimal_mode`` and ``hourly_usage``."""
    rich_http.clock = vloop.time
    bot = _bot(mk_bot, pbot)
    ss = [_session(bot, vloop, f"s{i}", msg_id=71 + i) for i in range(2)]
    samples: list[dict] = []

    async def _hooks(sess):
        rng = random.Random(f"{sess.label}-20260923")
        n = 0
        while True:
            await asyncio.sleep(rng.uniform(2.0, 8.0))
            n += 1
            await bot.notify(sess, "tool_use", _tool(n))

    async def _sampler():
        while True:
            await asyncio.sleep(60.0)
            u = vlimiter.hourly_usage(CHAT)
            samples.append({"minimal": vlimiter.minimal_mode(CHAT), **u})

    async def main():
        for s in ss:
            bot._start_animation(s)
        feeders = [asyncio.ensure_future(_hooks(s)) for s in ss]
        feeders.append(asyncio.ensure_future(_sampler()))
        await asyncio.sleep(2 * HOUR)
        for f in feeders:
            f.cancel()
        await asyncio.gather(*feeders, return_exceptions=True)
        for s in ss:
            s.status = Status.IDLE
            bot._stop_animation(s)
        await asyncio.sleep(0)

    start = vloop.time()
    vloop.run_until_complete(main())
    wire = sorted([t for _e, c, _m, t in pbot.log if c == CHAT]
                  + [t for (_e, c, _p), t in zip(rich_http.requests,
                                                 rich_http.stamps)
                     if c == CHAT])
    return {
        "start": start, "end": start + 2 * HOUR,
        "bubbles": pbot.stamps("sendChatAction"),
        "dashboard": pbot.dashboard(), "wire": wire, "samples": samples,
    }


@pytest.fixture
def two_hours(mk_bot, pbot, vloop, vlimiter, rich_http, dash_on):
    return _two_hour_dash(mk_bot, pbot, vloop, vlimiter, rich_http)


def test_s_the_dashboard_is_live_in_the_run(two_hours):
    """Setup check: the pinned dashboard really is refreshed during the
    run, so the rows below include its traffic."""
    assert len(two_hours["dashboard"]) >= 2, two_hours["dashboard"]


def test_s_the_dashboard_is_refreshed_about_once_per_busy_interval(
    two_hours,
):
    """At most one refresh per 600 s while both sessions are BUSY (12 in
    2 h), plus the first."""
    assert len(two_hours["dashboard"]) <= 2 * HOUR / BUSY_GAP + 1, \
        len(two_hours["dashboard"])


def test_s_the_bubble_never_goes_dark(two_hours):
    """"Flicker, never dark": no stretch without a bubble longer than the
    15 s tier plus a second, the run's ends included."""
    b = [two_hours["start"]] + two_hours["bubbles"] + [two_hours["end"]]
    assert max(y - x for x, y in zip(b, b[1:])) <= SLOW + 1.0 + EPS


def test_s_the_bubble_is_never_shed(two_hours):
    shed = [s["typing_shed"] for s in two_hours["samples"]]
    assert len(shed) >= 100 and not any(shed), shed.count(True)


def test_s_the_chat_never_enters_minimal_mode(two_hours):
    minimal = [s["minimal"] for s in two_hours["samples"]]
    assert len(minimal) >= 100 and not any(minimal), minimal.count(True)


def test_s_every_rolling_hour_on_the_wire_is_within_the_ornament_share(
    two_hours,
):
    """Every call in this run is an ornament (cards, bubbles, dashboard),
    so the wire's own count in every rolling 3600 s is held to 1080."""
    w = two_hours["wire"]
    worst = max(sum(1 for t in w if s <= t < s + HOUR) for s in w)
    assert worst <= SHARE, worst


def test_s_the_limiters_ornament_count_never_passes_the_share(two_hours):
    worst = max(s["ornament_used"] for s in two_hours["samples"])
    assert worst <= SHARE, worst


def test_s_no_essential_overflow_is_logged(two_hours):
    assert max(s["essential_overflow"] for s in two_hours["samples"]) == 0
