"""The outbound gate: rows A, B and C, plus the two rows that keep it from
being a gate on the daemon itself (rules R1, R8; 8.26).

What these prove that no existing test could. Until 0.7.12 the mute lived
in 22 per-site checks across five files, and ``grep -c MUTE
aipager/bot/flood_budget.py`` was 0 — the chokepoint every Bot API call
already passes through did not know bans existed. One forgotten site
(``animation.send_busy``, ``animation.py:1333``) put a user prompt's busy
card into an active ban on 2026-09-15 and the escalation that followed
cost 9.5 hours. Enforcement is now inside
``BudgetRateLimiter.process_request``, so a NEW call site inherits it
instead of needing to remember.

Every "no Bot API call" claim here runs on ``_GatedBot`` (see
``conftest.py``): ``mk_bot``'s ``MagicMock`` bot enforces nothing at all,
so it can only ever prove that a PER-SITE check fired, never that the gate
did.
"""

from __future__ import annotations

import pytest

from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.state import Status, TrackedSession

MUTED_CHAT = 256113222        # the chat `_pin_single_chat_config` pins
OTHER_CHAT = -1009999999
BAN = 34212.0                 # the real 2026-09-15 retry_after


def _busy(bot, label="dev", chat_id=MUTED_CHAT):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.scope_kind = "dm" if (chat_id or 1) > 0 else "group"
    sess.scope_chat_id = chat_id
    sess.trigger_msg_id = 11
    return sess


# ── row A: the card that started the incident ────────────────────────────────

def test_send_busy_makes_no_call_returns_none_and_raises_nothing_while_muted(
    mk_bot, gated_bot, flood_clock, run_async,
):
    """Row A. ``send_busy`` had no mute guard of any kind — the traceback
    at 18:10:21 on 2026-09-15 runs user prompt -> ``send_busy`` ->
    ``self._app.bot.send_message`` -> ``process_request`` -> HTTP ->
    ``RetryAfter: Retry in 33147 seconds``.

    Three assertions, and only these three (D-4): no HTTP, the caller
    survives, and the card is simply absent. No new ``TrackedSession``
    field, no pending flag, no retry loop — when the mute lifts the next
    tick creates the card normally.

    Mutation: delete the gate from ``process_request`` and ``calls`` grows
    a ``sendMessage`` into the ban.
    """
    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = _busy(bot)
    MUTE.mute(MUTED_CHAT, BAN)

    assert run_async(bot.send_busy(sess)) is None
    assert gated_bot.calls == [], "a busy card went into an active ban"


def test_send_busy_still_sends_for_a_chat_that_is_not_muted(
    mk_bot, gated_bot, flood_clock, run_async,
):
    """The other half of row A: the gate is per CHAT, not global. Without
    this, "no calls" would also pass if the gate refused everything."""
    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = _busy(bot, "other", OTHER_CHAT)
    MUTE.mute(MUTED_CHAT, BAN)

    assert run_async(bot.send_busy(sess)) == 4242
    assert gated_bot.endpoints() == ["sendMessage"]


# ── row C: the endpoints the budget exempts are NOT exempt from the mute ─────

@pytest.mark.parametrize("endpoint", [
    "deleteMessage", "sendDocument", "sendChatAction", "setMessageReaction",
])
def test_the_gate_refuses_every_endpoint_for_a_muted_chat(
    limiter, flood_clock, run_async, endpoint,
):
    """Row C / D-1. ``sendChatAction`` and ``setMessageReaction`` skip the
    per-chat BUDGET (Telegram meters them elsewhere — measured 2026-09-12)
    and that exemption is kept. They do NOT skip the MUTE: a request into
    an active ban extends it whatever endpoint it names, which is what
    drove 1283 -> 312 -> 34212.

    Mutation: move the gate BELOW the ``_CHAT_BUDGET_EXEMPT`` branch and
    the two exempt endpoints here start reaching their callback again.
    """
    MUTE.mute(MUTED_CHAT, BAN)
    ran = []

    async def _callback():
        ran.append(endpoint)

    with pytest.raises(FloodMuted) as ei:
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint=endpoint,
            data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
        ))
    assert ran == [], f"{endpoint} reached Telegram inside a ban"
    assert ei.value.chat_id == MUTED_CHAT
    assert ei.value.retry_after == pytest.approx(BAN, abs=1.0)


