"""False "halted by safety policy" blocks (roadmap 8.48 + 8.49).

8.48 — the built-in ``\\bclaude\\b`` Bash deny pattern matched a session's
own scratchpad, ``/tmp/claude-<uid>/<project>/<session>/scratchpad``,
which Claude Code gives every session. Three harmless commands were
halted on a friend's box (a ``cat``, a ``curl -o``, an ``ssh``), and one on
the operator's (a capped pytest run from the scratchpad).

8.49 — the ``UserPromptSubmit`` hook rebuilt the canonical policy
snapshot on EVERY prompt event, and with no Telegram note to attribute
the prompt to it wrote the floor (``bypass_safety`` False). Claude Code
fires that hook when a message is merely QUEUED behind a running turn
(measured on the operator's box 2026-09-26 12:00:55 and 12:01:00: two
terminal-typed messages, ``queue-operation enqueue`` at the same instant,
later ``remove reason=absorbed_mid_turn``), so an owner's running
Telegram turn was silently demoted to the floor for every tool call
after it.

Both halves are exercised through the real functions:
``safety.bash_violation`` with the shipped pattern list, and
``notify_hook._match_and_promote`` feeding ``enforce.decide``.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import policy_snapshot as ps
from aipager import safety
from aipager.dtach import enforce, notify_hook
from aipager.policy import Role
from aipager.safety import DENY_BASH_PATTERNS, bash_violation

SCRATCH = "/tmp/claude-1001/-workspace-hospital-accounting/5b1d7363-aaaa/scratchpad"
OWN_SCRATCH = "/tmp/claude-1000/-home-aly-aipager/10244463-bbbb/scratchpad"


# ===========================================================================
# 8.48 — the Bash deny patterns
# ===========================================================================

# Commands that must run. The first three are the commands halted on the
# friend's box (scratchpad path replaced with a fake one of the same
# shape); the fourth is the operator's own.
ALLOWED = [
    f'cd {SCRATCH}; file "/tmp/aipager-files/1790367657_x"; cat "/tmp/aipager-files/1790367657_x"',
    f'cd {SCRATCH}/omana; curl -s -m 120 -o t3m14.pdf "https://example.org/a/t3m14.pdf"',
    f'cd {SCRATCH}/km && ssh box "ha-api admin GET /posts/100501" | python3 - <<\'EOF\'\nprint(1)\nEOF',
    f"S={OWN_SCRATCH}; systemd-run --user --scope -q -p MemoryMax=2G .venv/bin/python -m pytest -q > $S/out.txt",
    "ls /tmp/claude-1000",
    "ls /tmp/claude-1000/",
    f'cat "{OWN_SCRATCH}/notes.md"',
    f"cat '{OWN_SCRATCH}/a b.txt'",
    f"(cd {OWN_SCRATCH} && make)",
    f"tar czf {OWN_SCRATCH}.tgz x",
    # The `--resume` flag pattern no longer matches a different, longer flag.
    "node tool.js --resume-from 3",
    "ls -la && npm test",
]

BLOCKED_BINARY = [
    "claude",
    "claude -p hi",
    "claude --version",
    "/usr/bin/claude -p hi",
    "~/.local/bin/claude -p hi",
    "$HOME/.local/bin/claude",
    "~/.local/share/claude/versions/2.1.283 -p hi",
    "env claude -p hi",
    "env -i claude",
    "$(which claude) -p hi",
    "`which claude` -p hi",
    "ls; claude -p hi",
    "true && claude -p hi",
    "echo hi | claude -p",
    "(claude -p hi)",
    "\\claude -p hi",
    "exec claude",
    "timeout 5 claude -p hi",
    "xargs claude < f",
    'bash -c "claude -p hi"',
    "sh -c 'claude'",
    "nohup claude &",
    # The binary launched from INSIDE the scratchpad: the root is exempt,
    # the second, separate `claude` token is not.
    f"cd {OWN_SCRATCH} && claude -p hi",
    "cd /tmp/claude-1000; claude",
    f"{OWN_SCRATCH}/x; claude",
    "npx @anthropic-ai/claude-code -p hi",
    "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js -p hi",
]

BLOCKED_CONFIG = [
    "cat ~/.claude/settings.json",
    "cat ~/.claude/.credentials.json",
    "ls ~/.claude",
    "cat $HOME/.claude/settings.json",
    "cat ${HOME}/.claude/settings.json",
    "cat /home/aly/.claude/settings.json",
    "cat /home/mohamad/.claude/projects/x.jsonl",
    "cat .claude/settings.local.json",
    "cd .claude && cat settings.json",
    "cat ~/.claude.json",
    "cp -r ~/.claude /tmp/x",
    "rm -rf ~/.claude",
    # Traversal out of the (exempt) scratchpad root into the config dir.
    "cat /tmp/claude-1000/../../home/aly/.claude/x",
    f"cat {OWN_SCRATCH}/../../../../../home/aly/.claude/settings.json",
    f"cd {OWN_SCRATCH} && cat ../../../../home/aly/.claude/.credentials.json",
]

# aipager's own control files under /tmp share the `claude-` prefix; the
# policy snapshot is what grants bypass, the dtach sockets drive other
# sessions. They stay blocked, including via traversal out of the root.
BLOCKED_AIPAGER_TMP = [
    "cat /tmp/claude-policy-claude-x.json",
    "echo '{}' > /tmp/claude-policy-claude-x.json",
    "ls /tmp/claude-notes-claude-x/",
    "cat /tmp/claude-status-claude-x.json",
    "cat /tmp/claude-reply-claude-x.txt",
    "dtach -a /tmp/claude-dtach-x.sock",
    "cat /tmp/claude-1000/../claude-policy-claude-x.json",
    f"cp x {OWN_SCRATCH}/../../../../claude-policy-claude-x.json",
    "cd /tmp && cat claude-policy-claude-x.json",
]

# Only the exact root `/tmp/claude-<digits>` followed by `/` or the end
# of the word is exempt. Globs, other tmp dirs and look-alikes stay
# blocked (fail closed).
BLOCKED_ROOT_LOOKALIKES = [
    "ls /tmp/claude-1000*",
    "ls /tmp/claude-1*/",
    "ls /tmp/claude-?000/",
    "ls /tmp/claude-[0-9]*/",
    "ls /tmp/claude-1000x/",
    "ls /tmp/claude-1000.d/",
    "ls /tmp/claude-/",
    "ls /var/tmp/claude-1000/",
    "ls $TMPDIR/claude-1000/",
    "ls tmp/claude-1000/",
    "ls /tmp//claude-1000/",
    "cd /tmp && ls claude-1000/",
]

# Judgment calls, kept BLOCKED (see the report / docs/security.md):
# mentions and file names containing `claude`. The pattern that catches
# them is the same one that catches the binary and aipager's /tmp
# control files, so there is no context-free way to allow the mention
# without also allowing `cd /tmp && cat claude-policy-...`, and the SDK
# packages launch the claude binary.
BLOCKED_JUDGMENT = [
    'git log --grep="claude"',
    "grep -rn claude src/",
    "cat notes/claude-notes.md",
    "cat docs/claude-code-docs.md",
    "pip show claude-agent-sdk",
    "npm ls @anthropic-ai/claude-agent-sdk",
    # A flag inside a quoted string can still reach a subprocess claude.
    "python3 -c \"import subprocess; subprocess.run(['x', '--system-prompt', 'y'])\"",
    "python3 -c \"a = ['--append-system-prompt', 'y']\"",
]

BLOCKED_FLAGS = [
    "x --resume abc",
    "x --resume",
    "x --resume=abc",
    'x "--resume" abc',
    "x --resume;",
    "x --system-prompt y",
    "x --system-prompt-file f",
    "x --append-system-prompt y",
    "x --append-system-prompt-file f",
    "x --dangerously-skip-permissions",
    "x --mcp-config c.json",
]

BLOCKED_OTHER = [
    "sudo ls",
    "su -",
    "aipager service restart",
    "systemctl --user restart aipager",
    "cat ~/.config/aipager/aipager.yaml",
    "cat ~/.local/share/aipager/x",
    "cat ~/.local/state/aipager/y",
    "rm -rf ~/.config/aipager",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_scratchpad_and_ordinary_commands_are_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


@pytest.mark.parametrize(
    "cmd",
    BLOCKED_BINARY + BLOCKED_CONFIG + BLOCKED_AIPAGER_TMP
    + BLOCKED_ROOT_LOOKALIKES + BLOCKED_JUDGMENT + BLOCKED_FLAGS + BLOCKED_OTHER,
)
def test_must_still_be_blocked(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def test_the_four_reported_commands_are_blocked_by_the_old_pattern_alone():
    """The reproduction, pinned: the old pattern, applied to the raw
    command, is exactly what halted each of these (and still would,
    without the scratchpad normalisation)."""
    import re
    for cmd in ALLOWED[:4]:
        assert re.search(r"\bclaude\b", cmd)
        assert re.search(r"\bclaude\b", safety.normalize_scratchpad(cmd)) is None


def test_operator_added_patterns_still_see_the_raw_command():
    """The scratchpad exemption applies to the BUILT-IN patterns only. A
    pattern an operator added in policy.yaml (unioned into the snapshot
    alongside the built-ins) keeps matching the command exactly as typed,
    so a rule written to block the scratchpad still does."""
    custom = (r"claude-\d+",)
    assert bash_violation(f"cat {OWN_SCRATCH}/x", DENY_BASH_PATTERNS + custom) is not None
    assert bash_violation(f"cat {OWN_SCRATCH}/x", custom) is not None


def test_scratchpad_exemption_does_not_hide_a_second_token():
    """Normalisation replaces only the root itself, so every other
    `claude` in the same command is still seen."""
    assert bash_violation(f"ls {OWN_SCRATCH} claude", DENY_BASH_PATTERNS) is not None
    assert bash_violation(
        f"ls {OWN_SCRATCH}/claude/x", DENY_BASH_PATTERNS) is not None


# ===========================================================================
# 8.49 — the snapshot a no-note UserPromptSubmit leaves behind
# ===========================================================================

SESSION = "claude-osta__d1"
OWNER = Role(name="owner", bypass_safety=True, bypass_role_denies=True)
USER = Role(name="user", deny_tools=("WebFetch",),
            deny_bash_patterns=(r"\bcurl\b",))

# A command only the owner may run, regardless of the 8.48 fix: it reads
# the config dir, which the floor denies.
PRIVILEGED = "cat ~/.claude/settings.json"


@pytest.fixture(autouse=True)
def _isolate_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")


_seq = {"n": 0}


def _send(role, text, *, label="owner", sender=(1, 1), style="", reply=""):
    """What ``session_ops._inject_prompt`` does for one Telegram message:
    write its note (with the marker-prefixed body) and return the body
    Claude Code will hand the UserPromptSubmit hook."""
    body = f"[via Telegram · @{label}]\n{text}"
    path = ps.write_note(
        SESSION, role, None, None, msg_id=None, chat_id=1,
        sender_key=sender, body=body, raw_text=text,
        style_text=style, reply_context=reply,
    )
    _seq["n"] += 1
    data = json.loads(path.read_text())
    data["queued_at"] = time.time() + _seq["n"]
    path.write_text(json.dumps(data))
    return body


def _transcript(tmp_path, governing_prompt: str) -> str:
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(
        {"type": "user", "message": {"content": governing_prompt}}) + "\n")
    return str(p)


def _decide(tmp_path, governing_prompt, command):
    return enforce.decide({
        "hook_event_name": "PreToolUse",
        "session": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": _transcript(tmp_path, governing_prompt),
    })


def _snap():
    return ps.read_snapshot(SESSION)


def _fresh_floor(snap) -> bool:
    """The floor, written for a turn the hook started with no Telegram
    message behind it: the floor plus the turn's origin (roadmap 8.77)."""
    rest = dict(snap)
    return rest.pop("turn_origin") == "terminal" and rest == ps.FLOOR_SNAPSHOT


