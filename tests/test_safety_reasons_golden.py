"""What Claude reads when a safety rule blocks a tool call: every reason
safety.py and enforce write, and the deny payload each becomes, byte for
byte (multilingual phase 3a).

The daemon now reads these reasons back into codes to show the block in
the chat's language (``aipager/bot/safety_notice.py``); nothing about
that may change a byte of what Claude reads, in any chat language.
``tests/golden/safety_reasons.json`` was recorded before any phase 3a
change (5a1f929) by running this module with
``AIPAGER_RECORD_SAFETY_GOLDEN=1``. Each reason comes from its real
writer (``tests/i18n_surfaces_p3a.safety_reasons`` and
``enforce_reasons``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from aipager import preferences
from aipager.dtach import enforce
from tests import i18n_surfaces_p3a as p3a

GOLDEN = Path(__file__).resolve().parent / "golden" / "safety_reasons.json"


def _corpus(tmp_path) -> list[dict]:
    rows = []
    for tool, reason in p3a.safety_reasons() + p3a.enforce_reasons(tmp_path):
        rows.append({"tool": tool, "reason": reason,
                     "deny": enforce.deny_decision_json(reason)})
    return rows


def test_claude_reads_every_safety_reason_byte_identical(tmp_path):
    rows = _corpus(tmp_path)
    if os.environ.get("AIPAGER_RECORD_SAFETY_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(rows, ensure_ascii=True, indent=1) + "\n",
                          encoding="utf-8")
    assert rows == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_corpus_covers_every_shape():
    """One row per writer (29: safety.py's 27, enforce's two), 28 distinct
    sentences: the confined and the unconfined search both write "with an
    unreadable search folder". A new writer needs its row here and in the
    golden (the members' root folder added three, appended before
    enforce's two; every earlier row is byte-identical)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(golden) == 29
    assert len({row["reason"] for row in golden}) == 28


def test_the_chat_language_never_reaches_a_reason(tmp_path):
    """The writers run in the hook, which never reads a chat's language:
    with every chat set to Persian, then Russian, the same bytes."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for code in ("fa", "ru"):
        for chat in (555, -1001, 256113222):
            preferences.set_ui_language(chat, code)
        assert _corpus(tmp_path) == golden, code
