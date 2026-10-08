"""The report's allow-list, and the validator every built report passes.

Every leaf has a SHAPE: an enum value, a version, a non-negative int, a
bool, a UTC day, a code location inside aipager's own files, an exception
type name from an allowed module, a fingerprint. The one free-text field
is ``note``: what the user typed for this report, shown to them verbatim
before anything is sent.

:func:`validate` returns a FRESH dict holding only the schema's keys: an
unknown key is dropped, a leaf of the wrong shape becomes ``"<invalid>"``.
A report can therefore carry nothing the schema does not name, whatever a
builder bug puts into it (the tests also fail on any ``"<invalid>"``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from aipager.report import fingerprint as fp

INVALID = "<invalid>"
NOTE_MAX = 500

#: A PEP 440 shaped version: release, an optional pre/dev/post part, an
#: optional lowercase local part (``0.0.0+unknown``, ``0.7.14.dev3+g1a2b``).
VERSION_RE = re.compile(
    r"^\d{1,4}(\.\d{1,6}){1,3}((a|b|rc|\.?dev|\.?post)\d{1,4})?(\+[0-9a-z.]{1,20})?$")
#: An os-release VERSION_ID: Debian's "12" as well as Ubuntu's "24.04".
DISTRO_VERSION_RE = re.compile(r"^\d{1,4}(\.\d{1,6}){0,3}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOOK_EVENT_RE = re.compile(r"^[A-Za-z]{1,32}$")
LOGGER_RE = re.compile(r"^(aipager(\.[a-z_][a-z0-9_]{0,39}){0,6}|asyncio|telegram\.ext\.Updater)$")
SCHEMA_ID = "aipager-report/1"
#: Seven digits in a row is an id's shape (a chat id, a phone number), never
#: a version's or a name's: refused in every patterned leaf.
LONG_DIGITS_RE = re.compile(r"\d{7}")


def _shaped(value, pattern: re.Pattern) -> bool:
    return (isinstance(value, str) and pattern.match(value) is not None
            and LONG_DIGITS_RE.search(value) is None)


# ---- the note ---------------------------------------------------------------
#
# What the user typed is shown verbatim in the preview, so it may hold no
# text the preview does not show. Unicode's default-ignorable code points
# are invisible by definition; a run of them is how hidden text travels
# (tag characters map one to one onto ASCII, variation selectors onto
# bytes, zero-width characters onto bits). They are all dropped, except
# the few real writing needs, each kept only where it does its job:
#
# - ZWNJ, ZWJ and the soft hyphen (Persian words, emoji sequences): one at
#   a time, after a visible character;
# - LRM, RLM and ALM (mixed-direction text): one at a time, anywhere (a
#   direction mark often opens a line or follows a space);
# - the emoji and text presentation selectors (VS16, VS15): one, after a
#   symbol, a keycap digit or one of the few emoji that are punctuation;
# - tag characters: only as one of the three subdivision flags any
#   platform shows (England, Scotland, Wales). Any other tag run behind a
#   black flag is invisible everywhere, so it is dropped.
#
# Every other space character becomes a plain space. What is lost: the
# glyph variant chosen by an ideographic or Mongolian variation selector
# (the character itself stays). What is left: at most one invisible mark
# per visible character, which real Persian, Indic and emoji text needs,
# and spaces at the end of a line (a fraction of a bit per character);
# nothing maps hidden text one to one onto letters any more.

_DEFAULT_IGNORABLE = frozenset(chr(c) for a, b in (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFFB), (0x13430, 0x1343F), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF)) for c in range(a, b + 1))
_JOINERS = frozenset("\u200c\u200d\u00ad")
_DIRECTION_MARKS = frozenset("\u200e\u200f\u061c")
_NOTE_MARKS = _JOINERS | _DIRECTION_MARKS
_PRESENTATION = frozenset("\ufe0e\ufe0f")
_EMOJI_PUNCTUATION = frozenset("#*\u203c\u2049\u3030\u303d\u2139")
_BLACK_FLAG, _CANCEL_TAG = "\U0001F3F4", "\U000E007F"
_FLAG_SUBDIVISIONS = frozenset({"gbeng", "gbsct", "gbwls"})


def _flag_tags(text: str, i: int) -> str:
    """The tag run of a known subdivision flag starting at *text[i]*, with
    its cancel tag; empty when there is none."""
    j = i
    while j < len(text) and "\U000E0020" <= text[j] <= "\U000E007E":
        j += 1
    if j < len(text) and text[j] == _CANCEL_TAG:
        if "".join(chr(ord(t) - 0xE0000) for t in text[i:j]) in _FLAG_SUBDIVISIONS:
            return text[i:j + 1]
    return ""


def clean_note(text: str) -> str:
    """*text* less everything the preview would not show (see above), and
    less controls (but the newline; a tab becomes a space), lone
    surrogates (unsendable) and line separators."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        i += 1
        prev = out[-1][-1] if out else ""
        if ch == _BLACK_FLAG:
            tags = _flag_tags(text, i)
            out.append(ch + tags)
            i += len(tags)
        elif ch in _JOINERS:
            if prev and not prev.isspace() and prev not in _NOTE_MARKS:
                out.append(ch)
        elif ch in _DIRECTION_MARKS:
            if prev not in _NOTE_MARKS:
                out.append(ch)
        elif ch in _PRESENTATION:
            if prev and (unicodedata.category(prev) in ("So", "Sm", "Nd")
                         or prev in _EMOJI_PUNCTUATION):
                out.append(ch)
        elif ch == "\n":
            out.append(ch)
        elif ch == "\t" or unicodedata.category(ch) == "Zs":
            out.append(" ")
        elif ch in _DEFAULT_IGNORABLE:
            continue
        elif unicodedata.category(ch) not in ("Cc", "Cs", "Zl", "Zp"):
            out.append(ch)
    return "".join(out)


