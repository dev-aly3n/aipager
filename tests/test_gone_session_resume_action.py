"""A gone session offers Resume; a live one offers Restart.

The ⋮ menu used to offer Restart for everything. For a GONE session that
can only fail — `_kill_and_relaunch_core` returns `not_live` — so the
user tapped twice to be told "[label] isn't running". Reported from live
use. Resume already existed on `/resume` and in the Mini App; the menu
built by the chat/Mini App parity ship was the one surface without it.
"""

from __future__ import annotations



from aipager.bot import session_parity as sp
from aipager.state import Status


def _cb(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def _labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _sess(bot, name, label, *, status, transcript=""):
    s = bot.registry.get_or_create(name)
    s.label = label
    s.claude_session_id = transcript
    bot.registry.transition(name, status)
    return s


# ---- the ⋮ menu ---------------------------------------------------------

def test_a_live_session_is_offered_restart_and_not_resume(mk_bot):
    bot = mk_bot()
    sess = _sess(bot, "claude-live1", "live1", status=Status.IDLE)

    _t, kb = sp._render_session_menu(bot, 555, sess)

    assert any("Restart" in t for t in _labels(kb))
    assert not any("Resume" in t for t in _labels(kb)), (
        "a running session cannot be resumed — that button would fail")


def test_a_gone_session_with_a_transcript_is_offered_resume_not_restart(mk_bot):
    """The operator's report: Restart on a gone session does nothing."""
    bot = mk_bot()
    sess = _sess(bot, "claude-gone1", "gone1", status=Status.GONE,
                 transcript="uuid-1")

    _t, kb = sp._render_session_menu(bot, 555, sess)

    assert any("Resume" in t for t in _labels(kb)), "gone session has no Resume"
    assert not any("Restart" in t for t in _labels(kb)), (
        "Restart on a gone session can only answer 'isn't running'")


def test_a_gone_session_without_a_transcript_is_offered_neither(mk_bot):
    """`_do_resume_core` refuses an empty `claude_session_id`
    (`no_transcript`), so offering Resume here would fail exactly the way
    Restart did."""
    bot = mk_bot()
    sess = _sess(bot, "claude-gone2", "gone2", status=Status.GONE)

    text, kb = sp._render_session_menu(bot, 555, sess)

    labels = _labels(kb)
    assert not any("Resume" in t for t in labels)
    assert not any("Restart" in t for t in labels)
    assert "resume" in text.lower(), (
        "the menu must say WHY it offers neither, not just show fewer buttons")


def test_delete_is_still_offered_only_for_gone_sessions(mk_bot):
    """Regression guard on the behaviour that was already correct."""
    bot = mk_bot()
    live = _sess(bot, "claude-live2", "live2", status=Status.IDLE)
    gone = _sess(bot, "claude-gone3", "gone3", status=Status.GONE,
                 transcript="uuid-2")

    assert not any("Delete" in t for t in _labels(sp._render_session_menu(bot, 555, live)[1]))
    assert any("Delete" in t for t in _labels(sp._render_session_menu(bot, 555, gone)[1]))


def test_every_menu_callback_fits_the_telegram_cap(mk_bot):
    """Including the new Resume row, at the longest name dtach permits."""
    bot = mk_bot()
    name = "claude-" + ("w" * 45) + "__d256113222"
    assert len(name) == 64
    sess = _sess(bot, name, "w" * 45, status=Status.GONE, transcript="uuid-3")

    _t, kb = sp._render_session_menu(bot, 256113222, sess)

    for cb in _cb(kb):
        assert len(cb.encode()) <= 64, f"{len(cb.encode())} bytes: {cb!r}"


# ---- the /resume picker -------------------------------------------------

def test_the_resume_picker_callbacks_fit_the_telegram_cap(mk_bot):
    """`callback_data=f"{s.name}:resume"` measured 71 bytes for a
    maximum-length name. Telegram rejects the WHOLE keyboard on one
    oversized row, so a single long-named gone session made the entire
    picker unusable."""
    bot = mk_bot()
    name = "claude-" + ("w" * 45) + "__d256113222"
    _sess(bot, name, "w" * 45, status=Status.GONE, transcript="uuid-4")

    _text, kb = bot._render_resume_picker(page=0, scope_chat_id=256113222)

    assert kb is not None
    for cb in _cb(kb):
        assert len(cb.encode()) <= 64, f"{len(cb.encode())} bytes: {cb!r}"


def test_the_picker_does_not_offer_sessions_that_cannot_be_resumed(mk_bot):
    """A gone session with no transcript fails `_do_resume_core` with
    `no_transcript` — listing it is the same broken promise as Restart
    on a gone session."""
    bot = mk_bot()
    _sess(bot, "claude-keeps", "keeps", status=Status.GONE, transcript="uuid-5")
    _sess(bot, "claude-nope", "nope", status=Status.GONE)

    text, kb = bot._render_resume_picker(page=0, scope_chat_id=None)

    shown = " ".join(_labels(kb)) if kb else ""
    assert "keeps" in shown
    assert "nope" not in shown, "offered a session that cannot be resumed"
    # NOT `assert "1" in text`: that passed on the broken code because the
    # pre-existing "(1 total)" header contains a "1". Assert the sentence.
    assert "no saved transcript" in text and "1 more" in text, (
        "hiding a session silently is its own confusion — say how many "
        f"were left out: {text!r}")


def test_a_picker_with_nothing_resumable_says_so(mk_bot):
    bot = mk_bot()
    _sess(bot, "claude-nope2", "nope2", status=Status.GONE)

    text, kb = bot._render_resume_picker(page=0, scope_chat_id=None)

    assert kb is None
    assert "resume" in text.lower()
    assert "no saved transcript" in text


# ---- tapping Resume -----------------------------------------------------

def test_tapping_resume_resumes_through_the_shared_core(mk_bot, run_async):
    """One resume implementation, not a fourth: the button must reach
    `_do_resume_core`, the same seam `/resume` and the Mini App use."""
    from unittest.mock import AsyncMock, MagicMock

    bot = mk_bot()
    sess = _sess(bot, "claude-r1", "r1", status=Status.GONE, transcript="uuid-9")
    bot._safe_answer = AsyncMock()
    query = MagicMock()
    query.from_user = MagicMock(id=1)
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=1)

    claimed = run_async(sp.handle_callback(
        bot, MagicMock(), query, sess.name, "resume"))
    assert claimed is True

    # Step 1 offers the same Ask/Auto choice `/resume` does.
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    verbs = [b.callback_data.rsplit(":", 1)[-1]
             for row in kb.inline_keyboard for b in row]
    assert set(verbs) == {"resume-ask", "resume-auto", "resume-cancel"}

    # Step 2 goes through `_do_resume`, the wrapper /resume's own mode
    # buttons call — not a second resume implementation.
    bot._do_resume = AsyncMock()
    run_async(sp.handle_callback(
        bot, MagicMock(), query, sess.name, "resume-auto"))
    bot._do_resume.assert_awaited_once()
    assert bot._do_resume.await_args.kwargs["skip_perms_override"] is True
    assert bot._do_resume.await_args.kwargs["label"] == "r1"


