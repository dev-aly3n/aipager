"""Scenario 9 (live reload, 8.80): bob is removed from the instance's
aipager.yaml and the TEST daemon gets SIGUSR1 (its own PID only); bob's
held message is dropped with the "Dropped" reply and a 🤷."""

from __future__ import annotations

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID


def _without_bob(cfg: dict) -> None:
    for scope in cfg["scopes"]:
        scope["members"] = [m for m in scope["members"] if m["id"] != fti.BOB]


def test_reload_drops_a_removed_members_held_message(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.ALICE, "ft13")
    card, _allow = flows.ask_write(inst, name, "ft13", G, fti.ALICE, "ft13")
    n = len(inst.prompts_seen(name))

    log_since = inst.log_mark()
    held = fake.inject_text(G, fti.user(fti.BOB), "/ft13 bob was here")
    flows.wait_reaction(fake, G, held["message_id"], "👀")
    inst.wait_log("[ft13] Held (", since=log_since)

    since = fake.mark()
    inst.rewrite_yaml(_without_bob)
    inst.sigusr1()
    inst.wait_log("SIGUSR1 received", since=log_since)
    flows.wait_text(fake, G, "Dropped: @bob is no longer allowed to send here.", since=since)
    flows.wait_reaction(fake, G, held["message_id"], "🤷")

    # Finish the open prompt: Deny.
    _msg, deny = fake.wait_button(G, "Deny", since=0, message_text_contains="ft13")
    cb = fake.inject_callback(fti.user(fti.ALICE), fake.message(G, card["message_id"]), deny)
    fake.wait_answer(cb)
    flows.wait_turn_end(inst, name, "ft13", since_log=log_since)
    assert not inst.file_in_project("ft13.txt").exists()
    assert not any("bob was here" in p for p in inst.prompts_seen(name)[n:])
