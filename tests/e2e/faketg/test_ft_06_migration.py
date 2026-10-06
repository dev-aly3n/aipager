"""Scenario 10 (supergroup migration, 8.87): Telegram upgrades the test
group; the instance's aipager.yaml, its sessions and the chat follow,
and the notice goes to the new chat."""

from __future__ import annotations

import json

import yaml

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G, SG = fti.GROUP_ID, fti.SUPERGROUP_ID
NOTICE = "This group was upgraded by Telegram. aipager moved with it, nothing to do."


def _registry_chat(inst, name):
    path = inst.home / ".claude" / "aipager-sessions.json"
    try:
        return json.loads(path.read_text())["sessions"][name].get("scope_chat_id")
    except (OSError, ValueError, KeyError):
        return None


def test_group_upgrade_is_followed(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.ALICE, "ft14")
    flows.settle(fake)
    since = fake.mark()
    fake.inject_migration(G, SG, by=fti.user(fti.ALICE))

    flows.wait_text(fake, SG, NOTICE, since=since, timeout=60)
    cfg = yaml.safe_load(inst.yaml_path.read_text())
    group = [s for s in cfg["scopes"] if s["kind"] == "group"]
    assert [s["chat_id"] for s in group] == [SG]
    assert cfg.get("chat_migrations") in ({G: SG}, {str(G): SG})
    fti.wait_until(lambda: _registry_chat(inst, name) == SG, 30,
                   "the session registry to follow the move")
    assert not [c for c in fake.calls(chat_id=SG, since=since) if c.status != 200]

    # The session answers in the new chat.
    n = len(inst.prompts_seen(name))
    fake.inject_text(SG, fti.user(fti.ALICE), f"@{fti.BOT_USERNAME} after the move")
    flows.wait_prompt(inst, name, "after the move", after=n)
