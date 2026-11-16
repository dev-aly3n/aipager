"""``/usage`` and the Mini App's Usage page: plan limits and tokens
(roadmap 8.131).

The data is :mod:`aipager.usage_store`'s: the newest ``rate_limits``
reading Claude Code handed its status line, per sign-in, and the per-day
token tally read from the transcripts of aipager's sessions. aipager never
asks Anthropic or claude.ai for any of it.

One access rule (:func:`usage_access`) and one language-rendered view
(:func:`usage_view`) serve both surfaces: ``/usage`` turns the view into
HTML lines (:func:`usage_html`), ``GET /api/usage`` sends it as JSON. Times
are the daemon's local clock (the rule ``flood.clear_time`` uses), so the
page never formats one in the phone's time zone.

There are no dollar figures here on purpose (decisions.md 2): Claude Code
prices a non-Claude model at Claude rates, and a subscription's tokens are
not money spent.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aipager import i18n, usage_store
from aipager.bot.transport import calling_chat_id, reply_text
from aipager.i18n import Msg, Plural, Safe

if TYPE_CHECKING:
    from aipager.bot import TelegramBot

log = logging.getLogger(__name__)

# ---- text -----------------------------------------------------------------------

DENIED = Msg("usage.denied", "🚫 Only an admin can see usage.",
             note="Reply: someone who is not an admin sent /usage")
TITLE = Msg("usage.title", "Usage", note="/usage: the title of the reply (bold, after 📊)")
PLAN_TITLE = Msg("usage.plan.title", "Plan limits",
                 note="/usage and the Mini App: heading of the plan limits Claude Code reports "
                      "(the 5-hour window, the week, a spend limit)")
_WINDOW = {
    "five_hour": Msg("usage.window.five_hour", "5-hour window: {pct} used",
                     eg={"pct": "7%"},
                     note="/usage: how much of the plan's 5-hour limit is used; {pct} is a "
                          "percentage"),
    "seven_day": Msg("usage.window.seven_day", "This week (all models): {pct} used",
                     eg={"pct": "83%"},
                     note="/usage: how much of the plan's weekly limit (all models together) "
                          "is used; {pct} is a percentage"),
    "spend_limit": Msg("usage.window.spend_limit", "Spend limit: {pct} used",
                       eg={"pct": "40%"},
                       note="/usage: how much of the spend limit a gateway sets is used; {pct} "
                            "is a percentage"),
}
_WINDOW_RESET = {
    "five_hour": Msg("usage.window.five_hour_reset",
                     "5-hour window: reset since the last reading",
                     note="/usage: the 5-hour limit's reset time has passed since Claude Code "
                          "last reported it, so its old percentage is not shown"),
    "seven_day": Msg("usage.window.seven_day_reset",
                     "This week (all models): reset since the last reading",
                     note="/usage: the weekly limit's reset time has passed since Claude Code "
                          "last reported it"),
    "spend_limit": Msg("usage.window.spend_limit_reset",
                       "Spend limit: reset since the last reading",
                       note="/usage: the spend limit's reset time has passed since it was "
                            "last reported"),
}
RESETS_DAYS = Msg("usage.resets.days", "Resets in {days} d {hours} h",
                  eg={"days": 2, "hours": 5},
                  note="/usage: when a limit resets, more than a day away (d = days, h = hours; "
                       "short forms are fine)")
RESETS_HOURS = Msg("usage.resets.hours", "Resets in {hours} h {minutes} min, at {time}",
                   eg={"hours": 3, "minutes": 10, "time": "21:00"},
                   note="/usage: when a limit resets, within a day; {time} is the clock time "
                        "on the computer aipager runs on")
RESETS_MINUTES = Msg("usage.resets.minutes", "Resets in {minutes} min, at {time}",
                     eg={"minutes": 25, "time": "20:40"},
                     note="/usage: when a limit resets, within an hour; {time} is the clock "
                          "time")
FRESH_NOW = Msg("usage.fresh.now", "As of {time} (just now), from session {session}.",
                eg={"time": "20:15", "session": "frontend"},
                note="/usage: when Claude Code last reported these limits, and in which "
                     "session; {time} is the clock time")
FRESH_MINUTES = Plural("usage.fresh.minutes",
                       one="As of {time} ({n} minute ago), from session {session}.",
                       other="As of {time} ({n} minutes ago), from session {session}.",
                       eg={"time": "20:15", "session": "frontend"},
                       note="/usage: when Claude Code last reported these limits")
FRESH_HOURS = Plural("usage.fresh.hours",
                     one="As of {time} ({n} hour ago), from session {session}.",
                     other="As of {time} ({n} hours ago), from session {session}.",
                     eg={"time": "20:15", "session": "frontend"},
                     note="/usage: when Claude Code last reported these limits")
FRESH_DAYS = Plural("usage.fresh.days",
                    one="As of {time} ({n} day ago), from session {session}.",
                    other="As of {time} ({n} days ago), from session {session}.",
                    eg={"time": "20:15", "session": "frontend"},
                    note="/usage: when Claude Code last reported these limits")
PLAN_NONE = Msg("usage.plan.none",
                "Plan limits show here after Claude answers in a session signed in with a "
                "Claude subscription. API keys, and gateways without a spend limit, do not "
                "report them.",
                note="/usage: no plan limits have been reported yet")
_LOGIN = {
    "token": Msg("usage.login.token", "Claude login token",
                 note="/usage: which sign-in these limits belong to: a login token "
                      "(claude setup-token) set for the sessions"),
    "saved": Msg("usage.login.saved", "Claude sign-in saved on this computer",
                 note="/usage: which sign-in these limits belong to: the one saved by "
                      "signing in to Claude Code"),
    "cloud": Msg("usage.login.cloud", "Cloud provider",
                 note="/usage: which sign-in these limits belong to: Bedrock, Vertex and "
                      "the like"),
    "unknown": Msg("usage.login.unknown", "Unknown sign-in",
                   note="/usage: aipager could not tell which sign-in a session used"),
}
LOGIN_GATEWAY = Msg("usage.login.gateway", "Gateway {host}", eg={"host": "gateway.example"},
                    note="/usage: which sign-in these limits belong to: a gateway; {host} is "
                         "its address")
TOKENS_TITLE = Msg("usage.tokens.title", "Tokens",
                   note="/usage: heading of the token counts of every chat's sessions")
TOKENS_TITLE_MANAGED = Msg("usage.tokens.title_managed", "Tokens in the chats you manage",
                           note="/usage: heading of the token counts, for an admin who sees "
                                "only the chats they manage")
TOKENS_TITLE_CHAT = Msg("usage.tokens.title_chat", "Tokens in this chat",
                        note="/usage in a group: heading of this chat's token counts")
TOKENS_TODAY = Msg("usage.tokens.today",
                   "Today: {input} in, {output} out, {cache_read} cache read, "
                   "{cache_write} cache write",
                   eg={"input": "1.2k", "output": "45", "cache_read": "30.0k",
                       "cache_write": "2.1k"},
                   note="/usage: tokens today: sent to Claude (in), written by Claude (out), "
                        "read from and written to the prompt cache; counts like 1.2k")
TOKENS_WEEK = Msg("usage.tokens.week",
                  "Last 7 days: {input} in, {output} out, {cache_read} cache read, "
                  "{cache_write} cache write",
                  eg={"input": "1.2k", "output": "45", "cache_read": "30.0k",
                       "cache_write": "2.1k"},
                  note="/usage: tokens over today and the 6 days before; same parts as "
                       "usage.tokens.today")
TOKENS_NONE = Msg("usage.tokens.none", "No tokens recorded yet.",
                  note="/usage: no tokens in the last 7 days")
TOKENS_BEHIND = Msg("usage.tokens.behind",
                    "Still reading earlier transcripts, so these totals may grow.",
                    note="/usage: aipager is still counting older conversations")
GROUP_NOTE = Msg("usage.group_note",
                 "Plan limits are not shown in a group. Send /usage in your private chat with "
                 "the bot, or open the Mini App.",
                 note="/usage in a group: why the plan limits are missing and where to see them")
COL_INPUT = Msg("usage.col.input", "In",
                note="Mini App Usage page: column of tokens sent to Claude (short)")
COL_OUTPUT = Msg("usage.col.output", "Out",
                 note="Mini App Usage page: column of tokens Claude wrote (short)")
COL_CACHE_READ = Msg("usage.col.cache_read", "Cache read",
                     note="Mini App Usage page: column of tokens read from the prompt cache")
COL_CACHE_WRITE = Msg("usage.col.cache_write", "Cache write",
                      note="Mini App Usage page: column of tokens written to the prompt cache")
ROW_TODAY = Msg("usage.row.today", "Today", note="Mini App Usage page: a table row")
ROW_WEEK = Msg("usage.row.week", "Last 7 days",
               note="Mini App Usage page: a table row, today and the 6 days before")
ROW_YESTERDAY = Msg("usage.row.yesterday", "Yesterday", note="Mini App Usage page: a table row")
ROW_DAYS_AGO = Plural("usage.row.days_ago", one="{n} day ago", other="{n} days ago",
                      note="Mini App Usage page: a table row for an earlier day (n is 2 to 6)")
DAYS_TITLE = Msg("usage.days.title", "By day", note="Mini App Usage page: a heading")
MODELS_TITLE = Msg("usage.models.title", "By model, last 7 days",
                   note="Mini App Usage page: a heading; rows are model names")

#: Window order on both surfaces.
WINDOW_ORDER = ("five_hour", "seven_day", "spend_limit")


# ---- who may see it ---------------------------------------------------------------

@dataclass(frozen=True)
class UsageAccess:
    """What a caller may see: the chat scopes whose tokens count (``None``
    is every scope), the token heading (``all``, ``managed``, ``chat``)
    and whether plan limits are shown."""

    scopes: frozenset[int] | None
    title: str
    plan: bool


def usage_access(bot: "TelegramBot", user_id: Any, chat_id: Any, *,
                 for_group: bool = False) -> UsageAccess | None:
    """``None`` unless *user_id* is an admin of *chat_id* (the ``/update``
    rule, ``_is_update_admin``: owner or admin in scope mode, the ADMIN
    role in a legacy team, the operator in personal mode).

    Plan limits are the whole account's and the token totals cover every
    chat's sessions, while ``/status`` only shows a chat's own sessions to
    its members, so plain and read-only members get nothing. In scope mode
    the owner sees every chat; another admin sees the chats they manage.
    *for_group* (a reply every member of a group reads) narrows it to that
    chat's tokens and no plan limits."""
    try:
        if not bot._is_update_admin(user_id, chat_id):
            return None
        scope_mode = bool(getattr(bot, "scopes", None))
        if for_group:
            # Personal and legacy mode have one chat: its sessions are all.
            return UsageAccess(frozenset({chat_id}) if scope_mode else None, "chat", False)
        if not scope_mode:
            return UsageAccess(None, "all", True)
        from aipager.bot import report_flow
        if report_flow.is_owner(bot, user_id):
            return UsageAccess(None, "all", True)
        managed = frozenset(s.chat_id for s in bot.scopes
                            if bot._is_admin_user(user_id, s.chat_id))
        return UsageAccess(managed, "managed", True)
    except Exception:  # noqa: BLE001 - an unknown caller sees nothing
        log.debug("usage: access check failed", exc_info=True)
        return None


