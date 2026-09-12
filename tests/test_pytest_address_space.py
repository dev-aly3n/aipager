"""Guard: driving a hook's ``main()`` in-process must not clamp pytest.

``notify_hook.main()`` and ``statusline_notify.main()`` both call
``resource.setrlimit(RLIMIT_AS, (1 GiB, 1 GiB))`` early in ``main()``,
before any real work — though not literally first: ``_prepare_cap_notifier``
pre-opens the cap-hit socket ahead of it by design, so those allocations
cannot themselves trip the cap.
Eight test files call those functions directly, so without the autouse
``_never_clamp_the_test_process`` fixture in ``tests/conftest.py`` the
first of them to run pins the **pytest process** to a 1 GiB address
space — permanently, because an unprivileged process cannot raise a hard
limit back. A full run then ends at 99.99 % of that ceiling and the next
thread-stack mmap dies with ``RuntimeError: can't start new thread``,
blamed on an unrelated test (``test_voice.py`` is the usual canary).

These tests fail if that fixture is removed or stops recording. Note for
whoever runs that mutation: deleting the fixture really does clamp the
mutant pytest process, so run it in its OWN pytest invocation — the
damage outlives the test that caused it and corrupts later results.
"""

from __future__ import annotations

import io
import re
import resource
import sys
import types
from pathlib import Path

import pytest

