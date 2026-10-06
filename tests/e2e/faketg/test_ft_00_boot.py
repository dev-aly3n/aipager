"""Boot: the isolated instance comes up against the fake and nothing else.

``test_isolated_instance_paths_and_api`` is the gate for every other
module: the daemon polls the fake with the fake token, binds its control
socket and floor file inside its own folder, wired this repo's hooks
into its own HOME, and runs with an environment that names nothing of
the operator's. ``test_claude_boots_in_instance`` proves Claude itself
starts in the fresh HOME (real mode) or the stand-in's plumbing works.
"""

from __future__ import annotations

import re

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc

STUCK = "Claude is stuck on a first-run screen in the instance HOME"


def test_isolated_instance_paths_and_api(faketg):
    faketg.assert_isolated()
    assert faketg.inst_dir.joinpath("aipager.sock").exists()
    # Nothing of this instance went to /tmp/claude-* or the real home.
    assert not list(faketg.inst_dir.glob("claude-dtach-*.sock"))
    floor = f"/tmp/claude-policy-.floor-{__import__('os').getuid()}.json"
    assert floor not in faketg.log_text()


def _answer_seen(fake, since, text):
    for c in fake.calls(since=since):
        if c.method in ("sendMessage", "sendRichMessage", "editMessageText") \
                and c.chat_id == fti.DM_ID and text in c.text:
            return c
    return None


def test_claude_boots_in_instance(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(fti.DM_ID, fti.ALICE, "ft0")
    since = fake.mark()
    log_since = inst.log_mark()
    fake.inject_text(fti.DM_ID, fti.user(fti.ALICE), "Reply with exactly: OK")
    try:
        inst.wait_log("[ft0]", "BUSY → IDLE", since=log_since, timeout=120)
        fti.wait_until(lambda: _answer_seen(fake, since, "OK"), 60,
                       "the answer OK in the DM")
    except AssertionError:
        screen = inst.screen(name)
        if inst.claude_mode == "real" and re.search(r"trust", screen, re.I):
            # The folder-trust dialog (test_e2e_live_daemon's fallback):
            # Enter once, then the prompt again.
            import asyncio

            from aipager.dtach import inject
            asyncio.run(inject.send_keys(name, "Enter"))
            fake.inject_text(fti.DM_ID, fti.user(fti.ALICE), "Reply with exactly: OK")
            inst.wait_log("[ft0]", "BUSY → IDLE", since=log_since, timeout=120)
        else:
            raise AssertionError(f"{STUCK}:\n{screen}") from None
    assert any("Reply with exactly: OK" in p for p in inst.prompts_seen(name))
    if inst.claude_mode == "standin":
        events = [e["event"] for e in inst.standin_log(name)]
        assert events[:2] == ["start", "prompt"] and "stop" in events


def test_no_unknown_methods_called(faketg):
    assert faketg.fake.unknown_methods == []
    assert faketg.fake.bad_token_calls == []
    assert ftc.harness_names_in([]) == []
