"""A compact summary (or other Claude-injected entry) is not a prompt.

Roadmap 8.51, confirmed 2026-09-26: after an auto-compact, the transcript's
newest user entry is Claude Code's "This session is being continued…"
summary. Read as a prompt it has no Telegram marker, so enforcement saw a
"terminal" turn and let a restricted Telegram turn run unrestricted — and
it counted as a turn boundary, clearing the sticky block.
"""
import json

from aipager.dtach import enforce

TG = {"type": "user", "message": {"role": "user",
                                  "content": "[via Telegram · @someone]\ndo the thing"}}
TERM = {"type": "user", "message": {"role": "user", "content": "typed in the terminal"}}
COMPACT = {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
           "message": {"role": "user", "content":
                       "This session is being continued from a previous conversation"}}
META = {"type": "user", "isMeta": True, "message": {"role": "user",
                                                    "content": "<local-command-caveat>x"}}
BLOCKED = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1",
     "content": enforce._BLOCK_MARKER + " Bash command blocked"}]}}


def _t(tmp_path, *entries):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(p)


def test_a_compact_summary_does_not_turn_a_telegram_turn_into_terminal(tmp_path):
    assert enforce._origin_from_transcript(_t(tmp_path, TG, COMPACT)) == "telegram"


def test_a_meta_entry_does_not_turn_a_telegram_turn_into_terminal(tmp_path):
    assert enforce._origin_from_transcript(_t(tmp_path, TG, META)) == "telegram"


def test_a_terminal_turn_stays_terminal_across_a_compact(tmp_path):
    assert enforce._origin_from_transcript(_t(tmp_path, TERM, COMPACT)) == "terminal"


def test_a_compact_summary_alone_fails_closed(tmp_path):
    assert enforce._origin_from_transcript(_t(tmp_path, COMPACT)) == "telegram"


def test_a_compact_does_not_clear_the_sticky_block(tmp_path):
    assert enforce._turn_already_blocked(_t(tmp_path, TG, BLOCKED, COMPACT)) is True


def test_a_meta_entry_between_the_block_and_the_prompt_keeps_the_block(tmp_path):
    assert enforce._turn_already_blocked(_t(tmp_path, TG, BLOCKED, META)) is True


def test_a_meta_entry_after_this_turns_prompt_does_not_revive_an_old_block(tmp_path):
    # the prior turn was blocked; this turn's own prompt is the boundary
    # even with a meta entry after it
    assert enforce._turn_already_blocked(
        _t(tmp_path, TG, BLOCKED, TG, META)) is False
