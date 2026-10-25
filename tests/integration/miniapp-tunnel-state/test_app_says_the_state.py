"""/app says the tunnel's state (spec requirement 4; design.md success
criteria "/app replies ..." and "/app in the slow phase ..."; the
entrypoints.md /app table and English texts).

Partitions: the phase (starting, working, retrying, slow) x the reason
(7 ids plus an unknown id) x the caller (personal-mode operator,
personal-mode stranger, scope admin, scope member without can_manage in
their own DM, legacy team admin, legacy team developer). Error guessing:
cloudflared's raw error text or a forbidden word leaking into chat; a
stranger starting tunnel attempts.

The tunnel state is produced by a real ``TunnelManager`` whose launch
seam fails with the reason under test, never by writing the in-memory
slot directly.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import aipager.miniapp.tunnel_manager as tm
from aipager.miniapp import tunnel_state
from aipager.miniapp.tunnel_manager import TunnelLaunchError

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    DNS_INCOMPLETE_TEXT, DNS_LOOKUP_TEXT, EDGE_DIAL_TEXT, QUICK_TUNNEL_TEXT, FakeProcess,
    forever, log_line, settle, wait_until, write_cf_files,
)

OPERATOR = 555
STRANGER = 999_999
SCOPE_ADMIN = 4_101
SCOPE_MEMBER = 4_102
TEAM_ADMIN = 7
TEAM_DEV = 8
GROUP = -100_4242

TEXT = {
    "dns_incomplete": "📱 The Mini App can't connect: this machine's DNS server gave an "
                      "incomplete answer when cloudflared looked up Cloudflare's servers. "
                      "Set the machine to use another DNS server, for example 1.1.1.1. "
                      "aipager keeps trying by itself.",
    "dns_lookup_failed": "📱 The Mini App can't connect: this machine could not look up "
                         "Cloudflare's servers in DNS. Check the machine's internet "
                         "connection and its DNS server, for example by switching to "
                         "1.1.1.1. aipager keeps trying by itself.",
    "quick_tunnel": "📱 The Mini App can't connect: cloudflared could not get a link from "
                    "Cloudflare. Check that this machine can reach the internet. aipager "
                    "keeps trying by itself.",
    "edge_unreachable": "📱 The Mini App can't connect: this machine can't reach "
                        "Cloudflare's servers on port 7844 (UDP), which cloudflared needs. "
                        "Check the machine's firewall or network. aipager keeps trying by "
                        "itself.",
    "binary_missing": "📱 The Mini App can't connect: aipager could not download or start "
                      "cloudflared, the program that makes its link. Check the machine's "
                      "internet connection. aipager keeps trying by itself.",
    "unreachable": "📱 The Mini App can't connect: cloudflared is running, but its link "
                   "never answered from the internet. Check the machine's network. "
                   "aipager keeps trying by itself.",
    "other": "📱 The Mini App isn't working right now. Run <code>aipager doctor</code> on "
             "this machine to see why. aipager keeps trying by itself.",
}
UNAVAILABLE = "📱 The Mini App isn't available right now. Try /app again later."
REASONS = list(TEXT)

#: What cloudflared itself would have written for each reason; none of
#: it may reach chat.
RAW = {
    "dns_incomplete": DNS_INCOMPLETE_TEXT,
    "dns_lookup_failed": DNS_LOOKUP_TEXT,
    "quick_tunnel": QUICK_TUNNEL_TEXT,
    "edge_unreachable": EDGE_DIAL_TEXT,
    "binary_missing": "zebra-marker-7781 checksum mismatch for cloudflared-linux-amd64",
    "unreachable": "zebra-marker-7781 Unauthorized: Failed to get tunnel",
    "other": "zebra-marker-7781 context deadline exceeded",
}
RAW_FRAGMENTS = ("SRV only returned", "srv records", "127.0.0.53", "api.trycloudflare.com",
                 "no recent network activity", "zebra-marker-7781", "quic", "regions")
FORBIDDEN_IN_APP_REPLY = ("tailscale", "sudo", "curl", "apt-get", "brew install", "| sh")


def _norm(text: str) -> str:
    """The text a person reads: HTML tags dropped (``<code>`` styling is
    presentation), entities decoded."""
    return html.unescape(re.sub(r"</?[a-z]+>", "", text))


@pytest.fixture(autouse=True)
def _miniapp_on(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.CHAT_ID", str(OPERATOR))
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)


@pytest.fixture
def clock(monkeypatch):
    c = {"now": 50_000.0}
    monkeypatch.setattr(tm, "_monotonic", lambda: c["now"])
    return c


def _personal_bot(mk_bot):
    return mk_bot()


def _scope_bot(mk_bot):
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    scopes = [
        Scope(chat_id=SCOPE_ADMIN, kind="dm", label="alice DM",
              members=(Member(id=SCOPE_ADMIN, label="alice", role="admin"),)),
        Scope(chat_id=SCOPE_MEMBER, kind="dm", label="bob DM",
              members=(Member(id=SCOPE_MEMBER, label="bob", role="user"),)),
        Scope(chat_id=GROUP, kind="group", label="frontend", members=(
            Member(id=SCOPE_ADMIN, label="alice", role="admin"),
            Member(id=SCOPE_MEMBER, label="bob", role="user"),
        )),
    ]
    bot = mk_bot(scopes=scopes)
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return bot


def _team_bot(mk_bot):
    from aipager.team import Role, Rules, Team, User as TeamUser
    return mk_bot(team=Team(group_id=GROUP, users={
        TEAM_ADMIN: TeamUser(id=TEAM_ADMIN, label="alice", role=Role.ADMIN),
        TEAM_DEV: TeamUser(id=TEAM_DEV, label="bob", role=Role.DEVELOPER),
    }, rules=Rules(deny_tools=[])))


CALLERS = {
    # name: (bot factory, user id, chat id, is a fixer)
    "personal_operator": (_personal_bot, OPERATOR, OPERATOR, True),
    "personal_stranger": (_personal_bot, STRANGER, STRANGER, False),
    "scope_admin": (_scope_bot, SCOPE_ADMIN, SCOPE_ADMIN, True),
    "scope_member": (_scope_bot, SCOPE_MEMBER, SCOPE_MEMBER, False),
    "team_admin": (_team_bot, TEAM_ADMIN, TEAM_ADMIN, True),
    "team_developer": (_team_bot, TEAM_DEV, TEAM_DEV, False),
}
FIXERS = [k for k, v in CALLERS.items() if v[3]]
OTHERS = [k for k, v in CALLERS.items() if not v[3]]


def _update(mk_update, user_id, chat_id):
    upd = mk_update("/app", user_id=user_id, chat_id=chat_id)
    upd.effective_message = upd.message
    upd.effective_chat.type = "private" if chat_id > 0 else "supergroup"
    upd.message.chat = upd.effective_chat
    return upd


def _failing_seam(seam, reason, n=50):
    """Every launch fails with ``reason``, cloudflared's raw text in its files."""
    def step():
        write_cf_files([log_line("Initiating shutdown", error=RAW[reason])],
                       f"{RAW[reason]}\n")
        return TunnelLaunchError(RAW[reason], reason=reason)
    seam.steps = [step for _ in range(n)]


