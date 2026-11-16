"""The transcript token tally, black box (design.md Success criteria 7-10;
decisions.md 1 and 3; entrypoints.md "Inputs").

Driven only through ``usage_store.scan`` / ``snapshot`` with transcripts
written as Claude Code writes them. Partitions: counted lines (assistant
with usage) vs not (user lines, ``<synthetic>``, no usage, bad counts);
one message over many lines; files that grow (/compact, resume), new files
(/clear), copies (fork), replaced or truncated files, subagent files.
Boundaries: a partial trailing line, the scan budget, a line longer than
MAX_LINE_BYTES, the scan interval. Error guessing: bools and huge numbers
as counts, a line of garbage, a missing file, non-UTF-8 bytes."""

from __future__ import annotations

import json
import os
import time

import pytest

from aipager import usage_store
from aipager.state import SessionRegistry

SCOPE = -100777
OTHER = -100888


@pytest.fixture
def reg():
    return SessionRegistry()


@pytest.fixture
def tx(tmp_path):
    d = tmp_path / "projects" / "-home-alice-frontend"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def one(reg, tx, h):
    """One session with an empty transcript in SCOPE."""
    path = tx / "conv-a.jsonl"
    path.write_text("")
    h.add_session(reg, "claude-frontend", scope=SCOPE, transcript=path)
    return path


def _today(h, reg, scopes=None):
    h.scan(reg)
    return h.counts(h.snap(scopes).today)


T = time.time()


# ---- what counts -------------------------------------------------------------------

def test_one_assistant_line_counts_all_four(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20, 3000, 400))
    assert _today(h, reg) == (100, 20, 3000, 400)


def test_the_week_includes_today(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20, 3000, 400))
    h.scan(reg)
    assert h.counts(h.snap().week) == (100, 20, 3000, 400)


def test_user_lines_count_nothing(h, reg, one):
    h.append(one, h.user_line(T))
    assert _today(h, reg) == (0, 0, 0, 0)


def test_a_non_assistant_type_with_usage_counts_nothing(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20, typ="user"))
    assert _today(h, reg) == (0, 0, 0, 0)


def test_a_synthetic_message_counts_nothing(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20, model="<synthetic>"))
    assert _today(h, reg) == (0, 0, 0, 0)


def test_an_assistant_line_without_usage_counts_nothing(h, reg, one):
    e = h.entry("msg_1", T, 100, 20)
    del e["message"]["usage"]
    h.append(one, e)
    assert _today(h, reg) == (0, 0, 0, 0)


@pytest.mark.parametrize("bad", [True, -5, "100", 1e9, 10**9, 2.5, None],
                         ids=["bool", "negative", "string", "1e9-float", "1e9", "float", "none"])
def test_a_bad_count_is_zero_and_the_others_still_count(h, reg, one, bad):
    e = h.entry("msg_1", T, 100, 20, 30, 40)
    e["message"]["usage"]["input_tokens"] = bad
    h.append(one, e)
    assert _today(h, reg)[1:] == (20, 30, 40)


@pytest.mark.parametrize("bad", [True, -5, "100", 10**9],
                         ids=["bool", "negative", "string", "1e9"])
def test_a_bad_count_adds_nothing_for_that_count(h, reg, one, bad):
    e = h.entry("msg_1", T, 100, 20, 30, 40)
    e["message"]["usage"]["input_tokens"] = bad
    h.append(one, e)
    assert _today(h, reg)[0] == 0


def test_a_count_just_below_1e9_counts(h, reg, one):
    h.append(one, h.entry("msg_1", T, 999_999_999, 0))
    assert _today(h, reg)[0] == 999_999_999


@pytest.mark.parametrize("garbage", ["{not json", "[]", "null", '"assistant" "usage"',
                                     json.dumps({"type": "assistant", "message": "usage"})],
                         ids=["corrupt", "list", "null", "prefilter-bait", "message-str"])
def test_a_garbage_line_is_skipped_and_the_next_one_counts(h, reg, one, garbage):
    h.append(one, garbage, h.entry("msg_1", T, 7, 1))
    assert _today(h, reg) == (7, 1, 0, 0)


def test_non_utf8_bytes_are_skipped(h, reg, one):
    with open(one, "ab") as f:
        f.write(b'{"type":"assistant","usage":"\xff\xfe"}\n')
    h.append(one, h.entry("msg_1", T, 7, 1))
    assert _today(h, reg) == (7, 1, 0, 0)


# ---- each message once ---------------------------------------------------------------

def test_one_message_over_three_lines_counts_once(h, reg, one):
    """Claude Code writes one line per content block, each with the same usage."""
    h.append(one, *[h.entry("msg_1", T, 100, 20, 3000, 400) for _ in range(3)])
    assert _today(h, reg) == (100, 20, 3000, 400)


