"""Shared kit for the black-box risky-rm clock scenarios (roadmap 8.128),
tests/integration/rm-countdown-clock-bb/.

Written from design.md's success criteria and entrypoints.md only. The
plumbing is the shared rig in ``tests/two_prompts_rig.py`` (the real hook
receiver, notify path and tap handler, with keys, hook decisions and
Telegram sends recorded) plus the real ``SessionMonitor`` scan. Clocks
move only through the documented seams ``rm_countdown._now_wall`` and
``session_monitor._rm_due_sleep``. Transcripts are real files under
``tmp_path`` (the filesystem boundary).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aipager import audit
from aipager.session_monitor import SessionMonitor
from tests.two_prompts_rig import CARD, Rig

T0 = 1_800_000_000.0
RM = "rm -rf build/"
REFUSE_AT = T0 + 120.0
WINDOW_AT = REFUSE_AT - 15.0

CLOCK_EN = "⏱ If nobody answers, Claude Code refuses this by itself at about {time}."
CLOCK_HEAD = "If nobody answers, Claude Code refuses this by itself"
REFUSED_EN = "⏱ Claude Code refused this by itself because nobody answered in time."
VERB_EN = "Refused by Claude Code"
TOAST_REFUSED_CLOSED = ("Claude Code already refused this by itself, so the question "
                        "is no longer open.")
TOAST_REFUSAL_DUE = ("Claude Code refuses this by itself about now, so answer it in the "
                     "terminal if it is still open.")
TOAST_UNKNOWN = ("aipager can't tell which prompt Claude Code is showing first, so "
                 "answer this one in the terminal.")

LOCALES = Path(__file__).resolve().parents[1] / "aipager" / "i18n" / "locales"
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def hhmm(epoch: float) -> str:
    """The host's local HH:MM, computed here, independently of aipager."""
    return time.strftime("%H:%M", time.localtime(epoch))


def catalog(code: str) -> dict[str, str]:
    out = {}
    for line in (LOCALES / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["k"]] = row["v"]
    return out


class Wall:
    def __init__(self, t: float):
        self.t = t


class Env:
    """One session with a transcript file, a monitor and the rig."""

    def __init__(self, rig: Rig, wall: Wall, tmp_path: Path, run_async):
        self.rig = rig
        self.wall = wall
        self.run_async = run_async
        self.path = tmp_path / "session.jsonl"
        rig.sess.transcript_path = str(self.path)
        self.monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)

    # -- hooks ------------------------------------------------------------
    def hook(self, event, **fields):
        fields.setdefault("transcript_path", str(self.path))
        self.rig.hook(event, **fields)

    def rm(self, command=RM, *, mode="bypassPermissions", suggestions=(),
           tool_use_id="toolu_p1", hook=True, request_id=None):
        self.rig.claude_bash(command, tool_use_id=tool_use_id, hook=hook,
                             request_id=request_id, mode=mode,
                             suggestions=None if suggestions is None else list(suggestions),
                             transcript_path=str(self.path))

    # -- transcript --------------------------------------------------------
    def _append(self, obj):
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    def call(self, command=RM, tuid="toolu_p1", name="Bash"):
        self._append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tuid, "name": name,
             "input": {"command": command, "description": command}}]}})

    def result(self, tuid="toolu_p1", is_error=True):
        block = {"type": "tool_result", "tool_use_id": tuid,
                 "content": "Permission for this command was denied."}
        if is_error is not None:
            block["is_error"] = is_error
        self._append({"type": "user", "message": {"role": "user", "content": [block]}})

    def refused_transcript(self, command=RM, tuid="toolu_p1"):
        self.call(command, tuid)
        self.result(tuid, True)

    # -- monitor -----------------------------------------------------------
    def scan(self, at=None):
        if at is not None:
            self.wall.t = at
        self.run_async(self.monitor._scan())

    # -- what is observable ------------------------------------------------
    def audit(self) -> list[dict]:
        p = audit.AUDIT_LOG_PATH
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def refused_audit(self) -> list[dict]:
        return [a for a in self.audit() if a.get("via") == "claude_code_timeout"]

    def text_edits(self) -> list[dict]:
        return [c.kwargs for c in self.rig.bot._app.bot.edit_message_text.await_args_list]

    def refused_text_edits(self) -> list[dict]:
        return [k for k in self.text_edits() if REFUSED_EN in (k.get("text") or "")]

    def refused_rows(self) -> list:
        return [row for row in self.rig.sess.tool_history
                if VERB_EN in str(row[0] if isinstance(row, (tuple, list)) else row)]

    def separate_ids(self) -> list[int]:
        return [m["msg_id"] for m in self.rig.prompt_messages()]

    def last_card_text(self) -> str:
        texts = [t for mid, t, _m in self.rig.card_edits if mid == CARD]
        return texts[-1] if texts else ""


def restart(env: Env, mk_bot, monkeypatch, run_async, *, recover: bool = True) -> Env:
    """A daemon restart: the registry is saved and loaded into a new bot
    (with its recovery when *recover*); the same transcript and clock."""
    from aipager.state import SessionRegistry

    env.rig.bot.registry.save()
    again = SessionRegistry()
    again.load()
    rig = Rig(mk_bot, monkeypatch, run_async, registry=again)
    if recover:
        run_async(rig.bot.recover_sessions())
    out = Env.__new__(Env)
    out.rig, out.wall, out.run_async, out.path = rig, env.wall, run_async, env.path
    rig.sess.transcript_path = str(env.path)
    out.monitor = SessionMonitor(again, rig.bot.notify)
    return out
