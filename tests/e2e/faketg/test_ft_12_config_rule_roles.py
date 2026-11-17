"""Scenario 12 (1.6.0 security): a Telegram turn that is not the owner's
may not change aipager's config, nor search the home folder through a
nested ``bash -c "... \\$HOME"``; the owner's same request runs.

The instance gives the ``user`` role Bash (``policy.yaml``: the built-in
``deny_tools`` minus Bash, as docs/groups.md shows), so bob (user) and
dave (admin, Bash through ``bypass_role_denies``) both have a shell and
the refusals come from the new command rules, not from a missing tool.
In the group, each of bob, dave and alice (owner) asks a fresh session
of their own to run:

- ``aipager config --help > <file>`` (the config-change rule);
- ``bash -c "grep -rln <word> \\$HOME > <file>"`` (the search rule now
  reads ``\\$HOME`` inside ``bash -c``).

bob's and dave's calls are refused by aipager's hook before they run:
the chat shows the safety notice with the rule's own reason, the session
is halted back to IDLE, and the file never appears. alice's calls are
not refused: she answers Claude Code's permission prompt (if one comes)
and the file is written.

Runs with real Claude or the stand-in (its "Bash command exactly" cue).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.fake_telegram.redaction import redact
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"
#: One fresh session per member: real Claude that saw a refusal in the
#: conversation may not try the same command again for the next member.
LABELS = {fti.BOB: "ft40", fti.DAVE: "ft41", fti.ALICE: "ft42"}
BLOCKED = "Blocked by safety policy:"
#: The safety notice's reason for each rule (safety_notice.REASON_MSGS).
REASON = {
    "config": f"{BLOCKED} Bash command blocked by safety policy (stopped)",
    "home": (f"{BLOCKED} Bash command blocked by safety policy: a recursive search or "
             "listing of the whole home folder or / is not allowed"),
}
#: What the Bash call must contain to be the form under test.
FORM = {
    "config": ("aipager config",),
    "home": ("bash -c", "\\$HOME"),
}


def _command(kind: str, out: str) -> str:
    if kind == "config":
        return f"aipager config --help > {out}"
    return f'bash -c "grep -rln zq7nothere \\$HOME > {out}"'


def _give_user_bash(inst) -> None:
    from aipager import safety
    deny = [t for t in safety.RESTRICTED_DENY_TOOLS if t != "Bash"]
    assert len(deny) == len(safety.RESTRICTED_DENY_TOOLS) - 1, deny
    (inst.home / ".config" / "aipager" / "policy.yaml").write_text(
        "roles:\n  user:\n    deny_tools: [" + ", ".join(deny) + "]\n")


def _bash_commands(inst, name: str) -> list[str]:
    """Every Bash command the session's Claude asked to run, in order."""
    if inst.claude_mode == "standin":
        return [e.get("command", "") for e in inst.standin_log(name)
                if e.get("event") == "bash_call"]
    path = inst._registry_transcript(name)
    out = []
    try:
        lines = Path(path).read_text(errors="replace").splitlines() if path else []
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = ((entry.get("message") or {}).get("content")
                   if isinstance(entry, dict) else None)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" \
                    and block.get("name") == "Bash":
                out.append(str((block.get("input") or {}).get("command", "")))
    return out


def _ask(inst, uid: int, name: str, kind: str, out: str) -> tuple[int, int, int]:
    """*uid* asks the session to run the *kind* command; returns the fake's
    mark, the log mark and how many Bash calls came before."""
    fake = inst.fake
    flows.settle(fake)
    before = len(_bash_commands(inst, name))
    since, log_since = fake.mark(), inst.log_mark()
    fake.inject_text(G, fti.user(uid),
                     f"{BOT} Run this Bash command exactly: `{_command(kind, out)}` "
                     "(one Bash call, the command exactly as written, nothing else; if it "
                     "is refused, stop). Then reply with exactly: DONE")
    return since, log_since, before


def _the_call(inst, name: str, kind: str, before: int) -> str:
    """The Bash call this request made (the form under test)."""
    def _found():
        cmds = _bash_commands(inst, name)[before:]
        return cmds[0] if cmds else None
    cmd = fti.wait_until(_found, 120, f"{name} to call Bash for {kind}",
                         lambda: inst.log_tail(25))
    missing = [part for part in FORM[kind] if part not in cmd]
    assert not missing, redact(f"Claude changed the command (no {missing}): {cmd!r}")
    return cmd


def _refused(inst, name: str, uid: int, kind: str) -> None:
    fake = inst.fake
    label = LABELS[uid]
    out = f"{label}-{kind}-{fti.MEMBERS[uid][0]}.txt"
    since, log_since, before = _ask(inst, uid, name, kind, out)
    _the_call(inst, name, kind, before)
    notice = flows.wait_text(fake, G, BLOCKED, since=since, timeout=120)
    assert REASON[kind] in notice.text, redact(notice.text)
    # The halt returns the session to IDLE itself (no "→ IDLE" line).
    inst.wait_log(f"[{label}] halted by safety policy", since=log_since, timeout=120)
    # Never ran, and never asked anyone to allow it.
    assert not inst.file_in_session(name, out).exists(), out
    asked = [c for c in fake.calls(chat_id=G, since=since)
             if c.method in flows.SEND_METHODS and "Allow" in json.dumps(
                 c.params.get("reply_markup") or {})]
    flows.assert_no_calls(asked, f"a permission prompt for {kind} from {uid}")


def _allowed(inst, name: str, kind: str) -> None:
    fake = inst.fake
    label = LABELS[fti.ALICE]
    out = f"{label}-{kind}-alice.txt"
    target = inst.file_in_session(name, out)
    since, log_since, before = _ask(inst, fti.ALICE, name, kind, out)
    _the_call(inst, name, kind, before)
    deadline = time.monotonic() + 180
    tapped: set[tuple[int, str]] = set()
    while not target.exists():
        assert time.monotonic() < deadline, redact(
            f"alice's {kind} command never ran\n{inst.log_tail(30)}")
        blocked = [c for c in fake.calls(chat_id=G, since=since)
                   if c.method in flows.SEND_METHODS and BLOCKED in c.text]
        flows.assert_no_calls(blocked, f"the owner's {kind} command was refused")
        found = fake.find_button(G, "Allow", since=since)
        if found is not None and (found[0]["message_id"], found[1]) not in tapped:
            msg, data = found
            tapped.add((msg["message_id"], data))
            fake.inject_callback(fti.user(fti.ALICE), msg, data)
        time.sleep(0.5)
    flows.wait_turn_end(inst, name, label, since_log=log_since, timeout=120)
    blocked = [c for c in fake.calls(chat_id=G, since=since)
               if c.method in flows.SEND_METHODS and BLOCKED in c.text]
    flows.assert_no_calls(blocked, f"the owner's {kind} command was refused")


@pytest.mark.parametrize("kind", ["config", "home"])
def test_non_owner_config_and_home_search_refused_owner_allowed(own_faketg, kind):
    inst = own_faketg({}, before_start=_give_user_bash)
    for uid in (fti.BOB, fti.DAVE, fti.ALICE):
        name = inst.new_session(G, fti.ALICE, LABELS[uid])
        if uid == fti.ALICE:
            _allowed(inst, name, kind)
        else:
            _refused(inst, name, uid, kind)
        inst.kill_session(name, LABELS[uid])
    # The policy file was the one the hook read: bob's refusal named the
    # command rule, not "Bash is in this role's deny_tools".
    assert not any("deny_tools" in c.text for c in inst.fake.calls(chat_id=G)
                   if c.method in flows.SEND_METHODS)