def _same_rules(snap, expected) -> bool:
    """Same safety rules, order of the lists aside."""
    return all(
        (sorted(snap.get(k) or []) == sorted(expected.get(k) or []))
        if isinstance(expected.get(k), list)
        else snap.get(k) == expected.get(k)
        for k in ("bypass_safety", "confine_writes", "deny_tools",
                  "allow_tools", "deny_paths_no_access",
                  "deny_paths_no_write", "deny_bash_patterns"))


def test_repro_owner_turn_keeps_bypass_after_a_queued_terminal_message(tmp_path):
    """The operator's own incident (2026-09-26 12:00:48 → 12:01:45),
    replayed: the owner's Telegram prompt is picked up (bypass), then a
    message typed in the terminal while that turn runs fires
    UserPromptSubmit at enqueue with no note. The owner's turn — still
    governed by the owner's prompt — must keep its bypass."""
    owner_prompt = _send(OWNER, "check this: /tmp/aipager-files/1_photo.jpg")
    consumed, _ = notify_hook._match_and_promote(SESSION, owner_prompt)
    assert len(consumed) == 1
    assert _snap()["bypass_safety"] is True
    assert _decide(tmp_path, owner_prompt, PRIVILEGED) is None
    assert _decide(tmp_path, owner_prompt, f"cd {OWN_SCRATCH} && ls") is None

    notify_hook._match_and_promote(SESSION, "hmm I dont know")        # 12:00:55
    notify_hook._match_and_promote(SESSION, "so we are good you think?")  # 12:01:00

    assert _snap()["bypass_safety"] is True
    assert _decide(tmp_path, owner_prompt, PRIVILEGED) is None
    assert _decide(tmp_path, owner_prompt, f"cd {OWN_SCRATCH} && ls") is None


