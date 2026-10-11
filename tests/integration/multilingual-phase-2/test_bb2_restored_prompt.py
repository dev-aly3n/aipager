"""Black-box, iteration 2: a separate permission prompt in a Persian chat,
pending across a daemon restart (entrypoints.md "After a daemon restart
with a separate permission prompt pending in a fa chat: the restored prompt
shows no ⟨U+200F⟩, ⟨U+2068⟩ or ⟨U+2069⟩ markers. Any other direction
control in the command still shows as ⟨U+202E⟩"; design.md decision 14,
live check 16).

Driven end to end through public seams: the prompt arrives as hook
datagrams (``HookReceiver``), the restart saves and reloads the REAL state
file and runs ``recover_sessions``, and the restored prompt is re-sent by
the pinned bar's Answer button and answered by Allow, both through
``_handle_callback``. The harness is the suite's restored-permission-prompt
one (loaded from its directory, which is not an importable package).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from aipager import preferences
from aipager.state import Status
from tests.i18n_ml2_bb import PERSIAN, catalog, strip_marks

_HARNESS = Path(__file__).resolve().parents[1] / "restored-permission-prompt" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_ml2_restored_prompt_harness", _HARNESS)
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

# The harness's fixtures, re-exported for this module.
vloop = H.vloop
card_chat = H.card_chat
vlimiter = H.vlimiter
rich_chat = H.rich_chat
daemon = H.daemon
replay = H.replay

RLO = "\u202e"
COMMAND = "rm -rf build " + RLO + "9"


def _scenario(replay, vloop, tmp_path, code):
    """Returns (text before restart, text re-sent after it, toast and edit
    of Allow after it)."""
    r = replay
    preferences.set_ui_language(H.CHAT, code)
    out = {}

    async def run():
        r.bot.registry.transition(r.sess.name, Status.BUSY)
        r.sess.trigger_msg_id = 1
        await r.permission(command=COMMAND, hook={"addr": str(tmp_path / "gone.sock"),
                                                  "request_id": "req-dead"})
        assert r.sess.status is Status.INTERACTIVE and not r.sess.busy_msg_id
        msg = max(r.chat.messages)
        out["before"] = r.chat.messages[msg]["text"]
        await r.restart()
        out["status"] = r.sess.status
        known = set(r.chat.messages)
        await r.tap(msg, f"{r.sess.name}:pin_answer")
        resent = [m for m in r.chat.messages if m not in known]
        assert len(resent) == 1, resent
        out["resent"] = r.chat.messages[resent[0]]["text"]
        out["toast"] = await r.tap(msg, r.cb(msg, "allow"),
                                   text=r.chat.messages[msg]["text"])
        await asyncio.sleep(2)
        edited = r.last_query.edit_message_text.await_args
        out["edited"] = edited.args[0] if edited and edited.args else ""

    vloop.run_until_complete(run())
    return out


@pytest.fixture
def fa_restore(replay, vloop, tmp_path):
    return _scenario(replay, vloop, tmp_path, "fa")


@pytest.fixture
def ru_restore(replay, vloop, tmp_path):
    return _scenario(replay, vloop, tmp_path, "ru")


def test_the_persian_prompt_comes_back_waiting(fa_restore):
    assert fa_restore["status"] is Status.INTERACTIVE


@pytest.mark.parametrize("marker", ["⟨U+200F⟩", "⟨U+2068⟩",
                                    "⟨U+2069⟩"])
def test_the_restored_persian_prompt_shows_no_marker_for_aipagers_marks(fa_restore, marker):
    assert marker not in fa_restore["resent"]


def test_the_restored_persian_prompt_still_reveals_a_foreign_override(fa_restore):
    assert "⟨U+202E⟩" in fa_restore["resent"]


def test_the_restored_persian_prompt_carries_no_raw_override(fa_restore):
    assert RLO not in fa_restore["resent"]


def test_the_restored_persian_prompt_keeps_the_persian_warning(fa_restore):
    assert catalog("fa")["perm.bidi_warning"]["v"] in strip_marks(fa_restore["resent"])


def test_the_restored_persian_prompt_keeps_its_persian_heading(fa_restore):
    assert PERSIAN.search(fa_restore["resent"].split("\n", 1)[0])


def test_the_restored_persian_prompt_keeps_the_command_intact(fa_restore):
    assert "<code>Bash: rm -rf build ⟨U+202E⟩9</code>" in fa_restore["resent"]


def test_the_restored_persian_prompt_reads_like_the_original_without_marks(fa_restore):
    assert fa_restore["resent"] == strip_marks(fa_restore["before"])


@pytest.mark.parametrize("marker", ["⟨U+200F⟩", "⟨U+2068⟩",
                                    "⟨U+2069⟩"])
def test_allow_after_the_restart_shows_no_marker(fa_restore, marker):
    assert marker not in fa_restore["edited"]


def test_allow_after_the_restart_toasts_in_persian(fa_restore):
    assert fa_restore["toast"] and PERSIAN.search(fa_restore["toast"])


def test_a_restored_russian_prompt_is_the_original_text(ru_restore):
    assert ru_restore["resent"] == ru_restore["before"]


def test_a_restored_russian_prompt_still_reveals_a_foreign_override(ru_restore):
    assert "⟨U+202E⟩" in ru_restore["resent"]
