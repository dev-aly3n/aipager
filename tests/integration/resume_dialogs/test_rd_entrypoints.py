"""Black-box rows for the 8.127 entry points a card and the age check stand
on (design.md success criteria 6, 7, 9 and 12): ``inject.press_key_in_dialog``
writes one key only while its check says open, ``inject.request_wipe``
wipes before the next typed prompt, and ``transcript.last_exchange_at``
finds the conversation's last exchange and never raises."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from aipager import transcript
from aipager.dtach import inject
from tests.resume_dialog_kit import ESC, NAME


def _press(world, still_open):
    return world.run_async(inject.press_key_in_dialog(NAME, "Escape", still_open))


async def _yes():
    return True


async def _no():
    return False


async def _boom():
    raise RuntimeError("record unreadable")


def test_press_key_writes_escape_when_open(world):
    _press(world, _yes)
    assert world.writes == [ESC]


def test_press_key_returns_true_when_sent(world):
    assert _press(world, _yes) is True


def test_press_key_writes_nothing_when_closed(world):
    _press(world, _no)
    assert world.writes == []


def test_press_key_returns_false_when_closed(world):
    assert not _press(world, _no)


def test_press_key_writes_nothing_when_check_raises(world):
    _press(world, _boom)
    assert world.writes == []


def test_press_key_ignores_the_dialog_probe(world):
    """The dialog is the target: an open probe does not stop the key."""
    world.waiting()
    world.scan()
    _press(world, _yes)
    assert world.writes == [ESC]


def test_press_key_checks_under_the_write_lock(world):
    """A text send in flight finishes before the check runs and the key goes."""
    order = []

    async def still_open():
        order.append("check")
        return True

    def on_write(data):
        order.append(data)
    world.on_write = on_write

    async def both():
        send = asyncio.ensure_future(inject.send_text_and_enter(NAME, "hello"))
        await asyncio.sleep(0)
        await inject.press_key_in_dialog(NAME, "Escape", still_open)
        await send
    world.run_async(both())
    assert order.index("check") > order.index(b"\r")


def test_request_wipe_wipes_before_next_text(world):
    inject.request_wipe(NAME, 2000)
    world.run_async(inject.send_text_and_enter(NAME, "hello"))
    first_text = next(i for i, w in enumerate(world.writes) if b"hello" in w)
    assert sum(len(w) for w in world.writes[:first_text] if set(w) <= {0x15}) >= 2000


def test_request_wipe_is_used_once(world):
    inject.request_wipe(NAME, 2000)
    world.run_async(inject.send_text_and_enter(NAME, "hello"))
    world.writes.clear()
    world.run_async(inject.send_text_and_enter(NAME, "again"))
    assert not any(w and set(w) <= {0x15} for w in world.writes)


def test_no_wipe_without_request(world):
    world.run_async(inject.send_text_and_enter(NAME, "hello"))
    assert not any(w and set(w) <= {0x15} for w in world.writes)


# -- transcript.last_exchange_at --------------------------------------------------------

def _stamp(t):
    at = datetime.fromtimestamp(t, timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


T0 = 1_790_000_000.0


@pytest.mark.parametrize("kind", ["user", "assistant"])
def test_last_exchange_from_user_or_assistant(tmp_path, kind):
    path = _write(tmp_path / "t.jsonl", [{"type": kind, "timestamp": _stamp(T0)}])
    assert transcript.last_exchange_at(path) == pytest.approx(T0, abs=0.01)


def test_last_exchange_takes_the_latest(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        {"type": "user", "timestamp": _stamp(T0)},
        {"type": "assistant", "timestamp": _stamp(T0 + 30)},
    ])
    assert transcript.last_exchange_at(path) == pytest.approx(T0 + 30, abs=0.01)


def test_last_exchange_skips_other_entry_types(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        {"type": "assistant", "timestamp": _stamp(T0)},
        {"type": "system", "timestamp": _stamp(T0 + 500)},
        {"type": "summary", "timestamp": _stamp(T0 + 600)},
    ])
    assert transcript.last_exchange_at(path) == pytest.approx(T0, abs=0.01)


def test_last_exchange_only_other_types_is_none(tmp_path):
    path = _write(tmp_path / "t.jsonl", [{"type": "system", "timestamp": _stamp(T0)}])
    assert transcript.last_exchange_at(path) is None


def test_last_exchange_missing_file_is_none(tmp_path):
    assert transcript.last_exchange_at(str(tmp_path / "none.jsonl")) is None


def test_last_exchange_empty_path_is_none():
    assert transcript.last_exchange_at("") is None


def test_last_exchange_empty_file_is_none(tmp_path):
    (tmp_path / "t.jsonl").write_text("")
    assert transcript.last_exchange_at(str(tmp_path / "t.jsonl")) is None


def test_last_exchange_skips_broken_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "timestamp": _stamp(T0)}) + "\n{broken\n\x00\n")
    assert transcript.last_exchange_at(str(path)) == pytest.approx(T0, abs=0.01)


def test_last_exchange_bad_timestamp_never_raises(tmp_path):
    path = _write(tmp_path / "t.jsonl", [{"type": "user", "timestamp": "yesterday"}])
    assert transcript.last_exchange_at(path) is None


def test_last_exchange_of_a_folder_is_none(tmp_path):
    assert transcript.last_exchange_at(str(tmp_path)) is None


def test_last_exchange_reads_only_the_tail(tmp_path):
    """An exchange before the tail read is not seen (bounded cost)."""
    path = tmp_path / "t.jsonl"
    filler = json.dumps({"type": "system", "content": "x" * 1000}) + "\n"
    n = transcript.TAIL_READ_BYTES // len(filler) + 4
    path.write_text(json.dumps({"type": "user", "timestamp": _stamp(T0)}) + "\n" + filler * n)
    assert transcript.last_exchange_at(str(path)) is None


def test_last_exchange_inside_the_tail_is_seen(tmp_path):
    path = tmp_path / "t.jsonl"
    filler = json.dumps({"type": "system", "content": "x" * 1000}) + "\n"
    n = transcript.TAIL_READ_BYTES // len(filler) + 4
    path.write_text(filler * n + json.dumps({"type": "user", "timestamp": _stamp(T0)}) + "\n")
    assert transcript.last_exchange_at(str(path)) == pytest.approx(T0, abs=0.01)
