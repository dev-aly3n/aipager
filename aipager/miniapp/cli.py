"""``aipager miniapp enable|disable|status`` — CLI toggle for the
self-hosted Mini App server (see design.md).

Only ever edits ``~/.config/aipager/config.env`` (read-modify-write on
the same ``KEY=VALUE`` lines format ``config._load_env_file`` already
parses); it never starts or stops a listener itself. A running daemon
picks up the change only after a restart, exactly like every other
``config.env``-backed setting (``CLAUDE_TG_BOT_TOKEN``, ``OBSERVER_BOTS``).
"""

from __future__ import annotations

import argparse
import os

from aipager.errors import friendly_error
from aipager.ui import console

_DEFAULT_PORT = 8765
_FALSY = ("0", "false", "no", "")


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
    keys, leave every other line (including comments and unrelated
    keys like ``CLAUDE_TG_BOT_TOKEN``) untouched."""
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


def _current_miniapp_config() -> dict[str, str]:
    """Snapshot of the three keys read straight from ``config.env`` (not
    from ``aipager.config``'s import-time cache, which reflects
    whatever was on disk when *this process* started — stale by
    definition for a CLI command whose whole job is to change that
    file). ``status`` deliberately doesn't require the daemon to be
    running, so this must work from a cold process too."""
    file_values = _parse_env_lines(_read_config_env_lines())
    return {
        "MINIAPP_ENABLED": file_values.get("MINIAPP_ENABLED", "0"),
        "MINIAPP_PORT": file_values.get("MINIAPP_PORT", str(_DEFAULT_PORT)),
        "MINIAPP_PUBLIC_URL": file_values.get("MINIAPP_PUBLIC_URL", ""),
    }


def _cmd_miniapp_enable(args: argparse.Namespace) -> int:
    current = _current_miniapp_config()
    port = getattr(args, "port", None) or int(current["MINIAPP_PORT"])
    url = (getattr(args, "url", None) or "").strip()
    if url and not url.startswith("https://"):
        friendly_error(
            "The Mini App public URL must start with https://.",
            "  Telegram refuses non-HTTPS `web_app` buttons.",
        )
        return 2

    updates = {"MINIAPP_ENABLED": "1", "MINIAPP_PORT": str(port)}
    if url:
        updates["MINIAPP_PUBLIC_URL"] = url
    _write_config_env(updates)

    console.print(f"[ok]✓[/ok]  Mini App enabled — port {port}")
    if url:
        console.print(f"    public URL override: {url}")
    else:
        console.print("    public URL: auto-detect via Tailscale (`tailscale status --json`)")

    from aipager.wizard.daemon_io import _restart_hint
    _restart_hint()
    return 0


def _cmd_miniapp_disable(args: argparse.Namespace) -> int:
    _write_config_env({"MINIAPP_ENABLED": "0"})
    console.print("[ok]✓[/ok]  Mini App disabled")

    from aipager.wizard.daemon_io import _restart_hint
    _restart_hint()
    return 0


def _cmd_miniapp_status(args: argparse.Namespace) -> int:
    from aipager.miniapp.tunnel import detect_public_url

    cfg = _current_miniapp_config()
    enabled = cfg["MINIAPP_ENABLED"] not in _FALSY

    console.print(f"enabled:        {enabled}")
    console.print(f"port:           {cfg['MINIAPP_PORT']}")
    console.print(f"manual url:     {cfg['MINIAPP_PUBLIC_URL'] or '(not set)'}")
    detected = detect_public_url()
    console.print(f"auto-detected:  {detected or '(none — install/enable Tailscale Funnel)'}")
    return 0


__all__ = [
    "_cmd_miniapp_enable",
    "_cmd_miniapp_disable",
    "_cmd_miniapp_status",
]
