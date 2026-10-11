"""A safety block shown in the chat's language, from reason codes
(multilingual phase 3a, design section B).

Claude reads the English reason and the audit records it; the chat sees
it through a key in its own language, with tool names and paths as they
are, and a reason aipager did not write exactly as received. The sticky
repeat after a block is told apart by its code, never by its words."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import i18n, preferences, safety
from aipager.bot import safety_notice
from aipager.dtach import enforce
from aipager.state import Status, TrackedSession
from tests import i18n_surfaces_p3a as p3a

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
FSI, PDI = "\u2068", "\u2069"
CHAT = -1001

#: The code each writer's sample must come back as (p3a.safety_reasons order).
EXPECTED_CODES = [
    safety.REASON_BASH, safety.REASON_BASH_SEARCH, safety.REASON_TOOL_NOT_ALLOWED,
    safety.REASON_TOOL_DENIED, safety.REASON_PATH_UNREADABLE, safety.REASON_PATH_PROTECTED,
    safety.REASON_PATH_PROTECTED_WRITE, safety.REASON_RUNS_COMMANDS,
    safety.REASON_OUTSIDE_WRITE, safety.REASON_WIDE_CWD_WRITE, safety.REASON_OUTSIDE_SEARCH,
    safety.REASON_WIDE_CWD_SEARCH, safety.REASON_SEARCH_FOLDER_UNREADABLE,
    safety.REASON_PATH_PROTECTED, safety.REASON_SEARCH_COULD_REACH,
    safety.REASON_SEARCH_REACHES, safety.REASON_GLOB_UNREADABLE, safety.REASON_GLOB_ESCAPED,
    safety.REASON_GLOB_NOT_PLAIN, safety.REASON_GLOB_CLIMBS,
    safety.REASON_SEARCH_FOLDER_UNREADABLE, safety.REASON_PATTERN_UNREADABLE,
    safety.REASON_SEARCH_ANYWHERE, safety.REASON_SEARCH_PROTECTED_FILES,
]


def _all(tmp_path):
    return p3a.safety_reasons() + p3a.enforce_reasons(tmp_path)


def test_every_writer_shape_parses(tmp_path):
    """Each writer's real sentence reads back as its own code, and the
    key's English filled with what was read is that sentence again."""
    reasons = _all(tmp_path)
    codes = [safety_notice.parse_reason(reason).code for _tool, reason in reasons]
    assert codes == EXPECTED_CODES + [safety_notice.HALTED, safety_notice.UNCHECKED]
    en = i18n.get("en")
    for tool, reason in reasons:
        parsed = safety_notice.parse_reason(reason)
        assert parsed.params.get("tool", tool) == tool
        assert en.text(safety_notice.REASON_MSGS[parsed.code], **parsed.params) == reason


def test_every_code_has_a_key_and_a_shape():
    """safety.REASON_SHAPES is the whole set, enforce's two included."""
    codes = {code for code, _shape in safety.REASON_SHAPES}
    assert codes == set(safety_notice.REASON_MSGS)
    assert {safety_notice.HALTED, safety_notice.UNCHECKED} <= codes
    assert len(codes) == len(safety.REASON_SHAPES) == 25


def test_enforces_two_shapes_match_its_sentences(tmp_path):
    """safety.py never imports enforce: its two shapes are plain data,
    pinned here against the sentences enforce really writes."""
    shapes = dict(safety.REASON_SHAPES)
    halted, unchecked = (reason for _tool, reason in p3a.enforce_reasons(tmp_path))
    assert re.match(shapes[safety.REASON_HALTED], halted, re.S)
    assert re.match(shapes[safety.REASON_UNCHECKED], enforce._FAIL_CLOSED_REASON, re.S)
    assert unchecked == enforce._FAIL_CLOSED_REASON
    assert not re.match(shapes[safety.REASON_UNCHECKED], unchecked + ".", re.S)


@pytest.mark.parametrize("reason", ["an operator's own <rule> & more", "Bash  x"])
@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_reason_text_is_an_unknown_reason_as_received(code, reason):
    """A sentence aipager did not write: as received in every language,
    escaped for HTML, raw as text; never None."""
    import html
    tr = i18n.get(code)
    assert safety_notice.reason_text(tr, reason) == html.escape(reason)
    assert safety_notice.reason_text(tr, reason, kind="text") == reason


def test_reason_text_in_english_is_the_reason_as_received(tmp_path):
    import html
    en = i18n.get("en")
    for _tool, reason in _all(tmp_path) + [("Write", "Write on protected path /a/<b>&'")]:
        assert safety_notice.reason_text(en, reason) == html.escape(reason)
        assert safety_notice.reason_text(en, reason, kind="text") == reason


def _notice(code, reason, label="x1"):
    return safety_notice.notice_html(i18n.get(code), label, reason)


