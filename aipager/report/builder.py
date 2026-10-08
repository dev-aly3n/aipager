"""Build a report from typed sources, and render what the user previews.

Every field is computed from a typed source into one of the schema's
shapes (:mod:`aipager.report.schema`): an enum, a version, a count, a
yes/no, a code location. Nothing is copied from free text: not an
exception's message, not a log line, not a path, not a label, not a chat
or user id. :func:`build_report` then runs the result through
:func:`schema.validate`, so even a bug here can only drop a field or mark
it ``"<invalid>"``, never add one.

The daemon-only facts (config shape, sessions, doctor statuses, flood
state) come in through :class:`ReportContext`, built by the caller from
live objects; everything else is read here from the machine, without a
subprocess and without the network.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from aipager.report import fingerprint as fp
from aipager.report import schema as sc

_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64",
         "arm64": "arm64", "armv7l": "armv7l", "i686": "i686", "i386": "i686"}
_SYSTEM = {"linux": "linux", "darwin": "darwin", "windows": "windows",
           "freebsd": "freebsd"}
_BUILTIN_ROLES = frozenset({"owner", "admin", "user", "read_only"})
_LIVE_CAP = 99

#: Machine files read for the ``os`` facts (module-level so a test can
#: point them at a fake machine).
OS_RELEASE = Path("/etc/os-release")
DOCKERENV = Path("/.dockerenv")
CONTAINERENV = Path("/run/.containerenv")
SYSTEMD_CONTAINER = Path("/run/systemd/container")


@dataclass
class ReportContext:
    """What only the running daemon knows, already reduced to shapes by
    the caller's typed objects (``scopes`` are ``aipager.scope.Scope``,
    ``flood_rows`` are :func:`aipager.status.read_flood_chats` rows,
    ``doctor`` is :func:`aipager.doctor.run_all_keyed`'s pairs)."""
    scopes: list | None = None
    mode: str | None = None
    custom_role_names: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    uptime_seconds: float | None = None
    sessions_live: int | None = None
    sessions_busy: int | None = None
    unclean_exits_7d: int = 0
    last_exit: str = sc.UNKNOWN
    doctor: list = field(default_factory=list)
    flood_rows: list[dict] = field(default_factory=list)
    claude_auth_source: str = sc.UNKNOWN


# ---- machine facts -------------------------------------------------------

def _version_or_none(text, leaf: sc.Leaf = sc.VERSION) -> str | None:
    return text if leaf.check(text) == text else None


def _aipager_facts() -> dict:
    try:
        from aipager import __version__
        version = _version_or_none(__version__) or "0.0.0+unknown"
    except Exception:  # noqa: BLE001 - a broken install still reports
        version = "0.0.0+unknown"
    install, origin, upgradable = sc.UNKNOWN, sc.UNKNOWN, False
    try:
        from aipager.install_source import detect_install_source
        src = detect_install_source()
        install, origin, upgradable = src.kind, src.origin, bool(src.upgradable)
    except Exception:  # noqa: BLE001 - unknown is a fine answer
        pass
    return {"version": version, "install": install, "origin": origin,
            "upgradable": upgradable}


def _python_facts() -> dict:
    v = sys.version_info
    impl = platform.python_implementation()
    return {"version": f"{v.major}.{v.minor}.{v.micro}",
            "impl": impl if impl in ("CPython", "PyPy") else "other"}


def _os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = OS_RELEASE.read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("ID", "VERSION_ID"):
            out[key] = value.strip().strip('"').strip("'")
    return out


def _kernel_major_minor() -> str | None:
    # Major.minor only: the full release string can name the cloud vendor.
    m = re.match(r"^(\d{1,4})\.(\d{1,4})", platform.release())
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _container() -> str:
    if DOCKERENV.exists():
        return "docker"
    if CONTAINERENV.exists():
        return "podman"
    try:
        kind = SYSTEMD_CONTAINER.read_text().strip().lower()
    except OSError:
        kind = ""
    if kind in ("docker", "podman", "lxc"):
        return kind
    if kind.startswith("lxc"):
        return "lxc"
    if "microsoft" in platform.release().lower():
        return "wsl"
    return "other" if kind else "none"


