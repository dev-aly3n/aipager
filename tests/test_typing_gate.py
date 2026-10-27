"""Roadmap 8.143: no prompt aipager types answers a permission dialog.

Claude Code draws a tool's permission dialog the moment the tool's
PreToolUse hooks return, and runs the PermissionRequest hooks beside it, so
the dialog took a prompt's keys before aipager heard of it: its Enter (or a
lone digit) picked "Yes" and the command ran (2.1.311, live). The typing
gate (``aipager/dtach/typing_gate.py``) holds a tool call at the end of its
PreToolUse hook while aipager types, and ``inject.send_text_and_enter``
waits out a call that passed just before, asks the dialog probe before every
piece and the Enter, and stops when one is open.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from aipager.dtach import inject, typing_gate

NAME = "claude-gatedev"
KILL = b"\x15" * inject.DISCARD_KILL_BATCH


def _base(tmp_path) -> str:
    # conftest redirects gate_dir into tmp_path whatever the base is.
    return str(tmp_path)


def _folder(tmp_path, session=NAME):
    return typing_gate.gate_dir(_base(tmp_path), session)


def _passes(tmp_path, session=NAME):
    folder = _folder(tmp_path, session)
    return sorted(n for n in os.listdir(folder) if n.startswith("pass-")) if folder.exists() else []


# ── the hook side ────────────────────────────────────────────────────────


def test_pass_gate_with_no_typing_passes_at_once_and_leaves_its_stamp(tmp_path):
    slept = []
    assert typing_gate.pass_gate(_base(tmp_path), NAME, sleep=slept.append) == "passed"
    assert slept == []
    assert len(_passes(tmp_path)) == 1
    assert typing_gate.fresh_passes(_base(tmp_path), NAME, 1.0) == 1


def test_pass_gate_writes_its_stamp_before_it_reads_the_marker(tmp_path, monkeypatch):
    seen = []
    real = typing_gate._marker_live

    def looking(folder, now):
        seen.append(len([n for n in os.listdir(folder) if n.startswith("pass-")]))
        return real(folder, now)

    monkeypatch.setattr(typing_gate, "_marker_live", looking)
    typing_gate.pass_gate(_base(tmp_path), NAME)
    assert seen == [1]


def test_pass_gate_waits_while_aipager_types_with_no_stamp_left(tmp_path):
    base = _base(tmp_path)
    assert typing_gate.begin(base, NAME)
    stamps_while_waiting = []

    def sleep(_secs):
        stamps_while_waiting.append(len(_passes(tmp_path)))
        if len(stamps_while_waiting) == 3:
            typing_gate.end(base, NAME)

    assert typing_gate.pass_gate(base, NAME, sleep=sleep) == "waited"
    # While it waited, no stamp of its own: the daemon does not wait for it.
    assert stamps_while_waiting == [0, 0, 0]
    # Once through, its stamp is there for the next typing to wait out.
    assert len(_passes(tmp_path)) == 1


def test_pass_gate_goes_on_at_the_cap_with_a_fresh_stamp(tmp_path):
    base = _base(tmp_path)
    typing_gate.begin(base, NAME)
    clock = [100.0]

    def sleep(secs):
        clock[0] += secs

    result = typing_gate.pass_gate(base, NAME, cap=0.1, monotonic=lambda: clock[0],
                                   sleep=sleep)
    assert result == "capped"
    assert typing_gate.fresh_passes(base, NAME, 1.0) == 1


def test_an_expired_marker_holds_nothing(tmp_path):
    base = _base(tmp_path)
    typing_gate.begin(base, NAME, ttl=5.0, now=time.time() - 10)
    slept = []
    assert typing_gate.pass_gate(base, NAME, sleep=slept.append) == "passed"
    assert slept == []


@pytest.mark.parametrize("body", [b"", b"not json", b'{"expires_at": "soon"}', b"[1]"])
def test_a_broken_marker_holds_nothing(tmp_path, body):
    base = _base(tmp_path)
    folder = _folder(tmp_path)
    folder.mkdir(mode=0o700)
    (folder / "typing").write_bytes(body)
    slept = []
    assert typing_gate.pass_gate(base, NAME, sleep=slept.append) == "passed"
    assert slept == []


def test_a_marker_of_another_user_holds_nothing(tmp_path, monkeypatch):
    typing_gate.begin(_base(tmp_path), NAME)
    assert typing_gate._marker_live(_folder(tmp_path), time.time()) is True
    monkeypatch.setattr(typing_gate, "_getuid", lambda: os.getuid() + 1)
    assert typing_gate._marker_live(_folder(tmp_path), time.time()) is False


@pytest.mark.parametrize("session", ["", "../x", "a/b", "x" * 200])
def test_unsafe_session_names_take_no_part(tmp_path, session):
    base = _base(tmp_path)
    assert typing_gate.pass_gate(base, session) == "error"
    assert typing_gate.begin(base, session) is False
    assert typing_gate.fresh_passes(base, session, 1.0) == 0
    typing_gate.end(base, session)


def test_a_file_in_place_of_the_folder_never_raises(tmp_path):
    base = _base(tmp_path)
    _folder(tmp_path).write_text("not a folder")
    assert typing_gate.pass_gate(base, NAME) == "error"
    assert typing_gate.begin(base, NAME) is False
    assert typing_gate.fresh_passes(base, NAME, 1.0) == 0
    typing_gate.end(base, NAME)


def test_a_folder_of_another_user_takes_no_part(tmp_path, monkeypatch):
    base = _base(tmp_path)
    _folder(tmp_path).mkdir(mode=0o700)
    monkeypatch.setattr(typing_gate, "_getuid", lambda: os.getuid() + 1)
    assert typing_gate.pass_gate(base, NAME) == "error"
    assert typing_gate.begin(base, NAME) is False


def test_fresh_passes_counts_young_stamps_and_tidies_stale_ones(tmp_path):
    base = _base(tmp_path)
    folder = _folder(tmp_path)
    folder.mkdir(mode=0o700)
    now = 1_000_000.0

    def stamp(age, pid=1):
        (folder / f"pass-{int((now - age) * 1e9)}-{pid}").touch()

    stamp(0.2)
    stamp(0.9, pid=2)
    stamp(5.0)
    stamp(typing_gate.STALE_PASS_SECONDS + 1)
    (folder / "pass-garbage").touch()
    (folder / "typing").write_text("{}")
    assert typing_gate.fresh_passes(base, NAME, 1.0, now=now) == 2
    left = sorted(os.listdir(folder))
    assert len([n for n in left if n.startswith("pass-")]) == 4   # stale one gone
    assert "pass-garbage" in left and "typing" in left


def test_begin_writes_an_expiry_and_end_removes_it(tmp_path):
    base = _base(tmp_path)
    assert typing_gate.begin(base, NAME, ttl=7.0, now=50.0)
    marker = _folder(tmp_path) / "typing"
    assert json.loads(marker.read_text()) == {"expires_at": 57.0}
    assert oct(marker.stat().st_mode & 0o777) == oct(0o600)
    assert oct(_folder(tmp_path).stat().st_mode & 0o777) == oct(0o700)
    typing_gate.end(base, NAME)
    assert not marker.exists()


# ── the daemon side: send_text_and_enter ────────────────────────────────


class _Terminal:
    """Every write, and whether the typing marker was live during it."""

    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.writes: list[bytes] = []
        self.marker_live: list[bool] = []
        self.on_write = None

    async def run(self, args, stdin=b"", timeout=5):
        self.writes.append(bytes(stdin))
        self.marker_live.append(typing_gate._marker_live(_folder(self.tmp_path), time.time()))
        if self.on_write is not None:
            self.on_write(len(self.writes))
        return True, ""


@pytest.fixture
def term(tmp_path, monkeypatch):
    t = _Terminal(tmp_path)
    monkeypatch.setattr(inject, "_run", t.run)

    async def no_gap(_secs):
        return None
    monkeypatch.setattr(inject, "_key_gap_sleep", no_gap)
    return t


@pytest.fixture
def gate_waits(monkeypatch):
    waits = []

    async def record(secs):
        waits.append(secs)
    monkeypatch.setattr(inject, "_gate_sleep", record)
    return waits


def test_a_prompt_is_typed_with_the_marker_up_and_it_stays_a_moment_after(term, tmp_path,
                                                                         run_async):
    async def scenario():
        ok = await inject.send_text_and_enter(NAME, "hello")
        right_after = typing_gate._marker_live(_folder(tmp_path), time.time())
        await asyncio.sleep(inject.GATE_AFTER_ENTER_SECONDS + 0.1)
        later = (_folder(tmp_path) / "typing").exists()
        return ok, right_after, later

    ok, right_after, later = run_async(scenario())
    assert ok is True
    assert term.writes == [b"hello", b"\r"]
    assert term.marker_live == [True, True]
    assert right_after is True
    assert later is False
    assert inject._GATE_HOLDS == {}


def test_it_waits_out_a_tool_call_that_passed_just_before(term, gate_waits, monkeypatch,
                                                          tmp_path, run_async):
    answers = [1, 1, 0]
    held = []

    def fresh(base, session, window):
        # Every look is made with the marker up: tool calls are held.
        held.append(typing_gate._marker_live(_folder(tmp_path), time.time()))
        return answers.pop(0) if answers else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert gate_waits == [inject.GATE_POLL_SECONDS, inject.GATE_POLL_SECONDS]
    assert term.writes == [b"hello", b"\r"]
    assert held and all(held)


def test_the_marker_is_renewed_once_the_lock_is_held(term, monkeypatch, tmp_path, run_async):
    """A send can wait long for the lock (behind a long queue wipe): the
    marker's expiry starts again when the typing really starts."""
    expiries = []
    marker = _folder(tmp_path) / "typing"

    def fresh(base, session, window):
        expiries.append(json.loads(marker.read_text())["expires_at"])
        return 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    real_begin = typing_gate.begin
    clock = [1000.0]

    def begin(base_dir, session, **kw):
        clock[0] += 10.0
        return real_begin(base_dir, session, now=clock[0])
    monkeypatch.setattr(typing_gate, "begin", begin)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert len(expiries) >= 2 and expiries[1] > expiries[0]


