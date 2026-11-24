"""Fixtures for the black-box tap-gap scenarios (a permission prompt's
hook gave up and a background agent's dialog is only now entering Claude
Code's terminal).

The plumbing is the shared rig in ``tests/two_prompts_rig.py``: the real
hook receiver, notify path and tap handler, with keys, hook decisions and
sends recorded. The rich-message sends are recorded beside the plain ones
so none reaches the real-call guard. Claude Code's own session record is
read through the documented seam ``aipager.prompt_gap._record``; it says
"no record" unless a test gives one, so no test reads a real file."""

from __future__ import annotations

import json

import pytest

from aipager import prompt_gap
from tests.two_prompts_rig import Rig


@pytest.fixture
def no_record(monkeypatch):
    async def _none(sess):
        return None

    monkeypatch.setattr(prompt_gap, "_record", _none)


def make_rig(mk_bot, monkeypatch, run_async):
    rig = Rig(mk_bot, monkeypatch, run_async)

    async def _post(method, payload, **kw):
        if not method.startswith("send"):
            return {"ok": True, "result": True}
        msg_id = next(rig.ids)
        rig.sent.append({"chat_id": payload.get("chat_id"),
                         "text": json.dumps(payload, ensure_ascii=False),
                         "msg_id": msg_id, "rich": method,
                         "reply_to_message_id": payload.get("reply_to_message_id")})
        return {"ok": True, "result": {"message_id": msg_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async, no_record):
    return make_rig(mk_bot, monkeypatch, run_async)


@pytest.fixture
def rig_factory(mk_bot, monkeypatch, run_async, no_record):
    """Build rigs on demand. Each rig records the keys and hook decisions
    made after it was built (the newest rig's recorders are the live ones)."""
    return lambda: make_rig(mk_bot, monkeypatch, run_async)
