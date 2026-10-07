"""Credential redaction for everything the fake-Telegram harness shows.

Every harness output path goes through :func:`redact`: the daemon log
tail, screen dumps, timeout and teardown messages, the fake's "last
calls" tails and the ``AIPAGER_E2E_FAKETG_KEEP_LOG`` copy. It removes
bot tokens (aipager's own ``redact_token`` plus any ``<id>:<secret>``
shape) and Claude OAuth tokens (``sk-ant-...``, the credential a
real-mode run copies into the instance), and the literal value of
``CLAUDE_CODE_OAUTH_TOKEN`` whatever its shape. Values are only
replaced, never printed.
"""

from __future__ import annotations

import os
import re

BOT_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    from aipager.errors import redact_token
    text = redact_token(text)
    text = BOT_TOKEN_RE.sub("<redacted>", text)
    text = ANTHROPIC_TOKEN_RE.sub("sk-ant-<redacted>", text)
    secret = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if len(secret) >= 8:
        text = text.replace(secret, "<redacted>")
    return text
