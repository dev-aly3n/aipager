"""Configuration for aipager — loads env from XDG path or project root."""

import json
import os
from pathlib import Path

_XDG_CONFIG = Path.home() / ".config" / "aipager" / "config.env"
_PROJECT_DOTENV = Path(__file__).parent.parent / ".env"


def _load_env_file() -> None:
    """Load environment variables.

    Source priority (first existing file wins):
      1. ~/.config/aipager/config.env (XDG, written by `aipager config`)
      2. <project-root>/.env (legacy / development checkouts)
    """
    for candidate in (_XDG_CONFIG, _PROJECT_DOTENV):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


_load_env_file()

BOT_TOKEN: str = os.environ.get("CLAUDE_TG_BOT_TOKEN", "")
CHAT_ID: str = os.environ.get("CLAUDE_TG_CHAT_ID", "")

# Optional group / team mode (see ``aipager.team``).
# ``TEAM`` is ``None`` for personal-mode installs (no team.yaml on disk),
# which preserves the existing one-user-one-DM behaviour.
# If team.yaml exists but is malformed, ``load_team`` raises
# ``TeamConfigError`` so the daemon fails loudly on startup rather
# than silently degrading to a less-safe mode.
from aipager.team import load_team as _load_team  # noqa: E402

TEAM = _load_team()
del _load_team

# ---- v2 multi-scope config ----
#
# ``aipager.yaml`` (the "who") + ``policy.yaml`` (the "what") are the
# v2 config surface, authoritative when present. The bot loads scopes
# fresh at init (see bot/core.py) — these module-level values are the
# import-time snapshot used by preflight/status/state-backfill.
#
# A malformed v2 file is recorded in ``CONFIG_ERROR`` rather than raised
# here. Raising at import time took down every command that imports this
# module, including `aipager doctor` — the one the error message tells
# users to run. Diagnostics must survive a broken config to be able to
# report it.
#
# This does NOT soften the daemon: ``bot/core.py`` re-loads scopes and
# policy itself and still raises, and ``preflight.require_config``
# refuses to start when ``CONFIG_ERROR`` is set. Both matter, because
# ``SCOPES = None`` means "legacy personal mode", under which
# ``auth._is_admin`` returns True for everyone — degrading to it on a
# parse error would be a fail-open. ``TEAM`` above is deliberately still
# loaded eagerly-and-loudly for the same reason: ``bot/core.py`` reads
# the snapshot rather than reloading, so it has no second line of
# defence.
from aipager.scope import ScopeConfigError as _ScopeConfigError  # noqa: E402
from aipager.scope import load_scopes as _load_scopes  # noqa: E402
from aipager.policy import PolicyError as _PolicyError  # noqa: E402
from aipager.policy import load_policy as _load_policy  # noqa: E402

CONFIG_ERROR: str | None = None

try:
    _v2 = _load_scopes()
except _ScopeConfigError as e:
    CONFIG_ERROR = str(e)
    _v2 = None
SCOPES = _v2[0] if _v2 else None

try:
    POLICY = _load_policy()
except _PolicyError as e:
    if CONFIG_ERROR is None:
        CONFIG_ERROR = str(e)
    # Built-in defaults = the restrictive safety floor. `load_policy`
    # documents "missing files → built-in defaults only", so pointing it
    # at a path that cannot exist yields that floor without the
    # unparseable layer.
    POLICY = _load_policy(Path("/nonexistent/aipager"),
                          Path("/nonexistent/aipager.d"))

if _v2 and _v2[1]:
    # v2 is authoritative for the bot token when aipager.yaml is present
    # — this is what lets config.env be retired (Phase C).
    BOT_TOKEN = _v2[1]

if SCOPES and not CHAT_ID:
    # v2 has no single chat id — scopes carry them — but plenty of code
    # predates scopes and still reads CHAT_ID: `aipager status` and
    # `doctor` gate on it, and `resolve_chat_id` uses it for a session
    # with no stamped scope. Once config.env was retired those callers saw
    # an empty string and reported a working install as unconfigured.
    # Same rule as state._default_scope(), deliberately not a second
    # definition of "the default chat": a lone scope wins outright,
    # otherwise the group does.
    _default_scope_obj = next(
        (s for s in SCOPES if s.kind == "group"), SCOPES[0],
    )
    CHAT_ID = str(_default_scope_obj.chat_id)
    del _default_scope_obj
del _load_scopes, _load_policy, _v2, _ScopeConfigError, _PolicyError

from aipager.scope import load_default_mode as _load_dm  # noqa: E402
DEFAULT_MODE: str = _load_dm()
del _load_dm

from aipager import scope as _scope_mod  # noqa: E402


def _load_miniapp() -> dict:
    # Reads `_scope_mod.CONFIG_PATH` at call time rather than relying on
    # load_miniapp's default argument, which binds the path once at
    # scope.py import and so ignores any later redirect (tests patch
    # `aipager.scope.CONFIG_PATH`; a default-bound path would silently
    # read the operator's real config instead).
    return _scope_mod.load_miniapp(_scope_mod.CONFIG_PATH)


def _parse_observer_bots(raw: str) -> list[tuple[str, str]]:
    """Parse 'token1:chatid1,token2:chatid2' into [(token, chatid), ...].

    Uses rsplit(":", 1) to split at the LAST colon, since bot tokens
    contain an internal colon (format NNNNN:XXXXXX).
    """
    if not raw.strip():
        return []
    result = []
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        parts = entry.rsplit(":", 1)
        if len(parts) != 2:
            continue
        token, chat_id = parts[0].strip(), parts[1].strip()
        if token and chat_id:
            result.append((token, chat_id))
    return result


