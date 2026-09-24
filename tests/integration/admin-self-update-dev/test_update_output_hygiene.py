"""What installer output may reach a chat (tester-iter1-001, rev-iter1-005).

The seam already redacts and trims; update_flow redacts and caps again
where the message is built (defence in depth), and a group chat gets no
output tail at all (it can carry home-directory paths).
"""

from __future__ import annotations

import html
import re

from aipager import self_update

TOKEN = "123456789:AAHfakefakefakefakefakefakefakefake12"


def _pre_blocks(text: str) -> list[str]:
    """The unescaped content of each <pre> block (escaping may lengthen it)."""
    return [html.unescape(b) for b in re.findall(r"<pre>(.*?)</pre>", text, flags=re.S)]


def test_installer_tail_is_redacted_and_capped_at_the_message(env, run):
    # The fake seam returns raw output, as a regressed seam would.
    env.upgrade_rc = 1
    env.upgrade_output = "y" * 6000 + f" bad token {TOKEN} end"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    text = env.last_text()
    assert TOKEN not in text
    (block,) = _pre_blocks(text)
    assert 0 < len(block) <= self_update.OUTPUT_TAIL_CHARS
    assert block.endswith("end")


def test_claude_update_tail_is_redacted_and_capped_at_the_message(env, run):
    env.claude_rc = 1
    env.claude_output = "z" * 6000 + f" {TOKEN}"

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    text = env.last_text()
    assert TOKEN not in text
    (block,) = _pre_blocks(text)
    assert len(block) <= self_update.OUTPUT_TAIL_CHARS


def test_group_chat_gets_no_installer_tail(env, run):
    env.chat_id = -100777
    env.upgrade_rc = 1
    env.upgrade_output = "error in /home/op/.local/pipx/venvs/aipager/lib"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    text = env.last_text()
    assert "/home/op" not in text
    assert "<pre>" not in text
    assert "output in the daemon log" in text
    assert "upgrade failed (exit 1)" in text


def test_private_chat_still_gets_the_tail(env, run):
    env.upgrade_rc = 1
    env.upgrade_output = "ERROR: No matching distribution"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert _pre_blocks(env.last_text()) == ["ERROR: No matching distribution"]


def test_upgrade_timeout_warns_the_install_may_be_partial(env, run):
    env.upgrade_timeout = True

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    text = env.last_text()
    assert "timed out" in text
    assert "may be partial" in text
    assert "<code>pipx install --force aipager</code>" in text
    assert env.schedule_calls() == []