def test_the_wait_for_a_passed_call_is_bounded(term, monkeypatch, run_async):
    monkeypatch.setattr(inject, "GATE_WAIT_LIMIT_SECONDS", 0.05)
    monkeypatch.setattr(inject, "GATE_WAIT_HARD_LIMIT", 0.08)
    monkeypatch.setattr(inject, "GATE_POLL_SECONDS", 0.01)
    looks = []

    def fresh(base, session, window):
        # Fresh far longer than the limits allow, then quiet: a missing
        # limit fails this test instead of hanging it.
        looks.append(window)
        return 1 if len(looks) < 2000 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    started = time.monotonic()
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert time.monotonic() - started < 1.0
    assert len(looks) < 500
    assert term.writes == [b"hello", b"\r"]


def test_a_real_stamp_is_waited_out_until_it_is_old(term, tmp_path, monkeypatch, run_async):
    monkeypatch.setattr(inject, "GATE_FRESH_SECONDS", 0.15)
    monkeypatch.setattr(inject, "GATE_POLL_SECONDS", 0.02)
    assert typing_gate.pass_gate(_base(tmp_path), NAME) == "passed"
    started = time.monotonic()
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert time.monotonic() - started >= 0.1
    assert term.writes == [b"hello", b"\r"]


def test_a_dialog_open_before_typing_gets_nothing(term, tmp_path, monkeypatch, run_async):
    inject.set_dialog_probe(lambda name: name == NAME)
    result = run_async(inject.send_text_and_enter(NAME, "hello"))
    assert result is inject.DIALOG_BEFORE_TYPING
    assert not result
    assert term.writes == []
    assert not (_folder(tmp_path) / "typing").exists()
    assert inject._PENDING_WIPE == {}