def normalize_note(note) -> str | None:
    """The note as it is shown and sent: cleaned, stripped, cut to
    :data:`NOTE_MAX`, and cleaned again until nothing changes (a cut can
    split a flag, a strip can expose a mark), or None when empty."""
    if not isinstance(note, str):
        return None
    text = note
    while True:
        new = clean_note(text).strip()[:NOTE_MAX]
        if new == text:
            return text or None
        text = new


@dataclass(frozen=True)
class Leaf:
    """One field's shape. ``kind`` names the check; ``values`` is the
    allowed set of an ``enum``; ``pattern`` the regex of a ``slug``-like
    field."""
    kind: str
    values: frozenset = frozenset()
    pattern: re.Pattern | None = None
    nullable: bool = False

    def check(self, value):
        if value is None:
            return None if self.nullable else INVALID
        kind = self.kind
        if kind == "enum":
            return value if isinstance(value, str) and value in self.values else INVALID
        if kind == "int":
            return value if type(value) is int and 0 <= value <= 10**6 else INVALID
        if kind == "bool":
            return value if type(value) is bool else INVALID
        if kind in ("version", "day", "regex"):
            pattern = {"version": VERSION_RE, "day": DAY_RE}.get(kind, self.pattern)
            return value if _shaped(value, pattern) else INVALID
        if kind == "type_name":
            if value == "<other>":
                return value
            if (_shaped(value, fp.TYPE_NAME_RE)
                    and fp._module_allowed(value.rsplit(".", 1)[0])):
                return value
            return INVALID
        if kind == "fingerprint":
            return value if isinstance(value, str) and fp.FINGERPRINT_RE.match(value) else INVALID
        if kind == "code_location":
            if (isinstance(value, dict) and set(value) == {"file", "line", "fn"}
                    and isinstance(value["file"], str)
                    and value["file"] in fp.shipped_files()
                    and type(value["line"]) is int and 0 <= value["line"] <= 10**6
                    and _shaped(value["fn"], fp.FN_RE)):
                return {"file": value["file"], "line": value["line"], "fn": value["fn"]}
            return INVALID
        if kind == "site":  # "aipager/x.py:123"
            if isinstance(value, str) and ":" in value:
                file, _, line = value.rpartition(":")
                if (file in fp.shipped_files() and line.isascii() and line.isdigit()
                        and len(line) <= 6):
                    return value
            return INVALID
        if kind == "note":
            # Non-empty, at most NOTE_MAX, nothing hidden: exactly what
            # normalize_note leaves unchanged.
            return value if isinstance(value, str) and normalize_note(value) == value else INVALID
        return INVALID  # an unknown kind never passes