from aipager.dtach import notify_hook, statusline_notify


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _isolate_snapshot_path(monkeypatch, tmp_path):
    """Mirror of ``test_hook_style_contract.py``'s helper — the hook reads
    the canonical snapshot by this function, so redirecting it keeps the
    run off the operator's real ``~/.claude``."""
    from aipager import policy_snapshot
    monkeypatch.setattr(policy_snapshot, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")


def _drive_notify_hook(monkeypatch, tmp_path):
    """Run ``notify_hook.main()`` exactly the way the unpatched callers in
    ``tests/integration/add-telegram-settings-menu/`` do: stdin stubbed,
    ``SOCKET_PATH`` pointed at ``tmp_path`` so no datagram can reach the
    operator's live daemon, snapshot path isolated."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    _set_stdin(monkeypatch,
               '{"hook_event_name":"PreToolUse","tool_name":"Read"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-rlimit-guard")
    notify_hook.main()


# ---- the limit itself is never touched ------------------------------------
#
# Deliberately does NOT request the recorder fixture by name: if the
# autouse fixture is deleted outright this still fails on a real
# assertion about the real limit, rather than erroring with "fixture not
# found", which is a far clearer signal for the next reader.

def test_notify_hook_main_does_not_clamp_this_process(monkeypatch, tmp_path):
    before = resource.getrlimit(resource.RLIMIT_AS)
    _drive_notify_hook(monkeypatch, tmp_path)
    after = resource.getrlimit(resource.RLIMIT_AS)
    assert after == before, (
        f"notify_hook.main() clamped the pytest process: {before} -> {after}. "
        "The autouse _never_clamp_the_test_process fixture in "
        "tests/conftest.py is missing or no longer intercepts setrlimit."
    )


def test_statusline_main_does_not_clamp_this_process(monkeypatch, tmp_path):
    """The second clamp site. ``statusline_notify.main()`` has its own
    copy of the cap, and ``test_dtach_hook_stubs.py`` reaches it without
    a stub too (``test_statusline_debug_logs_write_failure``)."""
    before = resource.getrlimit(resource.RLIMIT_AS)
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH",
                        str(tmp_path / "nope.sock"))
    # Empty stdin + no session skips ``_run``'s whole forwarding block, so
    # no /tmp/claude-status-*.json is written and no datagram is sent. It
    # is not a total no-op: the trailing status line still runs, fails its
    # ``json.loads("")`` and writes "" to stdout (statusline_notify.py:131).
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()
    after = resource.getrlimit(resource.RLIMIT_AS)
    assert after == before, (
        f"statusline_notify.main() clamped the pytest process: "
        f"{before} -> {after}."
    )


def test_the_process_is_not_already_clamped_to_the_hook_cap():
    """Belt and braces: if some earlier test in the same process clamped
    us, every assertion above still passes (before == after) while the
    suite is already doomed. This catches that state directly."""
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    assert hard != notify_hook._MEMORY_CAP_BYTES, (
        "the pytest process is already pinned to the hook's 1 GiB cap. "
        "Either something called a hook main() with setrlimit unstubbed, "
        "or you invoked pytest under a coincidental `ulimit -v 1048576` — "
        "check that before going hunting, the two look identical from here"
    )


# ---- the recorder still sees the real arguments ---------------------------

def test_recorder_captures_exactly_one_hook_cap_call(
    monkeypatch, tmp_path, _never_clamp_the_test_process,
):
    """The fixture swallows the call, so it must hand the arguments back —
    otherwise the contract ``test_dtach_hook_stubs.py`` asserts (the hook
    caps itself at ``_MEMORY_CAP_BYTES``) would become unobservable."""
    calls = _never_clamp_the_test_process
    assert calls == [], "recorder must start empty for each test"

    _drive_notify_hook(monkeypatch, tmp_path)

    assert len(calls) == 1, f"expected exactly one setrlimit call, got {calls!r}"
    res, (soft, hard) = calls[0]
    assert res == resource.RLIMIT_AS
    assert soft == notify_hook._MEMORY_CAP_BYTES
    assert hard == notify_hook._MEMORY_CAP_BYTES


def test_getrlimit_is_left_real(_never_clamp_the_test_process):
    """Only ``setrlimit`` is intercepted; reading the limits must still hit
    the kernel.

    This one is load-bearing rather than decorative. Every test above
    compares getrlimit-before against getrlimit-after, so a faked
    ``getrlimit`` would make all of them pass no matter what happened to
    the real limit — this is the only test in the file positioned to
    notice that the guards have gone blind. So it checks two things a
    stub cannot fake: that the attribute is still the C builtin, and
    that its answer agrees with ``/proc/self/limits``, which the kernel
    renders independently of anything Python has patched.
    """
    # CPython-specific: on another implementation the stdlib function may
    # not be a builtin_function_or_method. Deliberate — CI is CPython
    # 3.10-3.13 — and the /proc cross-check below is the portable half.
    assert isinstance(resource.getrlimit, types.BuiltinFunctionType), (
        f"resource.getrlimit is {resource.getrlimit!r}, not the C builtin — "
        "something replaced it, and every other guard in this file is now "
        "comparing one fake reading against another"
    )

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)

    try:
        rows = Path("/proc/self/limits").read_text(
            encoding="utf-8").splitlines()
    except OSError:  # non-Linux dev box; the builtin check above still ran
        pytest.skip("no /proc/self/limits on this platform")

    for row in rows:
        if row.startswith("Max address space"):
            # "Max address space  <soft>  <hard>  bytes", where an absent
            # limit renders as the word "unlimited" (== RLIM_INFINITY).
            fields = row.split()
            proc_soft, proc_hard = fields[3], fields[4]
            break
    else:
        pytest.skip("/proc/self/limits has no 'Max address space' row")

    def _as_int(word):
        return resource.RLIM_INFINITY if word == "unlimited" else int(word)

    assert (soft, hard) == (_as_int(proc_soft), _as_int(proc_hard)), (
        f"resource.getrlimit reports {(soft, hard)} but the kernel's own "
        f"/proc/self/limits says {(proc_soft, proc_hard)}"
    )


def test_a_later_monkeypatch_still_wins(monkeypatch, tmp_path,
                                        _never_clamp_the_test_process):
    """``_capture_setrlimit`` in ``test_dtach_hook_stubs.py`` patches the
    same attribute per-test. Verify the autouse fixture does not shadow
    it: the test's own stub must receive the call, and the fixture's
    recorder must stay empty."""
    mine: list = []
    monkeypatch.setattr(notify_hook.resource, "setrlimit",
                        lambda res, limits: mine.append((res, limits)))

    _drive_notify_hook(monkeypatch, tmp_path)

    assert len(mine) == 1, "the per-test stub must win over the autouse one"
    assert _never_clamp_the_test_process == [], (
        "the autouse recorder must not also see the call"
    )


def test_a_raising_setrlimit_stub_still_reaches_the_hook(monkeypatch, tmp_path):
    """The other per-test pattern: ``test_notify_hook_survives_rlimit_*``
    replace setrlimit with something that raises, to prove the hook
    swallows it. The autouse fixture must not absorb that instead.

    Counting the calls is the whole point. "``main()`` did not raise" is
    true either way — if the recorder won, ``_boom`` would simply never
    run — so an assertion-free version of this test could not fail for
    the reason its name gives.
    """
    boom_calls: list = []

    def _boom(res, limits):
        boom_calls.append((res, limits))
        raise ValueError("kernel refuses this tightening")

    monkeypatch.setattr(notify_hook.resource, "setrlimit", _boom)
    _drive_notify_hook(monkeypatch, tmp_path)  # must not raise

    assert len(boom_calls) == 1, (
        "the hook must reach the test's own raising stub rather than the "
        f"autouse recorder; got {boom_calls!r}"
    )


# ---- the production clamp is untouched ------------------------------------

def test_the_hook_still_asks_for_the_one_gib_cap():
    """Nothing in this fix may soften the real subprocess cap: the fixture
    is a test-process shield, not a behaviour change."""
    assert notify_hook._MEMORY_CAP_BYTES == 1024 * 1024 * 1024
    assert statusline_notify._MEMORY_CAP_BYTES == 1024 * 1024 * 1024


@pytest.mark.parametrize("module", [notify_hook, statusline_notify])
def test_both_hooks_call_setrlimit_qualified(module):
    """The fixture patches the attribute on the ``resource`` module object,
    so the shield holds only while the hooks look the function up through
    that object at call time, i.e. ``resource.setrlimit(...)``.

    The identity assertion below is the part doing the real work — it is
    what proves the module the hook consults is the very object the
    fixture patches. The source greps are a best-effort second line of
    defence against binding the real function at import time and calling
    that forever: ``from resource import setrlimit``, and a module-level
    alias ``_setrlimit = resource.setrlimit``.

    The alias grep is anchored at column 0 on purpose. A name bound
    *inside* a function is resolved when the function runs, by which time
    the fixture has already patched the module, so a function-local alias
    is harmless and must not be flagged. And a grep cannot see every
    shape — ``obj.attr =``, an annotated assignment, a tuple unpack, a
    ``getattr`` — so treat it as a tripwire for the obvious case, not
    proof. The identity assert is what actually holds the line.
    """
    assert module.resource is resource, (
        f"{module.__name__}.resource is not the stdlib module the fixture "
        "patches, so the shield does not cover it"
    )
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "from resource import" not in src, (
        f"{module.__name__} must reach setrlimit as resource.setrlimit, "
        "or tests/conftest.py's _never_clamp_the_test_process stops working"
    )
    alias = re.search(r"^\w+\s*=\s*resource\.setrlimit\b", src, re.M)
    assert alias is None, (
        f"{module.__name__} binds setrlimit to a module-level alias "
        f"({alias.group(0).strip() if alias else ''!r}), which captures the "
        "real function at import time and bypasses the test-process shield"
    )