def test_a_dialog_that_opens_during_the_passed_call_wait_gets_nothing(term, gate_waits,
                                                                      monkeypatch, run_async):
    monkeypatch.setattr(typing_gate, "fresh_passes", lambda base, session, window: 1)
    looks = []

    def probe(name):
        looks.append(name)
        return len(looks) >= 3
    inject.set_dialog_probe(probe)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []


def test_a_probe_that_raises_counts_as_a_dialog(term, run_async):
    def broken(_name):
        raise RuntimeError("boom")
    inject.set_dialog_probe(broken)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []


def test_a_dialog_opening_between_pieces_stops_the_rest_and_the_enter(term, run_async):
    text = "a" * 450
    opened = []
    term.on_write = lambda n: opened.append(n)
    inject.set_dialog_probe(lambda _name: len(opened) >= 1)
    result = run_async(inject.send_text_and_enter(NAME, text))
    assert result is inject.DIALOG_WHILE_TYPING
    assert term.writes == [b"a" * 200]
    assert b"\r" not in term.writes
    assert inject._PENDING_WIPE == {NAME: 200}


def test_a_dialog_opening_before_the_enter_stops_the_enter(term, run_async):
    # Quiet through the gate and the one piece; open once the piece is in.
    inject.set_dialog_probe(lambda _name: bool(term.writes))
    result = run_async(inject.send_text_and_enter(NAME, "hello"))
    assert result is inject.DIALOG_WHILE_TYPING
    assert term.writes == [b"hello"]
    assert inject._PENDING_WIPE == {NAME: 5}


