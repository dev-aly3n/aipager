"""design.md success criterion 10:

    "Across a full discover -> churn -> death -> restart cycle, nothing
    under ~/.config/aipager/ (or its test equivalent) changes -- the
    managed URL never touches disk."

``tests/conftest.py``'s autouse ``_isolate_home_paths`` fixture redirects
every config-writing target this codebase knows about (``aipager.yaml``,
``policy.yaml``, ``config.env``, ``preferences.json``, etc.) to a
throwaway ``tmp_path/home`` *before* this test runs. That means the
active, in-effect values of those path constants -- not their original,
pre-redirect values (that's what the sibling ``real_home_paths`` fixture
exposes, for a different purpose: asserting the production path shape
itself) -- are what a leak would actually land on. This test resolves
each constant's *current* value directly (import the defining module,
read the attribute at test time, after the autouse redirect has already
applied) and snapshots its full directory tree before and after a full
tunnel lifecycle.
"""
from __future__ import annotations

from importlib import import_module
from pathlib import Path


DISCOVERED_URL_1 = "https://disk-check-attempt-one.trycloudflare.com"
DISCOVERED_URL_2 = "https://disk-check-attempt-two.trycloudflare.com"

# The subset of _isolate_home_paths's redirected targets relevant to
# "where would a public_url / miniapp setting ever persist" -- the
# config file/dir the wizard and `aipager miniapp enable --url` write
# through, plus preferences (a plausible dumping ground for "current
# state") and policy (paranoia: it should never be miniapp-adjacent at
# all, which is exactly why it's worth checking).
_WATCHED_DOTTED_PATHS = (
    "aipager.scope.CONFIG_PATH",           # aipager.yaml
    "aipager.config._XDG_CONFIG",          # config.env
    "aipager.wizard._constants.CONFIG_DIR",  # ~/.config/aipager (dir)
    "aipager.policy.POLICY_PATH",          # policy.yaml
    "aipager.preferences._PREFERENCES_PATH",  # preferences.json
)


def _resolve(dotted: str) -> Path:
    module_name, _, attr = dotted.rpartition(".")
    return getattr(import_module(module_name), attr)


def _snapshot() -> dict:
    snap = {}
    for dotted in _WATCHED_DOTTED_PATHS:
        path = _resolve(dotted)
        if path.is_dir():
            snap[dotted] = tuple(sorted(
                (str(p), p.read_bytes())
                for p in path.rglob("*") if p.is_file()
            ))
        elif path.exists():
            snap[dotted] = ("file", path.read_bytes())
        else:
            snap[dotted] = None
    return snap


async def _noop(url):
    pass


def test_full_discover_churn_death_restart_cycle_writes_nothing_to_config(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    before = _snapshot()

    proc1 = FakeProcess()
    proc2 = FakeProcess()
    attempts = {"n": 0}

    async def fake_seam(binary, port):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return proc1, DISCOVERED_URL_1
        return proc2, DISCOVERED_URL_2

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.02)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        try:
            await manager.start()
            await wait_until(lambda: manager.current_url == DISCOVERED_URL_1, timeout=5.0)
            proc1.die(1)  # death
            await wait_until(  # restart + churn
                lambda: manager.current_url == DISCOVERED_URL_2, timeout=5.0,
            )
        finally:
            await manager.stop()

    run_async(scenario())

    after = _snapshot()
    changed = {
        dotted: (before[dotted], after[dotted])
        for dotted in _WATCHED_DOTTED_PATHS
        if before[dotted] != after[dotted]
    }
    assert changed == {}, (
        f"the managed tunnel cycle changed on-disk config state: {list(changed)}"
    )


def test_discovered_url_string_never_appears_in_any_watched_config_file(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    """A stronger, content-based check: even if some unrelated write
    happened to a watched path for an unrelated reason, the managed
    hostname string itself must never appear inside it.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    proc = FakeProcess()

    async def fake_seam(binary, port):
        return proc, DISCOVERED_URL_1

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        try:
            await manager.start()
            await wait_until(lambda: manager.current_url == DISCOVERED_URL_1, timeout=5.0)
        finally:
            await manager.stop()

    run_async(scenario())

    for dotted in _WATCHED_DOTTED_PATHS:
        path = _resolve(dotted)
        files = [path] if path.is_file() else (list(path.rglob("*")) if path.is_dir() else [])
        for f in files:
            if f.is_file():
                content = f.read_bytes()
                assert DISCOVERED_URL_1.encode() not in content, (
                    f"the managed tunnel hostname leaked into {dotted} ({f})"
                )
