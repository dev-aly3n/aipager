"""The per-session terminal write lock in ``aipager.dtach.inject``.

Every ``dtach -p`` write holds its session's lock until the write returns,
so writes are delivered one at a time: ``send_text_and_enter`` holds it
across its text and its Enter, and the send-now chord holds it for its
Ctrl+X, and again from its "anything written since my Ctrl+X?" check
through its Ctrl+S.

The fake ``_run`` below records when each write starts and ends and takes
real (short) time, so an overlap or an interleaving is visible. Nothing
here patches asyncio.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager.dtach import inject

NAME = "claude-lockdev"
ESC = b"\x1b"
KILL_LINE = b"\x15"
CTRL_X = b"\x18"
CTRL_S = b"\x13"
#: How long every fake ``dtach -p`` write takes.
WRITE = 0.05


class _Terminal:
    """Every write as ``(bytes, start, end)``, in start order."""

    def __init__(self) -> None:
        self.log: list[list] = []

    async def run(self, args, stdin=b"", timeout=5):
        loop = asyncio.get_running_loop()
        entry = [bytes(stdin), loop.time(), None]
        self.log.append(entry)
        await asyncio.sleep(WRITE)
        entry[2] = loop.time()
        return True, ""

    @property
    def writes(self) -> list[bytes]:
        return [e[0] for e in self.log]

    def overlaps(self) -> list[tuple[bytes, bytes]]:
        """Pairs of writes that were in flight at the same time."""
        out = []
        ordered = sorted(self.log, key=lambda e: e[1])
        for a, b in zip(ordered, ordered[1:]):
            if b[1] < a[2]:
                out.append((a[0], b[0]))
        return out


@pytest.fixture
def term(monkeypatch):
    t = _Terminal()
    monkeypatch.setattr(inject, "_run", t.run)
    return t


def test_two_key_writes_are_delivered_one_at_a_time(term, run_async):
    async def scenario():
        await asyncio.gather(inject.send_keys(NAME, "Escape"),
                             inject.send_keys(NAME, "KillLine"))

    run_async(scenario())
    assert term.writes == [ESC, KILL_LINE]
    assert term.overlaps() == []


@pytest.mark.parametrize("other,its_writes", [
    (lambda: inject.send_keys(NAME, "Escape"), [ESC]),
    (lambda: inject.discard_queued_input(NAME), [ESC, KILL_LINE]),
    (lambda: inject.send_now(NAME), [CTRL_X, CTRL_S]),
], ids=["send_keys", "discard_queued_input", "send_now"])
def test_nothing_lands_between_a_prompts_text_and_its_enter(term, run_async,
                                                            other, its_writes):
    """A Stop's Escape (or a send-now chord) that arrives while a prompt's
    text is being written waits for the prompt's Enter: it never lands
    between the text and the Enter, nor alongside either."""
    async def scenario():
        prompt = asyncio.ensure_future(inject.send_text_and_enter(NAME, "hi"))
        await asyncio.sleep(WRITE / 2)  # the text write is in flight
        assert term.writes == [b"hi"], "precondition: text write started"
        other_ok = await other()
        return await prompt, other_ok

    prompt_ok, other_ok = run_async(scenario())
    assert term.writes == [b"hi", b"\r", *its_writes]
    assert term.overlaps() == []
    assert prompt_ok is True and other_ok is True


def test_a_write_in_flight_when_the_chord_starts_ends_before_its_ctrl_x(
        term, run_async):
    """A write already going out when the chord starts (a Stop's Escape
    still being delivered as a tap arrives) is delivered before the
    Ctrl+X, so it cannot fall inside the chord unseen."""
    async def scenario():
        stop = asyncio.ensure_future(inject.send_keys(NAME, "Escape"))
        await asyncio.sleep(WRITE / 2)  # the Escape write is in flight
        assert term.writes == [ESC], "precondition: Escape write started"
        chord_ok = await inject.send_now(NAME)
        await stop
        return chord_ok

    assert run_async(scenario()) is True
    assert term.writes == [ESC, CTRL_X, CTRL_S]
    assert term.overlaps() == []


class _CheckHook(dict):
    """``inject._WRITES`` that starts a foreign writer the moment the chord
    reads the count for its check (the first read after its Ctrl+X). The
    writer's task first runs at the chord's next yield."""

    def __init__(self, term: _Terminal, foreign) -> None:
        super().__init__()
        self._term = term
        self._foreign = foreign
        self.task: asyncio.Task | None = None

    def get(self, key, default=None):
        if (self.task is None and CTRL_X in self._term.writes
                and all(e[2] is not None for e in self._term.log)):
            self.task = asyncio.get_running_loop().create_task(self._foreign())
        return super().get(key, default)


def test_no_write_between_the_chords_check_and_its_ctrl_s(term, monkeypatch,
                                                          run_async):
    """A writer that starts right at the chord's check (with no wait for
    the chord in flight) gets the terminal only after the Ctrl+S is
    delivered: nothing lands between the check and the Ctrl+S."""
    monkeypatch.setattr(inject, "_CHORD_WAIT_LIMIT", 0.0)
    hook = _CheckHook(term, lambda: inject.send_keys(NAME, "Escape"))
    monkeypatch.setattr(inject, "_WRITES", hook)

    async def scenario():
        chord_ok = await inject.send_now(NAME)
        assert hook.task is not None, "precondition: the check started it"
        await hook.task
        return chord_ok

    assert run_async(scenario()) is True
    assert term.writes == [CTRL_X, CTRL_S, ESC]
    assert term.overlaps() == []


def test_a_writer_behind_a_prompt_waits_at_most_its_settle_and_writes(
        term, run_async):
    """The documented bound: behind a long prompt, an Escape waits for the
    prompt's text, its settle delay (at most 0.5 s) and its Enter."""
    async def scenario():
        loop = asyncio.get_running_loop()
        prompt = asyncio.ensure_future(
            inject.send_text_and_enter(NAME, "x" * 1000))
        await asyncio.sleep(0)
        t0 = loop.time()
        await inject.send_keys(NAME, "Escape")
        waited = loop.time() - t0
        await prompt
        return waited

    waited = run_async(scenario())
    # text write + 0.5 s settle + Enter write + the Escape's own write
    assert waited <= 0.5 + 3 * WRITE + 0.1
    assert term.writes == [b"x" * 1000, b"\r", ESC]


