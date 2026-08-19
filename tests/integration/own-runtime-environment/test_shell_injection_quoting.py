"""design.md success criterion 1:

    "The resolved path is shlex.quote'd at inject.py:567; a fixture
    path containing a shell metacharacter does not execute injected
    content."

Strategy: resolve a fixture "claude" whose own filesystem path is
crafted to look like a shell-injection payload if ever dropped
unquoted into a ``bash -c`` string, drive it all the way through
``launch_session()``'s real command-construction code, capture the
exact ``bash_cmd`` argument that would be handed to ``dtach`` (dtach
itself is never spawned -- see conftest.py's safety notes), then
actually run that captured string through a REAL ``bash -c`` (a
harmless, self-contained subprocess touching only paths under
``tmp_path``) to prove end-to-end that the injected command never
separately executes.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from aipager.dtach.inject import launch_session


class _FakeDtachProc:
    """Stands in for the dtach child `asyncio.create_subprocess_exec`
    would normally return -- returncode != 0 so `launch_session` returns
    immediately after `communicate()`, without polling for a socket that
    (since dtach never really ran) would never appear.
    """

    def __init__(self):
        self.returncode = 1

    async def communicate(self):
        return b"", b"fake dtach: not really run"


@pytest.mark.parametrize("payload_kind", ["semicolon", "backtick", "dollar_paren"])
def test_shell_metacharacters_in_resolved_path_do_not_execute(
    tmp_path, monkeypatch, run_async, payload_kind, claude_fixture_factory,
):
    marker = tmp_path / "PWNED_MARKER"
    invoked_marker = tmp_path / "invoked_marker"

    payloads = {
        "semicolon": f"cl; touch {marker}; echo",
        "backtick": f"cl`touch {marker}`",
        "dollar_paren": f"cl$(touch {marker})",
    }
    weird_dirname = payloads[payload_kind]
    claude_path = claude_fixture_factory(
        name=weird_dirname, invoked_marker=invoked_marker,
    )

    from types import SimpleNamespace
    fake_resolved = SimpleNamespace(chosen=SimpleNamespace(path=claude_path))

    monkeypatch.setattr(
        "aipager.claude_resolve.try_resolve_claude_binary",
        lambda: fake_resolved,
    )
    monkeypatch.setattr(
        "aipager.daemon_secrets.build_session_env", lambda base_env=None: {}
    )

    captured = {}

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeDtachProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    ok, err = run_async(launch_session("injsession"))

    assert ok is False  # the fake dtach process always "fails"
    assert "args" in captured, "launch_session never reached create_subprocess_exec"

    # argv[5] is "bash", argv[6] is "-c", argv[7] is the constructed
    # command string per inject.py's `_DTACH, "-n", sock, "-Ez", "bash",
    # "-c", bash_cmd` call shape.
    dtach_argv = captured["args"]
    assert dtach_argv[4] == "bash"
    assert dtach_argv[5] == "-c"
    bash_cmd = dtach_argv[6]
    assert claude_path in bash_cmd

    assert not marker.exists(), (
        "the injected payload must not have run yet (nothing invoked bash_cmd)"
    )

    # Now actually run the captured command through a real bash -- the
    # true end-to-end proof. Only ever touches paths under tmp_path;
    # the "claude" it invokes is our own harmless fixture script.
    subprocess.run(["bash", "-c", bash_cmd], timeout=10, capture_output=True)

    assert not marker.exists(), (
        f"shell metacharacters in the resolved claude path executed as "
        f"injected commands -- {marker} was created by bash_cmd={bash_cmd!r}"
    )
    assert invoked_marker.exists(), (
        "the fixture claude binary itself was never actually invoked -- "
        "quoting broke legitimate invocation, not just injection"
    )