def test_a_refused_call_never_bumps_calls_but_is_counted_as_a_refusal(
    limiter, flood_clock, run_async,
):
    """The callback never ran, so the chat's ``calls`` counter must not
    move — it counts callbacks actually executed. ``muted_refusals`` is
    what records the refusal, so an incident can show the gate fired.
    """
    async def _callback():
        return "sent"

    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
    )) == "sent"
    MUTE.mute(MUTED_CHAT, BAN)
    for _ in range(3):
        with pytest.raises(FloodMuted):
            run_async(limiter.process_request(
                callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
            ))
    chat = next(c for c in limiter.snapshot()["chats"]
                if c["chat_id"] == MUTED_CHAT)
    assert chat["calls"] == 1, "a refused call must not count as a call"
    assert chat["muted_refusals"] == 3


def test_a_chat_action_spends_no_chat_token_when_the_chat_is_healthy(
    limiter, flood_clock, run_async,
):
    """The other half of row C, and of R8: keeping the mute gate must not
    cost the endpoints their BUDGET exemption. Mutation: drop
    ``sendChatAction`` from ``_CHAT_BUDGET_EXEMPT`` and the token count
    falls.
    """
    async def _callback():
        return None

    run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="sendChatAction",
        data={"chat_id": OTHER_CHAT}, rate_limit_args=None,
    ))
    after = next(c for c in limiter.snapshot()["chats"]
                 if c["chat_id"] == OTHER_CHAT)
    assert after["tokens"] == pytest.approx(config.TELEGRAM_CHAT_BURST), \
        "an exempt endpoint spent a chat token"
    assert after["chat_actions"] == 1


# ── R1: a mute on one chat must never mute the DAEMON ────────────────────────

@pytest.mark.parametrize("endpoint", ["getMe", "getFile", "setMyCommands"])
def test_a_call_with_no_resolvable_chat_is_never_gated(
    limiter, flood_clock, run_async, endpoint,
):
    """``getMe``/``getFile``/``setMyCommands``/``answerCallbackQuery`` carry
    no ``chat_id``. There is no chat to check, so there is nothing to
    refuse — and muting them would take the daemon down over one chat's
    ban. This is also why ``flood._key(None) == "None"`` never matters:
    the gate short-circuits on ``None`` before any ``flood`` key is built.

    Mutation: drop the ``chat_id is not None`` guard and every one of these
    raises ``FloodMuted`` as soon as ANY chat is muted.
    """
    MUTE.mute(MUTED_CHAT, BAN)
    ran = []

    async def _callback():
        ran.append(endpoint)
        return "ok"

    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint=endpoint,
        data={}, rate_limit_args=None,
    )) == "ok"
    assert ran == [endpoint]


def test_a_non_dict_data_payload_is_never_gated(
    limiter, flood_clock, run_async,
):
    """``data`` that is not a dict resolves to no chat, same contract."""
    MUTE.mute(MUTED_CHAT, BAN)
    ran = []

    async def _callback():
        ran.append("ran")

    run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="getMe",
        data=None, rate_limit_args=None,
    ))
    assert ran == ["ran"]


def test_the_gate_lifts_by_itself_and_sends_resume(
    limiter, flood_clock, run_async,
):
    """The mute is wall-clock and self-clearing: once the deadline passes,
    the very next call goes through with no intervention. Mutation: make
    ``is_muted`` non-destructive and the "lifted" INFO never fires."""
    MUTE.mute(MUTED_CHAT, 600.0)
    ran = []

    async def _callback():
        ran.append("ran")

    with pytest.raises(FloodMuted):
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
        ))
    flood_clock.advance(601.0)
    run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
    ))
    assert ran == ["ran"]
    assert not MUTE.is_muted(MUTED_CHAT)


# ── row B: the Mini App's 13 mirrors inherit the gate, unedited ──────────────