def test_a_later_line_with_more_output_adds_only_the_difference(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 5), h.entry("msg_1", T, 100, 25))
    assert _today(h, reg) == (100, 25, 0, 0)


def test_a_later_line_with_less_output_adds_nothing(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 25), h.entry("msg_1", T, 100, 5))
    assert _today(h, reg) == (100, 25, 0, 0)


def test_a_later_line_with_more_output_in_another_scan_adds_only_the_difference(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 5))
    h.scan(reg)
    h.append(one, h.entry("msg_1", T, 100, 25))
    assert _today(h, reg) == (100, 25, 0, 0)


def test_the_same_message_in_a_later_scan_counts_nothing_more(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20))
    h.scan(reg)
    h.append(one, h.entry("msg_1", T, 100, 20))
    assert _today(h, reg) == (100, 20, 0, 0)


def test_two_messages_add_up(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20), h.entry("msg_2", T, 1, 2, 3, 4))
    assert _today(h, reg) == (101, 22, 3, 4)


def test_a_line_without_an_id_counts(h, reg, one):
    h.append(one, h.entry("x", T, 9, 1, with_id=False))
    assert _today(h, reg) == (9, 1, 0, 0)


def test_a_copied_line_without_an_id_counts_once(h, reg, one, tx):
    e = h.entry("x", T, 9, 1, with_id=False)
    h.append(one, e)
    h.scan(reg)
    copy = tx / "conv-fork.jsonl"
    h.append(copy, e)
    h.add_session(reg, "claude-fork", scope=SCOPE, transcript=copy)
    assert _today(h, reg) == (9, 1, 0, 0)


def test_repeated_scans_of_an_unchanged_file_add_nothing(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20))
    for _ in range(4):
        h.scan(reg)
    assert _today(h, reg) == (100, 20, 0, 0)


# ---- /compact, resume, /clear, fork, rotation -------------------------------------------

def test_compact_appending_to_the_same_file_counts_only_the_new_lines(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20))
    h.scan(reg)
    h.append(one, {"type": "system", "subtype": "compact_boundary", "timestamp": h.iso(T)},
             h.entry("msg_2", T, 5, 5))
    assert _today(h, reg) == (105, 25, 0, 0)


def test_resume_after_a_daemon_restart_counts_only_the_new_lines(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20))
    h.scan(reg)
    usage_store.save_if_dirty(force=True)
    usage_store.reset_for_tests()
    usage_store.load()
    h.append(one, h.entry("msg_1", T, 100, 20), h.entry("msg_2", T, 5, 5))
    assert _today(h, reg) == (105, 25, 0, 0)


def test_clear_to_a_new_file_counts_both_files_once(h, reg, one, tx):
    h.append(one, h.entry("msg_1", T, 100, 20))
    h.scan(reg)
    h.append(one, h.entry("msg_2", T, 1, 1))          # the old file's tail
    new = tx / "conv-b.jsonl"
    h.append(new, h.entry("msg_3", T, 10, 10))
    reg.get("claude-frontend").transcript_path = str(new)
    assert _today(h, reg) == (111, 31, 0, 0)


def test_a_fork_with_copied_lines_counts_nothing_twice(h, reg, one, tx):
    lines = [h.entry("msg_1", T, 100, 20), h.entry("msg_2", T, 1, 1)]
    h.append(one, *lines)
    h.scan(reg)
    fork = tx / "conv-fork.jsonl"
    h.append(fork, *lines, h.entry("msg_9", T, 3, 3))
    h.add_session(reg, "claude-fork", scope=SCOPE, transcript=fork)
    assert _today(h, reg) == (104, 24, 0, 0)


def test_a_file_replaced_by_a_new_inode_counts_nothing_twice(h, reg, one, tx):
    lines = [h.entry("msg_1", T, 100, 20)]
    h.append(one, *lines)
    h.scan(reg)
    tmp = tx / "swap.tmp"
    h.append(tmp, *lines, h.entry("msg_2", T, 1, 1))
    os.replace(tmp, one)
    assert _today(h, reg) == (101, 21, 0, 0)


def test_a_replaced_file_with_new_ids_is_read_from_the_start(h, reg, one, tx):
    """A different inode resets the offset: a short replacement's lines count."""
    h.append(one, *[h.entry(f"msg_{i}", T, 1, 0) for i in range(20)])
    h.scan(reg)
    tmp = tx / "swap.tmp"
    h.append(tmp, h.entry("msg_new", T, 1000, 0))
    os.replace(tmp, one)
    assert _today(h, reg)[0] == 1020


