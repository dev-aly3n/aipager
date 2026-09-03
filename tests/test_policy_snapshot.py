"""Phase E: per-session policy snapshot."""

from __future__ import annotations

import json

from aipager import policy_snapshot as ps
from aipager import safety
from aipager.policy import load_policy
from aipager.scope import Member, Scope

# Captured at import time, before the suite-wide autouse
# `_isolate_notes_dir` fixture (tests/conftest.py) can monkeypatch it —
# the one test in this file that checks the REAL production path needs
# the genuine function, not the tmp_path-redirected one every other
# test in the suite relies on.
_REAL_NOTES_DIR = ps.notes_dir


def _policy():
    return load_policy()


def test_owner_bypass():
    pol = _policy()
    snap = ps.resolve_snapshot(pol.get_role("owner"), None, None)
    assert snap["bypass_safety"] is True


def test_user_gets_floor():
    pol = _policy()
    scope = Scope(chat_id=-1, kind="group", label="g",
                  members=(), deny_tools=("Bash",))
    member = Member(id=2, label="bob", role="user", deny_tools=("WebFetch",))
    snap = ps.resolve_snapshot(pol.get_role("user"), scope, member)
    assert snap["bypass_safety"] is False
    # safety floor present
    assert "~/.claude/**" in snap["deny_paths_no_access"]
    assert r"\bclaude\b" in snap["deny_bash_patterns"]
    # scope + member tool denies unioned
    assert "Bash" in snap["deny_tools"]
    assert "WebFetch" in snap["deny_tools"]


def test_admin_keeps_floor_but_skips_role_denies():
    pol = _policy()
    scope = Scope(chat_id=-1, kind="group", label="g",
                  members=(), deny_tools=("Bash",))
    snap = ps.resolve_snapshot(pol.get_role("admin"), scope, None)
    # admin bypasses role/scope deny_tools …
    assert "Bash" not in snap["deny_tools"]
    # … but the hard floor still applies (admin isn't owner)
    assert "~/.claude/**" in snap["deny_paths_no_access"]
    assert snap["bypass_safety"] is False


def test_write_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    p = tmp_path / "claude-x__d1.json"
    data = json.loads(p.read_text())
    assert data["origin"] == "telegram"
    assert "~/.claude/**" in data["deny_paths_no_access"]
    ps.clear_snapshot("claude-x__d1")
    assert not p.exists()


def test_floor_constants_match():
    snap = ps.resolve_snapshot(None, None, None)
    assert set(safety.DENY_PATHS_NO_ACCESS) <= set(snap["deny_paths_no_access"])


# ---- style_text field (item 6.2) -----------------------------------------

def test_resolve_snapshot_style_text_defaults_empty():
    snap = ps.resolve_snapshot(None, None, None)
    assert snap["style_text"] == ""


def test_resolve_snapshot_carries_style_text():
    snap = ps.resolve_snapshot(None, None, None, style_text="Keep it short.")
    assert snap["style_text"] == "Keep it short."


def test_write_snapshot_persists_style_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      style_text="Use simple, everyday words.")
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["style_text"] == "Use simple, everyday words."


def test_write_snapshot_style_text_defaults_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["style_text"] == ""


# ---- reply_context field (design.md "reply context") ----------------------

def test_resolve_snapshot_reply_context_defaults_empty():
    snap = ps.resolve_snapshot(None, None, None)
    assert snap["reply_context"] == ""


def test_resolve_snapshot_carries_reply_context():
    snap = ps.resolve_snapshot(None, None, None, reply_context="pointing at msg X")
    assert snap["reply_context"] == "pointing at msg X"


def test_write_snapshot_persists_reply_context(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      reply_context="pointing at an older message")
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["reply_context"] == "pointing at an older message"


def test_write_snapshot_reply_context_defaults_empty_and_clears_a_prior_value(
    tmp_path, monkeypatch,
):
    """The staleness guard (design.md): a caller that omits reply_context
    must overwrite a PRIOR turn's non-empty value with "", never leave
    it in place. Named specifically so it can serve as the mutation
    target for write_snapshot's own default."""
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      reply_context="stale pointer from turn 1")
    data1 = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data1["reply_context"] == "stale pointer from turn 1"

    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    data2 = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data2["reply_context"] == ""


# ---- reply-context /tmp file (design.md Part 2/5) --------------------------

def test_reply_context_path_uses_the_documented_filename():
    assert ps.reply_context_path("claude-jim") == (
        __import__("pathlib").Path("/tmp/claude-reply-claude-jim.txt")
    )


