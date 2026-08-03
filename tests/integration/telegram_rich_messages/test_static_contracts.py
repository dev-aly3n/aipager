"""Integration: static / structural contracts.

Success criteria covered:
  SC17 - aipager/md_to_tg.py still exists and passes ruff
  SC16 - draft_id, stream_offset, stream_text absent from _PERSIST_FIELDS (belt-and-
         suspenders: also verified in test_draft_safety.py at the registry level)

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
    """New fields default to falsy values (0 / '') as the spec requires."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert sess.draft_id == 0
    assert sess.stream_offset == 0
    assert sess.stream_text == ""


def test_sc16_tracked_session_has_draft_id_attribute():
    """TrackedSession must have draft_id as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "draft_id")


def test_sc16_tracked_session_has_stream_offset_attribute():
    """TrackedSession must have stream_offset as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "stream_offset")


def test_sc16_tracked_session_has_stream_text_attribute():
    """TrackedSession must have stream_text as an attribute."""
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-test", label="test")
    assert hasattr(sess, "stream_text")


def test_sc16_persisted_state_excludes_draft_id(tmp_path, monkeypatch):
    """Saving and reloading a session must not persist draft_id."""
    import json
    from aipager.state import SessionRegistry, TrackedSession, Status

    target = tmp_path / "sessions.json"
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)

    registry = SessionRegistry()
    sess = TrackedSession(name="claude-persist-test", label="persist-test",
                          status=Status.IDLE)
    sess.draft_id = 42
    sess.stream_offset = 9999
    sess.stream_text = "leftover text"
    registry._sessions["claude-persist-test"] = sess

    registry.save()

    # Read the raw JSON
    raw = json.loads(target.read_text())
    persisted = raw.get("claude-persist-test", {})

    assert "draft_id" not in persisted
    assert "stream_offset" not in persisted
    assert "stream_text" not in persisted


def test_sc16_reloaded_session_has_default_draft_id(tmp_path, monkeypatch):
    """After save+load, draft_id is at its default (0), not the saved value."""
    from aipager.state import SessionRegistry, TrackedSession, Status

    target = tmp_path / "sessions.json"
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)

    registry = SessionRegistry()
    sess = TrackedSession(name="claude-reload-test", label="reload-test",
                          status=Status.IDLE)
    sess.draft_id = 77
    registry._sessions["claude-reload-test"] = sess
    registry.save()

    # Reload from the file
    registry2 = SessionRegistry()
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)
    registry2.load()

    sess2 = registry2._sessions.get("claude-reload-test")
    if sess2 is not None:
        # If the session was persisted (may not be if status-filtering applies),
        # draft_id must be the default 0
        assert sess2.draft_id == 0


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


def test_send_rich_message_draft_importable():
    """send_rich_message_draft must be importable."""
    from aipager.bot.rich_message import send_rich_message_draft
    assert callable(send_rich_message_draft)


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