def test_a_truncated_file_is_read_again_from_the_start(h, reg, one):
    h.append(one, *[h.entry(f"msg_{i}", T, 1, 0) for i in range(20)])
    h.scan(reg)
    one.write_text(json.dumps(h.entry("msg_new", T, 1000, 0)) + "\n")
    assert _today(h, reg)[0] == 1020


def test_a_truncated_file_with_old_lines_counts_nothing_twice(h, reg, one):
    lines = [h.entry(f"msg_{i}", T, 1, 0) for i in range(20)]
    h.append(one, *lines)
    h.scan(reg)
    one.write_text("")
    h.append(one, *lines[:3])
    assert _today(h, reg)[0] == 20


def test_a_missing_transcript_never_raises(h, reg, tx):
    h.add_session(reg, "claude-gone", scope=SCOPE, transcript=tx / "nope.jsonl")
    assert _today(h, reg) == (0, 0, 0, 0)


def test_a_session_without_a_transcript_never_raises(h, reg):
    s = reg.get_or_create("claude-blank")
    s.scope_chat_id = SCOPE
    assert _today(h, reg) == (0, 0, 0, 0)


def test_a_transcript_deleted_after_a_scan_keeps_its_counts(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 20))
    h.scan(reg)
    one.unlink()
    assert _today(h, reg) == (100, 20, 0, 0)


# ---- partial lines, budget, long lines, interval ----------------------------------------

def test_a_partial_trailing_line_is_not_counted(h, reg, one):
    whole = json.dumps(h.entry("msg_1", T, 100, 20))
    h.append(one, partial=whole[:-1])
    assert _today(h, reg) == (0, 0, 0, 0)


def test_a_partial_line_is_counted_once_complete(h, reg, one):
    whole = json.dumps(h.entry("msg_1", T, 100, 20))
    h.append(one, partial=whole[:40])
    h.scan(reg)
    with open(one, "a") as f:
        f.write(whole[40:] + "\n")
    assert _today(h, reg) == (100, 20, 0, 0)


def test_a_complete_line_without_a_newline_yet_is_not_counted(h, reg, one):
    """Claude Code may be between write() and the newline."""
    h.append(one, partial=json.dumps(h.entry("msg_1", T, 100, 20)))
    assert _today(h, reg) == (0, 0, 0, 0)


