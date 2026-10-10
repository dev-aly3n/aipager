"""Success criterion 3 (and 18's "anything sent to Claude is unchanged"):
what Claude is sent (typed text, a /x1 message, a voice transcript, a file
with its caption, a typed /model, the Escape that halts a blocked turn) is
byte-identical whether the chat is English, Persian or Russian. The safety
sentences and deny JSON Claude reads are compared with the base tree by
test_qa_english_identity (scenario ``safety_reason_strings``)."""

from __future__ import annotations

import pytest

WITH_KEYS = ("claude_bytes", "media_files", "safety_blocks", "typed_model_command")

@pytest.fixture
def keys(scenario_out):
    return lambda name, lang: [t for t in scenario_out(name, lang) if t.startswith("[keys]")]


@pytest.mark.parametrize("name", WITH_KEYS)
def test_claude_is_sent_something(keys, name):
    assert keys(name, "en")


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("name", WITH_KEYS)
def test_claude_reads_the_same_bytes_in_every_chat_language(keys, name, lang):
    assert keys(name, lang) == keys(name, "en")


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_audit_keeps_every_english_reason(scenario_out, lang):
    en = [t for t in scenario_out("safety_blocks", "en") if t.startswith("[audit]")]
    other = [t for t in scenario_out("safety_blocks", lang) if t.startswith("[audit]")]
    assert en and other == en