# NOTE (multi-scope / R10): observer bots are a GLOBAL firehose — they
# receive a copy of events from EVERY scope, with no per-scope filtering.
# Treat an observer chat as trusted to see all scopes' activity. Per-scope
# observer routing is a documented non-goal (see researches multi-scope §R10).
OBSERVER_BOTS: list[tuple[str, str]] = _parse_observer_bots(
    os.environ.get("OBSERVER_BOTS", "")
)

# ---- Mini App server (stage 1 — plumbing + auth) -----------------------
#
# Opt-in loopback HTTP server, embedded in the daemon, serving one
# read-only Telegram Mini App page (daemon status + session list). Off
# by default: no listener, no tunnel, until explicitly enabled. Toggling
# requires a daemon restart — `aipager miniapp enable/disable` only
# writes the file.
#
# Source of truth is the `miniapp:` block in aipager.yaml, NOT config.env:
# `migrate.retire_v1()` renames config.env away on every daemon start once
# aipager.yaml is authoritative, so a setting stored there survived exactly
# one restart and then silently turned the Mini App off. Environment
# variables still win, matching _load_env_file's setdefault semantics and
# keeping one-off runs and tests overridable.
_miniapp_file: dict = _load_miniapp()

MINIAPP_ENABLED: bool = (
    os.environ["MINIAPP_ENABLED"] not in ("0", "false", "no", "")
    if "MINIAPP_ENABLED" in os.environ
    else bool(_miniapp_file["enabled"])
)
try:
    # `or` so an env var set to "" falls back to the file's port rather
    # than to the hardcoded default — consistent with MINIAPP_ENABLED and
    # MINIAPP_PUBLIC_URL, where an empty env var is not treated as a
    # request to discard what the file says.
    MINIAPP_PORT: int = int(
        os.environ.get("MINIAPP_PORT") or _miniapp_file["port"],
    )
except ValueError:
    MINIAPP_PORT = 8765
# Manual override for the Mini App's public URL (e.g. a Cloudflare
# tunnel address you run yourself). Empty means "let aipager manage a
# tunnel" — see aipager.miniapp.tunnel.resolve_public_url() for the
# full precedence (override → managed tunnel → Tailscale auto-detect).
MINIAPP_PUBLIC_URL: str = os.environ.get(
    "MINIAPP_PUBLIC_URL", _miniapp_file["public_url"],
)
del _miniapp_file

# ---- Managed Mini App tunnel (cloudflared) ------------------------------
#
# Operator tunables for aipager.miniapp.tunnel_manager.TunnelManager. The
# cloudflared version pin and its SHA256 checksums are NOT here — those
# are maintainer-pinned security constants, not something an operator
# should be able to override from the environment, and live in
# aipager.miniapp.cloudflared_fetch instead.

# Lifetime ceiling on failed restart attempts (fetch/spawn/discovery
# failure OR the child eventually exiting all count) before the
# supervision loop gives up for the rest of this daemon run. NOT reset
# on success — see tunnel_manager.py's module docstring for why a
# lifetime cap was chosen over "N failures in a row".
TUNNEL_RESTART_MAX_ATTEMPTS: int = int(
    os.environ.get("TUNNEL_RESTART_MAX_ATTEMPTS", "8")
)

# Backoff before the Nth restart attempt is `min(BASE * 2**(N-1), MAX)`
# seconds — see tunnel_manager._backoff_seconds.
TUNNEL_RESTART_BACKOFF_BASE_SECONDS: float = float(
    os.environ.get("TUNNEL_RESTART_BACKOFF_BASE_SECONDS", "2.0")
)
TUNNEL_RESTART_BACKOFF_MAX_SECONDS: float = float(
    os.environ.get("TUNNEL_RESTART_BACKOFF_MAX_SECONDS", "60.0")
)

# How long to wait for cloudflared to print its assigned
# `https://*.trycloudflare.com` hostname before killing it and counting
# the attempt as failed. Observed real latency is ~7s against a healthy
# connection to Cloudflare's edge; 20s is ~3x that, not "generous" —
# do not lower it.
TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS: float = float(
    os.environ.get("TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", "20.0")
)

# SIGTERM→SIGKILL escalation window when stopping a live cloudflared
# child, mirroring aipager.dtach.inject._KILL_TIMEOUT's shape.
TUNNEL_KILL_TIMEOUT_SECONDS: float = float(
    os.environ.get("TUNNEL_KILL_TIMEOUT_SECONDS", "3.0")
)

def _default_socket_path() -> str:
    """Resolve the control socket path.

    ``$AIPAGER_SOCKET_PATH`` wins outright; else
    ``$XDG_RUNTIME_DIR/aipager.sock`` (what the systemd unit's ``%t/``
    expands to); else ``/tmp/aipager.sock`` (containers, WSL1, minimal
    distros, and every platform without a runtime dir). Only the
    daemon's control socket moves this way — per-session dtach sockets
    stay under ``/tmp``.
    """
    override = os.environ.get("AIPAGER_SOCKET_PATH", "").strip()
    if override:
        return override
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir:
        return str(Path(runtime_dir) / "aipager.sock")
    return "/tmp/aipager.sock"


# Unix datagram socket for hook → daemon communication
SOCKET_PATH: str = _default_socket_path()

# Pane monitor interval (seconds)
PANE_POLL_INTERVAL: float = 2.0

