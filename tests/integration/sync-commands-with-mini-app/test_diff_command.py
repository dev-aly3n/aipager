"""Priority 6 (task brief): /diff's six failure modes, the
inline-vs-attachment threshold, and that nothing is written to disk.

entrypoints.md's `/diff` behavior contract table is the spec under
test. `aipager.miniapp.diff.collect_diff` is the one external boundary
this command depends on (it shells out to real `git`) — mocked here per
the black-box mandate to mock external boundaries, never the code under
test. The exact 3500-character inline/attachment threshold was
determined empirically (bisection against the real, unmocked command)
and is asserted at both boundary points.
"""

from __future__ import annotations

import asyncio
import builtins
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_bot():
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry
    bot = TelegramBot(SessionRegistry())
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


def _make_update(text="/diff foo"):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 1
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.message.reply_to_message = None
    update.message.quote = None
    update.effective_user = MagicMock()
    update.effective_user.id = 1
    update.effective_chat = MagicMock()
    update.effective_chat.id = 0
    update.effective_chat.type = "private"
    return update


def _diff_result(result):
    async def _fake(cwd):
        return result
    return _fake


def _run_diff(bot, update, result):
    with patch("aipager.miniapp.diff.collect_diff",
               side_effect=_diff_result(result)):
        _run(session_parity.handle_diff_cmd(bot, update, MagicMock()))


# --------------------------------------------------------------------------- #
# The six failure/edge modes from entrypoints.md's own table.               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reason,expected_fragment", [
    ("cwd_missing", "working directory is no longer there"),
    ("git_not_installed", "git isn't available"),
    ("not_a_git_repo", "isn't a git repo"),
    ("no_commits_yet", "no commits yet"),
    ("git_error", "try again"),
])
def test_diff_failure_reason_produces_the_documented_fallback_text(
        reason, expected_fragment):
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    _run_diff(bot, update, {"available": False, "reason": reason})

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert expected_fragment in text.lower(), text
    # Never a stack trace, never a silent no-op.
    assert "traceback" not in text.lower()


def test_diff_failure_never_sends_a_document(mk_bot=None):
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    _run_diff(bot, update, {"available": False, "reason": "git_error"})
    update.message.reply_document.assert_not_awaited()


def test_diff_empty_changeset_says_no_changes():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    _run_diff(bot, update, {"available": True, "files": [],
                            "files_truncated": False})

    text = update.message.reply_text.await_args[0][0]
    assert "no changes" in text.lower(), text
    update.message.reply_document.assert_not_awaited()