def test_write_reply_context_file_is_atomic_0600_and_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.write_reply_context_file("claude-x", "Header:", "the full replied-to text")
    p = tmp_path / "claude-x.txt"
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"
    content = p.read_text()
    assert "Header:" in content
    assert "the full replied-to text" in content


def test_write_reply_context_file_caps_full_text_at_4000_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    long_text = "x" * 6000
    ps.write_reply_context_file("claude-x", "", long_text)
    content = (tmp_path / "claude-x.txt").read_text()
    assert len(content) == 4000
    assert content == "x" * 4000


def test_clear_reply_context_file_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.write_reply_context_file("claude-x", "h", "full text")
    assert (tmp_path / "claude-x.txt").exists()
    ps.clear_reply_context_file("claude-x")
    assert not (tmp_path / "claude-x.txt").exists()


def test_clear_reply_context_file_is_a_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.clear_reply_context_file("claude-never-existed")  # must not raise


def test_clear_session_files_removes_both_policy_and_reply_context_files(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")
    pol = _policy()
    ps.write_snapshot("claude-x", pol.get_role("user"), None, None)
    ps.write_reply_context_file("claude-x", "h", "full text")
    assert (tmp_path / "claude-x.policy.json").exists()
    assert (tmp_path / "claude-x.reply.txt").exists()

    ps.clear_session_files("claude-x")

    assert not (tmp_path / "claude-x.policy.json").exists()
    assert not (tmp_path / "claude-x.reply.txt").exists()


def test_clear_session_files_also_clears_the_notes_dir(tmp_path, monkeypatch):
    """design.md's file plan: clear_session_files also clears the notes
    dir, so nothing lingers to restrict an unrelated future turn."""
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")
    ps.write_note(
        "claude-x", None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body="hi", raw_text="hi",
    )
    assert ps.list_outstanding_notes("claude-x") != []

    ps.clear_session_files("claude-x")

    assert ps.list_outstanding_notes("claude-x") == []
    assert not ps.notes_dir("claude-x").exists()


# ---- queue handoff: notes (design.md) --------------------------------------

def test_notes_dir_naming():
    assert _REAL_NOTES_DIR("claude-jim") == __import__("pathlib").Path(
        "/tmp/claude-notes-claude-jim")


def test_write_note_carries_resolved_permissions_plus_note_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    pol = _policy()
    scope = Scope(chat_id=-1, kind="group", label="g", members=(),
                  deny_tools=("Bash",))
    member = Member(id=2, label="bob", role="user")
    path = ps.write_note(
        "claude-x", pol.get_role("user"), scope, member,
        msg_id=42, chat_id=-1001, sender_key=(-1001, 2),
        body="[via Telegram]\nfix it", raw_text="fix it",
        style_text="Keep it short.", reply_context="pointing at msg 1",
    )
    assert path is not None
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"
    data = json.loads(path.read_text())
    assert data["msg_id"] == 42
    assert data["chat_id"] == -1001
    assert data["sender_key"] == [-1001, 2]
    assert data["body"] == "[via Telegram]\nfix it"
    assert data["raw_text"] == "fix it"
    assert data["style_text"] == "Keep it short."
    assert data["reply_context"] == "pointing at msg 1"
    assert "Bash" in data["deny_tools"]  # resolved permission fields present
    assert "queued_at" in data


def test_write_note_returns_none_on_directory_creation_failure(tmp_path, monkeypatch):
    # Point notes_dir at a path that can't be a directory (a file already
    # occupies it) — write_note must degrade to None, not raise.
    blocker = tmp_path / "blocked"
    blocker.write_text("occupied")
    monkeypatch.setattr(ps, "notes_dir", lambda n: blocker)
    result = ps.write_note(
        "claude-x", None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1), body="x", raw_text="x",
    )
    assert result is None


def test_list_outstanding_notes_oldest_first(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    base = time.time()
    for i, body in enumerate(("first", "second", "third")):
        p = ps.write_note(
            "claude-x", None, None, None,
            msg_id=i, chat_id=1, sender_key=(1, 1), body=body, raw_text=body,
        )
        data = json.loads(p.read_text())
        data["queued_at"] = base + i  # explicit, deterministic order
        p.write_text(json.dumps(data))
    notes = ps.list_outstanding_notes("claude-x")
    assert [n["body"] for n in notes] == ["first", "second", "third"]


def test_list_outstanding_notes_empty_dir_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / "does-not-exist")
    assert ps.list_outstanding_notes("claude-x") == []


