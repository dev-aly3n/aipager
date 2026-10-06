"""Scenarios 4 (per-person targets and Which session, 8.90/8.94i/8.91g),
5 (capture flows, 8.86) and 6 (confirm-card ownership and attribution,
8.91c/8.94j)."""

from __future__ import annotations

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"


def test_targets_are_per_person_and_which_session(fresh):
    inst, fake = fresh, fresh.fake
    n6 = inst.new_session(G, fti.ALICE, "ft6")
    n7 = inst.new_session(G, fti.BOB, "ft7")

    # Each person's plain mention goes to their own session.
    a, b = len(inst.prompts_seen(n6)), len(inst.prompts_seen(n7))
    fake.inject_text(G, fti.user(fti.ALICE), f"{BOT} alpha one")
    flows.wait_prompt(inst, n6, "alpha one", after=a)
    fake.inject_text(G, fti.user(fti.BOB), f"{BOT} bravo one")
    flows.wait_prompt(inst, n7, "bravo one", after=b)
    assert not any("alpha one" in p for p in inst.prompts_seen(n7))
    assert not any("bravo one" in p for p in inst.prompts_seen(n6))

    # dave has no target and the chat has two sessions: Which session?
    flows.settle(fake)
    since = fake.mark()
    a, b = len(inst.prompts_seen(n6)), len(inst.prompts_seen(n7))
    fake.inject_text(G, fti.user(fti.DAVE), f"{BOT} delta held")
    flows.wait_text(fake, G, "Which session?", since=since)
    card, data = fake.wait_button(G, "ft6", since=since, message_text_contains="Which session?")
    assert len(inst.prompts_seen(n6)) == a and len(inst.prompts_seen(n7)) == b
    cb = fake.inject_callback(fti.user(fti.DAVE), card, data)
    fake.wait_answer(cb)
    flows.wait_prompt(inst, n6, "delta held", after=a)
    flows.wait_text(fake, G, "Sent to ft6.", since=since)
    assert not any("delta held" in p for p in inst.prompts_seen(n7))

    # bob's own session ends: his next message asks instead of going anywhere.
    inst.kill_session(n7, "ft7")
    flows.settle(fake)
    since = fake.mark()
    a = len(inst.prompts_seen(n6))
    fake.inject_text(G, fti.user(fti.BOB), f"{BOT} bravo two")
    flows.wait_text(fake, G, "ft7 has ended. Which session?", since=since)
    flows.control(inst, G, fti.ALICE, n6, "ping7")
    assert not any("bravo two" in p for p in inst.prompts_seen(n6)[a:])


def _sent_with(fake, since, text):
    return fake.wait_for(lambda c: c.method in ("sendMessage", "sendRichMessage")
                         and c.chat_id == G and text in c.text and c.status == 200,
                         since=since, what=repr(text))


def test_capture_cards_answer_only_their_owner(fresh):
    inst, fake = fresh, fresh.fake
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.ALICE), "/new")
    alice_card = _sent_with(fake, since, "New session (@alice)").response["result"]
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.BOB), "/new")
    _sent_with(fake, since, "New session (@bob)")

    # bob answers alice's card: told whose it is, nothing created.
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.BOB), "ftbob", reply_to=alice_card)
    flows.wait_text(fake, G, "This card is @alice's. Send /new for your own.", since=since)
    assert not (inst.inst_dir / "claude-dtach-ftbob__g4000000001.sock").exists()

    # alice answers her own card: her session starts.
    log_since = inst.log_mark()
    fake.inject_text(G, fti.user(fti.ALICE), "ft8", reply_to=alice_card)
    name = inst.wait_session(G, "ft8", since=log_since, by=fti.ALICE)

    # A rename question takes only its asker's reply.
    flows.settle(fake)
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.ALICE), "/rename ft8")
    question = _sent_with(fake, since, "New name for [ft8]?").response["result"]
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.BOB), "ftbobname", reply_to=question)
    window = flows.quiet(fake, 3.0, since=since)
    assert not [c for c in window if "renamed to" in c.text]
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.ALICE), "ft8r", reply_to=question)
    flows.wait_text(fake, G, "[ft8] renamed to [ft8r].", since=since)
    assert inst.socket_for(name).exists()


def test_confirm_card_answers_its_owner_and_names_who(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.BOB, "ft9")
    flows.settle(fake)
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.BOB), "/kill ft9")
    card, end = fake.wait_button(G, "End", since=since, message_text_contains="End ft9?")

    cb = fake.inject_callback(fti.user(fti.ALICE), card, end)
    assert flows.wait_toast(fake, cb) == "This is @bob's card. Send /kill ft9 for your own."
    assert inst.socket_for(name).exists()

    log_since = inst.log_mark()
    cb = fake.inject_callback(fti.user(fti.BOB), card, end)
    assert flows.wait_toast(fake, cb, timeout=60) == "⏹ Ended ft9 by @bob"
    inst.wait_log("Killed dtach PID", name, since=log_since)
    fti.wait_until(lambda: not inst.socket_for(name).exists(), 30, "ft9's socket to go")