_MIRRORS = [
    ("_mirror_session_perms_switched", (MUTED_CHAT, "dev", True)),
    ("_mirror_session_queue_cleared", (MUTED_CHAT, "dev", 2)),
    ("_mirror_session_compacted", (MUTED_CHAT, "dev")),
    ("_mirror_session_restarted", (MUTED_CHAT, "dev")),
    ("_mirror_session_renamed", (MUTED_CHAT, "old", "new")),
    ("_mirror_session_stopped", (MUTED_CHAT, "dev", 1)),
    ("_mirror_session_killed", (MUTED_CHAT, "dev")),
    ("_mirror_session_resumed", (MUTED_CHAT, "dev")),
    ("_mirror_session_deleted", (MUTED_CHAT, "dev")),
]


@pytest.mark.parametrize("name,args", _MIRRORS, ids=[m[0] for m in _MIRRORS])
def test_every_miniapp_mirror_makes_no_call_while_muted(
    mk_bot, gated_bot, flood_clock, run_async, name, args,
):
    """Row B. ``aipager/miniapp/server.py`` carries 13
    ``self.bot._app.bot.send_message`` calls, none of which had a mute
    guard and none of which the old static sweep could even see — it
    walked ``aipager/bot/*.py`` only. They are deliberately left UNEDITED:
    inheriting the gate is the whole point of moving enforcement to the
    chokepoint (D-9), and a test is what proves inheritance happened.

    Mutation: delete the gate and each of these puts a ``sendMessage``
    into the ban.
    """
    from aipager.miniapp.server import MiniAppServer

    bot = mk_bot()
    bot._app.bot = gated_bot
    server = MiniAppServer.__new__(MiniAppServer)
    server.bot = bot
    MUTE.mute(MUTED_CHAT, BAN)

    run_async(getattr(server, name)(*args))
    assert gated_bot.calls == [], f"{name} sent into an active ban"


def test_a_miniapp_mirror_still_sends_for_an_unmuted_chat(
    mk_bot, gated_bot, flood_clock, run_async,
):
    """Row B's control: the mirrors are not simply broken."""
    from aipager.miniapp.server import MiniAppServer

    bot = mk_bot()
    bot._app.bot = gated_bot
    server = MiniAppServer.__new__(MiniAppServer)
    server.bot = bot
    MUTE.mute(MUTED_CHAT, BAN)

    run_async(server._mirror_session_killed(OTHER_CHAT, "dev"))
    assert gated_bot.endpoints() == ["sendMessage"]


def test_a_muted_session_notice_leaves_the_caller_running(
    mk_bot, gated_bot, flood_clock, run_async,
):
    """D-4's third assertion, on the path row A cares about: whatever the
    gate refuses, the caller keeps going. A bare ``FloodMuted`` escaping
    into one of ``NotifyMixin.notify``'s ~20 unwrapped direct sends would
    abort the rest of the turn — losing the answer the gate exists to
    protect. The tolerance sweep is what keeps that true file-wide; this
    is the behavioural half.
    """
    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = _busy(bot)
    MUTE.mute(MUTED_CHAT, BAN)

    reached_the_end = []

    async def _turn():
        await bot.send_busy(sess)
        reached_the_end.append(True)

    run_async(_turn())
    assert reached_the_end == [True]
    assert gated_bot.calls == []


def test_an_unresolvable_session_chat_does_not_crash_the_gate(
    mk_bot, gated_bot, flood_clock, run_async,
):
    """``resolve_chat_id`` can return ``""`` on an unconfigured install.
    The gate must treat that as "no chat" and pass through, never raise."""
    bot = mk_bot()
    bot._app.bot = gated_bot
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.scope_chat_id = None
    MUTE.mute(MUTED_CHAT, BAN)
    run_async(bot.send_busy(sess))  # must not raise


# ── D-2: the exception the gate raises is path-appropriate ───────────────────
#
# The sharpest hazard in this whole ship. `notify.py:2174` and
# `animation.py:1604` both wrap the rich send in
# `except (RichMessageFallbackRequired, Exception)`, so a BARE `FloodMuted`
# reaching them fires a plain-text fallback INTO THE BAN — a fresh violation
# created by the fix. `rich_message._post` therefore translates the gate's
# refusal at the boundary, and the three broad arms above it re-raise.

def _with_limiter(monkeypatch, limiter):
    import aipager.bot.rich_message as rm
    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    return rm