def test_the_next_prompt_first_wipes_what_a_stopped_one_left(term, run_async):
    inject._PENDING_WIPE[NAME] = 200
    assert run_async(inject.send_text_and_enter(NAME, "next")) is True
    wipes = inject.discard_presses(200) // inject.DISCARD_KILL_BATCH
    assert term.writes == [KILL] * wipes + [b"next", b"\r"]
    assert b"\x1b" not in b"".join(term.writes)          # never an Escape
    assert inject._PENDING_WIPE == {}


def test_the_wipe_waits_while_a_dialog_is_open(term, run_async):
    inject._PENDING_WIPE[NAME] = 50
    inject.set_dialog_probe(lambda _name: True)
    assert run_async(inject.send_text_and_enter(NAME, "x")) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []
    assert inject._PENDING_WIPE == {NAME: 50}


def test_a_failed_wipe_stays_due_and_types_nothing(term, monkeypatch, run_async):
    inject._PENDING_WIPE[NAME] = 50

    async def failing(args, stdin=b"", timeout=5):
        term.writes.append(bytes(stdin))
        return False, ""
    monkeypatch.setattr(inject, "_run", failing)
    assert run_async(inject.send_text_and_enter(NAME, "x")) is False
    assert term.writes == [KILL]
    assert inject._PENDING_WIPE == {NAME: 50}


def test_overlapping_sends_keep_the_marker_until_the_last_is_done(tmp_path, run_async):
    inject._gate_begin(NAME)
    inject._gate_begin(NAME)
    inject._gate_end(NAME)
    assert (_folder(tmp_path) / "typing").exists()
    inject._gate_end(NAME)
    assert not (_folder(tmp_path) / "typing").exists()
    assert inject._GATE_HOLDS == {}


@pytest.mark.parametrize("length,pieces", [
    (1, [1]), (200, [200]), (201, [201]), (202, [200, 2]), (401, [200, 201]), (0, [0]),
])
def test_no_piece_is_a_single_character_unless_the_prompt_is(length, pieces):
    assert [len(p) for p in inject._type_pieces("7" * length)] == pieces


def test_a_201_character_prompt_ending_in_a_digit_never_writes_it_alone(term, run_async):
    text = "x" * 200 + "1"
    assert run_async(inject.send_text_and_enter(NAME, text)) is True
    assert term.writes == [text.encode(), b"\r"]


def test_the_wait_for_a_passed_call_never_holds_the_write_lock(tmp_path, monkeypatch,
                                                               run_async):
    """A Stop's Escape is not kept waiting behind the gate's wait: it goes
    in while the prompt waits for a tool call that has just passed."""
    t = _Terminal(tmp_path)
    monkeypatch.setattr(inject, "_run", t.run)

    async def no_gap(_secs):
        return None
    monkeypatch.setattr(inject, "_key_gap_sleep", no_gap)
    answers = [1, 1, 1, 0, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)

    async def gate_sleep(_secs):
        await asyncio.sleep(0.01)
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)

    async def scenario():
        send = asyncio.ensure_future(inject.send_text_and_enter(NAME, "hello"))
        await asyncio.sleep(0)
        await inject.send_keys(NAME, "Escape")
        return await send

    assert run_async(scenario()) is True
    assert t.writes == [b"\x1b", b"hello", b"\r"]