# Use transcript JSONL for rich markdown→HTML summaries in Telegram notifications.
# When False, uses pane-scraped plain text in expandable blockquotes (old behavior).
RICH_SUMMARIES: bool = os.environ.get("CLAUDE_RICH_SUMMARIES", "1") not in ("0", "false", "no")

# Session state persistence (survives daemon restarts)
SESSION_STATE_FILE = Path.home() / ".claude" / "aipager-sessions.json"

# Per-chat FLOOD state that must outlive the process (roadmap 8.28).
# Beside the session state file and for the same reason: `$HOME` survives
# a reboot where `$XDG_RUNTIME_DIR` does not. The two runtime SIGNAL files
# (FLOOD_MUTE_FILE / FLOOD_BACKOFF_FILE, far below) live on tmpfs and are
# unlinked whenever nothing is muted or backing off, so neither can carry
# durable state — that is what this third file is for.
#
# A SEPARATE FILE, not a new key inside aipager-sessions.json:
# `SessionRegistry.save()` serialises per SESSION and this state is per
# CHAT, `state.py` must not import `flood_budget`, and two writers doing
# write-then-replace on one path race with the loser's half silently lost
# (the same reasoning FLOOD_BACKOFF_FILE was split out for).
#
# It carries the mute deadline on the WALL clock, each chat's earned rate
# and its ban history. Before 8.28 a restart forgot all of it — the
# daemon's own startup notice was then the first request into a ban it no
# longer knew about.
#
# TESTS: redirected into tmp_path by `tests/conftest.py::_isolate_home_paths`.
# That redirect is NOT optional — `_guard_real_home` deliberately excludes
# `~/.claude/` from its snapshot, so without it the suite would write into
# the operator's real home and no guard would catch it.
FLOOD_STATE_FILE = Path.home() / ".claude" / "aipager-flood-state.json"

# Never rewrite the durable file more often than this. It is driven by a
# dirty flag on the session monitor's existing 2 s tick; this is the floor
# under that, so a burst of 429s costs one write rather than twenty.
FLOOD_STATE_MIN_INTERVAL: float = 5.0

# There is deliberately no periodic-refresh constant here (review
# rev-iter1-008). `FLOOD_STATE_REFRESH_SECONDS = 30.0` was defined in
# iteration 1 with a docstring describing behaviour nothing implemented,
# and a documented constant wired to nothing is worse than an honest
# limitation: the file is written when something MATERIAL changes (a 429,
# a ban, a mute arming or lifting), so the volatile figures in it —
# sustained usage, minimal mode — are only fresh for
# `FLOOD_SUSTAINED_WINDOW` after a flood event, and `status.py` omits them
# as stale outside that. A healthy chat that has never been rate-limited
# produces no file and no line at all, which is the correct reading of
# "nothing is wrong".

# Sanity clamp on ANY mute deadline, armed or restored (8.28 D-7). Making
# the deadline wall-clock is what lets it survive a restart; it also makes
# it sensitive to an NTP jump, and a deadline computed from a skewed clock
# could otherwise mute the bot for years. The longest ban ever observed
# here is 34212 s (9.5 h), so 24 h is more than twice the worst case.
FLOOD_MUTE_MAX_SECONDS: float = 86400.0

# Minimum seconds between busy-message edits (rate-limit for Telegram API)
BUSY_EDIT_INTERVAL: float = float(os.environ.get("BUSY_EDIT_INTERVAL", "3.0"))

# Seconds between busy-message edits while the turn is producing new content.
# Also the minimum gap between any two busy-message edits (debounce floor).
STREAM_EDIT_INTERVAL: float = float(os.environ.get("STREAM_EDIT_INTERVAL", "1.2"))


# Keep the busy card in the chat when the turn ends, re-rendered once as a
# finished timeline, instead of deleting it. The card is the only record of
# which tools ran and what Claude said between them, so throwing it away at
# the moment it becomes readable loses the turn's whole shape. Set to 0 for
# the old behaviour where the card disappears as the answer arrives.
#
# Since /settings shipped, this is a SEED, not the last word: it only
# supplies a scope's *default* `layout` ("card" when true, "replace" when
# false) for a scope that has never touched the Message layout section
# of `/settings`. A stored per-scope preference (including "merged", a
# third mode this env var predates) always overrides it — see
# `aipager.preferences`'s layout resolution, the sole owner of "what
# does this scope's layout resolve to". Existing `KEEP_FINISHED_CARD=0`
# installs see zero behaviour change on upgrade: the seed reproduces
# their current default exactly, until they tap something in /settings.
KEEP_FINISHED_CARD: bool = os.environ.get(
    "KEEP_FINISHED_CARD", "1",
) not in ("0", "false", "no")

# Seconds the finished busy card gets to itself before the answer is sent,
# in the "card" layout only (roadmap 8.23). The daemon already does this in
# the right ORDER — the final card render is a blocking call that completes
# before the answer goes out — but during the 8.21 live round-trip both
# landed in the same second and the phone client drew the new message
# immediately and the edit a beat later, so `hmd`'s answer sat under a card
# that still read "Processing". That client behaviour cannot be changed;
# landing the two in the same second can. The grace is measured FROM the
# card edit, not added on top of it: the answer-text building in between
# already spends some of it, and only the remainder is ever slept. 0
# disables, restoring the old same-second behaviour.
FINISH_CARD_GRACE_SECONDS: float = float(
    os.environ.get("FINISH_CARD_GRACE_SECONDS", "0.8")
)

