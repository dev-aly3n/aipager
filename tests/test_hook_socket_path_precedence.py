"""The two hook scripts inline config._default_socket_path()'s precedence.

They must, because importing aipager.config from a hook blows the <5ms
budget (it transitively pulls in yaml, team.py, policy.py and does I/O).
The cost of that deliberate duplication is that nothing structurally
forces the three copies to agree, and the code comment saying "mirror the
change here" is not enforcement.

These tests are that enforcement. They compare each hook's real,
import-time-computed SOCKET_PATH against config's real function over the
same environment, so a future edit to one copy that diverges from the
others fails here instead of silently routing hook datagrams to a path
the daemon never bound -- which surfaces to a user as a session stuck
BUSY forever, with no error anywhere.
"""

import importlib

import pytest

from aipager import config

HOOK_MODULES = [
    "aipager.dtach.notify_hook",
    "aipager.dtach.statusline_notify",
]

# Each case is (env-to-set, human-readable id). Expected values are never
# written down here -- they are computed from config._default_socket_path()
# itself, so this file pins *agreement*, not a snapshot that would have to
# be hand-updated (and could be hand-updated wrongly) alongside a real
# precedence change.
CASES = [
    ({}, "no-env"),
    ({"XDG_RUNTIME_DIR": "/run/user/1000"}, "xdg-plain"),
    ({"XDG_RUNTIME_DIR": "/run/user/1000/"}, "xdg-trailing-slash"),
    ({"XDG_RUNTIME_DIR": "  /run/user/1000  "}, "xdg-padded-spaces"),
    ({"XDG_RUNTIME_DIR": "\t/run/user/1000\n"}, "xdg-padded-tab-newline"),
    ({"XDG_RUNTIME_DIR": "   "}, "xdg-whitespace-only"),
    ({"XDG_RUNTIME_DIR": ""}, "xdg-empty"),
    ({"AIPAGER_SOCKET_PATH": "/x/y.sock"}, "override-only"),
    (
        {"AIPAGER_SOCKET_PATH": "/x/y.sock", "XDG_RUNTIME_DIR": "/run/user/1000"},
        "override-beats-xdg",
    ),
    ({"AIPAGER_SOCKET_PATH": "  /x/y.sock  "}, "override-padded"),
    ({"AIPAGER_SOCKET_PATH": "   ", "XDG_RUNTIME_DIR": "/run/user/1000"}, "override-blank-falls-through"),
    # Isolated instance (aipager.instance): its folder wins over both.
    ({"AIPAGER_INSTANCE_DIR": "/srv/apg/i"}, "instance-only"),
    ({"AIPAGER_INSTANCE_DIR": "  /srv/apg/i  "}, "instance-padded"),
    ({"AIPAGER_INSTANCE_DIR": "/srv/apg/i/"}, "instance-trailing-slash"),
    (
        {"AIPAGER_INSTANCE_DIR": "/srv/apg/i", "AIPAGER_SOCKET_PATH": "/x/y.sock"},
        "instance-beats-override",
    ),
    (
        {"AIPAGER_INSTANCE_DIR": "/srv/apg/i", "XDG_RUNTIME_DIR": "/run/user/1000"},
        "instance-beats-xdg",
    ),
    (
        {"AIPAGER_INSTANCE_DIR": "   ", "XDG_RUNTIME_DIR": "/run/user/1000"},
        "instance-blank-falls-through",
    ),
]

ENV_KEYS = ("AIPAGER_INSTANCE_DIR", "AIPAGER_SOCKET_PATH", "XDG_RUNTIME_DIR")


@pytest.fixture
def reload_hooks(monkeypatch):
    """Reload a hook module under a patched env, then restore it.

    The hooks compute SOCKET_PATH at import time, so the env must be set
    before the reload. Teardown reloads once more -- after monkeypatch has
    undone the env -- so the module is left holding the same value the
    rest of the suite imported it with.
    """
    reloaded: list[str] = []

    def _load(modname: str, env: dict):
        for key in ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        mod = importlib.import_module(modname)
        reloaded.append(modname)
        return importlib.reload(mod)

    yield _load

    monkeypatch.undo()
    for modname in reloaded:
        importlib.reload(importlib.import_module(modname))


