"""A prompt another path closes while a Stop's Escapes run (rev-iter11-001).

The Stop reads the prompt records before its first await. A closer that
fires during the Escapes and edits nothing on the prompt's message (an
agent's SubagentStop, Claude's own Stop hook) leaves the message to the
Stop, which still takes its Allow / Deny off. Only a closer that shows its
own final edit on the message (Claude Code's refusal, the kill chord's
stopped line) is left alone: the Stop never edits that message again.
An agent's prompt carries no clock line, so the Stop's edit of a stopped
one would be a second, buttons-only edit; the refused line's case is in
test_bb_refused_while_stopping.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG


@pytest.fixture(autouse=True)
def _markup_recorded(env):
    env.rig.bot._app.bot.edit_message_reply_markup = AsyncMock()


def _edits_of(env, msg_id):
    bot = env.rig.bot._app.bot
    texts = [c.kwargs for c in bot.edit_message_text.await_args_list
             if c.kwargs.get("message_id") == msg_id]
    marks = [c.kwargs for c in bot.edit_message_reply_markup.await_args_list
             if c.kwargs.get("message_id") == msg_id]
    return texts, marks


def _buttons_taken(env, msg_id) -> bool:
    texts, marks = _edits_of(env, msg_id)
    return any("reply_markup" in k and k["reply_markup"] is None
               for k in texts + marks)


def _two_prompts(env):
    """Claude's own prompt (no clock) and a background agent's, each in
    its own message."""
    env.rig.launch(BG)
    env.rm(hook=False, mode="default")
    env.rig.agent_bash(BG, mode="default")
    sess = env.rig.sess
    assert sess.status is Status.INTERACTIVE and len(sess.extra_prompts) == 1
    primary, extra = env.separate_ids()
    return sess, primary, extra


def _hook_at_first_escape(env, monkeypatch, payload) -> list:
    """A real hook datagram reaches the receiver as the first Escape is
    typed (as Claude Code fires its hooks while the Stop's keys land)."""
    from aipager.dtach import inject
    real_keys = inject.send_keys
    fired: list = []

    async def keys(name, key, *a, **k):
        if not fired:
            fired.append(key)
            env.rig.recv._recent_fingerprints.clear()
            body = {"session": env.rig.sess.name, "transcript_path": "",
                    "_seq": 9999, **payload}
            await env.rig.recv._on_datagram(json.dumps(body).encode())
        return await real_keys(name, key, *a, **k)

    monkeypatch.setattr(inject, "send_keys", keys)
    return fired


def test_control_a_stop_with_no_hook_takes_both_prompts_buttons(env):
    """Nothing fires during the Escapes: both messages lose their buttons
    (so the tests below fail only for the hook they add)."""
    sess, primary, extra = _two_prompts(env)
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and env.rig.keys == ["Escape", "Escape"]
    assert _buttons_taken(env, primary) and _buttons_taken(env, extra)


def test_an_agents_subagent_stop_during_the_escapes_still_loses_its_buttons(
        env, monkeypatch):
    """The agent's SubagentStop closes its prompt and edits nothing. Mutant:
    the Stop skips every record another path closed, and the agent's
    message keeps its Allow / Deny."""
    sess, _primary, extra = _two_prompts(env)
    fired = _hook_at_first_escape(env, monkeypatch, {
        "hook_event_name": "SubagentStop", "agent_id": BG,
        "agent_type": "ship-reviewer"})
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and fired == ["Escape"]
    assert _buttons_taken(env, extra), _edits_of(env, extra)
    assert extra in outcome.stripped


def test_claudes_stop_hook_during_the_escapes_still_takes_its_prompts_buttons(
        env, monkeypatch):
    """Claude's Stop hook closes Claude's own prompt (the carry over its
    Stop) and edits nothing. Mutant: as above, Claude's prompt message
    keeps its Allow / Deny."""
    sess, primary, _extra = _two_prompts(env)
    fired = _hook_at_first_escape(env, monkeypatch, {"hook_event_name": "Stop"})
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and fired == ["Escape"]
    assert _buttons_taken(env, primary), _edits_of(env, primary)
    assert primary in outcome.stripped


def test_an_agent_prompt_the_kill_chord_stopped_meanwhile_keeps_its_stopped_line(
        env, monkeypatch):
    """The kill chord's closer (its agents' prompts closed, each message
    edited to the stopped line, with no buttons) runs during the Escapes:
    the Stop leaves that message to it and sends no edit of its own.
    Mutant: the Stop edits every record it read, and the stopped message
    gets a second edit from it."""
    from aipager import i18n
    from aipager.bot.session_ops import _agent_owned
    from aipager.bot.stop_text import PROMPT_AGENT_STOPPED
    from aipager.dtach import inject
    sess, primary, extra = _two_prompts(env)
    real_keys = inject.send_keys
    fired: list = []

    async def keys(name, key, *a, **k):
        if not fired:
            fired.append(key)
            records = [r for _w, perm, r in sess.open_prompt_records()
                       if isinstance(r, dict) and _agent_owned(perm)]
            sess.close_prompts(_agent_owned)
            await env.rig.bot._show_agents_stopped(sess, records)
        return await real_keys(name, key, *a, **k)

    monkeypatch.setattr(inject, "send_keys", keys)
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and fired == ["Escape"]
    line = i18n.for_sess(sess).html(PROMPT_AGENT_STOPPED)
    texts, marks = _edits_of(env, extra)
    assert len(texts) == 1 and line in texts[0]["text"], texts
    assert texts[0]["reply_markup"] is None
    assert marks == [] and extra not in outcome.stripped
    # Claude's own prompt, closed by nobody meanwhile, still loses its buttons.
    assert _buttons_taken(env, primary) and primary in outcome.stripped