# Seconds a session can stay BUSY with no hook activity before the bot
# posts an informational "still working" note in chat. Nothing is wrong
# when this fires — a session running a long tool call, generating
# against a big context, or compacting emits no hooks and looks
# identical to a wedged one. The note exists only so a genuinely stuck
# session (exhausted subscription, hung network, crashed claude) is
# discoverable at all, since no Stop / PostToolUse hook will ever
# arrive to reveal it.
#
# Was 120s, which fired constantly on healthy sessions and trained
# users to read it as an error. 600s (10 min) sits above the duration
# of nearly every legitimate quiet stretch, so the note becomes rare
# enough to be worth reading. Override via the env var.
STALE_BUSY_TIMEOUT: float = float(os.environ.get("STALE_BUSY_TIMEOUT", "600"))

# Seconds a Telegram send that started a turn may go without ANY hook
# before the daemon concludes Claude Code never took it (roadmap 8.11).
# An accepted prompt fires UserPromptSubmit ~0.15 s after the inject
# (measured 2026-09-05); /compact, /clear and /exit fire PreCompact,
# SessionStart and SessionEnd just as fast. What fires nothing at all is
# an input Claude Code refused outright — an unknown slash command, a
# built-in that only opens a dialog — and that used to leave the busy
# card spinning until STALE_BUSY_TIMEOUT. 8 s is ~50x the measured hook
# latency and still short enough to read as immediate in chat. Override
# via the env var.
#
# Known limit: the latency was measured against a running Claude Code.
# A message sent in the seconds right after /new or /resume, before the
# TUI is up, may see its first hook (SessionStart, then the prompt's
# own) later than this — the card then shows the warning briefly and
# is replaced by a fresh one when the hook lands (the watchdog is
# self-healing), which is the wrong picture for a moment, not lost work.
PROMPT_HOOK_GRACE_SECONDS: float = float(
    os.environ.get("PROMPT_HOOK_GRACE_SECONDS", "8")
)

# Days a session that has ended stays in the registry — and so in the
# /resume picker and the dashboard's gone list — before it is dropped
# (roadmap 8.12). MAX_GONE_HISTORY only caps the COUNT of gone entries,
# so a session that died weeks ago sat there until fifty newer ones
# pushed it out; test probes and one-off experiments accumulate faster
# than that. Dropping the row loses nothing but the picker entry: the
# Claude transcript stays on disk and `claude --resume <id>` still works.
# 0 (or negative) disables ageing. Override via the env var.
GONE_SESSION_MAX_AGE_DAYS: float = float(
    os.environ.get("GONE_SESSION_MAX_AGE_DAYS", "14")
)

# Upper bound on how long a resume may hold off the ageing sweep above
# (`TrackedSession.is_resuming`). The sweep drops a GONE session the
# instant it crosses the age cutoff, and a resume is GONE-with-the-old-
# stamp until its launch returns — so a resume landing in that instant
# used to have its registry entry removed mid-flight and replaced by a
# blank one (roadmap 8.12 follow-up). The launch's own timeouts cap it
# at ~8 s; 60 s is generous for a slow box and still short enough that a
# resume that dies without releasing the guard cannot pin a dead entry
# in the registry for long. Override via the env var.
RESUME_GUARD_SECONDS: float = float(
    os.environ.get("RESUME_GUARD_SECONDS", "60")
)

# Upper bound on how long a single tool call may run before the stale
# busy detector fires anyway. When a PreToolUse hook has fired without a
# matching PostToolUse, the session is legitimately "quiet" — no hooks
# emit mid-tool — so the STALE_BUSY_TIMEOUT is suppressed. This cap
# guards against a genuinely wedged tool (network hang, subprocess
# spinning) never surfacing. 15 minutes is long enough for real work
# (large git clones, deep WebSearches, multi-minute relay round-trips)
# and short enough that a wedged session still surfaces the same day.
TOOL_INFLIGHT_MAX_SECONDS: float = float(
    os.environ.get("TOOL_INFLIGHT_MAX_SECONDS", "900")
)

# Upper bound on how long a compaction may run before the stale-busy
# detector fires anyway. Between PreCompact and post-compact
# SessionStart, no hooks fire — a large transcript can take multiple
# minutes to compact. Observed ~3 min on a 40 MB transcript in
# production; 30 min gives 10x buffer for larger transcripts / slower
# model days while still surfacing a genuinely wedged compact.
COMPACT_INFLIGHT_MAX_SECONDS: float = float(
    os.environ.get("COMPACT_INFLIGHT_MAX_SECONDS", "1800")
)

# How long a "Compacting…" card may show before the live-message-stack
# sweeper (session_monitor.expired_compacting_sessions) force-resolves it
# with an honest, non-claiming "didn't confirm completion" edit instead of
# spinning forever. Deliberately a SEPARATE, much shorter knob from
# COMPACT_INFLIGHT_MAX_SECONDS above: that one suppresses a warning and
# should stay generous (a real compaction of a large transcript is slow);
# this one governs how long a user stares at a card that may be claiming
# nothing is happening, which should be short-lived. An early timeout is
# self-correcting — if a genuine compaction's confirming hook arrives
# after this fires, the same message is still corrected to "Compacted:
# X% → Y%" (pop_compacting() on an already-popped stack is a no-op, not
# an error) — so a too-short value costs a briefly-wrong card, while a
# too-long value costs long-lived silence on a wedged one. Ops can raise
# this if transcripts routinely compact slower than 3 minutes.
COMPACT_CARD_TIMEOUT_SECONDS: float = float(
    os.environ.get("COMPACT_CARD_TIMEOUT_SECONDS", "180")
)

