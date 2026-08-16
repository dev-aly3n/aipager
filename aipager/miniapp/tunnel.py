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


__all__ = ["detect_public_url"]
