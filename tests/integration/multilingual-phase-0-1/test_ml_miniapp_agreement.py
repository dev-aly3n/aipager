"""The Mini App and the bot agree on the clock (design.md success criterion 4:
"The Mini App shows the same string as the bot for the same seconds and
unit").

Two halves, both through public surfaces:
- the page served at ``GET /`` carries ``clockText(seconds, unit)`` between
  the documented marker comments; run under node, it must equal
  ``aipager.clockfmt.elapsed`` on every partition and boundary;
- ``GET /api/sessions/{label}`` carries a floored ``busy_elapsed_seconds``,
  the card's ``busy_elapsed_unit`` and a "Working for" fact that is the
  clock for those two, and the busy card built from the same session shows
  the same clock.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

import pytest

from aipager import clockfmt
from aipager.state import Status
from tests.i18n_tool import isolated_env

START = "// clock: port of aipager/clockfmt.py"
END = "// end clock"

SECONDS = [0, 1, 29, 30, 45, 59.9, 60, 250, 599, 599.9, 600, 1397, 1409, 1410,
           1425, 3599, 3599.9, 3600, 3629, 3630, 5010, 5025, 7200, 36000,
           360000, -0.1, -4]
UNITS = ["s", "m", "h", "x"]


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _page(mini_server, run_async):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/")
            assert resp.status == 200
            return await resp.text()
        finally:
            await client.close()
    return run_async(_go())


def _port(page: str) -> str:
    assert page.count(START) == 1, "the clock port's start marker is missing"
    body = page.split(START, 1)[1]
    assert END in body, "the clock port's end marker is missing"
    return body.split(END, 1)[0]


def _run_js(node, tmp_path, port_src, cases_js):
    script = tmp_path / "clock.js"
    script.write_text(
        port_src + "\n"
        + "var cases = " + cases_js + ";\n"
        + "var out = cases.map(function (c) { return clockText(c[0], c[1]); });\n"
        + "process.stdout.write(JSON.stringify(out));\n",
        encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True,
                          timeout=60, env=isolated_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_page_carries_exactly_one_clock_port(mini_server, run_async):
    assert "function clockText" in _port(_page(mini_server, run_async))


def test_the_old_duration_formatter_is_gone(mini_server, run_async):
    """design.md: duration() is deleted and replaced by clockText."""
    assert "function duration(" not in _page(mini_server, run_async)


def test_the_js_clock_equals_the_python_clock(mini_server, run_async, node_bin, tmp_path):
    cases = [(s, u) for s in SECONDS for u in UNITS]
    got = _run_js(node_bin, tmp_path, _port(_page(mini_server, run_async)),
                  json.dumps(cases))
    want = [clockfmt.elapsed(s, u) for s, u in cases]
    mismatches = [(c, g, w) for c, g, w in zip(cases, got, want) if g != w]
    assert mismatches == []


def test_the_js_clock_treats_invalid_input_like_python(
        mini_server, run_async, node_bin, tmp_path):
    js_cases = ('[[null,"s"],[NaN,"s"],[Infinity,"m"],[-Infinity,"s"],'
                '["abc","s"],["45","s"],[undefined,"m"]]')
    py_cases = [(None, "s"), (float("nan"), "s"), (float("inf"), "m"),
                (float("-inf"), "s"), ("abc", "s"), ("45", "s"), (None, "m")]
    got = _run_js(node_bin, tmp_path, _port(_page(mini_server, run_async)), js_cases)
    assert got == [clockfmt.elapsed(s, u) for s, u in py_cases]


def test_the_js_clock_defaults_to_true_seconds(mini_server, run_async, node_bin, tmp_path):
    script_cases = "[[1425]]"
    got = _run_js(node_bin, tmp_path, _port(_page(mini_server, run_async)), script_cases)
    assert got == ["23:45"]


def test_the_parity_check_would_see_a_drifted_port(mini_server, run_async, node_bin, tmp_path):
    """Guard the guard: a port that rounds instead of truncating differs."""
    port = _port(_page(mini_server, run_async))
    drifted = port + "\nvar _real = clockText;\n" \
        "clockText = function (s, u) { return _real(Math.round(Number(s) || 0), u); };\n"
    got = _run_js(node_bin, tmp_path, drifted, "[[599.9, \"s\"]]")
    assert got != [clockfmt.elapsed(599.9, "s")]


# ---- GET /api/sessions/{label} ----------------------------------------------------

GROUP_CHAT = -1001
BOB_ID = 2  # a member of the group scope only


def _busy(bot, age, unit):
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = GROUP_CHAT
    sess.scope_kind = "group"
    sess.status = Status.BUSY
    sess.busy_started_at = time.monotonic() - age
    sess.card_elapsed_unit = unit
    return sess


@pytest.fixture
def detail(mini_server, run_async, ml):
    return lambda: _detail(mini_server, run_async, ml.hdr(BOB_ID))


def _detail(mini_server, run_async, headers):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/api/sessions/dev", headers=headers)
            assert resp.status == 200
            return await resp.json()
        finally:
            await client.close()
    return run_async(_go())


def _working_for(body):
    values = [f["value"] for f in body["facts"] if f["label"] == "Working for"]
    assert len(values) == 1, body["facts"]
    return values[0]


def test_detail_floors_the_elapsed_seconds(tg_bot, detail):
    _busy(tg_bot, 250.7, "s")
    assert detail()["busy_elapsed_seconds"] == 250


@pytest.mark.parametrize("unit", ["s", "m", "h"])
def test_detail_carries_the_cards_unit(tg_bot, detail, unit):
    _busy(tg_bot, 1425.3, unit)
    assert detail()["busy_elapsed_unit"] == unit


@pytest.mark.parametrize("unit", [None, "", "x", "minutes"])
def test_detail_unit_falls_back_to_seconds(tg_bot, detail, unit):
    _busy(tg_bot, 1425.3, unit)
    assert detail()["busy_elapsed_unit"] == "s"


@pytest.mark.parametrize("age,unit,clock", [
    (250.3, "s", "4:10"),
    (599.3, "s", "9:59"),
    (1425.3, "s", "23:45"),
    (1425.3, "m", "23:30"),
    (1409.3, "m", "23:00"),
    (3599.3, "s", "59:59"),
    (3600.3, "s", "1:00:00"),
    (5025.3, "h", "1:23:30"),
])
def test_working_for_is_the_clock(tg_bot, detail, age, unit, clock):
    _busy(tg_bot, age, unit)
    assert _working_for(detail()) == clock


@pytest.mark.parametrize("age,unit", [(250.3, "s"), (1425.3, "m"), (5025.3, "m")])
def test_working_for_equals_the_clock_of_the_payloads_own_numbers(
        tg_bot, detail, age, unit):
    _busy(tg_bot, age, unit)
    body = detail()
    assert _working_for(body) == clockfmt.elapsed(
        body["busy_elapsed_seconds"], body["busy_elapsed_unit"])


@pytest.mark.parametrize("age,unit", [(250.3, "s"), (1425.3, "m"), (5025.3, "m")])
def test_the_app_and_the_busy_card_show_the_same_clock(
        tg_bot, detail, age, unit):
    from aipager.bot.animation import build_stream_card
    sess = _busy(tg_bot, age, unit)
    app = _working_for(detail())
    status = build_stream_card(sess, "Working").rpartition("\n\n")[2]
    assert f" · Working · {app}" in status


def test_the_timeline_elapsed_is_floored(tg_bot, detail):
    sess = _busy(tg_bot, 250.3, "s")
    sess.tool_history = [("Read foo.py", True), ("Bash: pytest", False)]
    body = detail()
    values = [row["elapsed_seconds"] for row in body["timeline"]
              if row.get("elapsed_seconds") is not None]
    assert all(isinstance(v, int) for v in values), values