def test_resume_is_refused_for_a_caller_who_cannot_prompt(mk_bot, run_async):
    """Re-checked at TAP time — a gone session's menu can sit on screen
    for days before anyone touches it."""
    from unittest.mock import AsyncMock, MagicMock

    bot = mk_bot()
    sess = _sess(bot, "claude-r2", "r2", status=Status.GONE, transcript="uuid-10")
    bot._can_prompt_user = MagicMock(return_value=False)
    bot._do_resume = AsyncMock()
    bot._safe_answer = AsyncMock()
    query = MagicMock()
    query.from_user = MagicMock(id=2)
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=1)

    run_async(sp.handle_callback(bot, MagicMock(), query, sess.name, "resume"))

    bot._do_resume.assert_not_awaited()


def test_resume_on_a_session_that_came_back_to_life_re_renders(mk_bot, run_async):
    """State can move under the button. Rather than falling into
    `_do_resume_core`'s `not_gone`, show the menu as it now is."""
    from unittest.mock import AsyncMock, MagicMock

    bot = mk_bot()
    sess = _sess(bot, "claude-r3", "r3", status=Status.IDLE, transcript="uuid-11")
    bot._do_resume = AsyncMock()
    bot._safe_answer = AsyncMock()
    query = MagicMock()
    query.from_user = MagicMock(id=1)
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=1)

    run_async(sp.handle_callback(bot, MagicMock(), query, sess.name, "resume"))

    bot._do_resume.assert_not_awaited()
    kb = query.edit_message_text.await_args.kwargs.get("reply_markup")
    assert kb is not None and any("Restart" in b.text
                                  for row in kb.inline_keyboard for b in row), (
        "a session that is running again should be offered Restart")


def test_the_menu_note_does_not_assert_how_the_session_ended(mk_bot):
    """`_do_resume_core` clears `claude_session_id` BEFORE launching, so a
    session mid-resume also reads as GONE-with-no-id for up to a few
    seconds. The note used to say the session "ended before it saved a
    transcript" — false in that window. It must state the present fact
    without claiming a history it cannot know.
    """
    bot = mk_bot()
    sess = _sess(bot, "claude-midflight", "midflight", status=Status.GONE)

    text, _kb = sp._render_session_menu(bot, 555, sess)

    assert "no saved transcript" in text.lower()
    assert "ended before" not in text.lower(), (
        "the note asserts how the session ended, which is wrong for a "
        "session being resumed right now")
