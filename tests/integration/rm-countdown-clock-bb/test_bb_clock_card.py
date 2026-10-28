"""The clock line on the prompt card, driven by real hook datagrams
(design.md success criteria 8, 9 and 10; entrypoints.md "Bot surface").

Partitions of the clock rule (all must hold): owner (Claude's own vs an
agent's), tool (Bash vs other), mode (bypassPermissions/auto vs every
other value, missing or empty), suggestions (an empty list vs missing or
non-empty), command (a removal vs not, a non-str). Surfaces: the separate
prompt message (no busy card), the inline prompt on the busy card, and a
second prompt sent as an extra message."""

from __future__ import annotations

import pytest

from aipager import preferences
from tests.two_prompts_rig import BG, CARD, GROUP

from tests.rm_clock_bb import CLOCK_EN, CLOCK_HEAD, REFUSE_AT, RM, T0, catalog, hhmm, FA_DIGITS

LINE = CLOCK_EN.format(time=hhmm(REFUSE_AT))


def sent_texts(env) -> list[str]:
    return [m.get("text") or "" for m in env.rig.sent]


def card_texts(env) -> list[str]:
    return [t for _mid, t, _m in env.rig.card_edits] + list(env.rig.frames)


def everything(env) -> str:
    return "\n".join(sent_texts(env) + card_texts(env))


# -- the line is there --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_separate_message_carries_the_clock_line(env, mode):
    env.rm(mode=mode)
    assert LINE in env.rig.prompt_messages()[0]["text"]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_inline_card_prompt_carries_the_clock_line(cenv, mode):
    cenv.rm(mode=mode)
    assert LINE in cenv.last_card_text()


def test_the_clock_line_is_there_once(env):
    env.rm()
    assert env.rig.prompt_messages()[0]["text"].count(CLOCK_HEAD) == 1


def test_the_inline_clock_line_is_there_once(cenv):
    cenv.rm()
    assert cenv.last_card_text().count(CLOCK_HEAD) == 1


def test_an_extra_prompt_message_carries_the_clock_line(cenv):
    """A second prompt goes out as its own message replying to the card."""
    cenv.rig.question()
    cenv.rm()
    extra = [m for m in cenv.rig.prompt_messages() if m.get("reply_to_message_id") == CARD]
    assert extra and LINE in extra[-1]["text"]


def test_the_time_is_arrival_plus_two_minutes(env):
    """Boundary: the shown minute follows the arrival clock (seam), not the
    host's real clock."""
    env.wall.t = T0 + 3600.0
    env.rm()
    assert CLOCK_EN.format(time=hhmm(T0 + 3600.0 + 120.0)) in env.rig.prompt_messages()[0]["text"]


def test_the_time_is_not_the_arrival_minute(env):
    env.wall.t = T0  # T0 + 120 crosses a minute: T0 is on a whole minute
    env.rm()
    assert CLOCK_EN.format(time=hhmm(T0)) not in env.rig.prompt_messages()[0]["text"]


# -- the reader's digits ---------------------------------------------------------------------

def fa_line() -> str:
    return catalog("fa")["perm.rm_clock"].replace("{time}", hhmm(REFUSE_AT).translate(FA_DIGITS))


def test_fa_chat_shows_the_time_in_persian_digits(env):
    preferences.set_ui_language(GROUP, "fa")
    env.rm()
    assert hhmm(REFUSE_AT).translate(FA_DIGITS) in env.rig.prompt_messages()[0]["text"]


def test_fa_chat_shows_the_persian_clock_sentence(env):
    preferences.set_ui_language(GROUP, "fa")
    env.rm()
    text = env.rig.prompt_messages()[0]["text"]
    assert fa_line().replace("⁨", "").replace("⁩", "") in (
        text.replace("⁨", "").replace("⁩", ""))


def test_fa_chat_has_no_latin_time(env):
    preferences.set_ui_language(GROUP, "fa")
    env.rm()
    assert hhmm(REFUSE_AT) not in env.rig.prompt_messages()[0]["text"]


def test_fa_inline_card_shows_persian_digits(cenv):
    preferences.set_ui_language(GROUP, "fa")
    cenv.rm()
    assert hhmm(REFUSE_AT).translate(FA_DIGITS) in cenv.last_card_text()


# -- no line: one condition flipped at a time ----------------------------------------------------