def _service() -> str:
    """Whether ``aipager service install`` put this platform's unit in
    place (``service.unit_path()`` is the plist on macOS, so the platform
    picks the name, not which path exists)."""
    try:
        from aipager import service
        plat = service._platform()
        if plat == "linux" and service.LINUX_UNIT_PATH.exists():
            return "systemd-user"
        if plat == "macos" and service.MACOS_PLIST_PATH.exists():
            return "launchd"
    except Exception:  # noqa: BLE001 - none is a fine answer
        pass
    return "none"


def _os_facts() -> dict:
    system = _SYSTEM.get(platform.system().lower(), "other")
    rel = _os_release() if system == "linux" else {}
    if system == "darwin":
        distro = "macos"
        distro_version = _version_or_none(platform.mac_ver()[0], sc.DISTRO_VERSION)
    else:
        distro = rel.get("ID", "").lower()
        distro_version = _version_or_none(rel.get("VERSION_ID", ""), sc.DISTRO_VERSION)
    if distro not in sc.DISTROS:
        distro = "other"
    return {"system": system, "arch": _ARCH.get(platform.machine().lower(), "other"),
            "distro": distro, "distro_version": distro_version,
            "kernel": _kernel_major_minor(), "container": _container(),
            "service": _service()}


def _deps() -> dict:
    from importlib import metadata
    out = {}
    for name in sc.DEPENDENCIES:
        try:
            out[name] = _version_or_none(metadata.version(name))
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _claude_facts(auth_source: str) -> dict:
    """From the process's memo only: no subprocess, no network."""
    version, installs = None, 0
    try:
        from aipager import claude_resolve
        memo = claude_resolve._memo
        if memo is not None:
            version = _version_or_none(getattr(memo.chosen, "version", None))
            installs = 1 + len(memo.others)
    except Exception:  # noqa: BLE001 - unknown is a fine answer
        pass
    return {"version": version, "installs": installs, "auth": auth_source}


# ---- daemon facts (from the context) --------------------------------------

