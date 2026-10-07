"""Small waits and oracles the fake-Telegram scenarios share.

Every wait polls (never a bare sleep for an outcome) and fails with the
fake's last calls or the daemon's last log lines, token redacted.
"""

from __future__ import annotations

import re
import time

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.fake_telegram.redaction import redact

SEND_METHODS = ("sendMessage", "sendRichMessage", "editMessageText", "sendDocument")
_BUSY_SENT = re.compile(r"\[([^\]]+)\] Busy message sent(?: late| again)? \(msg_id=(\d+)")


def wait_prompt(inst, name: str, contains: str, *, after: int = 0, timeout: float = 120) -> str:
    """The first prompt the session's Claude received (index >= *after*)
    containing *contains*."""
    def _find():
        for p in inst.prompts_seen(name)[after:]:
            if contains in p:
                return p
        return None
    return fti.wait_until(_find, timeout, f"{name} to receive a prompt with {contains!r}",
                          lambda: inst.log_tail(25))


def wait_turn_end(inst, name: str, label: str, *, since_log: int, timeout: float = 120) -> None:
    """The session's turn that started after the log mark *since_log*
    ended. Pass a mark taken BEFORE the prompt was sent: the session's
    start also logs ``→ IDLE``."""
    inst.wait_log(f"[{label}]", "→ IDLE", since=since_log, timeout=timeout)


def label_of(name: str) -> str:
    """``claude-ft6__g4000000001`` -> ``ft6``."""
    return name.removeprefix("claude-").split("__", 1)[0]


def prompt_turn(inst, chat_id: int, uid: int, name: str, text: str, contains: str, *,
                timeout: float = 120, **kw) -> str:
    """*uid* sends *text*; wait until session *name* got it AND its turn
    ended. Real Claude takes seconds per turn: the next message sent while
    this one still runs would be absorbed (same sender) or held (another
    sender) instead of starting its own turn. Not for scenarios that are
    about holds."""
    before = len(inst.prompts_seen(name))
    log_since = inst.log_mark()
    inst.fake.inject_text(chat_id, fti.user(uid), text, **kw)
    prompt = wait_prompt(inst, name, contains, after=before, timeout=timeout)
    wait_turn_end(inst, name, label_of(name), since_log=log_since, timeout=timeout)
    return prompt


def busy_card_ids(log_lines: list[str], label: str) -> list[int]:
    """The busy-card message ids the daemon logged for session *label*
    (``Busy message sent``, ``... sent late`` and ``... sent again``)."""
    return [int(m.group(2)) for line in log_lines
            if (m := _BUSY_SENT.search(line)) and m.group(1) == label]


def assert_no_calls(calls: list, what: str) -> None:
    """Fail if *calls* is not empty, naming each call by its redacted
    one-line summary. A plain ``assert calls == []`` would print every
    call's full repr (all its parameters) unredacted."""
    if calls:
        raise AssertionError(redact(f"{what}:\n  " + "\n  ".join(c.summary() for c in calls)))


def chat_texts(fake, chat_id: int, since: int) -> list[str]:
    return [c.text for c in fake.calls(chat_id=chat_id, since=since)
            if c.method in SEND_METHODS]


def wait_text(fake, chat_id: int, contains: str, *, since: int, timeout: float = 60,
              methods=SEND_METHODS):
    """The first send/edit in *chat_id* after *since* whose visible text
    contains *contains*."""
    return fake.wait_for(lambda c: c.method in methods and c.chat_id == chat_id
                         and contains in c.text, timeout=timeout, since=since,
                         what=f"{contains!r} in chat {chat_id}")


def wait_toast(fake, callback_id: str, timeout: float = 30) -> str:
    return str(fake.wait_answer(callback_id, timeout=timeout).get("text", ""))


def quiet(fake, seconds: float = 3.0, *, since: int, chat_id: int | None = None) -> list:
    """Wait *seconds*, then the calls made since *since* (optionally only
    those naming *chat_id*)."""
    time.sleep(seconds)
    return [c for c in fake.calls(chat_id=chat_id, since=since) if c.method != "getUpdates"]


def calls_about(fake, since: int, *, message_id: int | None = None,
                text: str | None = None) -> list:
    """Calls after *since* that reference *message_id* (a reaction, a
    reply to it) or carry *text*."""
    out = []
    for c in fake.calls(since=since):
        p = c.params
        refs = {p.get("message_id"), p.get("reply_to_message_id"),
                (p.get("reply_parameters") or {}).get("message_id")
                if isinstance(p.get("reply_parameters"), dict) else None}
        if message_id is not None and message_id in refs:
            out.append(c)
        elif text is not None and text in c.text:
            out.append(c)
    return out


def reactions_on(fake, chat_id: int, message_id: int) -> list[str]:
    return fake.reactions(chat_id, message_id)


def wait_reaction(fake, chat_id: int, message_id: int, emoji: str, timeout: float = 60):
    return fti.wait_until(lambda: emoji in fake.reactions(chat_id, message_id), timeout,
                          f"{emoji} on message {message_id}")


def control(inst, chat_id: int, uid: int, name: str, word: str, timeout: float = 120) -> str:
    """The positive control behind every negative check: a mention from
    *uid* that must reach *name*. Updates are handled one at a time, so
    once it arrived every earlier update was handled too. Returns after
    the control's turn ended (see :func:`prompt_turn`)."""
    target = f"@{fti.BOT_USERNAME} " if chat_id < 0 else ""
    return prompt_turn(inst, chat_id, uid, name, f"{target}{word}", word, timeout=timeout)


def settle(fake, idle: float = 2.0, timeout: float = 60) -> None:
    """Wait until the daemon has made no Bot API call (getUpdates aside)
    for *idle* seconds: a turn's trailing card edits are done."""
    def _quiet():
        calls = [c for c in fake.calls() if c.method != "getUpdates"]
        return not calls or time.monotonic() - calls[-1].ts >= idle
    fti.wait_until(_quiet, timeout, "the daemon to go quiet")


def ask_write(inst, name: str, label: str, chat_id: int, uid: int, fname: str,
              timeout: float = 120) -> tuple[dict, str]:
    """*uid* asks session *name* to write ``<fname>.txt``; returns the
    message carrying Allow and the Allow button's data once the prompt is
    shown. With the stand-in, the tool call waits for the turn's busy
    card, so the prompt lands on it (as with a Claude that thinks first)."""
    fake = inst.fake
    since, log_since = fake.mark(), inst.log_mark()
    inst.hold_tools(name)
    target = f"@{fti.BOT_USERNAME}" if chat_id < 0 else ""
    fake.inject_text(chat_id, fti.user(uid),
                     f"{target} Use the Write tool to create {fname}.txt containing hi".strip())
    if inst.claude_mode == "standin":
        inst.wait_log(f"[{label}] Busy message sent", since=log_since, timeout=timeout)
        inst.release_tools(name)
    return fake.wait_button(chat_id, "Allow", since=since, timeout=timeout)
