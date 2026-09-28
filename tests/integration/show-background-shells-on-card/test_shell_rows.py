"""Background shells on the busy card: detection, the live row, and how each
end settles it.

Each row drives the real daemon on the virtual loop (see conftest.py):
Telegram messages through ``_handle_message``, hooks through the hook
receiver, the transcript's ``queue-operation`` lines through the card's own
queue scan, and a Telegram that remembers every version of every card.
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs
from aipager import state as state_mod

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'


def _layout(layout: str = "card") -> None:
    prefs.set_preference(CHAT, "layout", layout)


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _worker(r):
    return asyncio.ensure_future(r._updates())


async def _turn(r, mid: int, text: str, *, tools: int = 1) -> None:
    """A Telegram prompt reaching an idle Claude, taken as a new turn."""
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)
    for i in range(tools):
        r.tool(f"{text} step {i}")
        await asyncio.sleep(1)


def _card_text(r) -> str:
    """The latest version of the one card of the scenario."""
    assert len(r.chat.cards) == 1, list(r.chat.cards.values())
    (card,) = r.chat.cards.values()
    return card["text"]


# ── detection ───────────────────────────────────────────────────────────────

def test_launch_shows_a_running_shell_row(replay, vloop):
    """G4: the launch's PostToolUse leaves the row live, drawn as a shell
    with its elapsed time, never as a finished ✅ Bash call."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert f"⏳ `shell: {DESC} (" in text
    assert f"✅ `Bash: {DESC}`" not in text
    assert r.chat.live_cards()


def test_label_falls_back_to_the_first_command_line(replay, vloop):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash("", "bshell01",
                  command="  pytest   -q tests/\n echo second line")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert "⏳ `shell: pytest -q tests/ (" in _card_text(r)


def test_non_bash_tool_with_background_id_is_not_a_shell(replay, vloop):
    """G1: only a Bash call's backgroundTaskId is a shell."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01", tool_name="Monitor")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert "`shell:" not in _card_text(r)


def test_subagent_background_shell_is_not_tracked(replay, vloop):
    """G2: a shell a subagent starts notifies that agent, not the parent."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-dev")
        await asyncio.sleep(1)
        r.bg_bash(DESC, "bshell01", agent_id="a1")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert "1 agent running" in pin
    assert "`shell:" not in _card_text(r)


def test_run_in_background_without_task_id_is_not_a_shell(replay, vloop):
    """G3: the flag alone is not a shell; the task id is."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        tool_input = {"command": "sleep 1", "description": DESC,
                      "run_in_background": True}
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input=tool_input)
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input=tool_input,
               tool_response={"stdout": "", "stderr": ""})
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert f"✅ `Bash: {DESC}`" in _card_text(r)


def test_ctrl_b_backgrounded_shell_is_tracked(replay, vloop):
    """G3: Ctrl+B moves a shell to the background with run_in_background
    false; its backgroundTaskId still makes it a shell."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01",
                  response_extra={"backgroundedByUser": True})
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "1 shell running" in pin
    assert f"⏳ `shell: {DESC} (" in _card_text(r)


def test_empty_task_id_is_not_a_shell(replay, vloop):
    """G3: an empty backgroundTaskId is nothing."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert "`shell:" not in _card_text(r)


def test_shell_launch_never_settles_another_pending_row(replay, vloop):
    """G5: a launch whose own PreToolUse row is missing (a lost datagram)
    appends its own row: it never takes another tool's pending one."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go", tools=0)
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "slow thing"})
        await asyncio.sleep(1)
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input={"command": "x", "description": DESC},
               tool_response={"backgroundTaskId": "bshell01"})
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert "⏳ `Bash: slow thing`" in text
    assert f"⏳ `shell: {DESC} (" in text


def test_settled_shell_row_still_tallies_as_bash(replay, vloop):
    """The status line's tally counts a settled shell row as the Bash call
    it is, the way its live row was counted."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.enqueue(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(10)
        r.stop("all done")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert "Bash ×2" in text
    assert "shell ×" not in text


# ── ends seen while the turn runs ───────────────────────────────────────────

def test_shell_ending_mid_turn_settles_its_row_done(replay, vloop):
    """G6: the transcript's queue-operation line is the only prompt sign
    of a mid-turn end; the card's queue scan settles the row, and the
    turn's Stop is then an ordinary Finished."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(5)
        block = r.notification("bshell01", "completed", DONE)
        r.enqueue(block)
        r.absorb(block)
        await asyncio.sleep(10)
        mid_turn = _card_text(r)
        r.stop("all done")
        await asyncio.sleep(10)
        w.cancel()
        return mid_turn

    mid_turn = _run(vloop, scenario())
    assert f"✅ `shell: {DESC} - done (" in mid_turn
    assert r.chat.live_cards() == []  # Finished, not waiting
    (answer,) = r.chat.answer_texts.values()
    assert "still running" not in answer[-1]


