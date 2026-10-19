"""The startup notice about Claude's login, in each chat's language.

The ONLY thing the daemon ever says to Telegram about auth, and it reaches
every configured chat: in a team setup, every member of every group. A
Telegram message is stored on Telegram's servers, forwardable and
screenshottable, so it names only the *kind* of credential found and what
to do about it: never where a credential lives, what it is called on disk,
or which version anything is. The machine detail (``format_provenance``)
is for the local journal and ``aipager doctor``.

What it says comes from :class:`aipager.claude_bootstrap.AuthNotice`,
which holds only environment variable names and a fix id, so nothing
worded here can name the machine. ``tests/test_auth_notice.py`` checks the
rendered text in every language.
"""

from __future__ import annotations

from aipager.claude_bootstrap import _CLOUD_SWITCHES, STORED_LOGIN, AuthNotice
from aipager.i18n import Msg

REJECTED = Msg(
    "login.rejected",
    "⚠️ Claude rejected its credential, which has probably expired or been revoked.",
    note="startup notice to every chat when Claude's login or key stopped working")
MISSING = Msg(
    "login.missing",
    "⚠️ Claude isn't logged in, and I couldn't find a credential anywhere I know to look.",
    note="startup notice when Claude has no login at all")
UNUSED = Msg(
    "login.unused",
    "⚠️ Claude isn't logged in, though I found a credential on this machine "
    "that it isn't picking up.",
    note="startup notice when a credential exists but Claude does not use it")
FOUND = Msg(
    "login.found", "Found: {kinds}",
    note="the kinds of credential found, joined as a list",
    eg={"kinds": "API key (ANTHROPIC_API_KEY), saved Claude login"})
KIND_SETUP_TOKEN = Msg(
    "login.kind.setup_token", "setup token ({name})",
    note="a `claude setup-token` token; {name} is the variable it is in",
    eg={"name": "CLAUDE_CODE_OAUTH_TOKEN"})
KIND_AUTH_TOKEN = Msg(
    "login.kind.auth_token", "auth token ({name})",
    note="a gateway's or provider's bearer token; {name} is the variable",
    eg={"name": "ANTHROPIC_AUTH_TOKEN"})
KIND_API_KEY = Msg(
    "login.kind.api_key", "API key ({name})",
    note="an Anthropic-format API key; {name} is the variable",
    eg={"name": "ANTHROPIC_API_KEY"})
KIND_BEDROCK_TOKEN = Msg(
    "login.kind.bedrock_token", "Bedrock token ({name})",
    note="an Amazon Bedrock API key; {name} is the variable",
    eg={"name": "AWS_BEARER_TOKEN_BEDROCK"})
KIND_CLOUD_SWITCH = Msg(
    "login.kind.cloud_switch", "cloud provider switch ({name})",
    note="a setting that makes Claude Code use Bedrock, Vertex AI and the like; "
         "{name} is the variable",
    eg={"name": "CLAUDE_CODE_USE_VERTEX"})
KIND_STORED_LOGIN = Msg(
    "login.kind.stored_login", "saved Claude login",
    note="the login `claude auth login` saves on the machine")
FIX_LOGIN = Msg(
    "login.fix.login", "Run {command} on the machine running aipager.",
    note="{command} is `claude auth login`",
    eg={"command": "`claude auth login`"})
FIX_GATEWAY = Msg(
    "login.fix.gateway",
    "Check the key for the address in {name} on the machine running aipager.",
    note="{name} is ANTHROPIC_BASE_URL, the gateway or provider Claude Code uses",
    eg={"name": "ANTHROPIC_BASE_URL"})
FIX_VARIABLE = Msg(
    "login.fix.variable",
    "Check or replace {name} on the machine running aipager.",
    note="{name} is the variable (or a list of them) holding the token or key Claude "
         "refused; Claude Code uses it before a saved login, so logging in again does not help",
    eg={"name": "ANTHROPIC_API_KEY"})
FIX_CLOUD = Msg(
    "login.fix.cloud",
    "Check your cloud provider's credentials on the machine running aipager.",
    note="Claude Code uses Bedrock, Vertex AI or another cloud provider")

#: The kinds named for a credential variable (``claude_bootstrap._kinds_in``).
_VAR_KINDS: dict[str, Msg] = {
    "CLAUDE_CODE_OAUTH_TOKEN": KIND_SETUP_TOKEN,
    "ANTHROPIC_AUTH_TOKEN": KIND_AUTH_TOKEN,
    "ANTHROPIC_API_KEY": KIND_API_KEY,
    "AWS_BEARER_TOKEN_BEDROCK": KIND_BEDROCK_TOKEN,
}
_FIXES = {"gateway": FIX_GATEWAY, "cloud": FIX_CLOUD}


def _kind(kind: str, tr) -> str | None:
    """*kind* worded, or ``None`` for one this does not know (left out
    rather than shown raw)."""
    if kind == STORED_LOGIN:
        return tr.text(KIND_STORED_LOGIN)
    msg = _VAR_KINDS.get(kind)
    if msg is None and kind in _CLOUD_SWITCHES:
        msg = KIND_CLOUD_SWITCH
    return tr.text(msg, name=kind) if msg is not None else None


def render(notice: AuthNotice, tr) -> str:
    """*notice* as plain text in *tr*'s language: what is wrong, what was
    found (when anything was) and what to do."""
    kinds = [k for k in (_kind(kind, tr) for kind in notice.found) if k]
    if notice.rejected:
        lines = [tr.text(REJECTED)]
    elif kinds:
        lines = [tr.text(UNUSED)]
    else:
        lines = [tr.text(MISSING)]
    if kinds:
        lines.append(tr.text(FOUND, kinds=tr.join(tr.bare(k) for k in kinds)))
    fix = _FIXES.get(notice.fix)
    variables = [var for var in notice.variables if var in _VAR_KINDS]
    if fix is FIX_GATEWAY:
        lines.append(tr.text(fix, name="ANTHROPIC_BASE_URL"))
    elif notice.fix == "variable" and variables:
        lines.append(tr.text(FIX_VARIABLE, name=tr.join(variables)))
    elif fix is not None:
        lines.append(tr.text(fix))
    else:
        lines.append(tr.text(FIX_LOGIN, command="`claude auth login`"))
    return "\n\n".join(lines)
