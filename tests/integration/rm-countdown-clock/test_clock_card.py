"""The clock line on Claude Code's risky-removal prompt (roadmap 8.128;
design.md criteria 8, 9 and 11): "If nobody answers, Claude Code refuses
this by itself at about HH:MM", the arrival plus 2 minutes in the
computer's local time and the chat's digits, on the card's inline prompt
and on a prompt sent as its own message; never on any other prompt.

Driven through the two-prompts rig: the real hook receiver, notify path
and card builder."""

from __future__ import annotations

import pytest

from aipager import i18n, preferences, rm_countdown
from aipager.bot import rm_refusal
from aipager.bot.flood import clear_time
from tests.two_prompts_rig import BG, CARD, GROUP, Rig

ARRIVED = 1_800_000_000.0
SHOWN = clear_time(ARRIVED + 120.0)
RM = "rm -rf /home/alice/app"


def clock_text(code="en") -> str:
    tr = i18n.get(code)
    return tr.html(rm_refusal.RM_CLOCK, time=tr.num(SHOWN))


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(rm_countdown, "_now_wall", lambda: ARRIVED)
    return Rig(mk_bot, monkeypatch, run_async)


@pytest.fixture
def cardless(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(rm_countdown, "_now_wall", lambda: ARRIVED)
    return Rig(mk_bot, monkeypatch, run_async, card=False)


def last_card(rig) -> str:
    return [text for mid, text, _kb in rig.card_edits if mid == CARD][-1]


def rm_prompt(rig, command=RM, **kw):
    kw.setdefault("mode", "bypassPermissions")
    kw.setdefault("suggestions", [])
    rig.claude_bash(command, **kw)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_the_inline_prompt_shows_the_clock(rig, mode):
    rm_prompt(rig, mode=mode)
    text = last_card(rig)
    assert text.count(clock_text()) == 1
    assert SHOWN in text


def test_the_clock_is_the_arrival_plus_two_minutes(rig):
    rm_prompt(rig)
    clock = rm_countdown.clock_of(rig.sess.open_prompts()[0])
    assert clock["refuse_at"] == ARRIVED + rm_countdown.REFUSE_AFTER_SECONDS == ARRIVED + 120


def test_the_separate_message_shows_the_clock(cardless):
    rm_prompt(cardless)
    [msg] = cardless.prompt_messages()
    assert msg["text"].count(clock_text()) == 1


def test_the_extra_message_shows_the_clock(rig):
    rig.question()
    rm_prompt(rig)
    [msg] = [m for m in rig.prompt_messages() if m.get("reply_to_message_id") == CARD]
    assert msg["text"].count(clock_text()) == 1


def test_the_bar_resend_keeps_the_clock(cardless):
    rm_prompt(cardless)
    text, _kb = cardless.bot._pending_prompt_markup(cardless.sess)
    assert text.count(clock_text()) == 1


def test_the_bar_resend_of_an_inline_prompt_keeps_the_clock(rig):
    rm_prompt(rig)
    text, _kb = rig.bot._pending_prompt_markup(rig.sess)
    assert text.count(clock_text()) == 1


def test_the_clock_is_in_the_chats_digits(rig):
    preferences.set_ui_language(GROUP, "fa")
    rm_prompt(rig)
    text = last_card(rig)
    fa = clock_text("fa")
    assert fa in text
    assert i18n.get("fa").num(SHOWN) in text
    assert SHOWN not in text or SHOWN == i18n.get("fa").num(SHOWN)


def test_the_tool_use_id_of_the_preceding_call_is_kept(rig):
    """2.1.311 sends the PermissionRequest with no tool_use_id: the
    parent's PreToolUse just before gives it (the transcript match)."""
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM},
             tool_use_id="toolu_pre1")
    rm_prompt(rig, tool_use_id=None)
    info = rig.sess.open_prompts()[0]["tool_info"]
    assert info["tool_use_id"] == "toolu_pre1"
    assert rm_countdown.clock_of(info) is not None


# -- no clock on any other prompt (criterion 9) --------------------------------------

def no_clock(rig) -> bool:
    texts = [text for _mid, text, _kb in rig.card_edits] + [
        m["text"] or "" for m in rig.sent]
    return (all("⏱" not in t and SHOWN not in t for t in texts)
            and all(rm_countdown.clock_of(p) is None for p in rig.sess.open_prompts()))


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", None, ""],
                         ids=["default", "accept-edits", "plan", "missing", "empty"])
def test_no_clock_in_another_mode(rig, mode):
    rm_prompt(rig, mode=mode)
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_without_suggestions(rig):
    rig.claude_bash(RM, mode="bypassPermissions")
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_with_a_rule_suggested(rig):
    rm_prompt(rig, suggestions=[{"type": "addRules", "rules": [
        {"toolName": "Bash", "ruleContent": "rm:*"}], "behavior": "allow",
        "destination": "localSettings"}])
    assert rig.sess.open_prompts() and no_clock(rig)


@pytest.mark.parametrize("command", ["ls", "echo rm", "grep -r rm .",
                                     'git commit -m "rm old"', "npm run rm-cache",
                                     "rmx", "perm", "cat rm.txt"])
def test_no_clock_without_a_removal(rig, command):
    rm_prompt(rig, command)
    assert rig.sess.open_prompts() and no_clock(rig)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_no_clock_on_an_agents_prompt(rig, mode):
    rig.launch(BG)
    rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": RM},
             agent_id=BG, agent_type="ship-reviewer", permission_mode=mode,
             permission_suggestions=[])
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_on_another_tool(rig):
    rig.hook("PermissionRequest", tool_name="Write",
             tool_input={"file_path": "/home/alice/rm", "content": "rm -rf x"},
             permission_mode="bypassPermissions", permission_suggestions=[])
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_for_a_non_str_command(rig):
    rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": ["rm", "x"]},
             permission_mode="bypassPermissions", permission_suggestions=[])
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_on_a_notification_prompt(rig):
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert rig.sess.open_prompts() and no_clock(rig)


def test_no_clock_on_a_question(rig):
    rig.question(mode="bypassPermissions")
    assert rig.sess.open_prompts() and no_clock(rig)
