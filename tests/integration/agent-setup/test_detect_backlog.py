"""Iteration 2: detect-chat ``warnings`` and the ``updates_backlog``
warning (entrypoints.md, detect-chat JSON): with no offset Telegram shows
only the oldest 100 updates, so when the last answered getUpdates
returned a full batch of 100 and the timeout hits, exit 8 carries
``updates_backlog`` and the fix asks for the numeric id instead of
pressing Start again. ``warnings`` is always present."""

from __future__ import annotations

import pytest

from agent_setup_support import HTTP, NET, ok

T0 = 1_800_000_000
OLD = T0 - 10_000          # far outside the 600 s lookback
ADA = 123456789


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        if len(self.sleeps) > 5000:
            raise AssertionError("detect-chat looped without end")
        self.mono += s


@pytest.fixture
def clock(env, monkeypatch):
    c = Clock()
    import aipager.setup_detect as sd
    monkeypatch.setattr(sd, "_now", lambda: float(T0))
    monkeypatch.setattr(sd, "_monotonic", lambda: c.mono)
    monkeypatch.setattr(sd, "_sleep", c.sleep)
    return c


def _upd(i, date=OLD, uid=None):
    uid = uid if uid is not None else 500_000_000 + i
    return {"update_id": 1000 + i,
            "message": {"message_id": i, "date": date,
                        "chat": {"id": uid, "type": "private",
                                 "first_name": f"U{i}"},
                        "from": {"id": uid, "is_bot": False,
                                 "first_name": f"U{i}"},
                        "text": "/start"}}


def _batch(n, date=OLD):
    return ok([_upd(i, date) for i in range(n)])


def _detect(env, *extra, json_out=True):
    args = ["setup", "detect-chat", "--token-file", env.token_file(),
            *extra]
    if json_out:
        args.append("--json")
    return env.run(*args)


def _codes(r):
    return [w["code"] for w in r.json["warnings"]]


# ---- full batch at the deadline ----

def test_full_batch_timeout_exits_8(env, clock):
    env.tg.set("getUpdates", _batch(100))
    r = _detect(env, "--timeout", "10")
    assert (r.code, r.json["error"]) == (8, "detect_timeout")


def test_full_batch_timeout_has_backlog_warning(env, clock):
    env.tg.set("getUpdates", _batch(100))
    assert "updates_backlog" in _codes(_detect(env, "--timeout", "10"))


def test_full_batch_single_poll_has_backlog_warning(env, clock):
    """--timeout 0 is one poll; that poll is the last answered one."""
    env.tg.set("getUpdates", _batch(100))
    assert "updates_backlog" in _codes(_detect(env, "--timeout", "0"))


def test_full_batch_backlog_warning_has_a_message(env, clock):
    env.tg.set("getUpdates", _batch(100))
    w = [w for w in _detect(env, "--timeout", "10").json["warnings"]
         if w["code"] == "updates_backlog"]
    assert len(w) == 1 and w[0]["message"].strip() != ""


def test_full_batch_fix_asks_for_the_numeric_id(env, clock):
    env.tg.set("getUpdates", _batch(100))
    fix = _detect(env, "--timeout", "10").json["fix"] or ""
    assert "numeric" in fix.lower() and "--chat-id" in fix


def test_full_batch_fix_does_not_say_press_start_again(env, clock):
    env.tg.set("getUpdates", _batch(100))
    fix = _detect(env, "--timeout", "10").json["fix"] or ""
    assert "press start" not in fix.lower()


def test_more_than_100_is_also_a_backlog(env, clock):
    """Boundary just above: >= 100 counts as full."""
    env.tg.set("getUpdates", _batch(101))
    assert "updates_backlog" in _codes(_detect(env, "--timeout", "10"))


def test_full_batch_writes_nothing(env, clock):
    env.tg.set("getUpdates", _batch(100))
    before = env.snapshot()
    _detect(env, "--timeout", "10")
    assert env.snapshot() == before


def test_full_batch_still_sends_no_offset(env, clock):
    env.tg.set("getUpdates", _batch(100))
    _detect(env, "--timeout", "10")
    assert [c.query for c in env.tg.calls_to("getUpdates")
            if c.query] == []


def test_full_batch_candidate_is_null(env, clock):
    env.tg.set("getUpdates", _batch(100))
    assert _detect(env, "--timeout", "10").json["candidate"] is None


def test_full_batch_plain_mode_mentions_the_limit(env, clock):
    env.tg.set("getUpdates", _batch(100))
    r = _detect(env, "--timeout", "10", json_out=False)
    assert r.code == 8 and "100" in r.err


# ---- not a backlog ----

def test_99_updates_is_a_plain_timeout(env, clock):
    """Boundary just below: 99 is not a full batch."""
    env.tg.set("getUpdates", _batch(99))
    assert "updates_backlog" not in _codes(_detect(env, "--timeout", "10"))


def test_empty_batch_timeout_has_empty_warnings(env, clock):
    env.tg.set("getUpdates", ok([]))
    assert _detect(env, "--timeout", "10").json["warnings"] == []


def test_full_then_small_batch_is_a_plain_timeout(env, clock):
    """Judged by the LAST answered poll."""
    polls = []

    def _resp(call):
        polls.append(1)
        return _batch(100) if len(polls) == 1 else _batch(3)
    env.tg.set("getUpdates", _resp)
    assert "updates_backlog" not in _codes(_detect(env, "--timeout", "10"))


def test_small_then_full_batch_is_a_backlog(env, clock):
    polls = []

    def _resp(call):
        polls.append(1)
        return _batch(3) if len(polls) == 1 else _batch(100)
    env.tg.set("getUpdates", _resp)
    assert "updates_backlog" in _codes(_detect(env, "--timeout", "10"))


def test_full_batch_with_a_fresh_message_is_found(env, clock):
    """A fresh private message inside a full batch is still a candidate
    (the batch limit only hides updates beyond the first 100)."""
    ups = [_upd(i) for i in range(99)] + [_upd(99, date=T0 - 5, uid=ADA)]
    env.tg.set("getUpdates", ok(ups))
    r = _detect(env, "--timeout", "10")
    assert (r.code, r.json["candidate"]["id"]) == (0, ADA)


# ---- warnings always present ----

@pytest.mark.parametrize("case", ["found", "timeout", "conflict",
                                  "network", "bad_token"])
def test_detect_warnings_key_always_a_list(env, clock, case):
    if case == "found":
        env.tg.set("getUpdates", ok([_upd(1, date=T0 - 5, uid=ADA)]))
    elif case == "conflict":
        env.tg.set("getUpdates", HTTP(409, "Conflict"))
    elif case == "network":
        env.tg.set("getUpdates", NET())
    elif case == "bad_token":
        env.tg.set("getMe", HTTP(401, "Unauthorized"))
    assert isinstance(_detect(env, "--timeout", "4").json["warnings"], list)


def test_detect_usage_error_has_warnings_list(env, clock):
    r = _detect(env, "--timeout", "abc")
    assert isinstance(r.json["warnings"], list)