def test_list_outstanding_notes_skips_corrupt_files(tmp_path, monkeypatch):
    import time
    d = tmp_path / "notes-claude-x"
    d.mkdir()
    (d / "1-aaaa.json").write_text("{ not json")
    (d / "2-bbbb.json").write_text(
        json.dumps({"body": "ok", "queued_at": time.time()}))
    monkeypatch.setattr(ps, "notes_dir", lambda n: d)
    notes = ps.list_outstanding_notes("claude-x")
    assert [n["body"] for n in notes] == ["ok"]


def test_list_outstanding_notes_prunes_ttl_expired_and_reports_them(tmp_path, monkeypatch):
    from aipager.state import QUEUE_MAX_AGE_SECONDS
    d = tmp_path / "notes-claude-x"
    d.mkdir()
    now = 2_000_000.0
    (d / "old.json").write_text(json.dumps(
        {"body": "ancient", "queued_at": now - QUEUE_MAX_AGE_SECONDS - 1}))
    (d / "new.json").write_text(json.dumps({"body": "fresh", "queued_at": now}))
    monkeypatch.setattr(ps, "notes_dir", lambda n: d)

    expired: list = []
    notes = ps.list_outstanding_notes("claude-x", now=now, expired_out=expired)

    assert [n["body"] for n in notes] == ["fresh"]
    assert [n["body"] for n in expired] == ["ancient"]
    assert not (d / "old.json").exists()  # actually unlinked, not just excluded
    assert (d / "new.json").exists()


def test_delete_notes_only_removes_the_given_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(1, 1), body="keep", raw_text="keep")
    ps.write_note("claude-x", None, None, None, msg_id=2, chat_id=1,
                  sender_key=(1, 1), body="drop", raw_text="drop")
    notes = ps.list_outstanding_notes("claude-x")
    to_delete = [n for n in notes if n["body"] == "drop"]

    ps.delete_notes("claude-x", to_delete)

    remaining = ps.list_outstanding_notes("claude-x")
    assert [n["body"] for n in remaining] == ["keep"]


def test_clear_notes_dir_removes_everything_and_returns_the_count(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    for i in range(3):
        ps.write_note("claude-x", None, None, None, msg_id=i, chat_id=1,
                      sender_key=(1, 1), body=f"m{i}", raw_text=f"m{i}")
    removed = ps.clear_notes_dir("claude-x")
    assert removed == 3
    assert ps.list_outstanding_notes("claude-x") == []


def test_clear_notes_dir_on_empty_or_missing_dir_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / "never-existed")
    assert ps.clear_notes_dir("claude-x") == 0


def test_outstanding_sender_keys_collects_distinct_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(100, 1), body="a", raw_text="a")
    ps.write_note("claude-x", None, None, None, msg_id=2, chat_id=1,
                  sender_key=(100, 2), body="b", raw_text="b")
    ps.write_note("claude-x", None, None, None, msg_id=3, chat_id=1,
                  sender_key=(100, 1), body="c", raw_text="c")  # dup key
    keys = ps.outstanding_sender_keys("claude-x")
    assert keys == {(100, 1), (100, 2)}


def test_outstanding_sender_keys_empty_when_no_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / "empty")
    assert ps.outstanding_sender_keys("claude-x") == set()


def test_combined_queue_depth_sums_pending_queue_and_outstanding_notes(
    tmp_path, monkeypatch,
):
    class _Sess:
        name = "claude-x"
        pending_queue = [("a", 1, 0.0, ""), ("b", 2, 0.0, "")]

    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(1, 1), body="note1", raw_text="note1")
    assert ps.combined_queue_depth(_Sess()) == 3  # 2 queued + 1 note


def test_write_merged_snapshot_round_trips_through_read_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    ps.write_merged_snapshot("claude-x", ps.merge_snapshots([]))
    assert ps.read_snapshot("claude-x") == ps.FLOOR_SNAPSHOT


def test_queue_depth_parts_reports_the_two_components_separately(
    tmp_path, monkeypatch,
):
    """intent.md requirement 4: a display surface must be able to show
    "N queued, M notes" instead of one opaque total — this is the
    function both /status and the dashboard call for that breakdown."""
    class _Sess:
        name = "claude-x"
        pending_queue = [("a", 1, 0.0, "")]

    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(1, 1), body="note1", raw_text="note1")
    ps.write_note("claude-x", None, None, None, msg_id=2, chat_id=1,
                  sender_key=(1, 1), body="note2", raw_text="note2")

    queued, notes = ps.queue_depth_parts(_Sess())

    assert (queued, notes) == (1, 2)
    assert queued + notes == ps.combined_queue_depth(_Sess())


# ---- requirement 3: the mixed-sender hold only consults recent notes -----

