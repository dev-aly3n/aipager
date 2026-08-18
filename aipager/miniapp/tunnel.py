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
import logging
import shutil
import subprocess

log = logging.getLogger(__name__)

# Kept short — this runs synchronously inside a Telegram command handler
# (`/app`) and `aipager miniapp status`; a hung `tailscale` binary must
# not hang either of those for long.
_TAILSCALE_TIMEOUT_SECONDS = 3

# The managed tunnel's current URL, if any. In-memory only — never
# written to aipager.yaml, config.env, or anywhere under
# ~/.config/aipager/, so a restarting daemon always re-discovers a
# fresh hostname rather than advertising a stale one. The ONLY writer
# is aipager.miniapp.tunnel_manager.TunnelManager; this module just
# holds the answer, keeping "URL resolution" (here) separate from
# "process management" (tunnel_manager.py).
_managed_tunnel_url: str = ""


def set_managed_tunnel_url(url: str) -> None:
    """Set (or clear, with ``""``) the in-memory managed-tunnel URL that
    :func:`resolve_public_url` consults."""
    global _managed_tunnel_url
    _managed_tunnel_url = url


def get_managed_tunnel_url() -> str:
    """Read back whatever :func:`set_managed_tunnel_url` last set —
    ``""`` if never set, or after a clear."""
    return _managed_tunnel_url


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

    Precedence: an explicit ``MINIAPP_PUBLIC_URL`` override wins
    outright; otherwise the managed tunnel's current URL (set by
    :class:`~aipager.miniapp.tunnel_manager.TunnelManager` via
    :func:`set_managed_tunnel_url`) is used if there is one; otherwise
    Tailscale is probed as the opt-in fallback for the startup window
    before the first tunnel URL, and for the (rare) case the tunnel's
    restart ceiling has been reached.  :func:`detect_public_url` shells
    out to ``tailscale status --json`` **synchronously**, and every
    caller here is on the daemon's single shared event loop, so the
    probe runs in an executor. A hung ``tailscale`` binary blocking that
    loop would stall every scope's message handling, hook processing and
    animation ticks at once — this is not a theoretical concern, it was
    a real stage-1 bug.

    Anything that is not an ``https://`` URL is reported as *no URL*:
    Telegram rejects a non-HTTPS Web App outright, so a plain-http value
    in config is a misconfiguration to surface, never something to pass
    on.
    """
    from aipager.config import MINIAPP_PUBLIC_URL

    if MINIAPP_PUBLIC_URL:
        url = MINIAPP_PUBLIC_URL
    elif _managed_tunnel_url:
        url = _managed_tunnel_url
    else:
        url = await asyncio.get_running_loop().run_in_executor(
            None, detect_public_url,
        )
    if not url or not url.startswith("https://"):
        return ""
    return url


__all__ = [
    "detect_public_url", "resolve_public_url",
    "set_managed_tunnel_url", "get_managed_tunnel_url",
]


# Tight on purpose: this runs on the daemon's single shared event loop at
# startup, so a slow or black-holed host must not delay Telegram polling,
# hook processing or the session monitor coming up.
PROBE_TIMEOUT_SECONDS = 3.0

# A freshly created quick tunnel is not immediately answerable: Cloudflare's
# edge returns 530 ("tunnel not ready") for the first several seconds while
# the hostname propagates. Observed live: cloudflared reported its URL, the
# probe fired, got 530, cleared the button — and the same URL served 200 a
# minute later. Because the URL never CHANGED, nothing ever republished, so
# the tunnel worked perfectly and the button never appeared.
#
# So a single probe is the wrong question. Retry across a window that
# comfortably covers edge propagation before concluding a URL is dead.
# Sized from MEASUREMENT, not guesswork. Observed live on this machine:
# cloudflared reported its URL at 20:40:09; the hostname still gave
# ClientConnectorDNSError at 20:40:21 and was still failing when a 20s
# window expired at 20:40:41; the very same URL served 200 shortly after.
# A first guess of 6x4s was therefore too short and produced exactly the
# bug it was meant to fix — a working tunnel with no button. 15x6s covers
# ~90s of edge/DNS propagation with headroom.
PROBE_ATTEMPTS = 15
PROBE_RETRY_DELAY_SECONDS = 6.0


async def _probe_once(url: str) -> bool:
    """Does ``url`` actually answer? Never raises.

    Exists because the daemon used to publish a Mini App button pointing at
    whatever the config said, having never checked it. When an ephemeral
    tunnel died, the hostname stopped resolving and the button spun forever
    on the phone — indistinguishable, from the user's side, from a working
    one. An unverified endpoint must not be advertised.

    Any HTTP response below 500 counts as reachable: a 404 still proves
    something is listening and terminating TLS for us, whereas a dead
    tunnel gives DNS failure, a connection error, or Cloudflare's own
    502/1033 from an origin that is gone.
    """
    if not url:
        return False
    try:
        import aiohttp
    except ImportError:      # miniapp extra not installed — nothing to probe
        return False
    try:
        timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 500:
                    log.warning(
                        "Mini App URL %s answered %d — treating as unreachable",
                        url, resp.status,
                    )
                    return False
                return True
    except Exception as exc:
        # Includes DNS failure (the dead-tunnel case), TLS errors, and the
        # timeout above. Deliberately broad: every one of them means "do
        # not advertise this".
        log.warning("Mini App URL %s is unreachable (%s: %s)",
                    url, type(exc).__name__, exc)
        return False


async def probe_public_url(url: str) -> bool:
    """Does ``url`` answer, allowing for a tunnel that is still coming up?

    Retries ``PROBE_ATTEMPTS`` times, ``PROBE_RETRY_DELAY_SECONDS`` apart,
    returning True on the first success. Never raises.

    The retry is not defensive padding — it is the difference between the
    feature working and silently not working. A brand-new quick tunnel
    answers 530 from Cloudflare's edge for the first several seconds. The
    original single-shot probe fired immediately after cloudflared reported
    its URL, got that 530, and cleared the button; the very same URL served
    200 a minute later, but nothing republished because the URL had not
    changed. Observed live, not hypothetically.
    """
    if not url:
        return False
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        if await _probe_once(url):
            if attempt > 1:
                log.info("Mini App URL %s answered on attempt %d", url, attempt)
            return True
        if attempt < PROBE_ATTEMPTS:
            await asyncio.sleep(PROBE_RETRY_DELAY_SECONDS)
    log.warning("Mini App URL %s did not answer after %d attempts",
                url, PROBE_ATTEMPTS)
    return False