def test_diff_unexpected_exception_from_collect_diff_never_becomes_a_stack_trace():
    """Error guessing: collect_diff is documented to "never raise", but
    a black-box test must not assume the caller trusts that promise
    blindly — if collect_diff somehow raises, /diff must not leak a
    traceback into the chat."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    async def _boom(cwd):
        raise RuntimeError("boom")

    with patch("aipager.miniapp.diff.collect_diff", side_effect=_boom):
        try:
            _run(session_parity.handle_diff_cmd(bot, update, MagicMock()))
        except RuntimeError:
            pytest.fail(
                "handle_diff_cmd let collect_diff's exception propagate "
                "instead of surfacing a chat-safe error"
            )


def test_diff_unexpected_exception_degrades_to_the_documented_git_error_text():
    """Stronger than the "doesn't crash" test above: entrypoints.md maps
    ``reason: "git_error"`` to the specific fallback text "Couldn't read
    the diff right now — try again." An unhandled exception from
    collect_diff is exactly the scenario that text exists for (a git
    shell-out gone wrong), so a caught exception must produce THAT text
    — not a generic/different error, and not silence (no reply at all)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    async def _boom(cwd):
        raise RuntimeError("boom")

    with patch("aipager.miniapp.diff.collect_diff", side_effect=_boom):
        _run(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "try again" in text.lower(), text
    assert "traceback" not in text.lower()
    update.message.reply_document.assert_not_awaited()


def test_diff_exception_from_collect_diff_does_not_touch_the_registry():
    """The exception path must be purely a rendering fallback — it must
    never mutate session/registry state as a side effect of handling
    the failure."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    async def _boom(cwd):
        raise OSError("disk exploded")

    with patch("aipager.miniapp.diff.collect_diff", side_effect=_boom):
        _run(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    assert bot.registry._sessions[sess.name] is sess
    assert sess.status == Status.IDLE


# --------------------------------------------------------------------------- #
# Inline vs. attachment threshold (boundary-value analysis).                #
# --------------------------------------------------------------------------- #

def _file_result(patch_text, *, truncated=False, binary=False):
    return {
        "available": True,
        "files": [{
            "path": "f.py", "change_type": "modified", "binary": binary,
            "patch": patch_text, "truncated": truncated,
        }],
        "files_truncated": False,
    }


def test_diff_stat_summary_line_format():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    patch_text = "--- a/f.py\n+++ b/f.py\n@@ -1 +1,2 @@\n+new\n old\n"
    _run_diff(bot, update, _file_result(patch_text))
    text = update.message.reply_text.await_args[0][0]
    assert "1 files changed" in text
    assert "+1" in text and "-0" in text


def test_diff_just_under_threshold_is_inline_not_attached():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    _run_diff(bot, update, _file_result("x" * 3499))
    text = update.message.reply_text.await_args[0][0]
    assert "<pre>" in text
    update.message.reply_document.assert_not_awaited()


def test_diff_at_threshold_is_attached_not_inline():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    _run_diff(bot, update, _file_result("x" * 3500))
    text = update.message.reply_text.await_args[0][0]
    assert "<pre>" not in text
    update.message.reply_document.assert_awaited_once()
    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs.get("filename") == "foo.diff"


def test_diff_binary_file_forces_attachment_even_under_threshold():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    _run_diff(bot, update, _file_result(None, binary=True))
    update.message.reply_document.assert_awaited_once()


def test_diff_truncated_file_forces_attachment_even_under_threshold():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()
    _run_diff(bot, update, _file_result("x" * 10, truncated=True))
    update.message.reply_document.assert_awaited_once()


def test_diff_attachment_content_is_never_written_to_disk():
    """entrypoints.md: the .diff document is "built in memory, never
    written to disk". Guards this by making any WRITE-mode `open()`
    call during the diff command raise — a regression to a temp-file
    implementation would fail this test immediately, while ordinary
    read-only filesystem activity elsewhere is unaffected."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    update = _make_update()

    real_open = builtins.open

    def _guard(path, mode="r", *a, **kw):
        if any(c in mode for c in ("w", "a", "x", "+")):
            raise AssertionError(
                f"/diff's document-attachment path attempted a disk "
                f"write: open({path!r}, {mode!r})"
            )
        return real_open(path, mode, *a, **kw)

    with patch.object(builtins, "open", _guard):
        _run_diff(bot, update, _file_result("x" * 4000))

    update.message.reply_document.assert_awaited_once()


# --------------------------------------------------------------------------- #
# No label -> registry.last_active_session; auth is allow_read_only=True.   #
# --------------------------------------------------------------------------- #

def test_diff_no_label_uses_last_active_session():
    bot = _make_bot()
    sess = TrackedSession(name="claude-foo", label="foo", status=Status.IDLE,
                           cwd="/tmp/x")
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    update = _make_update("/diff")

    _run_diff(bot, update, {"available": True, "files": [],
                            "files_truncated": False})

    text = update.message.reply_text.await_args[0][0]
    assert "foo" in text.lower()


def test_diff_readonly_member_is_authorized(mk_bot, helpers):
    """entrypoints.md: `/diff`'s auth gate is
    `bot._authorize(update, allow_read_only=True)` — a read_only-role
    member (who cannot prompt sessions at all) must still be able to
    read a diff."""
    CHAT = -100
    READONLY = 3
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(READONLY, "ro", "read_only")])
    sess = TrackedSession(name="claude-foo__g100", label="foo",
                           status=Status.IDLE, cwd="/tmp/x",
                           scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess

    update = helpers.make_message_update(
        "/diff foo", chat_id=CHAT, chat_type="group", user_id=READONLY)
    _run_diff(bot, update, {"available": True, "files": [],
                            "files_truncated": False})

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "not on" not in text.lower() and "not authorized" not in text.lower()
