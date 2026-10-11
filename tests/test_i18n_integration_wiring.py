"""Texts that crossed the multilingual phases' boundaries, read in Persian
and Russian (the integration step after phases 3a, 3b and 4).

Each case drives a real caller that used to pass a key on unrendered (so
a Persian or Russian chat read English) and checks the text its reader
gets: replies and shared messages in the chat's language, pop-ups in the
tapper's. English keeps its exact bytes."""

from __future__ import annotations

import html
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import group_intake, new_flow, report_flow, session_parity, transport
from aipager.i18n import Command
from aipager.miniapp import launch
from tests import i18n_surfaces_p3a as p3a
from tests.i18n_surfaces import Env

CYRILLIC = re.compile("[Ѐ-ӿ]")
PERSIAN = re.compile("[؀-ۿ]")
SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}
GROUP, BOB, OWNER, DM = p3a.GROUP, p3a.BOB, p3a.OWNER, p3a.DM
RLM = "\u200f"


def _env(mk_bot, run_async, monkeypatch, code):
    return Env(mk_bot, run_async, monkeypatch, code)


def _toasts(q) -> list[str]:
    return [str(c.args[0] if c.args else c.kwargs.get("text"))
            for c in q.answer.await_args_list if (c.args or c.kwargs.get("text"))]


# ---- /help: the owner's report button and the group Talk line -------------------------

@pytest.mark.parametrize("code", ["en", "fa", "ru"])
@pytest.mark.parametrize("chat", [DM, GROUP])
def test_help_report_button_reads_in_the_chats_language(mk_bot, run_async, monkeypatch,
                                                         code, chat):
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a.world(env)
    monkeypatch.setattr(report_flow, "is_owner", lambda *a, **k: True)
    p3a.cmd(env, bot, tg, "_handle_help_cmd", "/help", chat=chat)
    tr = i18n.get(code)
    label = tg.out[-1]
    assert label == tr.text(report_flow.REPORT_BUTTON)
    if code == "en":
        assert label == "🐞 Report a problem"
    else:
        assert SCRIPT[code].search(label), label
    if chat == GROUP:
        talk = group_intake.group_help_talk_line(bot, tr=tr)
        assert talk == tr.html(group_intake.HELP_TALK, mention=Command("@aipager_test_bot"))
        assert talk in tg.out[0].split("\n")


# ---- the memory-cap notice's Report this button ---------------------------------------

@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_memory_cap_report_button_reads_in_the_chats_language(mk_bot, run_async,
                                                               monkeypatch, code):
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg, x1 = p3a._notice_world(env)
    monkeypatch.setattr(report_flow, "resolve_owner", lambda b: 555)
    run_async(bot.notify(x1, "hook_memory_cap_hit", {"hook": "aipager-hook", "tool": ""}))
    tr = i18n.get(code)
    assert tg.out[-1] == tr.text(report_flow.REPORT_THIS_BUTTON)
    if code == "en":
        assert tg.out[-1] == "🐞 Report this"
    else:
        assert SCRIPT[code].search(tg.out[-1])
        # The nudge above it names the button by the same words.
        words = tr.text(report_flow.REPORT_THIS_BUTTON).lstrip(RLM).removeprefix("🐞 ")
        assert words in tg.out[-2]


# ---- an API error at the end of a turn ------------------------------------------------

def _api_error_line(env, summary="API Error: 529 overloaded"):
    bot, tg, x1 = p3a._notice_world(env)
    from aipager.state import Status
    x1.last_prompt = "go"
    x1.status = Status.IDLE
    env.run(bot.notify(x1, "idle_prompt", {"summary": summary}))
    lines = [t for t in tg.out if "⚠️" in t]
    assert len(lines) == 1, tg.out
    return lines[0], tg.out


def test_an_api_error_keeps_its_english(mk_bot, run_async, monkeypatch):
    env = _env(mk_bot, run_async, monkeypatch, "en")
    line, out = _api_error_line(env)
    assert line == "⚠️ <b>x1</b> · Anthropic's servers are overloaded. Try again in a moment."
    assert "🔄 Retry" in out


@pytest.mark.parametrize("code", ["fa", "ru"])
@pytest.mark.parametrize("summary,key", [
    ("API Error: 529 overloaded", "tx.api.overloaded"),
    ("API Error: 402 credit balance too low", "tx.api.billing"),
    ("API Error: 429 rate_limit_error, retry after 30 seconds", "tx.api.rate_limit_wait"),
])
def test_an_api_error_reads_in_the_chats_language(mk_bot, run_async, monkeypatch, code,
                                                  summary, key):
    env = _env(mk_bot, run_async, monkeypatch, code)
    line, _out = _api_error_line(env, summary)
    tr = i18n.get(code)
    friendly, _wait = transport._detect_api_error(summary, tr=tr)
    assert line == f"{tr.mark}⚠️ <b>x1</b> · {html.escape(friendly, quote=False)}"
    assert SCRIPT[code].search(line) and "Anthropic's servers" not in line
    if code == "fa":
        assert line.startswith(RLM)
    else:
        assert not line.startswith(RLM)