def test_repro_post_compact_redelivery_of_an_already_merged_message(tmp_path):
    """The same Telegram message firing the hook a second time (after an
    auto-compact) finds its note already consumed. It was merged into the
    snapshot the first time, so the snapshot stands."""
    owner_prompt = _send(OWNER, "fix post 100501")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    assert _snap()["bypass_safety"] is True

    notify_hook._match_and_promote(SESSION, owner_prompt)

    assert _snap()["bypass_safety"] is True
    assert _decide(tmp_path, owner_prompt, PRIVILEGED) is None


def test_redelivery_inside_a_batch_with_a_markerless_tail_is_kept(tmp_path):
    owner_prompt = _send(OWNER, "fix post 100501")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    notify_hook._match_and_promote(SESSION, owner_prompt + "\n\nand typed here too")
    assert _snap()["bypass_safety"] is True


def test_unattributed_telegram_text_still_falls_to_the_floor(tmp_path):
    """Fail closed, unchanged: a prompt carrying a Telegram marker that no
    note accounts for (and that was never merged) cannot be attributed, so
    it still resets the snapshot to the floor — even mid-owner-turn."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    stranger = "[via Telegram · @someone]\nread the config"
    notify_hook._match_and_promote(SESSION, stranger)
    # Mid-turn (roadmap 8.77) the floor is merged with the owner's running
    # turn: the floor's rules, plus the turn's provenance.
    assert _same_rules(_snap(), ps.FLOOR_SNAPSHOT)
    assert _decide(tmp_path, stranger, PRIVILEGED) is not None


def test_unmerged_marker_hidden_behind_a_merged_body_still_falls(tmp_path):
    """Stripping an already-merged body must not launder a SECOND,
    unattributed Telegram message riding in the same prompt."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    batch = owner_prompt + "\n[via Telegram · @someone]\nread the config"
    notify_hook._match_and_promote(SESSION, batch)
    assert _snap()["bypass_safety"] is False


