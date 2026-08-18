"""``aipager miniapp enable|disable|status`` — CLI toggle for the
self-hosted Mini App server (see design.md).

Only ever edits the ``miniapp:`` block of ``~/.config/aipager/aipager.yaml``;
it never starts or stops a listener itself. A running daemon picks up the
change only after a restart.

Deliberately NOT ``config.env``, which is where these settings lived
originally: ``migrate.retire_v1()`` renames config.env away on every daemon
start once aipager.yaml is authoritative, so a value written there survived
exactly one restart before the Mini App silently turned itself off.
"""

from __future__ import annotations

import argparse

from aipager.errors import friendly_error
from aipager.ui import console

_DEFAULT_PORT = 8765


def _read_config_env_lines() -> list[str]:
    # Imported here, not at module scope: `_isolate_home_paths` patches
    # `aipager.wizard._constants.CONFIG_ENV` for tests, and a by-value
    # top-level import would bake in the original path before the patch
    # ever runs (same reasoning as the fixture's own docstring).
    from aipager.wizard._constants import CONFIG_ENV
    if not CONFIG_ENV.exists():
        return []
    return CONFIG_ENV.read_text().splitlines()


def _parse_env_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _write_config_env(updates: dict[str, str]) -> None:
    """Read-modify-write ``config.env``: update/add only ``updates``'
    keys, leave every other line (including comments and unrelated keys
    like ``CLAUDE_TG_BOT_TOKEN``) untouched.

    Only used on a **v1 install that has no aipager.yaml yet** — see
    ``_save_miniapp_config``. `migrate.upgrade_to_v3()` moves whatever
    lands here into aipager.yaml the first time the daemon starts.
    """
    import os

    from aipager.wizard._constants import CONFIG_DIR, CONFIG_ENV

    lines = _read_config_env_lines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text("\n".join(out) + "\n")
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        pass


def _current_miniapp_config() -> dict:
    """Snapshot read straight from disk — not from ``aipager.config``'s
    import-time cache, which reflects whatever was on disk when *this
    process* started, stale by definition for a CLI command whose whole
    job is to change that file. ``status`` deliberately doesn't require
    the daemon to be running, so this must work from a cold process too.

    aipager.yaml wins when it exists; config.env is only consulted for a
    v1 install that has not been migrated yet.

    Imported inside the function, not at module scope: `_isolate_home_paths`
    patches these path constants for tests, and a by-value top-level
    import would bake in the original path before the patch ever runs.
    """
    from aipager import scope
    if scope.CONFIG_PATH.exists():
        return scope.load_miniapp(scope.CONFIG_PATH)

    env = _parse_env_lines(_read_config_env_lines())
    try:
        port = int(env.get("MINIAPP_PORT", _DEFAULT_PORT))
    except ValueError:
        port = _DEFAULT_PORT
    return {
        "enabled": env.get("MINIAPP_ENABLED", "0") not in ("0", "false", "no", ""),
        "port": port,
        "public_url": env.get("MINIAPP_PUBLIC_URL", ""),
    }


def _save_miniapp_config(settings: dict) -> None:
    """Persist to aipager.yaml, falling back to config.env on a v1 install.

    `dump_miniapp` edits an existing document and will not invent one — a
    file holding only a `miniapp:` block would have no bot_token, and
    `load_scopes` rejects that outright, which would take the daemon down.
    So when there is no aipager.yaml yet (a fresh install where `aipager
    config` has not run), keep writing config.env exactly as before;
    `migrate.upgrade_to_v3()` moves it across on the first daemon start,
    before `retire_v1()` can delete it.
    """
    from aipager import scope
    if scope.CONFIG_PATH.exists():
        scope.dump_miniapp(settings, scope.CONFIG_PATH)
        return

    updates = {
        "MINIAPP_ENABLED": "1" if settings.get("enabled") else "0",
        "MINIAPP_PORT": str(settings.get("port", _DEFAULT_PORT)),
    }
    if settings.get("public_url"):
        updates["MINIAPP_PUBLIC_URL"] = settings["public_url"]
    _write_config_env(updates)


def _cmd_miniapp_enable(args: argparse.Namespace) -> int:
    current = _current_miniapp_config()
    port = getattr(args, "port", None) or int(current["port"])
    url = (getattr(args, "url", None) or "").strip()
    if url and not url.startswith("https://"):
        friendly_error(
            "The Mini App public URL must start with https://.",
            "  Telegram refuses non-HTTPS `web_app` buttons.",
        )
        return 2

    # Omitting --url keeps whatever override is already stored rather than
    # clearing it, so `enable --port N` doesn't silently drop the URL.
    settings = {
        "enabled": True,
        "port": port,
        "public_url": url or current["public_url"],
    }
    try:
        _save_miniapp_config(settings)
    except Exception as e:
        friendly_error(
            f"Could not write the Mini App settings: {e}",
            "  Run `aipager config` first to create ~/.config/aipager/aipager.yaml.",
        )
        return 2

    console.print(f"[ok]✓[/ok]  Mini App enabled — port {port}")
    if settings["public_url"]:
        console.print(f"    public URL override: {settings['public_url']}")
    else:
        console.print(
            "    public URL: managed automatically — aipager starts a "
            "Cloudflare quick tunnel alongside the daemon and publishes "
            "whatever public https://*.trycloudflare.com address it is "
            "assigned"
        )
        console.print(
            "[muted]    That address is not secret and is not meant to "
            "be: every request is verified against Telegram's initData "
            "signature, not by the URL being hard to guess. The hostname "
            "changes on every restart and is never written to config — "
            "if Tailscale is set up, it is used as the fallback while "
            "the tunnel comes up.[/muted]"
        )

    from aipager.wizard.daemon_io import _restart_hint
    _restart_hint()
    return 0


def _cmd_miniapp_disable(args: argparse.Namespace) -> int:
    # Keep port/public_url so re-enabling doesn't lose them.
    current = _current_miniapp_config()
    try:
        _save_miniapp_config({**current, "enabled": False})
    except Exception as e:
        friendly_error(
            f"Could not write the Mini App settings: {e}",
            "  Run `aipager config` first to create ~/.config/aipager/aipager.yaml.",
        )
        return 2
    console.print("[ok]✓[/ok]  Mini App disabled")

    from aipager.wizard.daemon_io import _restart_hint
    _restart_hint()
    return 0


def _cmd_miniapp_status(args: argparse.Namespace) -> int:
    from aipager.miniapp.tunnel import detect_public_url

    cfg = _current_miniapp_config()

    console.print(f"enabled:        {cfg['enabled']}")
    console.print(f"port:           {cfg['port']}")
    console.print(f"manual url:     {cfg['public_url'] or '(not set)'}")
    detected = detect_public_url()
    console.print(
        f"tailscale:      {detected or '(none — install/enable Tailscale Funnel)'}"
    )
    if cfg["public_url"]:
        managed_note = "disabled — a manual url override is set"
    else:
        managed_note = (
            "starts with the daemon (this command runs cold and cannot "
            "show a live URL — check the Telegram button or `aipager "
            "logs`/journalctl once the daemon is running)"
        )
    console.print(f"managed tunnel: {managed_note}")
    return 0


__all__ = [
    "_cmd_miniapp_enable",
    "_cmd_miniapp_disable",
    "_cmd_miniapp_status",
]
