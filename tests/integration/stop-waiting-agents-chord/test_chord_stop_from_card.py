"""Stop from the waiting card, then the turn Claude Code starts by itself
(roadmap 8.121 F5, design-6 section 4, probe G1). Two background agents,
Claude's interim Stop listing both, then the card's Stop: two chord writes
1.0 s apart, no Escape, the card reads that two agents were stopped, and
every surface is idle at once. Then the wake is replayed as Claude Code
2.1.312 sends it: a UserPromptSubmit whose prompt is a plain sentence (no
wrapper, no Telegram marker), the transcript's ``agents_killed`` and
task-notification ``user`` lines, and a Stop whose ``background_tasks`` is
empty. It is the job's own self-woken turn: no "typed in the terminal"
origin, no person's words, no sender; a lazy self-woken card; Claude's
reply delivered once."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

from aipager.state import Status
from tests.two_prompts_rig import ALY, BG, CARD, GROUP, OTHER

CHORD = b"\x18\x0b"
ESC = b"\x1b"
WAKE = '2 background agents were stopped by the user: "Review", "Review".'
REPLY = "Both helpers were stopped; nothing else is running."


def _line(path, entry):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _stamp(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _tasks(*ids):
    return [{"id": i, "type": "subagent", "status": "running", "description": "Review"}
            for i in ids]


def test_stop_from_the_card_then_the_wake(rig, tmp_path, monkeypatch):
    transcript = tmp_path / "5e1d.jsonl"
    transcript.write_text("")
    sess = rig.sess
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-other")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.",
             background_tasks=_tasks(BG, OTHER), transcript_path=str(transcript))
    assert sess.status is Status.IDLE and sess.shown_state().state == "tasks"
    assert sess.bg_task_ids_seen == [BG, OTHER]

    rig.tap(CARD, "stop", uid=ALY)
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert rig.writes.gaps() == [1.0]
    assert ESC not in b"".join(rig.writes.bytes())
    assert rig.card_edits[-1][1] == "⚠️ <b>api</b> · @alice stopped 2 background agents"
    assert sess.shown_state().state == "idle"
    bar = rig.bot._render_pinned(GROUP, [sess])[0]
    assert "agent" not in bar and "waiting" not in bar
    from aipager.miniapp import sessions as mini
    row = mini.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == ("idle", 0)

    # The wake, as 2.1.312 sends it.
    sent_rich = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", sent_rich)
    busy = AsyncMock(wraps=rig.bot._send_busy_and_animate)
    rig.bot._send_busy_and_animate = busy
    origin_before = sess.last_prompt_origin
    now = time.time()
    _line(transcript, {"type": "system", "subtype": "agents_killed", "timestamp": _stamp(now)})
    _line(transcript, {"type": "user", "timestamp": _stamp(now + 0.1),
                       "origin": {"kind": "task-notification"},
                       "message": {"role": "user", "content": WAKE}})
    rig.hook("UserPromptSubmit", prompt=WAKE, transcript_path=str(transcript))
    assert sess.status is Status.BUSY
    assert sess.turn_prompt != WAKE
    assert sess.last_prompt_origin == origin_before != "terminal"
    assert sess.turn_sender_id is None
    assert busy.await_args_list and busy.await_args_list[-1].kwargs.get("lazy") is True
    assert sess.kill_expected_until == 0.0  # the window is one-shot

    rig.hook("Stop", stop_hook_active=False, last_assistant_message=REPLY,
             background_tasks=[], transcript_path=str(transcript))
    assert sess.status is Status.IDLE and sess.shown_state().state == "idle"
    texts = [str(c.args[1]) if len(c.args) > 1 else str(c.kwargs.get("text", ""))
             for c in sent_rich.await_args_list]
    texts += [m["text"] or "" for m in rig.sent]
    texts += [e[1] for e in rig.card_edits]
    assert sum(REPLY in t for t in texts) == 1, texts