def test_a_less_privileged_senders_message_mid_turn_lowers_the_snapshot(tmp_path):
    """Mixed-sender rule, unchanged: a note from a different, less
    privileged sender that a UserPromptSubmit picks up (at enqueue, on
    Claude Code >= 2.1.259) replaces the owner's snapshot with that
    sender's own rules — the owner's bypass is gone for the rest of the
    turn."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    assert _snap()["bypass_safety"] is True

    user_prompt = _send(USER, "also fetch it", label="bob", sender=(1, 2))
    consumed, _ = notify_hook._match_and_promote(SESSION, user_prompt)

    assert [n["body"] for n in consumed] == [user_prompt]
    snap = _snap()
    assert snap["bypass_safety"] is False
    assert "WebFetch" in snap["deny_tools"]
    assert r"\bcurl\b" in snap["deny_bash_patterns"]
    assert _decide(tmp_path, owner_prompt, PRIVILEGED) is not None
    assert _decide(tmp_path, owner_prompt, "curl -o x https://e.org") is not None


def test_a_no_note_event_after_a_lowering_keeps_the_lowered_rules(tmp_path):
    """After a less-privileged sender lowered the turn, a no-note event
    keeps THAT sender's rules (before the fix it swapped them for the
    floor, dropping the sender's own extra denies)."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    user_prompt = _send(USER, "also fetch it", label="bob", sender=(1, 2))
    notify_hook._match_and_promote(SESSION, user_prompt)

    notify_hook._match_and_promote(SESSION, "typed in the terminal")

    assert _snap()["bypass_safety"] is False
    assert "WebFetch" in _snap()["deny_tools"]