# Hard iteration ceiling on the "Compacting…" dot animation, independent
# of any clock. The animation sleeps 1s per tick, so this is ~4x the
# default card timeout above — generous enough that the sweeper always
# resolves the card first in production, while still guaranteeing the
# loop terminates on its own.
#
# This exists because much of the test suite patches `asyncio.sleep` via
# the SHARED asyncio module object (e.g. `setattr("aipager.bot.notify.
# asyncio.sleep", ...)` — `module.asyncio` IS `asyncio`), which silently
# removes the pacing from EVERY module's sleeps, not just the target's.
# A leaked animation task then spins as fast as the loop allows, growing
# an AsyncMock's `mock_calls` until the machine OOMs. A tick ceiling is
# the only bound that survives a neutralised clock.
COMPACT_ANIMATE_MAX_TICKS: int = int(
    os.environ.get("COMPACT_ANIMATE_MAX_TICKS", "720")
)

# Seconds between "Compacting…" dot frames. Named (not a bare literal)
# for the same reason as COMPACT_DONE_PAUSE_SECONDS: a test that needs
# this loop to run fast can patch THIS, instead of reaching for
# asyncio.sleep and unpacing every other module in the process.
COMPACT_ANIMATE_INTERVAL_SECONDS: float = float(
    os.environ.get("COMPACT_ANIMATE_INTERVAL_SECONDS", "1")
)

# Pause after a "Compacted: X% → Y%" edit so the user can read the delta
# before the busy animation resumes over it. A named constant rather than
# a bare literal specifically so tests can shorten it by patching THIS
# attribute — patching `asyncio.sleep` instead reaches the shared asyncio
# module and silently unpaces every other module's loops too.
COMPACT_DONE_PAUSE_SECONDS: float = float(
    os.environ.get("COMPACT_DONE_PAUSE_SECONDS", "2")
)

# A session's statusLine file being modified within this window counts
# as a liveness heartbeat and suppresses the stale-busy warning. The
# Claude Code statusLine hook fires on many small state changes during
# active work (streaming, tool cycles, etc.), so a fresh mtime is a
# reliable "session is doing something" signal even when no
# aipager-tracked hook (PreToolUse, PreCompact, …) has fired recently.
STATUSLINE_ALIVE_SECONDS: float = float(
    os.environ.get("STATUSLINE_ALIVE_SECONDS", "60")
)

# Upper bound on how long we're willing to `asyncio.sleep` for a single
# Telegram `RetryAfter` (429). Telegram can return retry_after values in
# the multi-hour range for aggressive flood-control — obeying that
# blindly wedges the daemon on one `asyncio.sleep` and blocks every
# subsequent send. When the reported retry_after exceeds this cap we
# log + give up on the message, letting the caller propagate the
# failure and letting the user see a visible signal via reaction.
TELEGRAM_MAX_RETRY_AFTER: float = float(
    os.environ.get("TELEGRAM_MAX_RETRY_AFTER", "90")
)

# Telegram's published flood limits: ~30 messages/s bot-wide and 20
# messages/min into any one group. The ONE `AIORateLimiter` the daemon
# builds from these (lifecycle._make_builder) paces every PTB call AND
# every raw rich-message POST (rich_message._post acquires through the
# same instance) — a second, independent bucket for the rich path let
# two chatty sessions put 40/min into one chat and earned an 8-hour
# ban (roadmap 8.17). Named here so nothing can rebuild the limiter
# from different numbers.
TELEGRAM_OVERALL_MAX_RATE: float = 30.0
TELEGRAM_OVERALL_TIME_PERIOD: float = 1.0
# The group limit is a ROLLING WINDOW, not a bucket: no more than
# TELEGRAM_GROUP_MAX_CALLS calls in ANY TELEGRAM_GROUP_WINDOW seconds.
# Modelling it as a 20-token bucket refilling at 20/60 s reads the same
# on paper and is not: a bucket starts full, so 25 calls paced only by
# the 1/s chat bucket all land inside the first 22 s and the group limit
# never binds (review iteration 1, rev-iter1-002).
TELEGRAM_GROUP_MAX_CALLS: float = 20.0
TELEGRAM_GROUP_WINDOW: float = 60.0

# Per-CHAT budget (roadmap 8.21, retuned by 8.27). The limiter above
# buckets per chat only for groups and channels (python-telegram-bot's
# AIORateLimiter keys its per-chat bucket on a NEGATIVE id), so a private
# DM got the 30/s overall bucket and nothing else.
#
# TELEGRAM_PRIVATE_MAX_RATE KEEPS ITS NAME, ITS VALUE AND ITS ROLE AS THE
# LIMITER'S `chat_max_rate=`, BUT IT NOW MEANS THE CEILING, NOT THE
# ALLOWANCE. 1 call/s is Telegram's PUBLISHED BURST ceiling for one chat.
# 8.21 read it as the sustained allowance and paced every chat at it
# permanently — which is how a bot with no violation history behaves, and
# is measurably wrong for a bot with one. On 2026-09-15 a chat pinned at
# this rate collected three bans in a day, escalating 1283 -> 312 ->
# 34212 s. Telegram's real limit is not a published number; it is a
# function of the account's recent history, and the only way to know it
# is to be told.
#
# So the sustained allowance is LEARNED per chat (8.27): every chat
# starts at FLOOD_START_RATE, earns FLOOD_RATE_INCREASE per quiet
# FLOOD_SUCCESS_WINDOW_SECONDS, halves on a 429 and drops to
# FLOOD_MIN_RATE on a ban — additive-increase / multiplicative-decrease,
# the same shape TCP uses on a link whose capacity it also cannot query.
# This constant is only the ceiling that climb may not pass.
TELEGRAM_PRIVATE_MAX_RATE: float = 1.0
TELEGRAM_CHAT_BURST: float = 3.0

