"""Auto-detect a public HTTPS URL for the Mini App server via Tailscale.

Mirrors the ``shutil.which`` + ``subprocess.run(capture_output=True,
text=True, timeout=...)`` probe pattern already used for the dtach
dependency check in :mod:`aipager.doctor`. This only proves the *node*
is reachable on the tailnet — it does NOT verify Funnel is turned on
for the configured port (that would need a second `tailscale serve
status --json` call, additional surface stage 1 doesn't need). If
Funnel isn't on, the button just won't load on the phone — a same-
session, discoverable failure, not a security hole (see design.md).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

# Kept short — this runs synchronously inside a Telegram command handler
# (`/app`) and `aipager miniapp status`; a hung `tailscale` binary must
# not hang either of those for long.
_TAILSCALE_TIMEOUT_SECONDS = 3


def detect_public_url() -> str | None:
    """Return ``https://<tailnet-dns-name>/`` if Tailscale is installed,
    logged in, and reports a DNS name for this node — else ``None``.

    Never raises: binary absent, a non-zero exit, a timeout, malformed
    JSON, or a missing ``Self.DNSName`` field are all treated the same
    as "no URL available" — callers turn that into an actionable chat
    message, never a stack trace.
    """
    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True, text=True, timeout=_TAILSCALE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    dns_name = (payload.get("Self") or {}).get("DNSName")
    if not dns_name or not isinstance(dns_name, str):
        return None
    # Tailscale reports the DNS name as a trailing-dot FQDN.
    return f"https://{dns_name.rstrip('.')}/"


async def resolve_public_url() -> str:
    """The Mini App's public HTTPS URL, or ``""`` if there isn't one.

    The single answer to "where does the Mini App live", shared by
    ``/app``, the chat menu button and the keyboard button — three
    surfaces that must never disagree about the URL they hand out.

    A configured URL wins; otherwise Tailscale is probed.
    :func:`detect_public_url` shells out to ``tailscale status --json``
    **synchronously**, and every caller here is on the daemon's single
    shared event loop, so the probe runs in an executor. A hung
    ``tailscale`` binary blocking that loop would stall every scope's
    message handling, hook processing and animation ticks at once —
    this is not a theoretical concern, it was a real stage-1 bug.

    Anything that is not an ``https://`` URL is reported as *no URL*:
    Telegram rejects a non-HTTPS Web App outright, so a plain-http value
    in config is a misconfiguration to surface, never something to pass
    on.
    """
    from aipager.config import MINIAPP_PUBLIC_URL

    if MINIAPP_PUBLIC_URL:
        url = MINIAPP_PUBLIC_URL
    else:
        url = await asyncio.get_running_loop().run_in_executor(
            None, detect_public_url,
        )
    if not url or not url.startswith("https://"):
        return ""
    return url


__all__ = ["detect_public_url", "resolve_public_url"]