@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", "", None, "Auto",
                                  "bypasspermissions", "dontAsk"],
                         ids=["default", "acceptEdits", "plan", "empty", "missing", "Auto-case",
                              "bypass-case", "dontAsk"])
def test_no_clock_outside_bypass_and_auto(env, mode):
    env.rm(mode=mode)
    assert CLOCK_HEAD not in everything(env)


@pytest.mark.parametrize("suggestions", [None, [{"type": "addRules", "rules": [
    {"toolName": "Bash", "ruleContent": "rm -rf build/"}], "behavior": "allow",
    "destination": "localSettings"}]], ids=["missing", "non-empty"])
def test_no_clock_without_empty_suggestions(env, suggestions):
    env.rm(suggestions=suggestions)
    assert CLOCK_HEAD not in everything(env)


@pytest.mark.parametrize("command", ["ls", "echo rm", "grep -r rm .", 'git commit -m "rm old"',
                                     "npm run rm-cache", "rmx", "perm", "cat rm.txt"])
def test_no_clock_for_a_command_that_removes_nothing(env, command):
    env.rm(command)
    assert CLOCK_HEAD not in everything(env)


@pytest.mark.parametrize("command", [5, None, ["rm", "x"]], ids=["int", "none", "list"])
def test_no_clock_for_a_non_str_command(env, command):
    env.hook("PermissionRequest", tool_name="Bash",
             tool_input={"command": command, "description": "x"},
             tool_use_id="toolu_p1", permission_mode="bypassPermissions",
             permission_suggestions=[])
    assert CLOCK_HEAD not in everything(env)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_no_clock_on_an_agents_rm(cenv, mode):
    cenv.rig.launch(BG)
    cenv.hook("PermissionRequest", tool_name="Bash",
              tool_input={"command": RM, "description": RM}, agent_id=BG,
              agent_type="ship-reviewer", permission_mode=mode, permission_suggestions=[],
              aipager_reply_addr="/nonexistent/reply-bg.sock", aipager_request_id="req-bg")
    assert CLOCK_HEAD not in everything(cenv)


def test_the_agents_rm_prompt_is_shown(cenv):
    """Control for the agent row: its prompt is shown, only without a clock."""
    cenv.rig.launch(BG)
    cenv.hook("PermissionRequest", tool_name="Bash",
              tool_input={"command": RM, "description": RM}, agent_id=BG,
              agent_type="ship-reviewer", permission_mode="bypassPermissions",
              permission_suggestions=[],
              aipager_reply_addr="/nonexistent/reply-bg.sock", aipager_request_id="req-bg")
    assert len(cenv.rig.sess.open_prompts()) == 1


@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": "/home/alice/rm", "content": "rm -rf build/"}),
    ("Edit", {"file_path": "/home/alice/a", "old_string": "a", "new_string": "rm x"}),
    ("PowerShell", {"command": "rm -rf build/"}),
], ids=["Write", "Edit", "PowerShell"])
def test_no_clock_for_a_non_bash_tool(env, tool, tool_input):
    env.hook("PermissionRequest", tool_name=tool, tool_input=tool_input,
             tool_use_id="toolu_p1", permission_mode="bypassPermissions",
             permission_suggestions=[])
    assert CLOCK_HEAD not in everything(env)


def test_no_clock_on_a_notification_fallback(env):
    env.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert CLOCK_HEAD not in everything(env)


def test_the_notification_fallback_is_shown(env):
    """Control: the fallback prompt is open, only without a clock."""
    env.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert len(env.rig.sess.open_prompts()) == 1


def test_no_clock_on_ask_user_question(cenv):
    cenv.rig.question(mode="bypassPermissions")
    assert CLOCK_HEAD not in everything(cenv)


# -- criterion 10: a clock prompt is a countdown candidate --------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
@pytest.mark.parametrize("command", [RM, "sudo rm x", "find . -exec rm {} +", "sh -c 'rm x'"])
def test_a_clock_prompt_beside_another_makes_every_verdict_unknown(cenv, mode, command):
    cenv.rig.question()
    cenv.rm(command, mode=mode)
    sess = cenv.rig.sess
    assert {sess.keys_verdict(p, 0.0) for p in sess.open_prompts()} == {"unknown"}


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_a_lone_clock_prompt_is_yes(cenv, mode):
    cenv.rm(mode=mode)
    sess = cenv.rig.sess
    assert sess.keys_verdict(sess.open_prompts()[0], 0.0) == "yes"
