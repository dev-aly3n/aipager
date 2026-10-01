"""The reverse-iterator contracts (entrypoints.md "Exported functions";
design.md success criterion "The raw iterator yields the
split(b"\\n")-reversed contract (empty file -> nothing), and a 4 MB line
at chunk_bytes=1024 takes under 1 s").

Black box: only ``enforce._iter_raw_lines_reversed`` and
``enforce._iter_lines_reversed`` are called. The str iterator is also
compared with the frozen 166a3f6 one (the oracle), since its contract is
"unchanged".
"""

from __future__ import annotations

import builtins
import time

import pytest

from aipager.dtach import enforce

CHUNKS = [1, 2, 3, 7, 64, 65536]

# Equivalence classes of file contents, with the boundaries that matter to
# a chunked reverse reader: empty, newline only, no trailing newline,
# CRLF, blank runs, multi-byte UTF-8, invalid UTF-8, NUL bytes.
CONTENTS = {
    "empty": b"",
    "one_newline": b"\n",
    "two_newlines": b"\n\n",
    "one_char": b"a",
    "one_line_lf": b"a\n",
    "two_lines_lf": b"a\nb\n",
    "no_trailing": b"first\nlast",
    "crlf": b"a\r\nb\r\n",
    "lone_cr": b"a\rb\r",
    "leading_newline": b"\nx",
    "blank_runs": b"\n\nx\n\n\ny\n\n",
    "utf8": "پپپپپپپپ\nسلام\n🙂x\n".encode(),
    "bad_utf8": b"ok\n\xff\xfe\nend\xc3\n\xed\xa0\x80\n",
    "nul": b"\x00\n\x00a\x00\n",
    "ws_only_lines": b" \n\t\n\x0c\n\x0b\n\r\n",
}


def _write(tmp_path, data, name="t.jsonl"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _expected(data: bytes):
    return data.split(b"\n")[::-1] if data else []


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(CONTENTS))
def test_raw_iterator_is_split_reversed(tmp_path, name, chunk):
    data = CONTENTS[name]
    p = _write(tmp_path, data)
    assert list(enforce._iter_raw_lines_reversed(p, chunk)) == _expected(data)


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(CONTENTS))
def test_str_iterator_matches_the_166a3f6_iterator(tmp_path, old, name, chunk):
    p = _write(tmp_path, CONTENTS[name])
    assert (list(enforce._iter_lines_reversed(p, chunk))
            == list(old._iter_lines_reversed(p, chunk)))


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(CONTENTS))
def test_str_iterator_is_the_decoded_raw_iterator(tmp_path, name, chunk):
    data = CONTENTS[name]
    p = _write(tmp_path, data)
    want = [r.decode("utf-8", "replace") for r in _expected(data)]
    assert list(enforce._iter_lines_reversed(p, chunk)) == want


def test_raw_iterator_yields_bytes(tmp_path):
    p = _write(tmp_path, b"a\nb")
    assert all(type(x) is bytes for x in enforce._iter_raw_lines_reversed(p))


def test_str_iterator_yields_str(tmp_path):
    p = _write(tmp_path, b"a\n\xffb")
    assert all(type(x) is str for x in enforce._iter_lines_reversed(p))


def test_default_chunk_size_needs_no_argument(tmp_path):
    p = _write(tmp_path, b"x\ny\n")
    assert list(enforce._iter_raw_lines_reversed(p)) == [b"", b"y", b"x"]


# ---- boundary-value: a newline exactly at, just before and just after a
# chunk boundary; lines of exactly chunk, chunk-1 and chunk+1 bytes -------

@pytest.mark.parametrize("chunk", [4, 16, 64])
@pytest.mark.parametrize("offset", [-2, -1, 0, 1, 2])
def test_newline_around_a_chunk_boundary(tmp_path, chunk, offset):
    # The reader works backwards from EOF, so a boundary is measured from
    # the end of the file.
    tail = b"T" * (chunk + offset)
    data = b"H" * (3 * chunk) + b"\n" + tail
    p = _write(tmp_path, data)
    assert list(enforce._iter_raw_lines_reversed(p, chunk)) == _expected(data)


@pytest.mark.parametrize("chunk", [8, 64])
@pytest.mark.parametrize("size_delta", [-1, 0, 1])
def test_lines_of_exactly_one_chunk(tmp_path, chunk, size_delta):
    one = b"L" * (chunk + size_delta)
    data = b"\n".join([one] * 5) + b"\n"
    p = _write(tmp_path, data)
    assert list(enforce._iter_raw_lines_reversed(p, chunk)) == _expected(data)


def test_a_line_spanning_many_chunks(tmp_path):
    data = b"head\n" + bytes(range(256)).replace(b"\n", b"") * 400 + b"\ntail"
    p = _write(tmp_path, data)
    assert list(enforce._iter_raw_lines_reversed(p, 7)) == _expected(data)


def test_multibyte_character_split_across_chunks_decodes_whole(tmp_path):
    data = ("a" + "پ" * 50 + "\n" + "🙂" * 30).encode()
    p = _write(tmp_path, data)
    assert list(enforce._iter_lines_reversed(p, 3)) == [
        "🙂" * 30, "a" + "پ" * 50]


def test_lone_surrogate_bytes_are_replaced_not_raised(tmp_path):
    p = _write(tmp_path, b"\xed\xa0\x80")
    assert list(enforce._iter_lines_reversed(p)) == ["�" * 3]