@pytest.mark.parametrize("modname", HOOK_MODULES)
@pytest.mark.parametrize("env,case_id", CASES, ids=[c[1] for c in CASES])
def test_hook_socket_path_matches_config(modname, env, case_id, monkeypatch, reload_hooks):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    expected = config._default_socket_path()

    mod = reload_hooks(modname, env)

    assert mod.SOCKET_PATH == expected, (
        f"{modname}'s inlined precedence disagrees with "
        f"config._default_socket_path() for {case_id} ({env!r}): "
        f"hook={mod.SOCKET_PATH!r} config={expected!r}. "
        "The daemon binds config's path; the hook sends to its own. "
        "When they differ, every hook event is silently dropped."
    )


def test_both_hooks_agree_with_each_other(reload_hooks):
    """Belt and braces: the two hooks must also agree with each other."""
    env = {"XDG_RUNTIME_DIR": "  /run/user/1000  "}
    paths = {name: reload_hooks(name, env).SOCKET_PATH for name in HOOK_MODULES}
    assert len(set(paths.values())) == 1, f"hook copies diverged: {paths}"


def test_instance_case_resolves_inside_the_instance(reload_hooks):
    """The agreement test above would pass if all three copies ignored
    the instance folder together; pin the actual value once."""
    env = {"AIPAGER_INSTANCE_DIR": " /srv/apg/i/ ", "AIPAGER_SOCKET_PATH": "/x/y.sock"}
    for name in HOOK_MODULES:
        assert reload_hooks(name, env).SOCKET_PATH == "/srv/apg/i/aipager.sock"


# ---------------------------------------------------------------------------
# The status-line file: one writer (aipager-statusline), two readers
# (aipager-hook and aipager.statusline_file). All three must agree on the
# folder, or token counts silently stop reaching the card.
# ---------------------------------------------------------------------------

@pytest.fixture
def status_env(monkeypatch, tmp_path, reload_hooks):
    """Reload statusline_file and both hooks under an env; restore after."""
    from aipager import statusline_file

    def _load(env: dict):
        mods = {name: reload_hooks(name, env) for name in HOOK_MODULES}
        importlib.reload(statusline_file)
        return mods

    yield _load
    monkeypatch.undo()
    importlib.reload(statusline_file)


@pytest.mark.parametrize("use_instance", [True, False], ids=["instance", "unset"])
def test_status_file_writer_and_readers_agree(use_instance, status_env, tmp_path,
                                              monkeypatch):
    import io
    import os

    from aipager import statusline_file

    # A path nobody listens on, so the writer's datagram reaches no daemon.
    env = {"AIPAGER_SOCKET_PATH": str(tmp_path / "sink.sock")}
    if use_instance:
        env["AIPAGER_INSTANCE_DIR"] = str(tmp_path)
    mods = status_env(env)
    if not use_instance:
        # Unset: the folder is the real /tmp; check the rule without
        # writing there.
        assert statusline_file.STATUS_DIR == "/tmp"
        for mod in mods.values():
            assert mod._STATUS_DIR == "/tmp"
        return

    session = f"claude-statusagree{os.getpid()}"
    expected = statusline_file.status_file_path(session)
    assert expected.parent == tmp_path
    stray = f"/tmp/claude-status-{session}.json"
    try:
        writer = mods["aipager.dtach.statusline_notify"]
        payload = (
            '{"context_window": {"used_percentage": 42, "total_output_tokens": 7},'
            ' "cost": {"total_lines_added": 3}}'
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.setattr("sys.stdout", io.StringIO())
        writer._run(session)
        assert expected.exists(), "aipager-statusline wrote somewhere else"

        reader = mods["aipager.dtach.notify_hook"]
        tokens = reader._read_statusline_tokens(session)
        assert tokens is not None, "aipager-hook read somewhere else"
        assert tokens["context_pct"] == 42
        assert statusline_file.read_raw(session)["context_window"]["used_percentage"] == 42
    finally:
        # Only a regressed writer could have put a file here.
        if os.path.exists(stray):
            os.unlink(stray)