def test_the_lock_is_per_session(term, run_async):
    """Another session's writes never wait for this one's."""
    async def scenario():
        await asyncio.gather(inject.send_text_and_enter(NAME, "hi"),
                             inject.send_keys("claude-other", "Escape"))

    run_async(scenario())
    starts = {e[0]: e[1] for e in term.log}
    assert starts[ESC] < starts[b"\r"]


class _LockedReads(dict):
    """``inject._WRITES`` that records, for every read of a session's count
    (the chord's check and every writer's count), whether that session's
    write lock was held at that moment."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[bool] = []

    def get(self, key, default=None):
        self.held.append(inject._write_lock(key).locked())
        return super().get(key, default)


@pytest.mark.parametrize("write", [
    lambda: inject.send_now(NAME),
    lambda: inject.send_keys(NAME, "Escape"),
    lambda: inject.send_text_and_enter(NAME, "hi"),
    lambda: inject.discard_queued_input(NAME),
], ids=["send_now", "send_keys", "send_text_and_enter",
        "discard_queued_input"])
def test_the_write_count_is_only_read_under_the_lock(term, monkeypatch,
                                                     run_async, write):
    """The chord's check and each count happen inside a lock hold, so a
    write the chord cannot see yet cannot be under way."""
    reads = _LockedReads()
    monkeypatch.setattr(inject, "_WRITES", reads)
    run_async(write())
    assert reads.held and all(reads.held), reads.held