def _filler(n_bytes: int) -> list[str]:
    line = json.dumps({"type": "user", "timestamp": "x", "message": {"content": "f" * 1000}})
    return [line] * (n_bytes // (len(line) + 1) + 1)


def test_one_scan_stops_within_the_budget(h, reg, tx):
    """Five files, each larger than the whole budget: one scan cannot reach
    the usage line at the end of any of them."""
    for i in range(5):
        p = tx / f"big-{i}.jsonl"
        h.append(p, *_filler(usage_store.SCAN_BUDGET_BYTES), h.entry(f"msg_{i}", T, 1, 0))
        h.add_session(reg, f"claude-big{i}", scope=SCOPE, transcript=p)
    assert _today(h, reg)[0] == 0


def test_a_backlog_is_reported_as_behind(h, reg, one):
    h.append(one, *_filler(3 * usage_store.CHUNK_BYTES), h.entry("msg_1", T, 1, 0))
    h.scan(reg)
    assert h.snap().behind is True


def test_a_backlog_is_counted_after_enough_scans(h, reg, one):
    h.append(one, *_filler(3 * usage_store.CHUNK_BYTES), h.entry("msg_1", T, 1, 0))
    for _ in range(8):
        h.scan(reg)
    assert h.counts(h.snap().today)[0] == 1


def test_no_longer_behind_once_caught_up(h, reg, one):
    h.append(one, *_filler(3 * usage_store.CHUNK_BYTES), h.entry("msg_1", T, 1, 0))
    for _ in range(8):
        h.scan(reg)
    assert h.snap().behind is False


def test_a_small_file_is_not_behind_after_one_scan(h, reg, one):
    h.append(one, h.entry("msg_1", T, 1, 0))
    h.scan(reg)
    assert h.snap().behind is False


def test_a_line_longer_than_max_line_bytes_is_skipped_and_the_next_counts(h, reg, one):
    huge = json.dumps({"type": "assistant", "timestamp": h.iso(T), "message": {
        "id": "msg_huge", "model": "claude-opus-test",
        "usage": {"input_tokens": 999, "output_tokens": 0},
        "content": [{"type": "text", "text": "x" * (usage_store.MAX_LINE_BYTES + 10)}]}})
    h.append(one, huge, h.entry("msg_1", T, 7, 0))
    for _ in range(12):
        h.scan(reg)
    assert h.counts(h.snap().today)[0] == 7


def test_a_long_line_under_max_line_bytes_counts(h, reg, one):
    """Longer than a chunk, shorter than MAX_LINE_BYTES: read in one go."""
    long = json.dumps({"type": "assistant", "timestamp": h.iso(T), "message": {
        "id": "msg_long", "model": "claude-opus-test",
        "usage": {"input_tokens": 999, "output_tokens": 0},
        "content": [{"type": "text", "text": "x" * (usage_store.CHUNK_BYTES * 2)}]}})
    h.append(one, long)
    for _ in range(6):
        h.scan(reg)
    assert h.counts(h.snap().today)[0] == 999


def test_an_unforced_scan_within_the_interval_reads_nothing_new(h, reg, one):
    h.append(one, h.entry("msg_1", T, 1, 0))
    t0 = time.time()
    usage_store.scan(reg, now=t0, force=True)
    h.append(one, h.entry("msg_2", T, 10, 0))
    usage_store.scan(reg, now=t0 + 5)
    assert h.counts(usage_store.snapshot(None, now=t0 + 5).today)[0] == 1


def test_a_forced_scan_within_the_interval_reads(h, reg, one):
    h.append(one, h.entry("msg_1", T, 1, 0))
    t0 = time.time()
    usage_store.scan(reg, now=t0, force=True)
    h.append(one, h.entry("msg_2", T, 10, 0))
    usage_store.scan(reg, now=t0 + 1, force=True)
    assert h.counts(usage_store.snapshot(None, now=t0 + 1).today)[0] == 11


# ---- subagents and scopes ----------------------------------------------------------------

def _subagent(one, name="agent-a1b2.jsonl"):
    d = one.with_suffix("") / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def test_a_subagent_transcript_counts(h, reg, one):
    h.append(_subagent(one), h.entry("msg_sub", T, 50, 5))
    assert _today(h, reg) == (50, 5, 0, 0)


def test_two_subagent_transcripts_both_count(h, reg, one):
    h.append(_subagent(one, "agent-1.jsonl"), h.entry("msg_s1", T, 50, 5))
    h.append(_subagent(one, "agent-2.jsonl"), h.entry("msg_s2", T, 5, 5))
    assert _today(h, reg) == (55, 10, 0, 0)


def test_a_subagent_counts_under_the_parent_scope(h, reg, one):
    h.append(_subagent(one), h.entry("msg_sub", T, 50, 5))
    assert _today(h, reg, frozenset({SCOPE})) == (50, 5, 0, 0)


def test_a_subagent_is_not_in_another_scope(h, reg, one):
    h.append(_subagent(one), h.entry("msg_sub", T, 50, 5))
    assert _today(h, reg, frozenset({OTHER})) == (0, 0, 0, 0)


def test_a_file_not_named_agent_in_subagents_is_ignored(h, reg, one):
    h.append(_subagent(one, "notes.jsonl"), h.entry("msg_sub", T, 50, 5))
    assert _today(h, reg) == (0, 0, 0, 0)


def test_tokens_are_kept_per_scope(h, reg, one, tx):
    h.append(one, h.entry("msg_1", T, 100, 0))
    other = tx / "conv-o.jsonl"
    h.append(other, h.entry("msg_2", T, 7, 0))
    h.add_session(reg, "claude-backend", scope=OTHER, transcript=other)
    assert _today(h, reg, frozenset({OTHER}))[0] == 7


def test_none_means_every_scope(h, reg, one, tx):
    h.append(one, h.entry("msg_1", T, 100, 0))
    other = tx / "conv-o.jsonl"
    h.append(other, h.entry("msg_2", T, 7, 0))
    h.add_session(reg, "claude-backend", scope=OTHER, transcript=other)
    assert _today(h, reg)[0] == 107


def test_an_empty_scope_set_sees_nothing(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 0))
    assert _today(h, reg, frozenset()) == (0, 0, 0, 0)


def test_models_are_kept_apart(h, reg, one):
    h.append(one, h.entry("msg_1", T, 100, 0, model="claude-opus-test"),
             h.entry("msg_2", T, 7, 0, model="claude-haiku-test"))
    h.scan(reg)
    models = h.snap().models
    got = {m: h.counts(v)[0] for m, v in (models.items() if isinstance(models, dict)
                                          else models)}
    assert got == {"claude-opus-test": 100, "claude-haiku-test": 7}


@pytest.mark.parametrize("status", ["IDLE", "BUSY", "GONE"])
def test_sessions_in_any_status_are_counted(h, reg, one, status):
    from aipager.state import Status
    reg.get("claude-frontend").status = getattr(Status, status)
    h.append(one, h.entry("msg_1", T, 3, 0))
    assert _today(h, reg)[0] == 3


def test_scan_never_raises_on_a_registry_without_sessions(h):
    assert usage_store.scan(SessionRegistry(), now=time.time(), force=True) is None
