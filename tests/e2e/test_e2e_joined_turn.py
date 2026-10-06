"""E2E (roadmap 8.77): a Telegram message that joins a turn started in the
terminal makes the rest of that turn the joiner's.

A message absorbed into a running turn is no prompt of its own in the
transcript, so the turn would still read as terminal (no rules). The
``UserPromptSubmit`` pick-up of the joining note sets
``joined_from_telegram``, and the hook then holds the turn to the
joiner's rules. Built through the production pipeline: the terminal
prompt's snapshot (``turn_origin`` terminal), then the joining ``user``
note with the turn open. Posts nothing to Telegram.
"""

from __future__ import annotations

from tests.e2e import harness

_TASK = ("Use the Bash tool to run exactly: "
         "echo E2E_JOINED_$(printf %s aipager-e2e-join-7 | sha256sum | cut -c1-8)  and report the output.")
# Only running the command shows this (Claude cannot work out a sha256).
_RAN = "E2E_JOINED_183f724c"


def test_joined_turn_applies_joiners_rules(claude_available, project, session):
    start = harness.write_terminal_prompt(session, _TASK)
    assert start["turn_origin"] == "terminal", start
    joined = harness.write_joined_message(session, role_name="user")
    assert joined["joined_from_telegram"] is True, joined
    assert joined["turn_origin"] == "terminal" and "Bash" in joined["deny_tools"], joined
    r = harness.run(_TASK, session=session, project=project, marker=False)
    r.assert_denied("Bash")
    r.assert_safety_block_recorded("Bash is in this role's deny_tools")
    r.assert_not_leaked(_RAN)