# ---- formatting -------------------------------------------------------------------

def fmt_tokens(n: int, tr) -> str:
    """A compact token count in *tr*'s digits: ``950``, ``1.2k``,
    ``15.0k``, ``150k``, ``1.2M``, ``15M``, ``1,234M``."""
    n = max(0, int(n))
    if n < 1000:
        text = str(n)
    elif n < 99_950:
        text = f"{n / 1000:.1f}k"
    elif n < 999_500:
        text = f"{round(n / 1000)}k"
    elif n < 9_950_000:
        text = f"{n / 1_000_000:.1f}M"
    else:
        text = f"{round(n / 1_000_000):,}M"
    return tr.num(text)


def _clock(ts: float, tr) -> str:
    """``HH:MM`` of *ts* on the daemon's clock, in *tr*'s digits."""
    return tr.num(time.strftime("%H:%M", usage_store.localtime(ts)))


def _pct(value: float, tr) -> str:
    return tr.num(f"{round(value)}%")


def _render(tr, kind: str, msg, **params):
    return tr.html(msg, **params) if kind == "html" else tr.text(msg, **params)


def _render_n(tr, kind: str, plural: Plural, count: int, **params):
    return tr.n(plural, count, kind=kind, **params)


def _reset_line(window, now: float, tr, kind: str) -> str:
    left = window.resets_at - now
    if left >= 86400:
        return _render(tr, kind, RESETS_DAYS, days=int(left // 86400),
                       hours=int(left % 86400 // 3600))
    at = _clock(window.resets_at, tr)
    if left >= 3600:
        return _render(tr, kind, RESETS_HOURS, hours=int(left // 3600),
                       minutes=int(left % 3600 // 60), time=at)
    return _render(tr, kind, RESETS_MINUTES, minutes=max(1, math.ceil(left / 60)), time=at)


def _fresh_line(reading, now: float, tr, kind: str) -> str:
    age = max(0.0, now - reading.seen_at)
    params = {"time": _clock(reading.seen_at, tr), "session": reading.session}
    if age < 60:
        return _render(tr, kind, FRESH_NOW, **params)
    if age < 3600:
        return _render_n(tr, kind, FRESH_MINUTES, int(age // 60), **params)
    if age < 86400:
        return _render_n(tr, kind, FRESH_HOURS, int(age // 3600), **params)
    return _render_n(tr, kind, FRESH_DAYS, int(age // 86400), **params)


def _login_name(reading, tr, kind: str) -> str:
    if reading.kind == "gateway":
        return _render(tr, kind, LOGIN_GATEWAY, host=reading.host or "?")
    return _render(tr, kind, _LOGIN.get(reading.kind, _LOGIN["unknown"]))


def _window_entry(name: str, window, now: float, tr, kind: str) -> dict:
    if window.resets_at <= now:
        # Its old percentage would be wrong: the window has reset since.
        return {"id": name, "state": "reset",
                "line": _render(tr, kind, _WINDOW_RESET[name]), "reset": ""}
    return {"id": name, "state": "ok", "pct": max(0, min(1000, round(window.pct))),
            "line": _render(tr, kind, _WINDOW[name], pct=_pct(window.pct, tr)),
            "reset": _reset_line(window, now, tr, kind)}


def _cells(counts, tr) -> list[str]:
    return [fmt_tokens(n, tr) for n in counts]


def _day_label(back: int, tr, kind: str) -> str:
    if back == 0:
        return _render(tr, kind, ROW_TODAY)
    if back == 1:
        return _render(tr, kind, ROW_YESTERDAY)
    return _render_n(tr, kind, ROW_DAYS_AGO, back)


def _view(snapshot, access: UsageAccess, tr, now: float, kind: str) -> dict:
    logins = []
    if access.plan:
        named = len(snapshot.plan) > 1
        for reading in snapshot.plan:
            windows = [_window_entry(n, reading.windows[n], now, tr, kind)
                       for n in WINDOW_ORDER if n in reading.windows]
            logins.append({"name": _login_name(reading, tr, kind) if named else None,
                           "windows": windows,
                           "fresh": _fresh_line(reading, now, tr, kind)})
    title = {"all": TOKENS_TITLE, "managed": TOKENS_TITLE_MANAGED,
             "chat": TOKENS_TITLE_CHAT}.get(access.title, TOKENS_TITLE)
    empty = not any(snapshot.week)
    return {
        "plan": {
            "shown": access.plan,
            "title": _render(tr, kind, PLAN_TITLE),
            "logins": logins,
            "empty": _render(tr, kind, PLAN_NONE) if access.plan and not logins else None,
        },
        "tokens": {
            "title": _render(tr, kind, title),
            "columns": ["", *(_render(tr, kind, m) for m in
                              (COL_INPUT, COL_OUTPUT, COL_CACHE_READ, COL_CACHE_WRITE))],
            "rows": [{"label": _render(tr, kind, ROW_TODAY), "cells": _cells(snapshot.today, tr)},
                     {"label": _render(tr, kind, ROW_WEEK), "cells": _cells(snapshot.week, tr)}],
            "days_title": _render(tr, kind, DAYS_TITLE),
            "days": [{"label": _day_label(i, tr, kind), "cells": _cells(counts, tr)}
                     for i, (_day, counts) in enumerate(snapshot.days)],
            "models_title": _render(tr, kind, MODELS_TITLE),
            "models": [{"label": model, "cells": _cells(counts, tr)}
                       for model, counts in snapshot.models],
            "note": _render(tr, kind, TOKENS_BEHIND) if snapshot.behind else None,
            "empty": _render(tr, kind, TOKENS_NONE) if empty else None,
        },
    }


def usage_view(snapshot, access: UsageAccess, tr, now: float) -> dict:
    """The ``GET /api/usage`` body (without ``ui``): every sentence in
    *tr*'s language as plain text, every number formatted. No dollar
    figure, path, config home or credential is in it."""
    return _view(snapshot, access, tr, now, "text")


def _token_params(counts, tr) -> dict:
    return dict(zip(("input", "output", "cache_read", "cache_write"),
                    (fmt_tokens(n, tr) for n in counts)))


def usage_html(snapshot, access: UsageAccess, tr, now: float) -> str:
    """The ``/usage`` reply (HTML)."""
    view = _view(snapshot, access, tr, now, "html")
    lines = [tr.line(f"📊 <b>{tr.html(TITLE)}</b>", kind="html")]
    plan = view["plan"]
    if plan["shown"]:
        lines += ["", tr.line(f"<b>{plan['title']}</b>", kind="html")]
        for i, login in enumerate(plan["logins"]):
            if i:
                lines.append("")
            if login["name"]:
                lines.append(tr.line(f"<b>{login['name']}</b>", kind="html"))
            for window in login["windows"]:
                lines.append(window["line"])
                if window["reset"]:
                    lines.append(window["reset"])
            lines.append(tr.line(f"<i>{login['fresh']}</i>", kind="html"))
        if plan["empty"]:
            lines.append(plan["empty"])
    tokens = view["tokens"]
    lines += ["", tr.line(f"<b>{tokens['title']}</b>", kind="html")]
    if tokens["empty"]:
        lines.append(tokens["empty"])
    else:
        lines.append(tr.html(TOKENS_TODAY, **_token_params(snapshot.today, tr)))
        lines.append(tr.html(TOKENS_WEEK, **_token_params(snapshot.week, tr)))
    if tokens["note"]:
        lines.append(tr.line(f"<i>{tokens['note']}</i>", kind="html"))
    if not plan["shown"]:
        lines += ["", tr.html(GROUP_NOTE)]
    return Safe("\n".join(str(line) for line in lines))


# ---- the command ------------------------------------------------------------------

async def handle_usage_cmd(bot: "TelegramBot", update, ctx) -> None:
    """``/usage``: plan limits and tokens, for owner and admins only."""
    if not await bot._authorize(update, allow_read_only=True):
        return
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user is not None else None
    chat_id = calling_chat_id(update)
    tr = i18n.for_update(update)
    in_group = isinstance(chat_id, int) and chat_id < 0
    access = usage_access(bot, user_id, chat_id, for_group=in_group)
    if access is None:
        log.info("usage.denied user=%s chat=%s", user_id, chat_id)
        try:
            await reply_text(update.message, tr.text(DENIED))
        except Exception:
            log.debug("usage denial reply failed", exc_info=True)
        return
    usage_store.scan(bot.registry, force=True)
    now = time.time()
    text = usage_html(usage_store.snapshot(access.scopes, now=now), access, tr, now)
    try:
        await reply_text(update.message, text, parse_mode="HTML")
    except Exception:
        log.debug("usage reply failed", exc_info=True)