def test_a_call_that_passed_while_the_lock_was_awaited_is_waited_out(term, gate_waits,
                                                                     monkeypatch, run_async):
    """The lock can wait behind another write; a tool call can pass its
    hook meanwhile (at its wait's cap): the gate is looked at again."""
    answers = [0, 1, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert gate_waits == [inject.GATE_POLL_SECONDS]
    assert term.writes == [b"hello", b"\r"]


def test_the_gated_enter_goes_in_with_the_marker_up(term, run_async):
    assert run_async(inject.press_enter_gated(NAME)) is True
    assert term.writes == [b"\r"]
    assert term.marker_live == [True]


def test_the_gated_enter_never_goes_into_a_dialog(term, tmp_path, run_async):
    inject.set_dialog_probe(lambda _name: True)
    assert run_async(inject.press_enter_gated(NAME)) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []
    assert not (_folder(tmp_path) / "typing").exists()


def test_the_gated_enter_waits_out_a_call_that_just_passed(term, gate_waits, monkeypatch,
                                                           run_async):
    answers = [1, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)
    assert run_async(inject.press_enter_gated(NAME)) is True
    assert gate_waits == [inject.GATE_POLL_SECONDS]


def test_the_gated_enter_looks_again_once_it_holds_the_lock(term, run_async):
    looks = []

    def probe(_name):
        looks.append(1)
        return len(looks) > 1          # a dialog opened while it waited for the lock
    inject.set_dialog_probe(probe)
    assert run_async(inject.press_enter_gated(NAME)) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []


def test_a_folder_that_cannot_be_made_takes_no_part(tmp_path, monkeypatch):
    missing = tmp_path / "no" / "such" / "place"
    monkeypatch.setattr(typing_gate, "gate_dir", lambda _b, session: missing / session)
    assert typing_gate.pass_gate(str(tmp_path), NAME) == "error"
    assert typing_gate.begin(str(tmp_path), NAME) is False
    inject._gate_begin(NAME)                     # never raises: the probe still guards
    inject._gate_end(NAME)
    assert inject._GATE_HOLDS == {}


def test_every_pass_tidies_stale_stamps(tmp_path):
    base = _base(tmp_path)
    folder = _folder(tmp_path)
    folder.mkdir(mode=0o700)
    now = time.time()
    stale = folder / f"pass-{int((now - typing_gate.STALE_PASS_SECONDS - 5) * 1e9)}-1"
    young = folder / f"pass-{int((now - 5) * 1e9)}-2"
    stale.touch()
    young.touch()
    assert typing_gate.pass_gate(base, NAME) == "passed"
    assert not stale.exists() and young.exists()


def test_the_send_now_chord_waits_for_a_stopped_prompts_wipe(term, run_async):
    """The chord would send the stopped prompt's text left in the box,
    though it was never sent whole (roadmap 8.143)."""
    inject._PENDING_WIPE[NAME] = 12
    assert run_async(inject.send_now(NAME)) is False
    assert term.writes == []


def test_the_gated_enter_renews_the_marker_once_it_holds_the_lock(term, monkeypatch,
                                                                  tmp_path, run_async):
    begins = []
    real_begin = typing_gate.begin

    def begin(base_dir, session, **kw):
        begins.append(session)
        return real_begin(base_dir, session, **kw)
    monkeypatch.setattr(typing_gate, "begin", begin)
    assert run_async(inject.press_enter_gated(NAME)) is True
    assert begins == [NAME, NAME]


def test_a_marker_write_that_raises_never_stops_a_send(term, monkeypatch, run_async):
    def broken(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(typing_gate, "begin", broken)
    inject._gate_begin(NAME)
    inject._gate_end(NAME)
    assert inject._GATE_HOLDS == {}


def test_a_call_that_passes_at_its_cap_mid_typing_is_waited_out_before_the_enter(
        term, gate_waits, monkeypatch, run_async):
    """A tool call held at the marker longer than its hook's cap goes on
    with a fresh stamp while the prompt is typed: the Enter waits it out,
    and a dialog it draws stops the Enter (review rev-iter2-001)."""
    looks = []

    def fresh(base, session, window):
        looks.append(len(term.writes))
        # Fresh once the text is in (the capped call passed mid-typing).
        return 1 if term.writes and len(looks) < 6 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    opened = []
    inject.set_dialog_probe(lambda _name: len(gate_waits) >= 2 and not opened.append(1))
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is inject.DIALOG_WHILE_TYPING
    assert term.writes == [b"hello"]
    assert gate_waits                          # it waited before the Enter
    assert inject._PENDING_WIPE == {NAME: 5}


def test_a_wipe_of_the_input_box_clears_a_pending_wipe(term, run_async):
    inject._PENDING_WIPE[NAME] = 12
    assert run_async(inject.discard_queued_input(NAME, 0)) is True
    assert inject._PENDING_WIPE == {}


def test_a_killed_session_forgets_its_pending_wipe(monkeypatch, run_async):
    inject._PENDING_WIPE[NAME] = 12
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [])
    run_async(inject.kill_session(NAME))
    assert inject._PENDING_WIPE == {}


def test_a_call_that_passes_at_its_cap_between_pieces_stops_the_rest(
        term, gate_waits, monkeypatch, run_async):
    looks = []

    def fresh(base, session, window):
        looks.append(1)
        return 1 if len(term.writes) == 1 and len(gate_waits) < 3 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    inject.set_dialog_probe(lambda _name: len(gate_waits) >= 2)
    result = run_async(inject.send_text_and_enter(NAME, "b" * 450))
    assert result is inject.DIALOG_WHILE_TYPING
    assert term.writes == [b"b" * 200]
    assert inject._PENDING_WIPE == {NAME: 200}


def test_no_wipe_goes_in_once_a_dialog_opened_while_the_lock_was_awaited(term, run_async):
    """The look under the lock comes before the wipe of a stopped prompt's
    text: Ctrl+U is never typed into a dialog that opened meanwhile."""
    inject._PENDING_WIPE[NAME] = 30
    looks = []

    def probe(_name):
        looks.append(1)
        return len(looks) > 1           # open from the look under the lock on
    inject.set_dialog_probe(probe)
    assert run_async(inject.send_text_and_enter(NAME, "x")) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []
    assert inject._PENDING_WIPE == {NAME: 30}


def test_every_look_while_typing_renews_the_marker(term, monkeypatch, run_async):
    begins = []
    real_begin = typing_gate.begin

    def begin(base_dir, session, **kw):
        begins.append(session)
        return real_begin(base_dir, session, **kw)
    monkeypatch.setattr(typing_gate, "begin", begin)
    assert run_async(inject.send_text_and_enter(NAME, "c" * 450)) is True
    # Before the lock, under it, before each of 3 pieces and before the Enter.
    assert len(begins) == 6


def test_no_key_goes_in_while_a_call_passed_a_moment_ago(term, gate_waits, monkeypatch,
                                                         run_async):
    """Past the wait's limit, typing goes on only once no call passed in
    the last GATE_MIN_PASS_AGE (its dialog may be drawing right then), up
    to the hard limit."""
    monkeypatch.setattr(inject, "GATE_WAIT_LIMIT_SECONDS", 0.0)
    monkeypatch.setattr(inject, "GATE_WAIT_HARD_LIMIT", 30.0)
    very_fresh = [1, 1, 1, 0]

    def fresh(base, session, window):
        if window == inject.GATE_MIN_PASS_AGE:
            return very_fresh.pop(0) if very_fresh else 0
        return 1
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert len(gate_waits) == 3
    assert term.writes == [b"hello", b"\r"]


def test_the_hard_limit_ends_the_wait(term, monkeypatch, run_async):
    monkeypatch.setattr(inject, "GATE_WAIT_LIMIT_SECONDS", 0.0)
    monkeypatch.setattr(inject, "GATE_WAIT_HARD_LIMIT", 0.05)
    monkeypatch.setattr(inject, "GATE_POLL_SECONDS", 0.01)
    looks = []

    def fresh(base, session, window):
        # Always a call that passed a moment ago, for far longer than the
        # hard limit allows (then quiet, so a missing limit fails, not hangs).
        looks.append(window)
        return 1 if len(looks) < 2000 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    assert run_async(inject.send_text_and_enter(NAME, "hello")) is True
    assert len(looks) < 500
    assert term.writes == [b"hello", b"\r"]


def test_a_launch_refused_for_a_live_name_keeps_that_sessions_wipe(monkeypatch, run_async):
    inject._PENDING_WIPE["claude-jim"] = 12
    monkeypatch.setattr(inject.Path, "is_socket", lambda self: True)
    ok, err = run_async(inject.launch_session("jim", mode="ask"))
    assert not ok and "already exists" in err
    assert inject._PENDING_WIPE == {"claude-jim": 12}


def test_a_stamp_from_the_future_is_no_pass_to_wait_for(tmp_path):
    """The clock stepped back: a stamp dated ahead would look fresh for as
    long as the step. It is not waited for, and it is removed."""
    base = _base(tmp_path)
    folder = _folder(tmp_path)
    folder.mkdir(mode=0o700)
    now = 1_000_000.0
    ahead = folder / f"pass-{int((now + 30) * 1e9)}-1"
    slight = folder / f"pass-{int((now + 0.5) * 1e9)}-2"
    ahead.touch()
    slight.touch()
    assert typing_gate.fresh_passes(base, NAME, 1.0, now=now) == 1   # only the slight one
    assert not ahead.exists() and slight.exists()