def enum(*values: str, nullable: bool = False) -> Leaf:
    return Leaf("enum", frozenset(values), nullable=nullable)


INT = Leaf("int")
BOOL = Leaf("bool")
VERSION = Leaf("version")
VERSION_OR_NONE = Leaf("version", nullable=True)
DAY = Leaf("day")
TYPE_NAME = Leaf("type_name")
FINGERPRINT = Leaf("fingerprint")
CODE_LOCATION = Leaf("code_location")
SITE = Leaf("site")
DISTRO_VERSION = Leaf("regex", pattern=DISTRO_VERSION_RE, nullable=True)
NOTE = Leaf("note")

UNKNOWN = "unknown"
DOCTOR_KEYS = ("config_parses", "config", "team", "role_shell_access", "claude",
               "dtach", "hook_scripts", "settings_json", "daemon",
               "service_installed", "service_unit_path", "miniapp")
COUNTER_KEYS = ("watchdog_restart", "watchdog_refresh", "interactive_demoted",
                "stale_busy", "orphan_card", "prompt_not_taken", "keystroke_fallback",
                "allow_always_degraded", "unknown_hook_event", "tg_400_deleted",
                "tg_400_parse_entities", "tg_400_too_long", "tg_400_other",
                "tg_404_rich", "tg_5xx", "tg_network", "tg_429_small",
                "hook_cap_hit", "hook_fail_closed", "hook_error", "dtach_failed")
FEATURES = ("miniapp", "tunnel_managed", "tunnel_override", "observers", "voice",
            "diff_preview_any", "rich_summaries")
DEPENDENCIES = ("python-telegram-bot", "httpx", "aiohttp", "PyYAML", "rich",
                "questionary", "dtach-bin")
DISTROS = ("ubuntu", "debian", "fedora", "arch", "manjaro", "endeavouros", "centos",
           "rhel", "rocky", "almalinux", "ol", "amzn", "opensuse-leap",
           "opensuse-tumbleweed", "sles", "alpine", "nixos", "gentoo", "void",
           "raspbian", "linuxmint", "pop", "elementary", "zorin", "kali", "macos",
           "other")
TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob",
         "Grep", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite",
         "AskUserQuestion", "Skill", "mcp", "other")

