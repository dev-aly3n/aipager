"""A risky-removal prompt Claude Code refuses by itself while a Stop's
Escapes run (rev-iter10-001): the Stop read the prompt's record before
its first await, but the refusal closed it meanwhile, so the Stop leaves
the message to the refusal. The message keeps its refused line ("Claude
Code refused this by itself ...") and shows no buttons; the Stop never
puts the plain prompt text back over it."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager import i18n
from aipager.bot import rm_refusal
from aipager.state import STOP_KEYS, Status
from tests.rm_clock_bb import CLOCK_HEAD


@pytest.fixture(autouse=True)
def _markup_recorded(env):
    env.rig.bot._app.bot.edit_message_reply_markup = AsyncMock()


def _text_edits(env, msg_id):
    bot = env.rig.bot._app.bot
    return [c.kwargs for c in bot.edit_message_text.await_args_list
            if c.kwargs.get("message_id") == msg_id]


def test_a_prompt_refused_during_the_stops_escapes_keeps_its_refused_line(
        env, monkeypatch):
    """Mutant: the Stop edits every record it read, closed or not, and its
    last edit is the prompt text without the refused line."""
    from aipager.dtach import inject
    env.rm(hook=False, mode="bypassPermissions")
    sess = env.rig.sess
    assert sess.status is Status.INTERACTIVE
    assert sess.stop_mode() == STOP_KEYS
    (msg_id,) = env.separate_ids()
    perm = sess.open_prompts()[0]
    real_keys = inject.send_keys
    refused = []

    async def keys(name, key, *a, **k):
        if not refused:
            refused.append(key)
            await env.rig.bot._on_rm_refused(
                sess, {"perms": [perm], "close": True, "outcome": "refused"})
        return await real_keys(name, key, *a, **k)

    monkeypatch.setattr(inject, "send_keys", keys)
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and env.rig.keys == ["Escape", "Escape"]
    assert refused == ["Escape"], "the refusal did not land during the Escapes"
    assert perm.get("rm_refused") is True
    line = i18n.for_sess(sess).html(rm_refusal.RM_REFUSED)
    texts = _text_edits(env, msg_id)
    assert texts and line in texts[-1]["text"], [t["text"] for t in texts]
    assert texts[-1]["reply_markup"] is None
    assert msg_id not in outcome.stripped


def test_control_a_stop_with_no_refusal_takes_the_clock_line(env):
    """The same prompt, Stop tapped with nothing closing it meanwhile: the
    Stop's own edit takes the clock line off (so the test above is not
    passing because the Stop never edits this message)."""
    env.rm(hook=False, mode="bypassPermissions")
    sess = env.rig.sess
    (msg_id,) = env.separate_ids()
    outcome = env.run_async(env.rig.bot._stop_session_core(sess))
    assert outcome.ok and msg_id in outcome.stripped
    line = i18n.for_sess(sess).html(rm_refusal.RM_REFUSED)
    texts = _text_edits(env, msg_id)
    assert texts and line not in texts[-1]["text"]
    assert CLOCK_HEAD not in texts[-1]["text"]
