"""Read the daemon's own Claude credential + build the env sessions launch in.

Two sources, read identically:

- ``$CREDENTIALS_DIRECTORY/claude_oauth`` — populated by systemd's
  ``LoadCredential=`` on Linux. Checked first.
- ``~/.config/aipager/daemon.env`` — read directly on macOS/Docker/no-
  systemd, where there is no ``LoadCredential=`` equivalent. This is
  also what ``$CREDENTIALS_DIRECTORY/claude_oauth`` is itself a copy
  of (see :mod:`aipager.service`'s install-time creation).

Both are parsed by :func:`_parse_env_file`, a strict ``KEY=VALUE``
grammar: no ``export``, no shell expansion, no multi-line values, ``#``
comments and blank lines allowed, values kept **verbatim including
literal quote characters**. This is deliberately NOT
``aipager.config._load_env_file``'s grammar, which strips surrounding
quotes — the same ``daemon.env`` file may be handed to ``docker run
--env-file``, which does not strip quotes either, so a stripping parser
here would silently disagree with Docker about identical bytes.

Nothing in this module ever raises. A missing/unreadable file, a
missing ``$CREDENTIALS_DIRECTORY``, or a malformed line each degrade to
"no credential found" rather than blocking a session launch — this is
read by both the real launch path and the startup auth probe, and
neither may ever crash the daemon.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DAEMON_ENV_PATH: Path = Path.home() / ".config" / "aipager" / "daemon.env"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse a strict ``KEY=VALUE`` env file. See module docstring for
    the grammar and why it never strips quotes."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        # `.strip()` trims incidental surrounding whitespace only — it
        # does NOT touch quote characters, unlike config.py's parser.
        result[key] = value.strip()
    return result


def read_daemon_credential() -> dict[str, str]:
    """Best-effort read of the daemon's own credential file.

    Checks ``$CREDENTIALS_DIRECTORY/claude_oauth`` first (set by
    systemd's ``LoadCredential=``), then ``daemon.env`` directly.
    ``{}`` on any failure — never raises.
    """
    # UnicodeDecodeError is NOT an OSError, so catching OSError alone left
    # the "never raises" promise unmet: a credential file that is truncated
    # mid-write, or written by another tool in another encoding, raised out
    # of here — and this runs on the session-launch path, so it would have
    # taken down every launch rather than degrading to "no credential".
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds_dir:
        candidate = Path(creds_dir) / "claude_oauth"
        try:
            return _parse_env_file(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            pass
    try:
        return _parse_env_file(DAEMON_ENV_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def build_session_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """``(base_env or os.environ)`` overlaid with the daemon credential file.

    Used identically for the real session launch (``dtach/inject.py``,
    ``dtach/launcher.py``) and the startup auth probe
    (``claude_resolve.detect_auth`` via ``claude_bootstrap``), so both
    see the same environment. Under ``LoadCredential=`` the daemon's own
    ``os.environ`` never contains the token — probing against it instead
    of this function would falsely report "not logged in" while sessions
    authenticate fine. Never raises.
    """
    env = dict(base_env if base_env is not None else os.environ)
    try:
        env.update(read_daemon_credential())
    except Exception:
        pass
    return env


__all__ = ["DAEMON_ENV_PATH", "build_session_env", "read_daemon_credential"]
