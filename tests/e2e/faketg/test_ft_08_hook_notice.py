"""Scenario (the "update is only half done" notice): aipager is installed
but the daemon was not restarted yet, so Claude Code's hooks already run
the new version while the daemon still runs the old one.

The daemon starts from a copy of the code under test whose version reads
0.0.1, while the session's hooks (the instance's shims) run the real code:
every hook datagram reports a newer aipager than the daemon runs, as right
after ``pipx install`` and before the restart. Both halves of the promise,
through the real daemon, the real hook binary, the session monitor's tick,
real restarts and the remembered-notices file:

A. a restart within the notice's wait (``HOOK_NOTICE_GRACE_SECONDS``, two
   minutes) shows nothing at all;
B. past the wait the notice arrives with its Restart now button, and once
   the daemon runs the new version the same message says the update is
   finished, without the button.

Runs with real Claude or the stand-in; about three minutes either way.
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import datetime

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

DM = fti.DM_ID
LABEL = "ft21"
OLD = "0.0.1"
HALF_DONE = "The aipager update is only half done"
#: The notice waits two minutes from when the newer version was first
#: seen (the daemon's hooks_newer line): none may have gone out this long
#: after it...
QUIET_FOR = 100
#: ... and it must have by then (the wait, a monitor tick, slack).
SENT_WITHIN = 180
#: Part A's restart comes this long after the newer hooks were seen (the
#: operator's came 38 s after the install): well inside the wait, and past
#: the 30 s a notice held for a chat that is not clear waits to try again,
#: so a notice with no wait would have gone out.
RESTART_AFTER = 45


def _real_version(inst) -> str:
    text = (inst.repo / "aipager" / "_version.py").read_text()
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def _old_copy(inst) -> None:
    """Copy the code under test into the instance (removed with it), make
    the copy's version read OLD, and start the daemon from it."""
    old = inst.root / "old"
    shutil.copytree(inst.repo / "aipager", old / "aipager",
                    ignore=shutil.ignore_patterns("__pycache__"))
    version = old / "aipager" / "_version.py"
    text, n = re.subn(r'^__version__ = "[^"]+"', f'__version__ = "{OLD}"',
                      version.read_text(), flags=re.M)
    assert n == 1, "no __version__ line in the copy's _version.py"
    version.write_text(text)
    inst.env["PYTHONPATH"] = str(old)


def _age(line: str) -> float:
    """Seconds since the daemon wrote log *line*, by its own ``HH:MM:SS``
    stamp (same machine, same clock): when the newer version was first
    seen, whatever brought the first datagram and however long the turn
    took. The stamp's whole seconds make this at most a second long."""
    m = re.match(r"(\d\d):(\d\d):(\d\d) ", line)
    assert m, f"no time stamp on {line!r}"
    now = datetime.now()
    then = now.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0)
    return (now - then).total_seconds() % 86400


def _run_old(inst) -> None:
    inst.env["PYTHONPATH"] = str(inst.root / "old")


def _run_real(inst) -> None:
    inst.env["PYTHONPATH"] = str(inst.repo)


def _notices(fake) -> list:
    return [c for c in fake.calls(chat_id=DM)
            if c.method == "sendMessage" and HALF_DONE in c.text]


def test_the_half_done_notice_waits_and_then_says_finished(own_faketg):
    inst = own_faketg({}, before_start=_old_copy)
    fake = inst.fake
    real = _real_version(inst)
    newer = f"update.hooks_newer hook={real} daemon={OLD}"
    held = ("update.hooks_newer.notice", f"hook={real}", "result=deferred reason=grace")

    # A. The hooks report the real version to the old daemon; a restart
    # into the real code RESTART_AFTER later: no notice, ever.
    mark = inst.log_mark()
    name = inst.new_session(DM, fti.ALICE, LABEL)
    flows.prompt_turn(inst, DM, fti.ALICE, name, "notice one: reply with exactly OK",
                      "notice one")
    first = inst.wait_log(newer, since=mark)
    inst.wait_log(*held, since=mark)        # held for the wait, not for a busy chat
    time.sleep(max(0.0, RESTART_AFTER - _age(first)))
    assert _notices(fake) == [], "the notice went out before a quick restart"
    _run_real(inst)
    inst.restart_daemon()
    flows.prompt_turn(inst, DM, fti.ALICE, name, "notice two: reply with exactly OK",
                      "notice two")
    sent_line = f"update.hooks_newer.notice hook={real} result=sent"
    assert not any(sent_line in line for line in inst.log_lines(mark))

    # B. Back on the old daemon: quiet for the wait, then the notice. The
    # mark comes before the restart: the one hooks_newer line of the new
    # run may come from any datagram, the first one included.
    _run_old(inst)
    mark = inst.log_mark()
    inst.restart_daemon()
    flows.prompt_turn(inst, DM, fti.ALICE, name, "notice three: reply with exactly OK",
                      "notice three")
    first = inst.wait_log(newer, since=mark)
    inst.wait_log(*held, since=mark)
    time.sleep(max(0.0, QUIET_FOR - _age(first)))
    assert _notices(fake) == [], "the notice went out during its wait"
    sent = flows.wait_text(fake, DM, HALF_DONE, since=0, methods=("sendMessage",),
                           timeout=SENT_WITHIN - QUIET_FOR)
    msg_id = (sent.response.get("result") or {}).get("message_id")
    inst.wait_log(sent_line, since=mark)
    found = fake.find_button(DM, "Restart now", message_text_contains=HALF_DONE)
    assert found is not None, "the notice has no Restart now button"
    assert found[0]["message_id"] == msg_id and found[1] == "_:up:rs", found

    # The daemon restarted into the new version: the same message says so.
    _run_real(inst)
    mark = inst.log_mark()
    inst.restart_daemon()
    inst.wait_log("update.hooks_finished.edited", f"message={msg_id}", f"hook={real}",
                  f"running={real}", since=mark, timeout=60)
    msg = fake.message(DM, msg_id)
    assert f"The aipager update is finished: aipager runs {real} now." in msg["text"], msg
    assert "reply_markup" not in msg, msg
    assert len(_notices(fake)) == 1, [c.text for c in _notices(fake)]