def test_the_english_notice_is_todays_bytes(tmp_path):
    import html
    for _tool, reason in _all(tmp_path) + [("Bash", "an operator's own <reason>")]:
        assert _notice("en", reason) == (
            f"🛑 <b>x1</b> · Blocked by safety policy: {html.escape(reason)} (stopped)")


def test_a_persian_notice(tmp_path):
    """The reason in Persian, the tool name and the path as they are and
    isolated, nothing English left of the sentence."""
    reason = safety.path_violation("Write", {"file_path": "/srv/keep/a.txt"}, (),
                                   ("/srv/keep/**",))
    text = _notice("fa", reason)
    assert PERSIAN.search(text)
    assert "write to protected path" not in text and reason not in text
    assert f"{FSI}Write{PDI}" in text and f"{FSI}/srv/keep/**{PDI}" in text
    assert text.startswith("\u200f🛑 <b>")
    for _tool, other in _all(tmp_path):
        rendered = _notice("fa", other)
        assert PERSIAN.search(rendered.split("·", 1)[1]), other


def test_a_russian_notice(tmp_path):
    reason = safety.tool_violation("WebSearch", (), ("Read",))
    text = _notice("ru", reason)
    assert CYRILLIC.search(text) and "WebSearch" in text and "allow_tools" in text
    assert "not in this role" not in text
    for _tool, other in _all(tmp_path):
        rendered = _notice("ru", other)
        assert CYRILLIC.search(rendered.split("·", 1)[1]), other
        assert not any(m in rendered for m in ("\u200f", FSI, PDI))


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_an_unknown_reason_is_shown_as_received(code):
    reason = "an operator's own <rule> & more"
    assert safety_notice.parse_reason(reason) is None
    text = _notice(code, reason)
    assert "an operator&#x27;s own &lt;rule&gt; &amp; more" in text


def test_an_unknown_reason_is_isolated_in_persian():
    """An English sentence aipager did not write sits inside the Persian
    line as one isolated value, so its punctuation keeps its order."""
    text = _notice("fa", "an operator's own <rule> & more")
    assert f"{FSI}an operator&#x27;s own &lt;rule&gt; &amp; more{PDI}" in text
    for code in ("en", "ru"):
        assert not any(m in _notice(code, "an operator's own <rule>") for m in (FSI, PDI))


def test_only_enforces_repeat_is_sticky(tmp_path):
    reasons = _all(tmp_path)
    sticky = [reason for _tool, reason in reasons if safety_notice.is_sticky(reason)]
    assert sticky == [reasons[-2][1]] and sticky[0].startswith("session halted")
    # The older wording (an em dash) is the same repeat.
    assert safety_notice.is_sticky("session halted — a prior tool call …")
    assert not safety_notice.is_sticky("Bash command blocked by safety policy")
    assert not safety_notice.is_sticky(None)


def _bot(mk_bot, monkeypatch, code):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    preferences.set_ui_language(CHAT, code)
    sess = TrackedSession(name="claude-x1__g1001", label="x1", status=Status.BUSY)
    sess.scope_chat_id = CHAT
    bot.registry._sessions[sess.name] = sess
    return bot, sess


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_a_sticky_repeat_does_not_halt_again(mk_bot, run_async, monkeypatch, tmp_path, code):
    from aipager import audit as audit_mod
    records = []
    monkeypatch.setattr(audit_mod, "append", lambda **k: records.append(k))
    bot, sess = _bot(mk_bot, monkeypatch, code)
    halted = p3a.enforce_reasons(tmp_path)[0][1]
    run_async(bot.notify(sess, "safety_blocked", {"tool": "Bash", "reason": halted}))
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.status == Status.BUSY
    assert records and records[0]["action"] == "Blocked"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_audit_keeps_the_english_reason(mk_bot, run_async, monkeypatch, code):
    """The chat reads the block in its language; the audit keeps exactly
    what Claude was told."""
    from aipager import audit as audit_mod
    records = []
    monkeypatch.setattr(audit_mod, "append", lambda **k: records.append(k))
    bot, sess = _bot(mk_bot, monkeypatch, code)
    reason = safety.tool_violation("Bash", ("Bash",), ())
    run_async(bot.notify(sess, "safety_blocked", {"tool": "Bash", "reason": reason}))
    shown = bot._app.bot.send_message.await_args.args[1]
    assert reason not in shown and "Bash" in shown
    assert records[0]["summary"] == reason and records[0]["reason"] == reason
    assert sess.status == Status.IDLE


def test_the_deny_claude_reads_is_unchanged_in_every_language(tmp_path):
    for code in ("fa", "ru"):
        preferences.set_ui_language(CHAT, code)
        for _tool, reason in _all(tmp_path):
            assert enforce.deny_decision_json(reason).count(reason) == 1


def test_safety_imports_nothing_from_the_daemon():
    """safety.py is in the hook's import closure: plain data only, never
    the daemon's modules or aipager.i18n (not even inside a function)."""
    tree = ast.parse(Path(safety.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert names == {"__future__", "fnmatch", "logging", "os", "re", "sys", "aipager"}