def test_chunk_size_does_not_change_the_lines(tmp_path):
    import random
    rng = random.Random(4242)
    data = bytes(rng.choice(b"ab\n\r\xff{}") for _ in range(5000))
    p = _write(tmp_path, data)
    got = {n: list(enforce._iter_raw_lines_reversed(p, n))
           for n in (1, 5, 13, 100, 4096, 1 << 20)}
    assert all(v == _expected(data) for v in got.values())


# ---- laziness: only the tail is read for an early break -----------------

def _spy_open(monkeypatch, target):
    counts = {"bytes": 0, "opens": 0}
    real_open = builtins.open

    class _Spy:
        def __init__(self, f):
            self._f = f

        def read(self, *a):
            d = self._f.read(*a)
            counts["bytes"] += len(d)
            return d

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

        def __getattr__(self, name):
            return getattr(self._f, name)

    def spy(path, *a, **k):
        f = real_open(path, *a, **k)
        if str(path) == str(target):
            counts["opens"] += 1
            return _Spy(f)
        return f

    monkeypatch.setattr(builtins, "open", spy)
    return counts


def test_early_break_reads_only_the_tail(tmp_path, monkeypatch):
    p = tmp_path / "big.jsonl"
    p.write_bytes((b"x" * 1023 + b"\n") * 8192 + b"tail\n")   # ~8 MB
    counts = _spy_open(monkeypatch, p)
    it = enforce._iter_raw_lines_reversed(p, 4096)
    first = next(x for x in it if x)
    it.close()
    assert (first, counts["bytes"] <= 2 * 4096) == (b"tail", True)


def test_str_iterator_early_break_reads_only_the_tail(tmp_path, monkeypatch):
    p = tmp_path / "big.jsonl"
    p.write_bytes((b"y" * 1023 + b"\n") * 8192 + b"tail\n")
    counts = _spy_open(monkeypatch, p)
    it = enforce._iter_lines_reversed(p)
    first = next(x for x in it if x)
    it.close()
    assert (first, counts["bytes"] <= 2 * 65536) == ("tail", True)


def test_iterator_reads_through_the_builtin_open(tmp_path, monkeypatch):
    p = _write(tmp_path, b"a\nb\n")
    counts = _spy_open(monkeypatch, p)
    list(enforce._iter_raw_lines_reversed(p))
    assert counts["opens"] == 1


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        list(enforce._iter_raw_lines_reversed(tmp_path / "nope.jsonl"))


# ---- error guessing: the transcript grows while it is being scanned -----

@pytest.mark.parametrize("chunk", [5, 64, 65536])
@pytest.mark.parametrize("after", [0, 1, 3])
def test_append_during_iteration_matches_166a3f6(tmp_path, old, chunk, after):
    """Claude Code (or a background agent) appends to the transcript while
    the hook reads it. The new iterator must yield what the old one
    yielded under the same interleaving."""
    base = b"".join(b'{"n":%d,"pad":"%s"}\n' % (i, b"p" * (i * 7))
                    for i in range(12))

    def run(make_iter):
        p = tmp_path / "grow.jsonl"
        p.write_bytes(base)
        it = make_iter(p)
        out = []
        for i, x in enumerate(it):
            out.append(x)
            if i == after:
                with open(p, "ab") as f:
                    f.write(b'{"appended":true}\n' * 3)
        return out

    new = run(lambda p: enforce._iter_lines_reversed(p, chunk))
    ref = run(lambda p: old._iter_lines_reversed(p, chunk))
    assert new == ref


@pytest.mark.parametrize("chunk", [5, 65536])
def test_raw_append_during_iteration_yields_the_opening_snapshot(tmp_path,
                                                                 chunk):
    base = b"a\nbb\nccc\n" * 20
    p = _write(tmp_path, base)
    out = []
    for i, x in enumerate(enforce._iter_raw_lines_reversed(p, chunk)):
        out.append(x)
        if i == 2:
            with open(p, "ab") as f:
                f.write(b"late\n")
    assert out == _expected(base)


# ---- linear time and bounded memory ------------------------------------

def test_4mb_single_line_raw_at_1024_byte_chunks_under_1s(tmp_path):
    data = b"z" * (4 << 20)
    p = _write(tmp_path, data)
    t = time.perf_counter()
    got = list(enforce._iter_raw_lines_reversed(p, 1024))
    took = time.perf_counter() - t
    assert (got == [data], took < 1.0) == (True, True), took


def test_4mb_single_line_str_at_1024_byte_chunks_under_1s(tmp_path):
    data = b"z" * (4 << 20)
    p = _write(tmp_path, data)
    t = time.perf_counter()
    got = list(enforce._iter_lines_reversed(p, 1024))
    took = time.perf_counter() - t
    assert (got == ["z" * (4 << 20)], took < 1.0) == (True, True), took


def test_time_is_linear_in_the_line_length(tmp_path):
    """Doubling the line twice must not multiply the time by ~16 (the
    quadratic reader); allow 8x for a 4x longer line plus noise."""
    def cost(n):
        p = _write(tmp_path, b"q" * n, f"l{n}.jsonl")
        best = float("inf")
        for _ in range(3):
            t = time.perf_counter()
            for _x in enforce._iter_raw_lines_reversed(p, 2048):
                pass
            best = min(best, time.perf_counter() - t)
        return best
    small, big = cost(2 << 20), cost(8 << 20)
    assert big < 8 * small + 0.05, (small, big)


def test_peak_memory_is_about_twice_the_longest_line(tmp_path):
    import tracemalloc
    n = 16 << 20
    p = _write(tmp_path, b"head\n" + b"m" * n + b"\nshort\n")
    tracemalloc.start()
    try:
        for _x in enforce._iter_raw_lines_reversed(p):
            pass
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 2.5 * n + (1 << 20), peak