def test_the_gate_reaches_the_rich_path_as_rich_message_flood_banned(
    limiter, flood_clock, run_async, monkeypatch, rich_http,
):
    """``_post`` is the boundary. The gate under it raises a bare
    ``FloodMuted``, which is right for the PTB path and a live hazard on
    this one; it must arrive here as ``RichMessageFloodBanned``.

    Driven through ``_post`` directly (a sanctioned test seam) because
    ``send_rich_message``'s own ``_raise_if_muted`` pre-check would
    otherwise answer first and mask the translation — which is exactly
    what happens in production when the mute is armed by another
    coroutine BETWEEN that pre-check and this POST.

    Mutation: delete the ``except FloodMuted`` translation in ``_post``
    and this raises a bare ``FloodMuted``, which every broad-except caller
    turns into a plain-text fallback into the ban.
    """
    import aipager.bot.rich_message as rm
    _with_limiter(monkeypatch, limiter)
    MUTE.mute(MUTED_CHAT, BAN)

    with pytest.raises(rm.RichMessageFloodBanned) as ei:
        run_async(rm._post("sendRichMessage", {"chat_id": MUTED_CHAT}))
    assert type(ei.value) is rm.RichMessageFloodBanned
    assert ei.value.chat_id == MUTED_CHAT
    assert rich_http.requests == [], "the POST was made anyway"


def test_a_gated_rich_send_never_degrades_into_a_fallback_request(
    limiter, flood_clock, run_async, monkeypatch, rich_http,
):
    """The trap itself, as an assertion. ``_send_rich_message_once`` wraps
    ``_post`` in ``except Exception -> RichMessageFallbackRequired``; the
    ``except RichMessageFloodBanned: raise`` arm ABOVE it is the only
    thing that keeps a ban from being re-classified as "safe to re-send
    as plain text".

    Mutation: delete that arm and this raises RichMessageFallbackRequired
    — and the caller obediently sends the answer into the ban.
    """
    import aipager.bot.rich_message as rm
    _with_limiter(monkeypatch, limiter)
    MUTE.mute(MUTED_CHAT, BAN)

    with pytest.raises(rm.RichMessageFloodBanned):
        run_async(rm._send_rich_message_once(
            {"chat_id": MUTED_CHAT, "rich_message": {"markdown": "hi"}},
            allow_retry=True,
        ))
    assert rich_http.requests == []


def test_a_gated_rich_edit_is_never_swallowed_into_none(
    limiter, flood_clock, run_async, monkeypatch, rich_http,
):
    """``edit_message_text_rich``'s broad arm returns ``None`` — which the
    animator reads as "message gone", dropping ``busy_msg_id`` and losing
    the card for good over a ban that lifts by itself. The
    ``except RichMessageFloodBanned: raise`` arm above it is what stops
    that.

    The pre-check is bypassed the way production bypasses it: the mute is
    armed after the call has begun. Mutation: delete the arm and this
    returns None instead of raising.
    """
    import aipager.bot.rich_message as rm
    _with_limiter(monkeypatch, limiter)

    real_post = rm._post

    async def _arm_then_post(method, payload, **kw):
        MUTE.mute(MUTED_CHAT, BAN)
        return await real_post(method, payload, **kw)

    monkeypatch.setattr(rm, "_post", _arm_then_post)
    with pytest.raises(rm.RichMessageFloodBanned):
        run_async(rm.edit_message_text_rich(MUTED_CHAT, 42, "hello"))
    assert rich_http.requests == []


def test_the_ptb_path_still_gets_a_bare_flood_muted(
    limiter, flood_clock, run_async,
):
    """The other half of D-2: translation happens at the RICH boundary
    only. PTB callers — and the seven ``transport.py`` seam helpers that
    turn it into the ``MUTED`` sentinel — need the bare type.

    Mutation: make the gate raise ``RichMessageFloodBanned`` directly and
    ``flood_budget`` has to import ``rich_message``, which cycles.
    """
    from aipager.bot.rich_message import RichMessageFloodBanned

    MUTE.mute(MUTED_CHAT, BAN)

    async def _callback():
        return "sent"

    with pytest.raises(FloodMuted) as ei:
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": MUTED_CHAT}, rate_limit_args=None,
        ))
    assert not isinstance(ei.value, RichMessageFloodBanned)