# ---- /new: a confined member's folder hint and refusals -------------------------------

def _confined_card(env, *, parents: bool):
    bot, tg = p3a._new_world(env)
    roots = ["/work/app"] if parents else []
    env.mp.setattr(launch, "allowed_roots",
                   lambda *a, confined=False, **k: [] if confined else roots)
    env.mp.setattr(launch, "default_folder_is_wide", lambda: True)
    bot._is_confined_user = lambda *a, **k: True
    _not_held_to_a_root(env)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new", chat=GROUP, user=BOB)
    return bot, tg


def _not_held_to_a_root(env):
    """The 8.79 home folder rule alone: a confined role not held to a
    members' root folder (a custom role with can_manage and no bypass)."""
    from aipager import member_root
    env.mp.setattr(member_root, "restricted_root", lambda *a, **k: None)


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
@pytest.mark.parametrize("parents,hint", [(True, launch.NO_PROJECT_FOLDER),
                                          (False, launch.NO_FOLDER_AT_ALL)])
def test_the_name_cards_folder_hint_reads_in_the_chats_language(mk_bot, run_async,
                                                                 monkeypatch, code,
                                                                 parents, hint):
    env = _env(mk_bot, run_async, monkeypatch, code)
    preferences.set_ui_language(BOB, "ru" if code == "fa" else "fa")
    _bot, tg = _confined_card(env, parents=parents)
    card = tg.out[0]
    want = f"📁 {html.escape(i18n.get(code).text(hint))}"
    assert want in card.split("\n"), card
    if code == "en":
        assert f"📁 {html.escape(hint)}" in card.split("\n")
    else:
        assert SCRIPT[code].search(want)


def test_a_wide_default_folder_tap_pops_up_in_the_tappers_language(mk_bot, run_async,
                                                                   monkeypatch):
    env = _env(mk_bot, run_async, monkeypatch, "fa")
    preferences.set_ui_language(BOB, "ru")
    bot, tg = _confined_card(env, parents=True)
    card = bot._new_wizard_pending[(GROUP, BOB)]["msg_id"]
    q = p3a.tap(env, bot, tg, "_:nw:path:default", chat=GROUP, user=BOB, message_id=card)
    assert _toasts(q) == [i18n.get("ru").text(launch.WIDE_FOLDER_REFUSAL)]
    assert CYRILLIC.search(_toasts(q)[0])


def test_a_replace_refused_for_its_folder_pops_up_in_the_tappers_language(
        mk_bot, run_async, monkeypatch):
    env = _env(mk_bot, run_async, monkeypatch, "fa")
    preferences.set_ui_language(BOB, "ru")
    bot, tg = p3a._new_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=GROUP, user=BOB)
    _not_held_to_a_root(env)
    monkeypatch.setattr(launch, "launch_folder_refusal",
                        lambda cwd, confined: launch.WIDE_FOLDER_REFUSAL)
    start = len(tg.out)
    q = p3a.tap(env, bot, tg, p3a._sess_cb(x1, "new_replace"), chat=GROUP, user=BOB)
    assert _toasts(q) == [i18n.get("ru").text(launch.WIDE_FOLDER_REFUSAL)]
    assert all(not CYRILLIC.search(t) for t in tg.out[start:] if t not in _toasts(q))


