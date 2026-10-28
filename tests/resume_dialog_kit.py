"""Shared set-up for the roadmap 8.127 bot-side tests (a dialog in Claude
Code's terminal that no hook tells of): a bot with a mocked Telegram, the
daemon's dialog probe wired, a session monitor that tells the bot, a fake
Claude session record under tmp_path, every terminal write captured
through ``inject._run`` (exact bytes) and the clocks on
``term_dialog``'s seams."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aipager import claude_jobs, i18n, term_dialog
from aipager.bot import session_ops
from aipager.cli.daemon import _wire_dialog_probe
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor
from aipager.state import Status

NAME = "claude-frontend"
LABEL = "frontend"
CHAT_ID = -1001
USER = 12345
SID = "11111111-1111-4111-8111-111111111111"
CLIENT = 4100
EN = i18n.get("en")
ESC = b"\x1b"
KILLS = b"\x15" * inject.DISCARD_KILL_BATCH


class Clock:
    """``term_dialog``'s clocks; ``wall`` starts at the real time (the
    registry reader checks a record's time against it)."""

    def __init__(self):
        self.wall = time.time()
        self.mono = 100_000.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


class World:
    def __init__(self, mk_bot, monkeypatch, tmp_path, run_async):
        self.run_async = run_async
        self.mp = monkeypatch
        root = tmp_path / "claude-config"
        (root / "sessions").mkdir(parents=True)
        self.cfg = root
        monkeypatch.setattr(claude_jobs, "config_dir", lambda: root)
        self.proc = SimpleNamespace(start={CLIENT: 777}, alive={CLIENT})
        monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: self.proc.start.get(pid))
        monkeypatch.setattr(claude_jobs, "_ppid", lambda pid: None)
        monkeypatch.setattr(claude_jobs, "_dtach_pids", lambda name: [])
        monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in self.proc.alive)
        self.clock = Clock()
        monkeypatch.setattr(term_dialog, "_now_wall", lambda: self.clock.wall)
        monkeypatch.setattr(term_dialog, "_now_mono", lambda: self.clock.mono)

        self.writes: list[bytes] = []
        self.on_write = None          # callable(bytes) after each write

        async def run(args, stdin=b"", timeout=5):
            self.writes.append(bytes(stdin))
            if self.on_write is not None:
                self.on_write(bytes(stdin))
            return True, ""

        async def nosleep(_seconds):
            return None

        self.sleeps: list[float] = []

        async def ops_sleep(seconds):
            self.sleeps.append(seconds)
            if self.on_sleep is not None:
                self.on_sleep(len(self.sleeps))

        self.on_sleep = None
        monkeypatch.setattr(inject, "_run", run)
        monkeypatch.setattr(inject, "_key_gap_sleep", nosleep)
        monkeypatch.setattr(inject, "_gate_sleep", nosleep)
        monkeypatch.setattr(session_ops, "_sleep", ops_sleep)
        monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))

        bot = mk_bot()
        bot._app.bot = AsyncMock()
        ids = iter(range(900, 5000))
        bot._app.bot.send_message = AsyncMock(
            side_effect=lambda *a, **kw: MagicMock(message_id=next(ids)))
        bot._mark_driver = MagicMock()
        bot._react = AsyncMock()
        bot._send_busy_and_animate = AsyncMock()
        bot._card_for_injected = AsyncMock()
        bot._maybe_update_bot_name = AsyncMock()
        bot._build_reply_context = MagicMock(return_value="")
        bot._edit_busy_rich = AsyncMock(return_value=True)
        bot._resume_animation_if_dead = MagicMock(return_value=False)
        self.bot = bot
        _wire_dialog_probe(bot.registry, hr)
        sess = bot.registry.get_or_create(NAME)
        sess.label = LABEL
        sess.scope_chat_id = CHAT_ID
        sess.claude_pid = CLIENT
        sess.claude_session_id = SID
        bot.registry.last_active_session = NAME
        bot.registry.transition(NAME, Status.IDLE)
        self.sess = sess
        self.monitor = SessionMonitor(bot.registry, bot.notify)
        self._bump = 0

    # -- the record ------------------------------------------------------------

    def record(self, **fields):
        path = self.cfg / "sessions" / f"{CLIENT}.json"
        body = {"pid": CLIENT, "kind": "interactive", "sessionId": SID,
                "procStart": "777", **fields}
        path.write_text(json.dumps(body))
        self._bump += 1
        ns = 1_700_000_000_000_000_000 + self._bump * 1_000_000
        os.utime(path, ns=(ns, ns))
        return path

    def waiting(self, *, ago: float = 1.5, waiting_for: str = "dialog open") -> float:
        at = self.clock.wall - ago
        self.record(status="waiting", waitingFor=waiting_for,
                    statusUpdatedAt=int(at * 1000))
        return at

    def idle(self, *, ago: float = 0.0) -> float:
        at = self.clock.wall - ago
        self.record(status="idle", statusUpdatedAt=int(at * 1000))
        return at

    def transcript(self, *, last_exchange_ago: float):
        """A transcript whose last exchange was *last_exchange_ago* s ago."""
        from datetime import datetime, timezone
        at = datetime.fromtimestamp(self.clock.wall - last_exchange_ago, timezone.utc)
        stamp = at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"
        path = self.cfg / "t.jsonl"
        path.write_text(json.dumps({"type": "assistant", "timestamp": stamp}) + "\n")
        self.sess.transcript_path = str(path)

    def cold_idle(self) -> float:
        """The idle cold-resume question, open: an old conversation, a quiet
        session (no write, no hook)."""
        self.transcript(last_exchange_ago=600)
        return self.waiting(ago=1.0)

    def after_resume(self, *, ago: float = 1.5) -> float:
        self.sess.session_start_at["resume"] = self.clock.wall - ago - 1.0
        return self.waiting(ago=ago)

    # -- driving ------------------------------------------------------------------

    def scan(self):
        async def live():
            return [NAME]
        self.mp.setattr("aipager.dtach.inject.list_sessions", live)
        self.run_async(self.monitor._scan())

    def send(self, mk_update, text, *, msg_id, user_id=USER):
        update = mk_update(text, message_id=msg_id, user_id=user_id, chat_id=CHAT_ID)
        ctx = MagicMock()
        ctx.bot = MagicMock(id=87654321)
        self.run_async(self.bot._handle_message(update, ctx))
        return update

    def sent(self):
        """Every send_message call's kwargs, in order."""
        return [c.kwargs for c in self.bot._app.bot.send_message.await_args_list]

    def typed(self) -> list[bytes]:
        """What was typed as text (pieces and Enter), wipes left out."""
        return [w for w in self.writes if w and not set(w) <= {0x15}]

    def query(self, message_id: int = 900):
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.chat = MagicMock(id=CHAT_ID)
        query.message.chat_id = CHAT_ID
        query.from_user = MagicMock(id=USER)
        return query

    def tap(self, verb: str, epoch: int | None = None, query=None):
        from aipager.bot import resume_dialog
        query = query or self.query()
        if epoch is None:
            epoch = self.sess.term_dialog["epoch"]
        self.run_async(resume_dialog.handle_tap(
            self.bot, MagicMock(), query, self.sess, verb, epoch, CHAT_ID, USER, EN, EN))
        return query

    def toasts(self, query) -> list:
        return [c.args[0] if c.args else c.kwargs.get("text")
                for c in query.answer.await_args_list]

    def bodies(self) -> list[str]:
        """The text typed before each Enter, marker line dropped."""
        out, cur = [], b""
        for w in self.writes:
            if w == b"\r":
                text = cur.decode()
                out.append(text.split("\n", 1)[1] if text.startswith("[via Telegram") else text)
                cur = b""
            elif w and not set(w) <= {0x15} and w != ESC:
                cur += w
        return out


def buttons(markup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
