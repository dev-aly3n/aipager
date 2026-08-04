"""Integration: static / structural contracts.

Success criteria covered:
  SC17 - aipager/md_to_tg.py still exists and passes ruff
  SC16 - new streaming fields absent from _PERSIST_FIELDS and present on TrackedSession

These are structural assertions that cannot flake — they test properties of
the codebase that are invariant across test runs.
"""

from __future__ import annotations

import importlib
import pathlib


# ── SC17 — md_to_tg.py still exists ──────────────────────────────────────────

def test_sc17_md_to_tg_module_exists():
    """aipager/md_to_tg.py must still exist (not deleted)."""
    # Resolve via the installed package location
    spec = importlib.util.find_spec("aipager.md_to_tg")
    assert spec is not None, "aipager.md_to_tg module not found — was md_to_tg.py deleted?"


def test_sc17_md_to_tg_importable():
    """aipager.md_to_tg must be importable without error."""
    mod = importlib.import_module("aipager.md_to_tg")
    assert mod is not None


def test_sc17_md_to_tg_file_on_disk():
    """The physical file aipager/md_to_tg.py must exist on disk."""
    # Walk up from this test file to find the project root
    here = pathlib.Path(__file__).resolve()
    # Go up: test file → telegram_rich_messages → integration → tests → project root
    project_root = here.parents[3]
    md_to_tg = project_root / "aipager" / "md_to_tg.py"
    assert md_to_tg.exists(), f"md_to_tg.py not found at {md_to_tg}"


# ── SC16 — new transient fields not in _PERSIST_FIELDS ───────────────────────

def test_sc16_new_fields_default_to_falsy():
    """New streaming fields default to falsy values as the spec requires."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert sess.stream_offset == 0
    assert sess.stream_transcript_path == ""
    assert sess.stream_pending == ""
    assert sess.stream_shown == ""
    assert sess.stream_dirty is False
    assert sess.stream_last_rendered == ""


def test_sc16_tracked_session_has_stream_offset_attribute():
    """TrackedSession must have stream_offset as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "stream_offset")


def test_sc16_tracked_session_has_stream_pending_attribute():
    """TrackedSession must have stream_pending as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "stream_pending")


def test_sc16_tracked_session_has_stream_shown_attribute():
    """TrackedSession must have stream_shown as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "stream_shown")


def test_sc16_tracked_session_no_draft_id():
    """TrackedSession must NOT have a draft_id attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert not hasattr(sess, "draft_id")


def test_sc16_tracked_session_no_stream_text():
    """TrackedSession must NOT have a stream_text attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert not hasattr(sess, "stream_text")


def test_sc16_persisted_state_excludes_stream_fields(tmp_path, monkeypatch):
    """Saving and reloading a session must not persist streaming transient fields."""
    import json
    from aipager.state import SessionRegistry, TrackedSession, Status

    target = tmp_path / "sessions.json"
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)

    registry = SessionRegistry()
    sess = TrackedSession(name="claude-persist-test", label="persist-test",
                          status=Status.IDLE)
    sess.stream_offset = 9999
    sess.stream_pending = "pending text"
    sess.stream_shown = "shown text"
    sess.stream_dirty = True
    sess.stream_last_rendered = "rendered"
    registry._sessions["claude-persist-test"] = sess

    registry.save()

    raw = json.loads(target.read_text())
    sessions_data = raw.get("sessions", {})
    persisted = sessions_data.get("claude-persist-test", {})

    assert "stream_offset" not in persisted
    assert "stream_pending" not in persisted
    assert "stream_shown" not in persisted
    assert "stream_dirty" not in persisted
    assert "stream_last_rendered" not in persisted
    assert "draft_id" not in persisted
    assert "stream_text" not in persisted


# ── exception types are importable ───────────────────────────────────────────

def test_rich_message_fallback_required_importable():
    """RichMessageFallbackRequired must be importable from the public surface."""
    from aipager.bot.rich_message import RichMessageFallbackRequired
    assert RichMessageFallbackRequired is not None


def test_rich_message_blocked_importable():
    """RichMessageBlocked must be importable from the public surface."""
    from aipager.bot.rich_message import RichMessageBlocked
    assert RichMessageBlocked is not None


def test_send_rich_message_importable():
    """send_rich_message must be importable."""
    from aipager.bot.rich_message import send_rich_message
    assert callable(send_rich_message)


def test_edit_message_text_rich_importable():
    """edit_message_text_rich must be importable."""
    from aipager.bot.rich_message import edit_message_text_rich
    assert callable(edit_message_text_rich)


def test_rich_message_gone_importable():
    """RichMessageGone must be importable from the public surface."""
    from aipager.bot.rich_message import RichMessageGone
    assert RichMessageGone is not None


def test_send_rich_message_draft_not_importable():
    """send_rich_message_draft must no longer exist in the module."""
    import importlib
    mod = importlib.import_module("aipager.bot.rich_message")
    assert not hasattr(mod, "send_rich_message_draft"), (
        "send_rich_message_draft still exists — the draft path was not removed"
    )


def test_detect_rtl_importable():
    """detect_rtl must be importable."""
    from aipager.bot.rich_message import detect_rtl
    assert callable(detect_rtl)


def test_close_client_importable():
    """close_client must be importable."""
    from aipager.bot.rich_message import close_client
    assert callable(close_client)


def test_read_turn_text_importable():
    """read_turn_text must be importable from aipager.transcript."""
    from aipager.transcript import read_turn_text
    assert callable(read_turn_text)