def _uptime_bucket(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return sc.UNKNOWN
    for limit, name in ((600, "<10m"), (3600, "10m-1h"), (86400, "1-24h"),
                        (7 * 86400, "1-7d")):
        if seconds < limit:
            return name
    return ">7d"


def _cap(n) -> int:
    return min(max(n, 0), _LIVE_CAP) if type(n) is int else 0


def _config_facts(ctx: ReportContext) -> dict:
    kinds = [getattr(s, "kind", "") for s in (ctx.scopes or [])]
    custom = [r for r in ctx.custom_role_names if isinstance(r, str) and r not in _BUILTIN_ROLES]
    return {
        "mode": ctx.mode if ctx.mode in ("personal", "team", "scope") else sc.UNKNOWN,
        "scopes_dm": _cap(kinds.count("dm")),
        "scopes_group": _cap(kinds.count("group")),
        "custom_roles": _cap(len(custom)),  # how many, never their names
        "features": [f for f in ctx.features if f in sc.FEATURES],
    }


def _runtime_facts(ctx: ReportContext) -> dict:
    return {
        "uptime": _uptime_bucket(ctx.uptime_seconds),
        "sessions_live": _cap(ctx.sessions_live or 0),
        "sessions_busy": _cap(ctx.sessions_busy or 0),
        "unclean_exits_7d": _cap(ctx.unclean_exits_7d),
        "last_exit": ctx.last_exit if ctx.last_exit in ("clean", "crash", "reboot")
        else sc.UNKNOWN,
    }


def _doctor_facts(ctx: ReportContext) -> dict:
    out = {}
    for pair in ctx.doctor:
        try:
            key, result = pair
            status = getattr(result, "status", result)
        except (TypeError, ValueError):
            continue
        if key in sc.DOCTOR_KEYS and isinstance(status, str):
            low = status.lower()
            if low in ("ok", "warn", "fail"):
                out[key] = low
    return out


def _load_bucket(used, budget) -> str:
    if not isinstance(used, int | float) or not isinstance(budget, int | float) or budget <= 0:
        return sc.UNKNOWN
    share = used / budget
    for limit, name in ((0.25, "<25%"), (0.5, "25-50%"), (0.75, "50-75%"), (1.0, "75-100%")):
        if share < limit:
            return name
    return ">100%"


def _flood_facts(ctx: ReportContext) -> dict:
    """Aggregated over chats: the chat ids are dropped, never reported."""
    rows = [r for r in ctx.flood_rows if isinstance(r, dict)]
    bans = sum(r["bans_7d"] for r in rows if type(r.get("bans_7d")) is int)
    loads = [_load_bucket(r.get("hourly_used"), r.get("hourly_budget")) for r in rows]
    order = ["<25%", "25-50%", "50-75%", "75-100%", ">100%"]
    known = [b for b in loads if b in order]
    return {
        "bans_7d": _cap(bans),
        "muted_now": any(bool(r.get("muted_until")) for r in rows),
        "minimal_now": any(r.get("minimal") is True for r in rows),
        "backoff_now": any(isinstance(r.get("warning_remaining"), int | float)
                           and r["warning_remaining"] > 0 for r in rows),
        "hour_load": max(known, key=order.index) if known else sc.UNKNOWN,
    }


# ---- errors ---------------------------------------------------------------

def error_entry(exc: BaseException, *, where: str, trigger: str, tier: str = "bug",
                logger: str | None = None, count: int = 1, first_day: str | None = None,
                last_day: str | None = None, versions_seen: list[str] | None = None,
                event: str | None = None, tool: str | None = None,
                tg_class: str | None = None) -> dict:
    """One ``errors[]`` entry for *exc*: :func:`fingerprint.describe_exception`
    plus the capture point's own typed facts. Never the message."""
    today = _today()
    facts = fp.describe_exception(exc)
    return {
        "fingerprint": facts["fingerprint"], "tier": tier, "where": where,
        "trigger": trigger, "logger": logger, "type": facts["type"],
        "cause_types": facts["cause_types"], "errno": facts["errno"],
        "tg_class": tg_class, "event": event,
        "tool": _tool_name(tool),
        "count": count, "first_day": first_day or today, "last_day": last_day or today,
        "versions_seen": versions_seen or [], "frames": facts["frames"],
        "external": facts["external"],
    }


def _tool_name(tool) -> str | None:
    """A built-in tool by name; an MCP tool as ``mcp`` (its server and tool
    names are the user's); anything else ``other``."""
    if tool is None or tool in sc.TOOLS:
        return tool
    if isinstance(tool, str) and tool.startswith("mcp__"):
        return "mcp"
    return "other"


def _digest_rank(row) -> int:
    n = row.get("n") if isinstance(row, dict) else None
    return -n if type(n) is int else 0


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _clean_note(note: str | None) -> str | None:
    """What the user typed, less what the preview would not show
    (:func:`schema.normalize_note`), cut to the cap."""
    return sc.normalize_note(note)


def build_report(trigger: str, *, errors: list[dict] | None = None,
                 counters: dict | None = None, log_digest: list[dict] | None = None,
                 note: str | None = None, context: ReportContext | None = None) -> dict:
    """The report, validated: only :data:`schema.SCHEMA`'s fields, each
    in its shape. ``errors`` are :func:`error_entry` dicts (or a store's
    records of the same shape), ``counters`` the fixed counter keys,
    ``log_digest`` ``{"site", "level", "n"}`` rows."""
    ctx = context or ReportContext()
    report = {
        "schema": sc.SCHEMA_ID,
        "trigger": trigger,
        "day": _today(),
        "aipager": _aipager_facts(),
        "python": _python_facts(),
        "os": _os_facts(),
        "deps": _deps(),
        "claude_code": _claude_facts(ctx.claude_auth_source),
        "config": _config_facts(ctx),
        "runtime": _runtime_facts(ctx),
        "doctor": _doctor_facts(ctx),
        "flood": _flood_facts(ctx),
        "counters_24h": {k: v for k, v in (counters or {}).items() if k in sc.COUNTER_KEYS},
        "log_digest_24h": sorted(log_digest or [], key=_digest_rank)[:15],
        "errors": list(errors or [])[:10],
    }
    cleaned_note = _clean_note(note)
    if cleaned_note is not None:
        report["note"] = cleaned_note
    clean, _problems = sc.validate(report)
    return clean


def render_preview(report: dict) -> str:
    """The exact text the user sees before Send, and the exact bytes that
    are sent: one renderer for both."""
    return json.dumps(report, indent=2, ensure_ascii=False)