def test_a_replace_whose_start_is_refused_tells_the_group_in_its_language(
        mk_bot, run_async, monkeypatch):
    env = _env(mk_bot, run_async, monkeypatch, "fa")
    preferences.set_ui_language(BOB, "ru")
    bot, tg = p3a._new_world(env)
    x1 = p3a.session(bot, "x1", chat=GROUP)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=GROUP, user=BOB)
    _not_held_to_a_root(env)
    bot.create_session = AsyncMock(return_value=("", launch.WIDE_FOLDER_REFUSAL))
    start = len(tg.out)
    p3a.tap(env, bot, tg, p3a._sess_cb(x1, "new_replace"), chat=GROUP, user=BOB)
    fa = i18n.get("fa")
    from aipager.bot.callbacks import NEW_CREATE_FAILED
    want = fa.html(NEW_CREATE_FAILED, error=fa.text(launch.WIDE_FOLDER_REFUSAL))
    assert want in tg.out[start:], tg.out[start:]
    assert PERSIAN.search(want)
    assert not any("Your role" in t for t in tg.out[start:])


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_a_refused_start_from_new_reads_in_the_chats_language(mk_bot, run_async,
                                                               monkeypatch, code):
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._new_world(env, launch_error=launch.WIDE_FOLDER_REFUSAL)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new x5", chat=GROUP)
    tr = i18n.get(code)
    want = tr.html(new_flow.NEW_CREATE_FAILED, error=tr.text(launch.WIDE_FOLDER_REFUSAL))
    assert tg.out[-1] == want
    if code == "en":
        assert want == i18n.get("en").html(new_flow.NEW_CREATE_FAILED,
                                           error=str(launch.WIDE_FOLDER_REFUSAL))
    else:
        assert "Your role" not in tg.out[-1] and SCRIPT[code].search(tg.out[-1])


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_a_reserved_name_is_refused_in_the_chats_language(mk_bot, run_async, monkeypatch,
                                                          code):
    """A Mini App rule with parameters (a Phrase) on the Name card."""
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._new_world(env)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new status", chat=DM)
    tr = i18n.get(code)
    _clean, err = launch.validate_session_name("status")
    want = tr.html(new_flow.WARNING_LINE, text=tr.text(
        launch._NAME_RESERVED, name="status", command=Command("/status")))
    assert any(want in t.split("\n") for t in tg.out), tg.out
    if code == "en":
        assert any(i18n.get("en").html(new_flow.WARNING_LINE, text=str(err)) in t.split("\n")
                   for t in tg.out)
    else:
        assert SCRIPT[code].search(want) and "reserved" not in want


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_a_bad_custom_model_is_refused_in_the_chats_language(mk_bot, run_async,
                                                             monkeypatch, code):
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._new_world(env)
    p3a.cmd(env, bot, tg, "_handle_new_cmd", "/new", chat=DM)
    p3a.tap(env, bot, tg, "_:nw:model:custom", chat=DM, message_id=7001)
    ctx = MagicMock()
    ctx.bot.id = 999
    run_async(bot._handle_message(p3a.msg(tg, "-bad", chat=DM), ctx))
    tr = i18n.get(code)
    want = tr.html(new_flow.WARNING_LINE, text=tr.text(
        launch._MODEL_PATTERN, example="claude-opus-5"))
    assert any(want in t.split("\n") for t in tg.out), tg.out
    if code != "en":
        assert SCRIPT[code].search(want) and "listed models" not in want


# ---- /rename: the Mini App's name rules -----------------------------------------------

@pytest.mark.parametrize("code", ["en", "fa", "ru"])
@pytest.mark.parametrize("new_name,msg,params", [
    ("status", launch._NAME_RESERVED, {"name": "status", "command": Command("/status")}),
    ("a" * 70, launch._NAME_TOO_LONG, {"max": 64}),
    ("-x", launch._NAME_PATTERN, {}),
])
def test_a_refused_rename_reads_in_the_chats_language(mk_bot, run_async, monkeypatch,
                                                      code, new_name, msg, params):
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._sess_world(env)
    p3a.session(bot, "x1", chat=GROUP)
    run_async(session_parity.handle_rename_cmd(
        bot, p3a.msg(tg, f"/rename x1 {new_name}", chat=GROUP), MagicMock()))
    tr = i18n.get(code)
    want = tr.html(session_parity.WARNING_LINE, text=tr.text(msg, **params))
    assert tg.out == [want]
    _clean, err = launch.validate_session_name(new_name)
    if code == "en":
        assert want == i18n.get("en").html(session_parity.WARNING_LINE, text=str(err))
    else:
        assert SCRIPT[code].search(want)


# ---- the Ask reason on a resume -------------------------------------------------------

@pytest.mark.parametrize("code", ["fa", "ru"])
def test_resumed_in_ask_is_whole_in_the_chats_language(mk_bot, run_async, monkeypatch,
                                                       code):
    from aipager.bot import session_ops
    from aipager.state import Status
    env = _env(mk_bot, run_async, monkeypatch, code)
    bot, tg = p3a._sess_world(env)
    bot._build_session_dashboard = lambda sess, *a, **k: "📊"
    bot._is_admin_user = lambda *a, **k: False
    x1 = p3a.session(bot, "x1", chat=GROUP, status=Status.GONE)
    x1.claude_session_id = "abc"
    x1.skip_perms = True
    x1.transcript_path = ""
    from aipager import member_root
    x1.cwd = member_root.restricted_root(bot, BOB, GROUP)   # inside bob's root
    bot._do_resume_core = AsyncMock(return_value=SimpleNamespace(ok=True, reason="resumed",
                                                                 err=""))
    p3a.cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=GROUP, user=BOB)
    tr = i18n.get(code)
    head = tg.out[-1].split("\n")[0]
    assert head == tr.html(session_ops.RESUMED_IN_ASK, session="x1")
    assert SCRIPT[code].search(head) and "Auto needs" not in head


def test_an_api_errors_translation_is_escaped_in_the_notice(mk_bot, run_async, monkeypatch):
    """The friendly text is plain text inside an HTML message: a translation
    holding HTML's special characters cannot break the notice."""
    from aipager.i18n import core
    env = _env(mk_bot, run_async, monkeypatch, "ru")
    monkeypatch.setitem(core._catalog("ru"), "tx.api.overloaded", "Серверы <b> & </b> заняты.")
    line, _out = _api_error_line(env)
    assert line == "⚠️ <b>x1</b> · Серверы &lt;b&gt; &amp; &lt;/b&gt; заняты."