def _app(caller, *, mk_bot, mk_update, make_manager, run_async, phase, prepare=None,
         after=None):
    """Bring a manager to ``phase``, send /app as ``caller``, return
    ``(reply_text, reply_kwargs, extra)``."""
    factory, user_id, chat_id, _fixer = CALLERS[caller]
    bot = factory(mk_bot)
    upd = _update(mk_update, user_id, chat_id)
    extra = {}

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: tunnel_state.current_status() is not None
                             and tunnel_state.current_status().phase == phase, timeout=5.0)
            if prepare:
                await prepare(extra)
            await bot._handle_app_cmd(upd, MagicMock())
            if after:
                await after(extra)
        finally:
            await mgr.stop()

    run_async(scenario())
    upd.message.reply_text.assert_awaited_once()
    args, kwargs = upd.message.reply_text.await_args
    return args[0], kwargs, extra


@pytest.fixture
def retrying(monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", 5)


# ---- the fixer gets the reason ------------------------------------------------------

@pytest.mark.parametrize("reason", REASONS)
def test_the_operator_gets_the_reason_while_retrying(
    reason, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, reason)
    text, _kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert _norm(text) == _norm(TEXT[reason])


@pytest.mark.parametrize("reason", REASONS)
def test_the_operator_gets_the_reason_in_the_slow_phase(
    reason, seam, mk_bot, mk_update, make_manager, run_async, clock,
):
    _failing_seam(seam, reason)
    text, _kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="slow")
    assert _norm(text) == _norm(TEXT[reason])


