"""Fixtures for a background agent's prompt after Claude's own turn ended.
The plumbing is ``tests/two_prompts_rig.py``: the real hook receiver,
notify path and tap handler, with keys, hook decisions and sends recorded.
The rich-message sends (the interim answer) are recorded beside the plain
ones, so none reaches the real-call guard."""

from __future__ import annotations

import json

import pytest

from tests.two_prompts_rig import Rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
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