def test_outstanding_sender_keys_excludes_notes_past_the_hold_window(
    tmp_path, monkeypatch,
):
    from aipager.state import MIXED_SENDER_HOLD_WINDOW_SECONDS

    d = tmp_path / "notes-claude-x"
    d.mkdir()
    now = 2_000_000.0
    (d / "old.json").write_text(json.dumps({
        "sender_key": [1, 1],
        "queued_at": now - MIXED_SENDER_HOLD_WINDOW_SECONDS - 1,
    }))
    (d / "fresh.json").write_text(json.dumps({
        "sender_key": [1, 2],
        "queued_at": now,
    }))
    monkeypatch.setattr(ps, "notes_dir", lambda n: d)

    keys = ps.outstanding_sender_keys("claude-x", now=now)

    assert keys == {(1, 2)}, (
        "a note older than the hold window still counted toward the "
        f"mixed-sender hold: {keys}")


def test_outstanding_sender_keys_still_full_TTL_reach_with_explicit_max_age(
    tmp_path, monkeypatch,
):
    """The narrower default is opt-out: a caller that explicitly wants
    the full (list_outstanding_notes-length) window can still get it."""
    from aipager.state import MIXED_SENDER_HOLD_WINDOW_SECONDS, QUEUE_MAX_AGE_SECONDS

    d = tmp_path / "notes-claude-x"
    d.mkdir()
    now = 2_000_000.0
    (d / "old.json").write_text(json.dumps({
        "sender_key": [1, 1],
        "queued_at": now - MIXED_SENDER_HOLD_WINDOW_SECONDS - 1,
    }))
    monkeypatch.setattr(ps, "notes_dir", lambda n: d)

    keys = ps.outstanding_sender_keys(
        "claude-x", now=now, max_age=QUEUE_MAX_AGE_SECONDS)

    assert keys == {(1, 1)}


# ---- requirement 2: absorbed mid-turn notes must not outlive their turn --

def test_expire_notes_after_turn_end_removes_snapshotted_survivors(
    tmp_path, monkeypatch, run_async,
):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(0, 1), body="absorbed", raw_text="absorbed")
    pre_notes = ps.list_outstanding_notes("claude-x")
    assert len(pre_notes) == 1, "setup assumption broke"

    removed = run_async(
        ps.expire_notes_after_turn_end("claude-x", pre_notes, grace=0))

    assert removed == 1
    assert ps.list_outstanding_notes("claude-x") == []


def test_expire_notes_after_turn_end_leaves_an_already_picked_up_note_alone(
    tmp_path, monkeypatch, run_async,
):
    """If the normal pick-up path (a real UserPromptSubmit match) already
    deleted the note before the grace period elapses, the sweep must
    not error or otherwise misbehave — there's simply nothing left to
    remove."""
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(0, 1), body="picked-up", raw_text="picked-up")
    pre_notes = ps.list_outstanding_notes("claude-x")
    assert len(pre_notes) == 1

    ps.delete_notes("claude-x", pre_notes)  # simulates a normal pick-up

    removed = run_async(
        ps.expire_notes_after_turn_end("claude-x", pre_notes, grace=0))

    assert removed == 0
    assert ps.list_outstanding_notes("claude-x") == []


def test_expire_notes_after_turn_end_never_touches_a_note_written_after_the_snapshot(
    tmp_path, monkeypatch, run_async,
):
    """A note a LATER turn writes (e.g. the held-and-queued drain firing
    on the same turn-end) must survive a sweep armed by an EARLIER
    turn's snapshot, even if it happens to still be outstanding when
    that earlier sweep's grace period elapses."""
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    ps.write_note("claude-x", None, None, None, msg_id=1, chat_id=1,
                  sender_key=(0, 1), body="old-turn", raw_text="old-turn")
    pre_notes = ps.list_outstanding_notes("claude-x")

    # A later turn's own injection writes a brand-new note AFTER the
    # snapshot was taken.
    ps.write_note("claude-x", None, None, None, msg_id=2, chat_id=1,
                  sender_key=(0, 1), body="new-turn", raw_text="new-turn")

    removed = run_async(
        ps.expire_notes_after_turn_end("claude-x", pre_notes, grace=0))

    assert removed == 1
    remaining = ps.list_outstanding_notes("claude-x")
    assert [n["body"] for n in remaining] == ["new-turn"]


def test_expire_notes_after_turn_end_is_a_cheap_noop_with_nothing_outstanding(
    tmp_path, monkeypatch, run_async,
):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / "empty")
    removed = run_async(ps.expire_notes_after_turn_end("claude-x", grace=999))
    assert removed == 0  # returns immediately — never actually sleeps 999s