#: The report. A dict maps keys to sub-schemas; ``("list", item, max)`` is a
#: list of at most ``max`` items; ``("set", enum_leaf)`` a list of distinct
#: enum values; ``("map", key_leaf, value_leaf)`` a dict over fixed keys.
SCHEMA: dict = {
    "schema": enum(SCHEMA_ID),
    "trigger": enum("auto", "manual"),
    "day": DAY,
    "aipager": {
        "version": VERSION,
        "install": enum("pipx", "uv", "brew", "pip", "editable", "nix", "snap",
                        "docker", "system", "foreign", UNKNOWN),
        "origin": enum("index", "local", "vcs", UNKNOWN),
        "upgradable": BOOL,
    },
    "python": {"version": VERSION, "impl": enum("CPython", "PyPy", "other")},
    "os": {
        "system": enum("linux", "darwin", "windows", "freebsd", "other"),
        "arch": enum("x86_64", "aarch64", "arm64", "armv7l", "i686", "other"),
        "distro": enum(*DISTROS),
        "distro_version": DISTRO_VERSION,
        "kernel": VERSION_OR_NONE,
        "container": enum("none", "docker", "lxc", "wsl", "podman", "other"),
        "service": enum("systemd-user", "launchd", "none"),
    },
    "deps": ("map", enum(*DEPENDENCIES), VERSION_OR_NONE),
    "claude_code": {
        "version": VERSION_OR_NONE,
        "installs": INT,
        "auth": enum("env", "file", "keychain", "unknown", "probe-failed",
                     "version-gated"),
    },
    "config": {
        "mode": enum("personal", "team", "scope", UNKNOWN),
        "scopes_dm": INT,
        "scopes_group": INT,
        "custom_roles": INT,
        "features": ("set", enum(*FEATURES)),
    },
    "runtime": {
        "uptime": enum("<10m", "10m-1h", "1-24h", "1-7d", ">7d", UNKNOWN),
        "sessions_live": INT,
        "sessions_busy": INT,
        "unclean_exits_7d": INT,
        "last_exit": enum("clean", "crash", "signal", "oom", UNKNOWN),
    },
    "doctor": ("map", enum(*DOCTOR_KEYS), enum("ok", "warn", "fail")),
    "flood": {
        "bans_7d": INT,
        "muted_now": BOOL,
        "minimal_now": BOOL,
        "backoff_now": BOOL,
        "hour_load": enum("<25%", "25-50%", "50-75%", "75-100%", ">100%", UNKNOWN),
    },
    "counters_24h": ("map", enum(*COUNTER_KEYS), INT),
    "log_digest_24h": ("list", {"site": SITE, "level": enum("WARNING", "ERROR", "CRITICAL"),
                                "n": INT}, 15),
    "errors": ("list", {
        "fingerprint": FINGERPRINT,
        "tier": enum("bug", "anomaly"),
        "where": enum("daemon", "hook", "statusline", "cli", "miniapp"),
        "trigger": enum("log_exception", "log_error", "task", "ptb_handler", "crash",
                        "hook_error", "hook_cap", "fail_closed", "counter", "manual"),
        "logger": Leaf("regex", pattern=LOGGER_RE, nullable=True),
        "type": TYPE_NAME,
        "cause_types": ("list", TYPE_NAME, fp.MAX_CAUSES),
        "errno": Leaf("regex", pattern=re.compile(r"^E[A-Z0-9]{1,15}$"), nullable=True),
        "tg_class": enum("message_deleted", "not_modified", "parse_entities", "too_long",
                         "chat_not_found", "canceled_by_edit", "other", nullable=True),
        "event": Leaf("regex", pattern=HOOK_EVENT_RE, nullable=True),
        "tool": enum(*TOOLS, nullable=True),
        "count": INT,
        "first_day": DAY,
        "last_day": DAY,
        "versions_seen": ("list", VERSION, 5),
        "frames": ("list", CODE_LOCATION, fp.MAX_FRAMES),
        "external": ("list", enum(*sorted(fp.EXTERNAL_PACKAGES), "<other>"), 8),
    }, 10),
    "note": Leaf("note", nullable=True),
}


def _validate(schema, value, path: str, problems: list[str]):
    if isinstance(schema, Leaf):
        out = schema.check(value)
        if out == INVALID:
            problems.append(path)
        return out
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            problems.append(path)
            return INVALID
        for key in value:
            if key not in schema:  # never named: an unknown key can be data
                problems.append(f"{path} (unknown key dropped)")
        return {key: _validate(sub, value.get(key), f"{path}.{key}", problems)
                for key, sub in schema.items() if key in value}
    kind = schema[0]
    if kind == "list":
        _, item, limit = schema
        if not isinstance(value, list):
            problems.append(path)
            return []
        if len(value) > limit:
            problems.append(f"{path} (over {limit} items, cut)")
        return [_validate(item, v, f"{path}[{i}]", problems)
                for i, v in enumerate(value[:limit])]
    if kind == "set":
        _, item = schema
        if not isinstance(value, list):
            problems.append(path)
            return []
        out = []
        for i, v in enumerate(value):
            checked = _validate(item, v, f"{path}[{i}]", problems)
            if checked not in out:
                out.append(checked)
        return out
    if kind == "map":
        _, key_leaf, value_leaf = schema
        if not isinstance(value, dict):
            problems.append(path)
            return {}
        out = {}
        for key, v in value.items():
            if key_leaf.check(key) == INVALID:
                problems.append(f"{path} (unknown key dropped)")
                continue
            out[key] = _validate(value_leaf, v, f"{path}.{key}", problems)
        return out
    problems.append(path)  # an unknown schema form never passes
    return INVALID


def validate(report: dict) -> tuple[dict, list[str]]:
    """A fresh copy of *report* holding only what :data:`SCHEMA` allows,
    and the paths of everything dropped or replaced by ``"<invalid>"``."""
    problems: list[str] = []
    clean = _validate(SCHEMA, report, "report", problems)
    return (clean if isinstance(clean, dict) else {}), problems