@pytest.mark.parametrize("caller", FIXERS)
def test_every_fixer_gets_the_reason(
    caller, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, "dns_incomplete")
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert _norm(text) == _norm(TEXT["dns_incomplete"])


def test_a_missing_binary_from_the_real_fetch_step_says_binary_missing(
    retrying, mk_bot, mk_update, make_manager, run_async, monkeypatch,
):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
                        AsyncMock(return_value=None))
    text, _kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert _norm(text) == _norm(TEXT["binary_missing"])


def test_an_unknown_reason_id_falls_back_to_other(
    seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    seam.steps = [TunnelLaunchError("x", reason="martian_reason") for _ in range(10)]
    text, _kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert _norm(text) == _norm(TEXT["other"])


# ---- everyone else gets "not available right now" -----------------------------

@pytest.mark.parametrize("caller", OTHERS)
@pytest.mark.parametrize("reason", ["dns_incomplete", "edge_unreachable", "other"])
def test_a_non_fixer_gets_unavailable_without_details(
    caller, reason, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, reason)
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert _norm(text) == UNAVAILABLE


@pytest.mark.parametrize("caller", OTHERS)
def test_a_non_fixer_gets_unavailable_in_the_slow_phase(
    caller, seam, mk_bot, mk_update, make_manager, run_async, clock,
):
    _failing_seam(seam, "dns_incomplete")
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="slow")
    assert _norm(text) == UNAVAILABLE


# ---- no raw cloudflared text, no forbidden word, no button ----------------------

@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize("reason", REASONS)
def test_no_reply_carries_cloudflared_text(
    caller, reason, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, reason)
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    low = _norm(text).lower()
    leaked = [f for f in RAW_FRAGMENTS + (RAW[reason],) if f.lower() in low]
    assert leaked == [], text


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize("reason", REASONS)
def test_no_reply_carries_a_forbidden_word(
    caller, reason, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, reason)
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    low = _norm(text).lower()
    assert [w for w in FORBIDDEN_IN_APP_REPLY if w in low] == [], text


@pytest.mark.parametrize("caller", ["personal_operator", "scope_member"])
def test_a_failing_tunnel_reply_has_no_button(
    caller, seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    _failing_seam(seam, "dns_incomplete")
    _text, kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert kw.get("reply_markup") is None


@pytest.mark.parametrize("reason", REASONS)
def test_no_reply_has_an_em_dash(reason, seam, retrying, mk_bot, mk_update, make_manager,
                                 run_async):
    _failing_seam(seam, reason)
    text, _kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    assert "—" not in text


def test_a_group_app_while_failing_names_no_machine_detail(
    seam, retrying, mk_bot, mk_update, make_manager, run_async,
):
    """/app in a group is a DM-only answer as before: not the reason."""
    _failing_seam(seam, "dns_incomplete")
    bot = _scope_bot(mk_bot)
    upd = _update(mk_update, SCOPE_ADMIN, GROUP)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: tunnel_state.current_status() is not None
                             and tunnel_state.current_status().phase == "retrying")
            await bot._handle_app_cmd(upd, MagicMock())
        finally:
            await mgr.stop()

    run_async(scenario())
    text = " ".join(str(c.args[0]) for c in upd.message.reply_text.await_args_list
                    if c.args)
    assert "DNS" not in text and "1.1.1.1" not in text, text


# ---- starting and working are unchanged ---------------------------------------------

@pytest.mark.parametrize("caller", ["personal_operator", "scope_member"])
def test_starting_says_still_being_set_up(
    caller, seam, mk_bot, mk_update, make_manager, run_async,
):
    seam.steps = [forever]
    text, _kw, _ = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="starting")
    assert "still being set up" in _norm(text)


