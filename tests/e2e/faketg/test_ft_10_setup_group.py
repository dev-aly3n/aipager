"""Scenario 15 (1.5, ``aipager setup group``): the instance's own CLI sets
up a second group with no prompts.

The command writes the instance's aipager.yaml, its read-only Telegram
checks reach the fake Bot API, and the live reload reaches the TEST
daemon (its log says ``SIGUSR1 received``), never the operator's: an
isolated instance only signals a daemon of its own instance. The new
members are then known in the new group with their roles (``/whoami``).
Running it again changes nothing, and a role change needs ``--force``.
"""

from __future__ import annotations

import json
import subprocess

import yaml

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

SG = fti.SUPERGROUP_ID
TITLE = "second team"
MEMBERS = [f"{fti.ALICE}:alice:owner", f"{fti.BOB}:bob:admin", f"{fti.CAROL}:carol:user"]


def _setup_group(inst, *args: str) -> tuple[int, dict]:
    """``aipager setup group ... --json`` through the instance's own shim,
    in the instance's environment (its HOME, its instance folder, the fake
    Bot API), without the user bus."""
    # No user bus: even if the instance check in the daemon pick broke,
    # ``systemctl --user`` could not hand this run the operator's daemon.
    env = {k: v for k, v in inst.env.items() if k != "DBUS_SESSION_BUS_ADDRESS"}
    p = subprocess.run(
        [str(inst.bin_dir / "aipager"), "setup", "group", *args, "--json"],
        env=env, cwd=str(inst.project), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120)
    assert p.stdout.strip(), f"no JSON (rc={p.returncode}): {p.stderr[-600:]!r}"
    return p.returncode, json.loads(p.stdout)


def _member_args(members: list[str]) -> list[str]:
    return [a for m in members for a in ("--member", m)]


def _group_scope(inst) -> dict | None:
    cfg = yaml.safe_load(inst.yaml_path.read_text())
    return next((s for s in cfg["scopes"] if s["chat_id"] == SG), None)


def test_setup_group_adds_a_group_live(fresh):
    inst, fake = fresh, fresh.fake
    fake.add_chat(SG, "supergroup", TITLE)
    assert _group_scope(inst) is None
    args = ["--chat-id", str(SG), "--label", TITLE, *_member_args(MEMBERS)]

    log_since = inst.log_mark()
    rc, doc = _setup_group(inst, *args)
    assert rc == 0, doc
    assert doc["ok"] is True and doc["status"] == "created", doc
    assert doc["daemon"]["outcome"] == "reloaded_live", doc["daemon"]
    # The read-only checks reached the fake: an admin bot that may pin.
    assert doc["telegram"]["checked"] is True, doc["telegram"]
    assert doc["telegram"]["chat_type"] == "supergroup", doc["telegram"]
    assert doc["telegram"]["bot_status"] == "administrator", doc["telegram"]
    assert doc["telegram"]["bot_can_pin"] is True, doc["telegram"]
    assert fake.calls("getChatMember"), "the bot check never reached the fake"
    # The reload reached THIS daemon (the operator's is never signalled).
    inst.wait_log("SIGUSR1 received", since=log_since, timeout=30)

    scope = _group_scope(inst)
    assert scope is not None and scope["kind"] == "group", scope
    assert scope["label"] == TITLE, scope
    assert {(m["id"], m["role"]) for m in scope["members"]} == {
        (fti.ALICE, "owner"), (fti.BOB, "admin"), (fti.CAROL, "user")}, scope

    # The running daemon knows the new members, with their roles, in the
    # new group: no restart.
    since = fake.mark()
    fake.inject_text(SG, fti.user(fti.BOB), "/whoami")
    flows.wait_text(fake, SG, "role: admin", since=since, timeout=60)
    since = fake.mark()
    fake.inject_text(SG, fti.user(fti.CAROL), "/whoami")
    flows.wait_text(fake, SG, "role: user", since=since, timeout=60)

    # The same command again changes nothing and exits 0.
    before = inst.yaml_path.read_bytes()
    rc, doc = _setup_group(inst, *args)
    assert rc == 0, doc
    assert doc["status"] == "unchanged" and doc["changed"] == [], doc
    assert doc["daemon"]["outcome"] == "not_needed", doc["daemon"]
    assert inst.yaml_path.read_bytes() == before

    # Changing a member's role needs --force; without it nothing is written.
    promoted = [f"{fti.ALICE}:alice:owner", f"{fti.BOB}:bob:admin",
                f"{fti.CAROL}:carol:admin"]
    rc, doc = _setup_group(inst, "--chat-id", str(SG), "--label", TITLE,
                           *_member_args(promoted))
    assert rc != 0 and doc["ok"] is False, doc
    assert doc["error"] == "needs_force", doc
    assert inst.yaml_path.read_bytes() == before

    log_since = inst.log_mark()
    rc, doc = _setup_group(inst, "--chat-id", str(SG), "--label", TITLE,
                           *_member_args(promoted), "--force")
    assert rc == 0 and doc["status"] == "updated", doc
    assert doc["daemon"]["outcome"] == "reloaded_live", doc["daemon"]
    inst.wait_log("SIGUSR1 received", since=log_since, timeout=30)
    since = fake.mark()
    fake.inject_text(SG, fti.user(fti.CAROL), "/whoami")
    flows.wait_text(fake, SG, "role: admin", since=since, timeout=60)


def test_setup_group_warns_when_the_bot_cannot_pin(fresh):
    """The bot is only a member of the group: the group is still set up,
    and the JSON names the step only the person can do."""
    inst, fake = fresh, fresh.fake
    gid = SG - 1
    fake.add_chat(gid, "supergroup", "third team")
    fake.set_bot_member(gid, "member")
    rc, doc = _setup_group(inst, "--chat-id", str(gid), "--label", "third team",
                           "--member", f"{fti.ALICE}:alice:owner")
    assert rc == 0 and doc["status"] == "created", doc
    assert doc["telegram"]["bot_status"] == "member", doc["telegram"]
    assert "bot_not_admin" in {w["code"] for w in doc["warnings"]}, doc["warnings"]
    assert doc["next_step"], doc
