"""Scenario 1 (intake, 8.84/8.85/8.91f) and scenario 11 (noise, 8.91a/e).

In a group the bot hears only what is addressed to it: a command, a
reply to one of its messages, a mention, or a MARKED keyboard word.
Everything else is chatter and gets no call and no injection. Every
negative check is followed by a positive control (a mention that must
arrive) and a quiet window, so "nothing happened" is never just "not
yet".
"""

from __future__ import annotations

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"


def _ignored(inst, uid: int, text: str, name: str, word: str, *,
             forbidden_text: str | None = None, **kw) -> dict:
    """Inject *text* from *uid*; assert the daemon did nothing with it:
    no call names the message (reaction, reply) or its words, nothing
    reached Claude, within a quiet window closed by a positive control.
    The daemon's own ticks (the pinned bar, a card's last edit) may run
    meanwhile; they never touch this message."""
    fake = inst.fake
    flows.settle(fake)
    since = fake.mark()
    n_before = len(inst.prompts_seen(name))
    msg = fake.inject_text(G, fti.user(uid), text, **kw)
    window = flows.quiet(fake, 3.0, since=since)
    assert flows.calls_about(fake, since, message_id=msg["message_id"]) == []
    assert flows.calls_about(fake, since, text=text) == []
    if forbidden_text:
        assert not [c for c in window if forbidden_text in c.text], forbidden_text
    flows.control(inst, G, fti.ALICE, name, word)
    assert not any(text in p for p in inst.prompts_seen(name)[n_before:])
    assert flows.calls_about(fake, since, message_id=msg["message_id"]) == []
    assert fake.reactions(G, msg["message_id"]) == []
    return msg


def test_intake_routes_only_what_is_addressed(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.ALICE, "ft1")

    _ignored(inst, fti.ALICE, "just chatting about lunch", name, "ping1")

    prompt = flows.prompt_turn(inst, G, fti.ALICE, name, f"{BOT} hi there mention",
                               "hi there mention")
    assert BOT not in prompt, "the mention was not stripped"
    assert prompt.splitlines()[-1].strip() == "hi there mention"

    # A reply to one of the bot's messages in the group.
    bot_msgs = fake.visible_messages(G)
    assert bot_msgs, "the bot has sent nothing in the group"
    flows.prompt_turn(inst, G, fti.ALICE, name, "reply to the bot r1", "reply to the bot r1",
                      reply_to=bot_msgs[-1])

    prompt = flows.prompt_turn(inst, G, fti.ALICE, name, f"/ft1@{fti.BOT_USERNAME} do it now",
                               "do it now")
    assert "/ft1" not in prompt

    # A keyboard word typed without the marker is chatter ...
    _ignored(inst, fti.ALICE, "status", name, "ping2", forbidden_text="Sessions (")
    # ... the marked form is a tap.
    since = fake.mark()
    fake.inject_text(G, fti.user(fti.ALICE), "▫️ status")
    flows.wait_text(fake, G, "Sessions (", since=since)

    # An edited message is ignored everywhere.
    n = len(inst.prompts_seen(name))
    log_since = inst.log_mark()
    first = fake.inject_text(G, fti.user(fti.ALICE), f"{BOT} before edit e1")
    flows.wait_prompt(inst, name, "before edit e1", after=n)
    flows.wait_turn_end(inst, name, "ft1", since_log=log_since)
    flows.settle(fake)
    since = fake.mark()
    n = len(inst.prompts_seen(name))
    fake.inject_edited(G, fti.user(fti.ALICE), first["message_id"], f"{BOT} after edit e2")
    flows.quiet(fake, 3.0, since=since)
    assert flows.calls_about(fake, since, text="after edit e2") == []
    flows.control(inst, G, fti.ALICE, name, "ping3")
    assert not any("after edit e2" in p for p in inst.prompts_seen(name)[n:])


def test_noise_read_only_told_once_and_anonymous_told_once(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.ALICE, "ft2")

    since = fake.mark()
    first = fake.inject_text(G, fti.user(fti.CAROL), f"{BOT} carol one")
    told = flows.wait_text(fake, G, "your role is read_only", since=since)
    assert "@carol" in told.text
    assert told.params.get("reply_to_message_id") == first["message_id"] or \
        (told.params.get("reply_parameters") or {}).get("message_id") == first["message_id"]

    # Her second refused message gets no reply at all.
    _ignored(inst, fti.CAROL, f"{BOT} carol two", name, "ping4")

    # An anonymous admin: "Post as yourself" once, then silence.
    sender_chat = {"id": G, "type": "group", "title": fti.GROUP_TITLE}
    since = fake.mark()
    fake.inject_sender_chat(G, f"{BOT} anon one", sender_chat)
    flows.wait_text(fake, G, "Post as yourself to use the bot.", since=since)
    flows.settle(fake)
    since = fake.mark()
    second = fake.inject_sender_chat(G, f"{BOT} anon two", sender_chat)
    flows.quiet(fake, 3.0, since=since)
    assert flows.calls_about(fake, since, message_id=second["message_id"]) == []
    flows.control(inst, G, fti.ALICE, name, "ping5")
    assert len([t for t in flows.chat_texts(fake, G, 0)
                if "Post as yourself" in t]) == 1
    assert not any("anon" in p or "carol" in p for p in inst.prompts_seen(name))