def test_notification_without_status_does_not_end_a_shell(replay, vloop):
    """G7: a Monitor event (no <status>) for the same id ends nothing."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.enqueue(r.notification("bshell01", None, 'Monitor "x" event: tick'))
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "1 shell running" in pin
    assert f"⏳ `shell: {DESC} (" in _card_text(r)


def test_agent_result_quoting_a_shell_notification_ends_nothing(replay,
                                                                vloop):
    """G8: only a notification's own tags count. An agent's <result> may
    quote a whole shell notification, and a block with no status of its
    own may carry an unterminated result naming one."""
    _layout()
    r = replay
    quoted = r.notification("bshell01", "completed", DONE)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.enqueue(r.notification("a77", "completed", 'Agent "x" completed',
                               result=f"I saw this: {quoted}"))
        await asyncio.sleep(5)
        after_quote = r.bot._render_pinned(CHAT)[0]
        r.enqueue("<task-notification><task-id>bshell01</task-id>"
                  "<result>tail <status>completed</status>")
        await asyncio.sleep(5)
        after_open = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return after_quote, after_open

    after_quote, after_open = _run(vloop, scenario())
    assert "1 shell running" in after_quote
    assert "1 shell running" in after_open
    assert f"⏳ `shell: {DESC} (" in _card_text(r)


def test_failed_shell_row_shows_exit_code(replay, vloop):
    """G9."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.enqueue(r.notification(
            "bshell01", "failed",
            f'Background command "{DESC}" failed with exit code 144'))
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert f"❌ `shell: {DESC} - failed (exit 144)`" in _card_text(r)


def test_killed_notification_marks_shell_stopped(replay, vloop):
    """G10: a killed notification (the memory-pressure reap) is ⏹."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.enqueue(r.notification(
            "bshell01", "killed", f'Background command "{DESC}" was stopped'))
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert f"⏹ `shell: {DESC} - stopped (" in _card_text(r)


def test_taskstop_marks_shell_stopped(replay, vloop):
    """G10: TaskStop is a stopped shell's only end signal."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.task_stop("bshell01")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert f"⏹ `shell: {DESC} - stopped (" in _card_text(r)


def test_killshell_with_shell_id_ends_the_shell(replay, vloop):
    """G11: the older KillShell name and the shell_id alias."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.task_stop("bshell01", tool_name="KillShell", key="shell_id")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert "shell" not in pin
    assert f"⏹ `shell: {DESC} - stopped (" in _card_text(r)


def test_long_turn_trim_settles_the_right_row(replay, vloop, monkeypatch):
    """G12: the card's history is trimmed on a long turn; the shell's row
    reference shifts with it, so its end settles ITS row, not a later
    tool's."""
    monkeypatch.setattr(state_mod, "TOOL_HISTORY_CAP", 5)
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go", tools=3)
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(1)
        for i in range(3):
            r.tool(f"later {i}")
            await asyncio.sleep(1)
        r.enqueue(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert f"✅ `shell: {DESC} - done (" in text
    for i in range(3):
        assert f"✅ `Bash: later {i}`" in text


def test_failed_shell_row_stays_failed_on_the_finished_card(replay, vloop):
    """G14: the Finished card marks every row done, but a shell's failed
    and stopped outcomes keep their own mark."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        r.bg_bash("second", "bshell02")
        await asyncio.sleep(2)
        r.enqueue(r.notification(
            "bshell01", "failed",
            f'Background command "{DESC}" failed with exit code 1'))
        r.task_stop("bshell02")
        await asyncio.sleep(5)
        r.stop("one failed")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.live_cards() == []
    text = _card_text(r)
    assert "Done" in text
    assert f"❌ `shell: {DESC} - failed (exit 1)`" in text
    assert "⏹ `shell: second - stopped (" in text


# ── rows a running shell holds ──────────────────────────────────────────────

def test_two_shells_with_the_same_description_get_a_row_each(replay, vloop):
    """Two background launches with one description: each binds its own
    row, never the first shell's still-pending one, and each settles on
    its own end."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(1)
        r.bg_bash(DESC, "bshell02")
        await asyncio.sleep(10)
        both = _card_text(r)
        r.enqueue(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(10)
        one_ended = _card_text(r)
        r.enqueue(r.notification(
            "bshell02", "failed",
            f'Background command "{DESC}" failed with exit code 3'))
        await asyncio.sleep(10)
        w.cancel()
        return both, one_ended

    both, one_ended = _run(vloop, scenario())
    assert both.count(f"⏳ `shell: {DESC} (") == 2
    assert f"`Bash: {DESC}`" not in both
    assert one_ended.count(f"✅ `shell: {DESC} - done (") == 1
    assert one_ended.count(f"⏳ `shell: {DESC} (") == 1
    text = _card_text(r)
    assert text.count(f"✅ `shell: {DESC} - done (") == 1
    assert text.count(f"❌ `shell: {DESC} - failed (exit 3)`") == 1


def test_foreground_bash_with_a_live_shells_summary_settles_its_own_row(
        replay, vloop):
    """A plain Bash call whose summary equals a running shell's settles
    its own row: the shell's row stays live."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(1)
        fg = {"command": "x", "description": DESC}
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input=fg)
        await asyncio.sleep(1)
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input=fg, tool_response={"stdout": "ok"})
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert f"⏳ `shell: {DESC} (" in text
    assert f"✅ `Bash: {DESC}`" in text
    assert f"⏳ `Bash: {DESC}`" not in text


def test_unmatched_tool_end_never_takes_a_live_shells_row(replay, vloop):
    """A tool end with no row of its own (a lost PreToolUse) settles the
    last pending row that is not a running shell's."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "go", tools=0)
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "slow thing"})
        await asyncio.sleep(1)
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(1)
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input={"command": "lost pre"},
               tool_response={"stdout": "ok"})
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    text = _card_text(r)
    assert f"⏳ `shell: {DESC} (" in text
    assert "✅ `Bash: slow thing`" in text
