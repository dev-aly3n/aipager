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
]


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
        monkeypatch.delenv("AIPAGER_SOCKET_PATH", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
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
    for key in ("AIPAGER_SOCKET_PATH", "XDG_RUNTIME_DIR"):
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