# ── the earned rate (roadmap 8.27) ──────────────────────────────────────
# All non-env, like the budget above: nothing may rebuild the limiter from
# different numbers, and a per-box override is exactly how one install
# ends up with a tuning nobody can reproduce.
#
# Half the ceiling: high enough that a single session's card is unhindered
# (its cadence floor is 1 s/edit shared across the chat's sessions), low
# enough that a chat we know nothing about is not opened at full rate.
FLOOD_START_RATE: float = 0.5
# Additive increase. Ten steps from MIN to the ceiling, so the climb is
# legible in a log rather than a curve nobody can reason about.
FLOOD_RATE_INCREASE: float = 0.1
# Multiplicative decrease bottoms out here: one call per 20 s. Low enough
# to be a real penalty, non-zero so an answer still eventually goes out —
# a rate of 0 is a deadlock, not a back-off.
FLOOD_MIN_RATE: float = 0.05
# A quiet window that earns one increase. 60 s after a 429.
FLOOD_SUCCESS_WINDOW_SECONDS: float = 60.0
# After a BAN the same ten steps are stretched over this many hours
# (2160 s per step), because a ban is evidence about hours, not minutes.
# Two regimes on purpose: +0.1 per minute would climb MIN -> ceiling in
# 9.5 MINUTES, which is not a memory of a 9.5-HOUR ban at all.
FLOOD_RATE_RECOVERY_HOURS: float = 6.0
# A rolling ceiling on volume, for EVERY chat kind — the token bucket
# alone permits 60 calls/minute indefinitely at 1/s, which is what two
# BUSY sessions did for ~45 minutes before the 9.5-hour ban. Groups keep
# TELEGRAM_GROUP_MAX_CALLS (20/60 s) as the stricter of the two.
FLOOD_SUSTAINED_MAX: float = 30.0
FLOOD_SUSTAINED_WINDOW: float = 60.0
# Below this earned rate a chat enters MINIMAL MODE: ornaments (the busy
# card, the typing bubble, the pinned dashboard) are suspended and only
# answers, replies and signals go out. Sited so the ladder is meaningful:
# START 0.5 -> one 429 -> 0.25 (above the floor; the card keeps animating,
# slower) -> a second 429 -> 0.125 (below it; pixels stop, answers do not).
FLOOD_MINIMAL_MODE_RATE_FLOOR: float = 0.2

# ── the long-run volume budget (roadmap 8.30) ──────────────────────────
# Non-env like everything above, and for the same reason.
#
# Every window above is a minute or shorter, and a minute is not what got
# the vm3 DM banned on 2026-09-23. Two sessions streamed into it for
# 3 h 23 min at a rate every short window allowed, ~57 calls a minute with
# the typing bubble included, with no 429 and no warning until a straight
# 7-hour ban. Telegram meters long-run VOLUME and does not publish the
# window it meters it over, so this is a window of our own, chosen
# conservatively: a rolling hour per chat.
#
# 1200 an hour is 20 a minute, ~2.9x below the volume that earned the ban.
# The last 120 of them belong to ESSENTIAL calls (answers, replies,
# prompts): ornaments — the card, the typing bubble — stop at 1080, and
# the chat enters minimal mode until the hour frees. Essentials are never
# refused by this window; if they overflow it, that is logged.
FLOOD_HOURLY_MAX: int = 1200
FLOOD_HOURLY_WINDOW: float = 3600.0
FLOOD_HOURLY_ESSENTIAL_RESERVE: int = 120
# The typing bubble is the LOWEST ornament: it stops once the hour's
# ornament share is 75 % used, so the cards keep flowing, and comes back
# only once use falls under 60 % — hysteresis, so it does not flicker on
# and off every time one minute ages out of the window.
FLOOD_HOURLY_TYPING_SHED_AT: float = 0.75
FLOOD_HOURLY_TYPING_RESUME_BELOW: float = 0.60
# Minimal mode entered on a spent ornament share lifts only at 80 % of it.
# Every entry and every exit costs one ESSENTIAL edit per live card (the
# "updates paused" line, then the resume), so a latch that flapped on each
# freed slot would spend the essential reserve announcing itself.
FLOOD_HOURLY_MINIMAL_EXIT_AT: float = 0.80
# Any 429, on any endpoint — the typing bubble included — puts the chat in
# a WARNING REGIME for this long: the earned rate may not climb past
# FLOOD_WARNED_CEILING and recovers on the slow, post-ban schedule. Before
# 8.30 a 429 was forgiven in about five minutes; on vm3 the one warning
# Telegram gave came 3 h 23 min before the ban, and the rate was back at
# the ceiling within minutes of it.
FLOOD_WARNING_HOURS: float = 6.0
FLOOD_WARNED_CEILING: float = 0.5
# How long a ban is remembered. While a chat has N bans within this many
# days, its rate ceiling AND its hourly budget are divided by (1 + N): one
# ban in a week halves both, two third them. A 24-hour memory (0.7.13)
# had already forgotten vm3's 2026-09-19 ban when the 2026-09-23 one came.
FLOOD_BAN_MEMORY_DAYS: float = 7.0

# Busy-card cadence (roadmap 8.21 §4.3). The card interval is
# `max(BASE, N * floor) * MARGIN * backoff`, where N is the number of
# BUSY sessions sharing the chat: the cards of a chat share its budget
# instead of each assuming it owns one. MARGIN leaves headroom for the
# answers, replies, reactions and dashboard refreshes that are NOT cards.
CARD_CADENCE_MARGIN: float = 1.1
CARD_CADENCE_FLOOR_PRIVATE: float = 1.0
CARD_CADENCE_FLOOR_GROUP: float = 3.0

