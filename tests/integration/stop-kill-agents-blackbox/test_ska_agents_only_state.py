"""design-amend-5 A, through the real hook replay: only the job's
background agents put a session in the waiting state.

Equivalence classes of an IDLE session whose job is open, each built from
the hook events Claude Code sends (not from fields set by hand), except
the pending continuation, which no single hook leaves on an idle session:
- a job shell only (a background Bash, then Claude's Stop);
- the grace window only (the last agent's SubagentStop);
- a pending continuation only;
- agents plus a job shell, and one agent alone (the waiting state).

Every surface is read through its entry point: the pinned bar, /status,
the /x switch line, ``GET /api/sessions`` and ``/api/sessions/{label}``,
the CLI rows and ``aipager status --json``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re

import pytest

from aipager import i18n

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
EN = i18n.get("en")


async def _shell_only(h):
    await h.waiting(agents=(), shells=(kit.SHELL,))


async def _grace(h):
    await h.prompt()
    await h.launch(kit.A1)
    await h.turn_stop(agents=(kit.A1,))
    await h.hook("SubagentStop", agent_id=kit.A1, agent_type="general-purpose")


async def _continuation(h):
    await h.prompt()
    await h.turn_stop("Done.", agents=())
    h.sess.job_continuation_active = True


async def _agents_and_shell(h):
    await h.waiting(agents=(kit.A1, kit.A2), shells=(kit.SHELL,))


async def _one_agent(h):
    await h.waiting(agents=(kit.A1,))


IDLE_CASES = {"shell_only": _shell_only, "grace": _grace,
              "continuation": _continuation}
WAITING_CASES = {"agents_and_shell": (_agents_and_shell, 2),
                 "one_agent": (_one_agent, 1)}


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", text)


def _surfaces(h, setup, monkeypatch, capsys):
    from aipager import status
    out = {}

    async def go():
        await setup(h)
        out["job_open"] = h.sess.job_background_open()
        out["rows"] = (await h.get_json("/api/sessions"))[1]
        out["detail"] = (await h.get_json(f"/api/sessions/{kit.LABEL}"))[1]
    h.run(go())
    out["bar"] = _plain(h.bot._render_pinned(kit.DM, [h.sess])[0]).split("\n")[0]
    out["status"] = _plain(h.bot._status_block(h.sess, None, tr=EN).split("\n")[0])
    out["switch"] = _plain(h.bot._render_switch_reply(kit.DM, h.sess)[0])
    rows = out["rows"]["sessions"] if isinstance(out["rows"], dict) else out["rows"]
    out["row"] = next(r for r in rows if r["label"] == kit.LABEL)
    h.bot.registry.save()
    monkeypatch.setattr(status, "_live_sessions", lambda: {kit.NAME})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    cli_rows, _live = status._gather_sessions()
    out["cli"] = next(r for r in cli_rows if r["name"] == kit.NAME)
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    capsys.readouterr()
    status.cmd_status(argparse.Namespace(as_json=True))
    out["json"] = capsys.readouterr().out
    return out


# ---- a job without agents: idle on every surface -------------------------------------

@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_precondition_the_job_is_open(dm, monkeypatch, capsys, case):
    assert _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["job_open"] is True


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_bar_reads_idle(dm, monkeypatch, capsys, case):
    bar = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["bar"]
    assert bar.startswith("💤 x1 (idle")


def test_bar_shell_only_keeps_mains_running_note(dm, monkeypatch, capsys):
    bar = _surfaces(dm, _shell_only, monkeypatch, capsys)["bar"]
    assert bar == "💤 x1 (idle, 1 shell running)"


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_status_head_reads_idle(dm, monkeypatch, capsys, case):
    head = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["status"]
    assert head.endswith("x1 · 💤 idle")


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_switch_line_does_not_say_waiting(dm, monkeypatch, capsys, case):
    text = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["switch"]
    assert "🔄" not in text and "waiting for" not in text


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_miniapp_row_reads_idle(dm, monkeypatch, capsys, case):
    row = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["row"]
    assert (row["status"], row["job_agents"]) == ("idle", 0)


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_miniapp_detail_reads_idle(dm, monkeypatch, capsys, case):
    detail = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["detail"]
    assert detail["status"] == "idle"


@pytest.mark.parametrize("case", list(IDLE_CASES))
def test_cli_row_reads_idle(dm, monkeypatch, capsys, case):
    cli = _surfaces(dm, IDLE_CASES[case], monkeypatch, capsys)["cli"]
    assert (cli["status"], cli["job_agents"]) == ("IDLE", 0)


# ---- agents (with or without a job shell): waiting for N agents, never shells ----------

@pytest.mark.parametrize("case", list(WAITING_CASES))
def test_bar_names_only_the_agents(dm, monkeypatch, capsys, case):
    setup, n = WAITING_CASES[case]
    bar = _surfaces(dm, setup, monkeypatch, capsys)["bar"]
    word = "agent" if n == 1 else "agents"
    assert bar == f"🔄 x1 (waiting for {n} {word})"


@pytest.mark.parametrize("case", list(WAITING_CASES))
def test_status_head_names_only_the_agents(dm, monkeypatch, capsys, case):
    setup, n = WAITING_CASES[case]
    head = _surfaces(dm, setup, monkeypatch, capsys)["status"]
    word = "agent" if n == 1 else "agents"
    assert head.endswith(f"x1 · 🔄 waiting for {n} {word}")


@pytest.mark.parametrize("case", list(WAITING_CASES))
def test_switch_line_names_only_the_agents(dm, monkeypatch, capsys, case):
    setup, n = WAITING_CASES[case]
    text = _surfaces(dm, setup, monkeypatch, capsys)["switch"]
    word = "agent" if n == 1 else "agents"
    assert f"· 🔄 waiting for {n} {word} ·" in text


def test_no_surface_names_a_shell_in_the_waiting_text(dm, monkeypatch, capsys):
    out = _surfaces(dm, _agents_and_shell, monkeypatch, capsys)
    assert not any("shell" in out[k] for k in ("bar", "status", "switch"))


@pytest.mark.parametrize("case", list(WAITING_CASES))
def test_miniapp_row_is_tasks_with_the_agent_count(dm, monkeypatch, capsys, case):
    setup, n = WAITING_CASES[case]
    row = _surfaces(dm, setup, monkeypatch, capsys)["row"]
    assert (row["status"], row["job_agents"]) == ("tasks", n)


@pytest.mark.parametrize("case", list(WAITING_CASES))
def test_cli_row_is_tasks_with_the_agent_count(dm, monkeypatch, capsys, case):
    setup, n = WAITING_CASES[case]
    cli = _surfaces(dm, setup, monkeypatch, capsys)["cli"]
    assert (cli["status"], cli["job_agents"]) == ("TASKS", n)


# ---- job_shells is gone from every payload ----------------------------------------------

ALL_CASES = {**IDLE_CASES, **{k: v[0] for k, v in WAITING_CASES.items()}}


@pytest.mark.parametrize("case", list(ALL_CASES))
def test_miniapp_list_has_no_job_shells(dm, monkeypatch, capsys, case):
    row = _surfaces(dm, ALL_CASES[case], monkeypatch, capsys)["row"]
    assert "job_shells" not in row


@pytest.mark.parametrize("case", list(ALL_CASES))
def test_miniapp_detail_has_no_job_shells(dm, monkeypatch, capsys, case):
    detail = _surfaces(dm, ALL_CASES[case], monkeypatch, capsys)["detail"]
    assert "job_shells" not in detail


@pytest.mark.parametrize("case", list(ALL_CASES))
def test_cli_rows_have_no_job_shells(dm, monkeypatch, capsys, case):
    cli = _surfaces(dm, ALL_CASES[case], monkeypatch, capsys)["cli"]
    assert "job_shells" not in cli


@pytest.mark.parametrize("case", list(ALL_CASES))
def test_cli_json_has_no_job_shells(dm, monkeypatch, capsys, case):
    out = _surfaces(dm, ALL_CASES[case], monkeypatch, capsys)["json"]
    assert "job_shells" not in out and json.loads(out) is not None


def test_saved_record_names_no_shells(dm, monkeypatch, capsys):
    from aipager import state
    _surfaces(dm, _agents_and_shell, monkeypatch, capsys)
    saved = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][kit.NAME]
    assert saved.get("job_wait") == {"agents": 2}
