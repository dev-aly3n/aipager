"""Scenario 7 (holds, 8.77) and scenario 8 (wrong-chat routing,
8.81/8.82)."""

from __future__ import annotations

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
DM = fti.DM_ID


def test_another_senders_message_is_held_then_runs_as_its_own_turn(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.BOB, "ft10")
    card, allow = flows.ask_write(inst, name, "ft10", G, fti.BOB, "ft10")
    n = len(inst.prompts_seen(name))

    # alice writes to bob's session while its permission prompt is open.
    log_since = inst.log_mark()
    held = fake.inject_text(G, fti.user(fti.ALICE), "/ft10 hello from alice")
    flows.wait_reaction(fake, G, held["message_id"], "👀")
    inst.wait_log("[ft10] Held (", since=log_since)
    assert len(inst.prompts_seen(name)) == n, "the held message reached Claude"

    # bob allows; his turn ends; then alice's message runs as its own turn.
    cb = fake.inject_callback(fti.user(fti.BOB), card, allow)
    fake.wait_answer(cb)
    fti.wait_until(inst.file_in_project("ft10.txt").exists, 60, "bob's file")
    prompt = flows.wait_prompt(inst, name, "hello from alice", after=n)
    assert "@alice" in prompt.splitlines()[0], prompt
    assert "@bob" not in prompt
    seen = inst.prompts_seen(name)
    assert sum("hello from alice" in p for p in seen) == 1
    assert not any("hello from alice" in p and "Write tool" in p for p in seen)


def test_dm_session_traffic_stays_in_the_dm(fresh):
    inst, fake = fresh, fresh.fake
    inst.new_session(G, fti.ALICE, "ft12")
    name = inst.new_session(DM, fti.ALICE, "ft11")
    start = fake.mark()

    turn_log = inst.log_mark()
    card, allow = flows.ask_write(inst, name, "ft11", DM, fti.ALICE, "ft11")
    assert card["chat"]["id"] == DM
    since = fake.mark()
    cb = fake.inject_callback(fti.user(fti.ALICE), card, allow)
    fake.wait_answer(cb)
    flows.wait_text(fake, DM, "Allowed", since=since)
    fti.wait_until(inst.file_in_project("ft11.txt").exists, 60, "alice's file")
    flows.wait_turn_end(inst, name, "ft11", since_log=turn_log)
    flows.settle(fake)

    # /stop on a running turn: the result goes to the DM too.
    log_since = inst.log_mark()
    since = fake.mark()
    inst.hold_tools(name)
    fake.inject_text(DM, fti.user(fti.ALICE), "Use the Write tool to create ft11b.txt containing hi")
    inst.wait_log("[ft11] Busy message sent", since=log_since, timeout=120)
    fake.inject_text(DM, fti.user(fti.ALICE), "/stop")
    flows.wait_text(fake, DM, "Stopped", since=since, timeout=60)
    inst.release_tools(name)
    flows.settle(fake)

    leaked = [c.summary() for c in fake.calls(chat_id=G, since=start) if "ft11" in c.text]
    assert leaked == [], leaked
    dm_calls = [c for c in fake.calls(chat_id=DM, since=start) if "ft11" in c.text]
    assert any(c.method == "editMessageText" for c in dm_calls), "no card edit in the DM"
