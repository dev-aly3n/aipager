"""Scenario 2 (roles and buttons, 8.75/8.83/8.78) and scenario 3 (slash
rule, 8.74).

A permission prompt in Ask mode shows Allow/Deny in the group; a
read_only member's tap is refused and decides nothing, a user's tap
decides and is attributed in the group. Switching to Auto needs an admin.
A button of a DM session tapped from the group is refused. A user may
not send a project slash command; an admin may.
"""

from __future__ import annotations

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
DM = fti.DM_ID
BOT = f"@{fti.BOT_USERNAME}"
REFUSED = "Your role can't do that here."
OTHER_CHAT = "This button belongs to another chat."


def _decisions(inst, name):
    return [e for e in inst.standin_log(name) if e.get("event") == "decision"] \
        if inst.claude_mode == "standin" else []


def test_roles_and_buttons(fresh):
    inst, fake = fresh, fresh.fake
    since_new = fake.mark()
    name = inst.new_session(G, fti.BOB, "ft3")
    ready, _cb = fake.wait_button(G, "Switch to Auto", since=since_new)
    dm_name = inst.new_session(DM, fti.ALICE, "ft4")

    # Bob asks for a file write: the prompt shows Allow/Deny in the group,
    # on the turn's busy card (the stand-in asks once that card exists).
    since = fake.mark()
    turn_log = inst.log_mark()
    card, allow = flows.ask_write(inst, name, "ft3", G, fti.BOB, "ft3")
    target = inst.file_in_project("ft3.txt")

    # carol (read_only) taps Allow: refused, nothing reaches Claude.
    cb = fake.inject_callback(fti.user(fti.CAROL), card, allow)
    assert flows.wait_toast(fake, cb) == REFUSED
    flows.settle(fake)
    assert not target.exists()
    assert _decisions(inst, name) == []

    # bob (user) taps Allow: decided, and the group is told who allowed it.
    since = fake.mark()
    cb = fake.inject_callback(fti.user(fti.BOB), card, allow)
    fake.wait_answer(cb)
    flows.wait_text(fake, G, "Allowed by @bob", since=since, timeout=60)
    fti.wait_until(target.exists, 60, "the file bob allowed")
    if inst.claude_mode == "standin":
        assert [d["decision"] for d in _decisions(inst, name)] == ["allowed"]
    flows.wait_turn_end(inst, name, "ft3", since_log=turn_log)
    flows.settle(fake)

    # Switching to Auto: refused for bob (user), done for dave (admin).
    _msg, auto = fake.wait_button(G, "Switch to Auto", since=since_new)
    ready = fake.message(G, ready["message_id"])
    cb = fake.inject_callback(fti.user(fti.BOB), ready, auto)
    assert flows.wait_toast(fake, cb) in ("Auto mode needs an admin.", REFUSED)
    starts = [e for e in inst.standin_log(name) if e.get("event") == "start"]
    since = fake.mark()
    cb = fake.inject_callback(fti.user(fti.DAVE), ready, auto)
    fake.wait_answer(cb)
    flows.wait_text(fake, G, "Switched to 🤖 Auto by @dave", since=since, timeout=90)
    if inst.claude_mode == "standin":
        fti.wait_until(lambda: len([e for e in inst.standin_log(name)
                                    if e.get("event") == "start"]) > len(starts),
                       60, "the session to restart in Auto")
        assert inst.standin_log(name)[-1].get("skip_perms") is True or any(
            e.get("event") == "start" and e.get("skip_perms")
            for e in inst.standin_log(name)[len(starts):])

    # A DM session's button tapped from the group is refused.
    flows.settle(fake)
    group_msg = fake.visible_messages(G)[-1]
    n_dm = len(inst.prompts_seen(dm_name))
    cb = fake.inject_callback(fti.user(fti.ALICE), group_msg, f"{dm_name}:rdy_auto")
    assert flows.wait_toast(fake, cb) == OTHER_CHAT
    assert len(inst.prompts_seen(dm_name)) == n_dm


def test_slash_rule_user_refused_admin_allowed(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.BOB, "ft5")

    since = fake.mark()
    n = len(inst.prompts_seen(name))
    msg = fake.inject_text(G, fti.user(fti.BOB), "/ft5 /ftcmd")
    flows.wait_text(fake, G, "That command needs an admin.", since=since)
    flows.wait_reaction(fake, G, msg["message_id"], "🤷")
    flows.control(inst, G, fti.BOB, name, "ping6")
    assert not any("/ftcmd" in p for p in inst.prompts_seen(name)[n:])

    prompt = flows.prompt_turn(inst, G, fti.DAVE, name, "/ft5 /ftcmd", "/ftcmd")
    assert prompt.strip().startswith("/ftcmd")