# Busy-card cadence DECAYS WITH TURN AGE (roadmap 8.30). The cadence above
# is constant for the life of a turn, so on vm3 one card was edited every
# few seconds for four hours. From these turn ages on, the card is edited
# no more often than the paired interval, and its elapsed counter switches
# to a matching unit — minutes from 10 min, hours and minutes from 1 h —
# so a slow card never looks frozen: it shows a counter that moves at its
# own granularity. Below the first age the cadence is exactly as before.
# A STATE change (busy -> waiting, a permission prompt) is shown at once,
# at most once per CARD_STATE_BYPASS_MIN_GAP; new tool rows and prose wait
# for the next due edit. Per-session opt-out: the `card_age_decay`
# preference.
CARD_AGE_TIER1_AT: float = 120.0
CARD_AGE_TIER1_INTERVAL: float = 10.0
CARD_AGE_TIER2_AT: float = 600.0
CARD_AGE_TIER2_INTERVAL: float = 30.0
CARD_AGE_TIER3_AT: float = 3600.0
CARD_AGE_TIER3_INTERVAL: float = 60.0
CARD_STATE_BYPASS_MIN_GAP: float = 10.0

# A small 429 (retry_after <= TELEGRAM_MAX_RETRY_AFTER) doubles that
# chat's card interval, up to this ceiling, and one quiet window halves
# it back. Telegram escalates on the COUNT of violations, so a handled
# 429 is still a problem: the answer is to go slower, not to retry.
FLOOD_BACKOFF_MAX: float = 8.0
FLOOD_BACKOFF_DECAY_SECONDS: float = 60.0

# How soon a busy card whose last edit the budget REFUSED tries again,
# instead of sitting out a whole card interval. N cards started by one
# burst of prompts tick in phase, and a chat's burst (3) against the
# 2-token skip reserve admits only two of them — so without this the
# third card loses every cluster and the starvation guard becomes its
# normal cadence. It can never make a card edit FASTER than its interval:
# `_animate_tick`'s debounce is the gate and is unchanged. One second is
# exactly one token of a 1 call/s chat budget.
CARD_RETRY_WAKE: float = 1.0

# Upper bound on the one BLOCKING card edit the starvation guard makes
# after a card has been refused for 2 x its interval. It runs inside
# `sess._stream_edit_lock`, and the stale-card watchdog replaces a task
# that holds that lock for CARD_REFRESH_TIMEOUT (20 s) — so this must
# stay well under it. It only bites during a 429 deferral, where not
# editing is the right answer anyway.
CARD_STARVATION_BLOCK_TIMEOUT: float = 5.0

# How often the "typing…" chat action is re-sent, per SESSION, while its
# busy card is live (roadmap 8.24). 0 — or any value <= 0 — disables the
# indicator outright.
#
# Telegram's own contract for `sendChatAction`: "The status is set for 5
# seconds or less (when a message arrives from your bot, Telegram clients
# clear its typing status)". So keeping the bubble lit needs one call
# every <= 5 s per chat; 4.5 s leaves margin for the round trip without
# spending calls on an indicator that is already lit. The refresh runs on
# its own task (`animation._animate_typing`), whose period is this value
# measured from the start of each send — NOT on the busy card's wake grid,
# which would round it up to the card's interval and let the status lapse.
#
# It is deliberately NOT paced by the per-chat budget, and does not need
# to be: measured live on the operator's bot, 2026-09-12 (design §12),
# `sendChatAction typing` returned 200 on all ELEVEN calls made DURING a
# `retry_after=10` window in which every `editMessageText` into the same
# chat was refused, and the phone showed "typing…" throughout. Chat
# actions are not in the message/edit bucket. 0.7.11 (8.21) removed the
# indicator outright on the opposite assumption — see
# `bot/animation.py`'s `_animate_typing` / `_typing_chat` / `_send_typing`
# and `bot/flood_budget.CHAT_ACTION_ENDPOINT`.
TYPING_INDICATOR_INTERVAL: float = float(
    os.environ.get("TYPING_INDICATOR_INTERVAL", "4.5")
)

# Signal file the daemon drops beside its control socket while a chat is
# flood-muted (`{"muted": [{"chat_id", "until", "retry_after"}]}`), so
# `aipager status` / `aipager doctor` — separate processes with no reply
# channel to the daemon — can say "Telegram flood-muted until HH:MM".
# The daemon writes it and never reads it back: the mute itself lives in
# memory only (bot/flood.py) and is gone on restart, which also unlinks
# whatever a previous daemon left here.
FLOOD_MUTE_FILE: str = str(Path(SOCKET_PATH).parent / "aipager-flood-mute.json")

# Signal file for the 8.21 per-chat backoff, beside the mute file and
# deliberately NOT the same path: `FloodMute._write_signal` unlinks
# FLOOD_MUTE_FILE whenever no mute is active (bot/flood.py), so a second
# writer's section would vanish the first time a mute lapsed — and two
# writers doing write-then-replace on one path race, with the loser's
# half silently lost. Written only by bot/flood_budget.py, read only by
# `status.read_flood_backoffs`, unlinked at daemon start/stop and as soon
# as no chat is backing off.
FLOOD_BACKOFF_FILE: str = str(Path(SOCKET_PATH).parent / "aipager-flood-backoff.json")

