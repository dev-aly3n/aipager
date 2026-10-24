"""Shared helpers for the black-box QA scenarios (``test_qa_*.py``).

They drive the daemon the conftest wires (``Stack``) and read only what the
spec says is observable: the chat (the mocked ``send_message``, replies and
reactions), the dtach calls the stack records, Claude's fake files, the
runtime files ``<rt>/claude-bgjob-<job>.json`` and
``<rt>/claude-bgjob-pids.json`` (read as plain JSON), and the session's
documented fields.

English texts are the ones design.md lists ("User-facing strings"), with the
session's label in place of ``{session}``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aipager.dtach import bg_jobs
from aipager.state import Status

LABEL = "frontend"

PARK_NOTICE = ("🌙 <b>frontend</b> moved to Claude Code's background, and your next "
               "message brings it back here.")
BACK = "↩️ <b>frontend</b> is back from Claude Code's background."
BUSY_QUESTION = ("<b>frontend</b> is still working in Claude Code's background: stop "
                 "that work and bring the conversation back here?")
HELD_NOTICE = ("Your message to <b>frontend</b> waits until someone brings it back "
               "from Claude Code's background.")
LEFT = "<b>frontend</b> keeps working in Claude Code's background."


def park(stack, *, job_status="idle", job=None, js=None, worker=None):
    """``←`` on an empty prompt: the session's own claude parks on the job,
    the job's worker holds the conversation, and the client's transcript
    ends with ``continued-in``."""
    ids, claude = stack.ids, stack.claude
    job = job or ids.JOB
    js = js or ids.JS
    worker = worker or ids.WORKER
    claude.reg(ids.CLIENT, kind="interactive", sessionId=ids.X, status="idle",
               parkedJobId=job)
    claude.reg(worker, kind="bg", jobId=job, sessionId=js, status=job_status)
    claude.stop_ends[job] = worker
    claude.append(ids.X, {"type": "continued-in", "continuedInSessionId": js})
    claude.transcript(js, {"type": "user"}, {"type": "assistant"})


def park_and_tick(stack, **kw):
    park(stack, **kw)
    stack.tick()
    assert stack.sess.parked_job == (kw.get("job") or stack.ids.JOB), "precondition: parked"


def job_status(stack, status, *, job=None, js=None, worker=None):
    ids = stack.ids
    stack.claude.reg(worker or ids.WORKER, kind="bg", jobId=job or ids.JOB,
                     sessionId=js or ids.JS, status=status)


def own_pid_hook(stack):
    """An own hook that reports the session's claude pid (D1)."""
    ids = stack.ids
    stack.hook(hook_event_name="Notification", notification_type="idle_prompt_seen",
               claude_pid=ids.CLIENT, session_id=ids.X)


def sent_texts(stack) -> list[str]:
    out = []
    for c in stack.sent():
        text = c.kwargs.get("text")
        if text is None and len(c.args) > 1:
            text = c.args[1]
        out.append(text or "")
    return out


def replies(update) -> list[str]:
    out = []
    for c in update.message.reply_text.await_args_list:
        text = c.args[0] if c.args else c.kwargs.get("text", "")
        out.append(text or "")
    return out


def all_texts(stack, *updates) -> list[str]:
    """Everything the chat shows: sent messages and replies to *updates*."""
    out = sent_texts(stack)
    for u in updates:
        out += replies(u)
    return out


def count(texts, needle) -> int:
    return sum(1 for t in texts if t == needle)


def buttons(call) -> list[str]:
    markup = call.kwargs.get("reply_markup")
    if markup is None:
        return []
    return [b.callback_data.rsplit(":", 1)[1]
            for row in markup.inline_keyboard for b in row]


def calls_with_text(stack, needle) -> list:
    out = []
    for c in stack.sent():
        text = c.kwargs.get("text") or (c.args[1] if len(c.args) > 1 else "")
        if needle in (text or ""):
            out.append(c)
    return out


def link(job) -> dict | None:
    path = Path(bg_jobs.link_path(job))
    if not path.exists():
        return None
    return json.loads(path.read_text())


def pidmap() -> dict | None:
    path = Path(bg_jobs.pidmap_path())
    if not path.exists():
        return None
    return json.loads(path.read_text())


def reactions(stack) -> list[str]:
    """Every emoji the bot put on a message, in order."""
    out = []
    calls = list(stack.bot._react.await_args_list)
    calls += [c for c in stack.bot._app.bot.mock_calls
              if "set_message_reaction" in str(c)]
    for c in calls:
        for a in list(c.args) + list(c.kwargs.values()):
            if isinstance(a, str) and a in ("👀", "🤷"):
                out.append(a)
    return out


def stops(stack) -> list[list[str]]:
    """The ``claude stop`` commands run (``claude agents --json`` is a
    documented read-only source of the job's state, not counted)."""
    return [r for r in stack.claude.runs if r[:1] == ["stop"]]


def write_exit(stack, *, code, start, end):
    path = Path(bg_jobs.exit_path(stack.ids.NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"code": code, "start": start, "end": end}))
    return path


def vanish(stack):
    """The session's dtach socket is gone at the next tick."""
    stack.live.discard(stack.ids.NAME)
    stack.tick()


def relaunched_start(stack):
    """The relaunched claude's first own SessionStart."""
    ids = stack.ids
    stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
    stack.hook(hook_event_name="SessionStart", source="resume", session_id=ids.JS,
               claude_pid=ids.RELAUNCHED)


def own_stop(stack):
    """The relaunched claude's own turn ends (its answer already in the
    transcript, as Claude Code flushes it before Stop)."""
    stack.claude.append(stack.ids.JS, {
        "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "ok"}]}})
    stack.hook(hook_event_name="Stop", session_id=stack.ids.JS,
               transcript_path=str(stack.claude.projects / f"{stack.ids.JS}.jsonl"),
               last_assistant_message="ok")


def setup_session(s, cwd):
    """The ``stack`` fixture's session: ``frontend``, BUSY on X, its own
    claude registered as CLIENT."""
    sess = s.registry.get_or_create(s.ids.NAME)
    sess.label = s.ids.LABEL
    sess.scope_chat_id = s.ids.CHAT
    sess.cwd = str(cwd)
    sess.perm_mode = "auto"
    sess.claude_session_id = s.ids.X
    sess.transcript_path = str(s.claude.transcript(s.ids.X, {"type": "user"}))
    s.registry.transition(s.ids.NAME, Status.BUSY)
    sess.busy_started_at = time.monotonic()
    s.registry.last_active_session = s.ids.NAME
    s.claude.reg(s.ids.CLIENT, kind="interactive", sessionId=s.ids.X, status="busy")
    s.sess = sess
    return sess


def own_turns(stack, n):
    """The relaunched claude takes each typed prompt (``UserPromptSubmit``)
    and finishes it (``Stop``), *n* times or until nothing more is typed."""
    done = 0
    while done < n and done < len(stack.typed):
        stack.hook(hook_event_name="UserPromptSubmit", prompt=stack.typed[done],
                   session_id=stack.ids.JS,
                   transcript_path=str(stack.claude.projects / f"{stack.ids.JS}.jsonl"))
        own_stop(stack)
        done += 1