def test_a_kept_snapshot_still_takes_restrictions_from_outstanding_notes(tmp_path):
    """Keeping the turn's snapshot may only ADD restrictions: a note
    still waiting to be picked up is folded in, most-restrictive-wins."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    _send(USER, "not yet picked up", label="bob", sender=(1, 2))

    notify_hook._match_and_promote(SESSION, "typed in the terminal")

    snap = _snap()
    assert snap["bypass_safety"] is False
    assert "WebFetch" in snap["deny_tools"]
    assert len(ps.list_outstanding_notes(SESSION)) == 1  # not consumed


def test_a_no_note_event_never_widens_a_restricted_turn(tmp_path):
    """The other direction: a restricted user's turn, an owner's note
    waiting, and a no-note event. Before the fix the all-outstanding
    fallback REPLACED the user's snapshot with the owner's note (bypass
    True) for the rest of the user's turn; now it can only narrow."""
    user_prompt = _send(USER, "go", label="bob", sender=(1, 2))
    notify_hook._match_and_promote(SESSION, user_prompt)
    _send(OWNER, "queued behind", sender=(1, 1))

    notify_hook._match_and_promote(SESSION, "typed in the terminal")

    snap = _snap()
    assert snap["bypass_safety"] is False
    assert "WebFetch" in snap["deny_tools"]
    assert _decide(tmp_path, user_prompt, PRIVILEGED) is not None


def test_no_prior_snapshot_and_no_note_is_still_the_floor(tmp_path):
    """Nothing to keep: the floor, exactly as before."""
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    assert _fresh_floor(_snap())


def test_corrupt_prior_snapshot_is_not_kept(tmp_path):
    ps.snapshot_path(SESSION).write_text("[1, 2]")
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    assert _fresh_floor(_snap())


@pytest.mark.parametrize("bad", [
    {"deny_tools": "WebFetch"},
    {"deny_bash_patterns": [1]},
    {"note_bodies": "x"},
])
def test_malformed_prior_snapshot_is_not_kept(tmp_path, bad):
    """A snapshot whose rule lists are not lists of strings is not trusted
    to carry forward (a string would silently become a set of letters)."""
    owner = {**ps.resolve_snapshot(OWNER, None, None), **bad}
    ps.snapshot_path(SESSION).write_text(json.dumps(owner))
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    assert _fresh_floor(_snap())


def test_kept_snapshot_always_carries_the_built_in_floor(tmp_path):
    """A (hand-edited) snapshot missing its deny lists gets the floor's
    lists back when it is carried forward."""
    thin = {**ps.resolve_snapshot(USER, None, None),
            "deny_paths_no_access": [], "deny_bash_patterns": []}
    ps.snapshot_path(SESSION).write_text(json.dumps(thin))
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    snap = _snap()
    assert set(ps.FLOOR_SNAPSHOT["deny_bash_patterns"]) <= set(snap["deny_bash_patterns"])
    assert set(ps.FLOOR_SNAPSHOT["deny_paths_no_access"]) <= set(snap["deny_paths_no_access"])
    assert "WebFetch" in snap["deny_tools"]