# When two hook events arrive with identical (session, event_name,
# payload-hash) within this window, drop the second. Belt-and-braces
# against double-wiring scenarios (e.g. a wrapper script whose name
# doesn't match the bootstrap detector, so the standard hook gets
# appended alongside → every event fires twice). 3 s is long enough
# to catch back-to-back duplicates from a single Claude Code stop
# event; short enough that legitimately-identical events (rare, but
# e.g. the same tool called twice in quick succession with the same
# input) mostly aren't collapsed.
HOOK_DEDUP_WINDOW_SECONDS: float = float(
    os.environ.get("HOOK_DEDUP_WINDOW_SECONDS", "3")
)

# Spinner verbs for animated busy messages (curated from Claude Code's terminal spinner)
SPINNER_VERBS: list[str] = [
    "Thinking", "Reasoning", "Pondering", "Considering", "Analyzing",
    "Processing", "Synthesizing", "Deliberating", "Evaluating", "Mulling",
    "Contemplating", "Inferring", "Cogitating", "Puzzling", "Calculating",
    "Deciphering", "Formulating", "Examining", "Investigating", "Brewing",
    "Cooking", "Crafting", "Forging", "Conjuring", "Noodling",
    "Percolating", "Simmering", "Ruminating", "Musing", "Tinkering",
]

# Quick template buttons for Telegram persistent keyboard
TEMPLATES_BUTTON = "Templates"
BACK_BUTTON = "\u00ab Back"
# Opens the Mini App straight from the keyboard. A `web_app` keyboard
# button sends no text when tapped \u2014 it just opens the app \u2014 so the
# label only ever reaches the router on a client too old to know what
# `web_app` is, where it falls back to /app's inline button.
APP_BUTTON = "\U0001f4f1 App"
_DEFAULT_TEMPLATES: list[tuple[str, str]] = [
    ("Continue", "Continue"),
    ("Run tests", "Run the tests"),
    ("Write tests", "Write tests for the changes"),
    ("Commit", "Commit the changes with a descriptive message"),
    ("LGTM ship it", "LGTM, ship it"),
    ("Show diff", "Show me the git diff of all changes"),
    ("Explain plan", "Explain your plan before making changes"),
    ("Update memory", "Update CLAUDE.md with what you learned"),
]

# Claude Code slash commands — instant commands (no BUSY transition)
# Only commands that CHANGE BEHAVIOR belong here. Commands that just
# display info in the terminal (cost, context, stats, doctor) are useless
# remotely since the user can't see the terminal output in Telegram.
COMMANDS_BUTTON = "Commands"
_DEFAULT_COMMANDS: list[tuple[str, str]] = [
    ("Compact", "/compact"),
    ("Clear", "/clear"),
    ("Plan mode", "/plan"),
    ("Init", "/init"),
    ("Security review", "/security-review"),
]

# Model submenu — accessible from Commands → Model
MODELS_BUTTON = "Model \u203a"
_DEFAULT_MODELS: list[tuple[str, str]] = [
    ("Sonnet", "/model sonnet"),
    ("Opus", "/model opus"),
    ("Haiku", "/model haiku"),
    ("OpusPlan", "/model opusplan"),
]

# ---- Customizable keyboard layout (item 4.1) -------------------------
#
# Optional override at ``~/.config/aipager/keyboard.json``. Any missing
# section falls back to the hardcoded defaults above; an unparseable
# file logs a warning and uses defaults. Changes require a daemon
# restart (the bot rebuilds the keyboard on every render but the
# constants are imported once at startup).
#
# Schema:
#   {
#     "templates": [{"label": "Continue", "prompt": "Continue"}, ...],
#     "commands":  [{"label": "Compact",  "send":   "/compact"},  ...],
#     "models":    [{"label": "Sonnet",   "send":   "/model sonnet"}, ...]
#   }

_KEYBOARD_CONFIG_PATH = Path.home() / ".config" / "aipager" / "keyboard.json"


def _coerce_pair_list(items, *, payload_key, fallback):
    """Turn a list-of-dicts spec into ``[(label, payload), ...]`` tuples."""
    out: list[tuple[str, str]] = []
    if isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            payload = entry.get(payload_key)
            if isinstance(label, str) and isinstance(payload, str):
                out.append((label.strip(), payload))
    return out or fallback


def _load_keyboard_overrides():
    """Return (templates, commands, models) honoring keyboard.json."""
    import logging
    _log = logging.getLogger(__name__)
    if not _KEYBOARD_CONFIG_PATH.exists():
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    try:
        data = json.loads(_KEYBOARD_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("keyboard.json could not be loaded (%s); using defaults", e)
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    if not isinstance(data, dict):
        _log.warning("keyboard.json root must be an object; using defaults")
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    return (
        _coerce_pair_list(data.get("templates", []),
                          payload_key="prompt",
                          fallback=_DEFAULT_TEMPLATES),
        _coerce_pair_list(data.get("commands", []),
                          payload_key="send",
                          fallback=_DEFAULT_COMMANDS),
        _coerce_pair_list(data.get("models", []),
                          payload_key="send",
                          fallback=_DEFAULT_MODELS),
    )


QUICK_TEMPLATES, QUICK_COMMANDS, MODEL_CHOICES = _load_keyboard_overrides()

# Parent level for each keyboard level (for context-aware Back button)
KEYBOARD_PARENTS: dict[str, str] = {
    "templates": "main",
    "commands": "main",
    "models": "commands",
}

# Directory for files downloaded from Telegram (photos, documents)
FILE_DOWNLOAD_DIR = Path("/tmp/aipager-files")