def test_no_manager_says_still_being_set_up(mk_bot, mk_update, run_async):
    bot = mk_bot()
    upd = _update(mk_update, OPERATOR, OPERATOR)
    run_async(bot._handle_app_cmd(upd, MagicMock()))
    assert "still being set up" in _norm(upd.message.reply_text.await_args.args[0])


def test_working_sends_the_button_with_the_probed_url(
    seam, mk_bot, mk_update, make_manager, run_async,
):
    url = "https://app-working-words.trycloudflare.com"
    seam.steps = [(FakeProcess(), url)]
    _text, kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="working")
    buttons = [b for row in kw["reply_markup"].inline_keyboard for b in row]
    assert [b.web_app.url for b in buttons] == [url]


def test_a_tailscale_url_still_wins_over_a_failing_managed_tunnel(
    seam, retrying, mk_bot, mk_update, make_manager, run_async, monkeypatch,
):
    """MINIAPP_PUBLIC_URL and Tailscale keep their behaviour (spec, out of scope)."""
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url",
                        lambda: "https://frontend.tailnet-example.ts.net/")
    _failing_seam(seam, "dns_incomplete")
    _text, kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    buttons = [b for row in kw["reply_markup"].inline_keyboard for b in row]
    assert [b.web_app.url for b in buttons] == ["https://frontend.tailnet-example.ts.net/"]


def test_an_override_url_still_wins_over_a_failing_managed_tunnel(
    seam, retrying, mk_bot, mk_update, make_manager, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://manual.example/")
    _failing_seam(seam, "dns_incomplete")
    _text, kw, _ = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                        make_manager=make_manager, run_async=run_async, phase="retrying")
    buttons = [b for row in kw["reply_markup"].inline_keyboard for b in row]
    assert [b.web_app.url for b in buttons] == ["https://manual.example/"]


# ---- /app in the slow phase starts an attempt ------------------------------------------

def _count_after_app(caller, seam, mk_bot, mk_update, make_manager, run_async):
    _failing_seam(seam, "dns_incomplete")

    async def prepare(extra):
        extra["before"] = await settle(lambda: seam.calls)

    async def after(extra):
        extra["after"] = await settle(lambda: seam.calls)

    _t, _k, extra = _app(caller, mk_bot=mk_bot, mk_update=mk_update,
                         make_manager=make_manager, run_async=run_async, phase="slow",
                         prepare=prepare, after=after)
    return extra["after"] - extra["before"]


@pytest.mark.parametrize("caller", ["personal_operator", "scope_admin", "scope_member",
                                    "team_admin", "team_developer"])
def test_app_in_the_slow_phase_starts_one_attempt(
    caller, seam, mk_bot, mk_update, make_manager, run_async, clock,
):
    assert _count_after_app(caller, seam, mk_bot, mk_update, make_manager, run_async) == 1


def test_a_personal_mode_strangers_app_starts_no_attempt(
    seam, mk_bot, mk_update, make_manager, run_async, clock,
):
    assert _count_after_app("personal_stranger", seam, mk_bot, mk_update, make_manager,
                            run_async) == 0


def test_app_while_retrying_fast_starts_no_extra_attempt(
    seam, retrying, mk_bot, mk_update, make_manager, run_async, clock,
):
    _failing_seam(seam, "dns_incomplete")

    async def prepare(extra):
        extra["before"] = await settle(lambda: seam.calls)

    async def after(extra):
        extra["after"] = await settle(lambda: seam.calls)

    _t, _k, extra = _app("personal_operator", mk_bot=mk_bot, mk_update=mk_update,
                         make_manager=make_manager, run_async=run_async, phase="retrying",
                         prepare=prepare, after=after)
    assert extra["after"] == extra["before"]


def test_two_apps_in_the_same_minute_start_one_attempt(
    seam, mk_bot, mk_update, make_manager, run_async, clock,
):
    _failing_seam(seam, "dns_incomplete")
    bot = _personal_bot(mk_bot)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: tunnel_state.current_status() is not None
                             and tunnel_state.current_status().phase == "slow")
            before = await settle(lambda: seam.calls)
            for _ in range(2):
                await bot._handle_app_cmd(_update(mk_update, OPERATOR, OPERATOR),
                                          MagicMock())
                clock["now"] += 30.0
            return await settle(lambda: seam.calls) - before
        finally:
            await mgr.stop()

    assert run_async(scenario()) == 1