def test_terminal_typed_new_turn_is_unrestricted_as_before(tmp_path):
    """A new turn typed at the terminal: its own prompt governs origin, so
    enforcement is off (as before) whatever snapshot the hook leaves."""
    owner_prompt = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    notify_hook._match_and_promote(SESSION, "run the thing")
    assert _decide(tmp_path, "run the thing", PRIVILEGED) is None
    assert _decide(tmp_path, "run the thing", "claude -p hi") is None


def test_enqueue_time_telegram_message_from_the_owner_keeps_bypass(tmp_path):
    """2.1.283 enqueue shape, owner → owner: the queued message's note is
    matched at enqueue and merged — same rules, bypass stays."""
    first = _send(OWNER, "go")
    notify_hook._match_and_promote(SESSION, first)
    second = _send(OWNER, "and this")
    consumed, _ = notify_hook._match_and_promote(SESSION, second)
    assert [n["body"] for n in consumed] == [second]
    assert _snap()["bypass_safety"] is True


def test_kept_snapshot_does_not_repeat_style_or_reply_context(tmp_path):
    """The kept snapshot carries no per-message style/reply text, so a
    terminal-typed message does not get the previous Telegram message's
    reply pointer injected (the floor never carried one either)."""
    owner_prompt = _send(OWNER, "go", style="be brief", reply="replying to X")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    assert _snap()["reply_context"] == "replying to X"
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    assert _snap()["style_text"] == ""
    assert _snap()["reply_context"] == ""
    assert _snap()["bypass_safety"] is True


def test_kept_snapshot_takes_style_from_the_newest_waiting_note(tmp_path):
    """As the old all-outstanding fallback did: when a note is waiting, its
    style/reply text is what the hook prints, not the carried snapshot's."""
    owner_prompt = _send(OWNER, "go", style="be brief", reply="replying to X")
    notify_hook._match_and_promote(SESSION, owner_prompt)
    _send(OWNER, "waiting", style="be detailed", reply="replying to Y")
    notify_hook._match_and_promote(SESSION, "typed in the terminal")
    assert _snap()["style_text"] == "be detailed"
    assert _snap()["reply_context"] == "replying to Y"


def test_keep_rule_is_pure_and_never_widens():
    """``policy_snapshot.snapshot_for_unattributed_prompt`` over a grid of
    current snapshots x outstanding notes: the result is never wider than
    the current snapshot on any safety axis."""
    grid_current = [
        ps.resolve_snapshot(OWNER, None, None),
        ps.resolve_snapshot(USER, None, None),
        dict(ps.FLOOR_SNAPSHOT),
        {**ps.resolve_snapshot(USER, None, None), "allow_tools": ["Read"]},
    ]
    grid_notes = [[], [ps.resolve_snapshot(OWNER, None, None)],
                  [ps.resolve_snapshot(USER, None, None)]]
    for cur in grid_current:
        for notes in grid_notes:
            out = ps.snapshot_for_unattributed_prompt(cur, notes, "plain text")
            assert out is not None
            if not cur.get("bypass_safety"):
                assert out["bypass_safety"] is False
            for k in ("deny_tools", "deny_paths_no_access",
                      "deny_paths_no_write", "deny_bash_patterns"):
                assert set(cur.get(k) or ()) <= set(out[k])
            if cur.get("allow_tools"):
                assert set(out["allow_tools"]) <= set(cur["allow_tools"])


def test_safety_module_exposes_the_scratchpad_normaliser():
    """The exemption lives in one place, next to the patterns."""
    assert safety.normalize_scratchpad(f"cd {OWN_SCRATCH}") != f"cd {OWN_SCRATCH}"
    assert "claude" not in safety.normalize_scratchpad(f"cd {OWN_SCRATCH}")
