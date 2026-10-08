"""A report never carries the user's data (roadmap 8.112, design section 7).

A "canary world" plants recognisable fake secrets everywhere a report's
inputs come from: chat and user ids, a bot token, session labels and
names, usernames, a working folder under /home, prompts, a policy pattern,
a custom role name, a tunnel URL, the hostname, an Anthropic key, and the machine sources the
builder reads (the cached Claude Code resolve, the install source, the
os-release file, the service and container markers). Then
exceptions carrying them in every place an exception can (message, args,
notes, the chained cause, an OSError's filename, the frame's locals) go
through the report builder, and neither the report nor its preview may
contain any canary in any encoding.
"""

from __future__ import annotations

import base64
import json
import random
import re
import urllib.parse
from types import SimpleNamespace

import pytest

from aipager import claude_resolve, install_source, service
from aipager.report import builder, fingerprint as fp
from aipager.report import schema as sc

CHAT = 777000123456
GROUP = -1009876543210
TOKEN_SECRET = "AAHcnryQx7Zk2Lm9Pq4Rs8Tv1Wy5Xb3Nd6Fg"
TOKEN = f"1234567890:{TOKEN_SECRET}"
LABEL = "cnry-lbl-QX7"
USERNAME = "cnryuser_QX7"
CWD = "/home/cnryuser/cnryproj"
PROMPT = "CNRY-PROMPT-zebra-42 please fix the invoice for acme"
POLICY = "cnry_pattern_*.secret"
ROLE = "cnry-role-QX7"
TUNNEL = "https://cnry-x.trycloudflare.com"
HOST = "cnry-host"
API_KEY = "sk-ant-cnry-abcdef1234567890"
SESSION = f"claude-cnrylbl__d{CHAT}"

CANARIES = [str(CHAT), str(GROUP), TOKEN, LABEL, USERNAME, CWD, PROMPT, POLICY,
            ROLE, TUNNEL, HOST, API_KEY, SESSION, "cnryuser", "cnryproj", "CNRY-PROMPT"]


def _encodings(value: str) -> list[str]:
    return [value, value.lower(), urllib.parse.quote(value), urllib.parse.quote_plus(value),
            json.dumps(value)[1:-1], base64.b64encode(value.encode()).decode()]


def assert_no_leak(text: str) -> None:
    for canary in CANARIES:
        for form in _encodings(canary):
            assert form not in text, f"canary {canary!r} leaked as {form!r}"
    for i in range(len(TOKEN_SECRET) - 5):
        assert TOKEN_SECRET[i:i + 6] not in text, "a piece of the bot token leaked"
    # A fingerprint is hex and may hold a digit run by chance; it hashes
    # only names (see the guards), so it is set aside for this check.
    unhashed = re.sub(r"ap1-[0-9a-f]{12}", "ap1-", text)
    assert not re.search(r"\d{7,}", unhashed), "a 7+ digit number (an id?) is in the report"
    for marker in ("/home/", "/Users/", "/root/", "@", "://", "sk-ant"):
        assert marker not in text, f"{marker!r} is in the report"


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A fake Linux machine: every file the builder reads is under tmp."""
    root = tmp_path / "machine"
    root.mkdir()
    paths = SimpleNamespace(
        os_release=root / "os-release", dockerenv=root / "dockerenv",
        containerenv=root / "containerenv", systemd_container=root / "container",
        unit=root / "aipager.service", plist=root / "com.aipager.daemon.plist")
    monkeypatch.setattr(builder, "OS_RELEASE", paths.os_release)
    monkeypatch.setattr(builder, "DOCKERENV", paths.dockerenv)
    monkeypatch.setattr(builder, "CONTAINERENV", paths.containerenv)
    monkeypatch.setattr(builder, "SYSTEMD_CONTAINER", paths.systemd_container)
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", paths.unit)
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", paths.plist)
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.release", lambda: "6.8.0-139-generic")
    monkeypatch.setattr(claude_resolve, "_memo", None)
    return paths


@pytest.fixture
def canary_world(monkeypatch, tmp_path, machine):
    monkeypatch.setenv("HOME", str(tmp_path / "cnryuser"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("CLAUDE_TG_CHAT_ID", str(CHAT))
    monkeypatch.setattr("socket.gethostname", lambda: HOST)
    monkeypatch.setattr("platform.node", lambda: HOST)
    # The machine sources, each holding canaries next to the one fact the
    # report may take from it.
    machine.os_release.write_text(
        f'NAME="{HOST}"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="{LABEL} {CWD}"\n'
        f"HOME_URL={TUNNEL}\n")
    machine.unit.write_text(f"Environment=CLAUDE_TG_BOT_TOKEN={TOKEN}\n")
    machine.systemd_container.write_text(f"{USERNAME}\n")
    monkeypatch.setattr(claude_resolve, "_memo", claude_resolve.ResolvedClaude(
        chosen=claude_resolve.ClaudeInstall(path=f"{CWD}/bin/claude",
                                            realpath=f"{CWD}/.local/claude", version="2.1.291"),
        others=(claude_resolve.ClaudeInstall(path=f"{CWD}/old/claude", realpath=TUNNEL,
                                             version=f"9.9.9-{LABEL}"),)))
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda *a, **k: install_source.InstallSource(
                            kind="pipx", prefix=f"{CWD}/venvs/aipager",
                            python=f"{CWD}/venvs/aipager/bin/python", origin="local",
                            origin_detail=CWD, upgradable=True, reason=f"{USERNAME} {TUNNEL}",
                            owner_kind=None))
    scopes = [SimpleNamespace(kind="dm", chat_id=CHAT, label=LABEL, members=[USERNAME]),
              SimpleNamespace(kind="group", chat_id=GROUP, label=LABEL, members=[USERNAME])]
    doctor = [("config", SimpleNamespace(status="OK", title=f"chat {CHAT}",
                                         detail=[f"{CWD} {TOKEN}"], fix=TUNNEL)),
              ("token_valid", SimpleNamespace(status="FAIL", title=USERNAME, detail=[], fix=None))]
    flood = [{"chat_id": CHAT, "bans_7d": 2, "minimal": True, "muted_until": 1.0,
              "hourly_used": 10, "hourly_budget": 100, "label": LABEL}]
    return builder.ReportContext(
        scopes=scopes, mode="scope", custom_role_names=[ROLE, "admin"],
        features=["miniapp", TUNNEL], uptime_seconds=5000, sessions_live=3,
        sessions_busy=1, doctor=doctor, flood_rows=flood, claude_auth_source="file")


def _aipager_raiser(exc_factory, module="aipager.state"):
    """A function that raises from an aipager module (its frame counts as
    aipager's) and holds canaries in its locals."""
    src = ("def raiser(make):\n"
           f"    token = {TOKEN!r}\n"
           f"    prompt = {PROMPT!r}\n"
           f"    cwd = {CWD!r}\n"
           "    raise make()\n")
    namespace = {"__name__": module}
    exec(compile(src, str(fp.PACKAGE_DIR / "state.py"), "exec"), namespace)
    return namespace["raiser"]


def _caught(raiser, make):
    try:
        raiser(make)
    except Exception as exc:  # noqa: BLE001 - the exception is the input
        return exc
    raise AssertionError("did not raise")


def _make_exception(rng: random.Random):
    canary = rng.choice(CANARIES)
    kind = rng.randrange(8)
    if kind == 0:
        exc = ValueError(f"bad {canary} {TOKEN}")
    elif kind == 1:
        exc = KeyError(SESSION)
    elif kind == 2:
        exc = OSError(28, f"No space {canary}", CWD)
    elif kind == 3:
        exc = RuntimeError(canary, CHAT, {"prompt": PROMPT})
    elif kind == 4:
        try:
            raise ValueError(PROMPT)
        except ValueError as inner:
            try:
                raise TypeError(f"wrapped {canary}") from inner
            except TypeError as outer:
                exc = outer
    elif kind == 5:
        from telegram.error import BadRequest
        exc = BadRequest(f"Bad Request: chat {CHAT} not found {canary}")
    elif kind == 6:
        cls = type("CnryError", (Exception,), {"__module__": "cnry_mod_" + LABEL.replace("-", "_")})
        exc = cls(canary)
    else:
        exc = Exception(f"{USERNAME} {TUNNEL}")
    if rng.random() < 0.5 and hasattr(exc, "add_note"):
        exc.add_note(f"note {canary} {HOST}")
    return exc


def test_a_report_from_the_canary_world_holds_no_canary(canary_world):
    exc = _caught(_aipager_raiser(None), lambda: OSError(13, f"denied {CWD}", CWD))
    entry = builder.error_entry(exc, where="daemon", trigger="log_exception",
                                logger="aipager.state")
    report = builder.build_report(
        "auto", errors=[entry], context=canary_world,
        counters={"tg_5xx": 1, f"cnry_{LABEL}": 9},
        log_digest=[{"site": "aipager/state.py:10", "level": "ERROR", "n": 2},
                    {"site": f"{CWD}/x.py:1", "level": "ERROR", "n": 1}])
    preview = builder.render_preview(report)
    assert_no_leak(preview)
    assert_no_leak(json.dumps(report))
    # And the facts are still there, as shapes.
    assert report["config"] == {"mode": "scope", "scopes_dm": 1, "scopes_group": 1,
                                "custom_roles": 1, "features": ["miniapp"]}
    assert report["doctor"] == {"config": "ok"}          # token_valid never
    assert report["flood"]["bans_7d"] == 2 and report["flood"]["muted_now"] is True
    assert report["errors"][0]["type"] == "builtins.PermissionError"
    assert report["errors"][0]["errno"] == "EACCES"
    assert report["errors"][0]["frames"][0]["file"] == "aipager/state.py"
    assert report["claude_code"] == {"version": "2.1.291", "installs": 2, "auth": "file"}
    assert {k: report["aipager"][k] for k in ("install", "origin", "upgradable")} == {
        "install": "pipx", "origin": "local", "upgradable": True}
    assert report["os"] == {"system": "linux", "arch": report["os"]["arch"],
                            "distro": "ubuntu", "distro_version": "24.04", "kernel": "6.8",
                            "container": "other", "service": "systemd-user"}


@pytest.mark.parametrize("seed", range(200))
def test_seeded_exceptions_never_leak(canary_world, seed):
    rng = random.Random(seed)
    exc = _caught(_aipager_raiser(None), lambda: _make_exception(rng))
    entry = builder.error_entry(exc, where=rng.choice(["daemon", "hook", "cli"]),
                                trigger="log_exception", logger="aipager.bot.notify")
    report = builder.build_report("auto", errors=[entry], context=canary_world)
    assert_no_leak(builder.render_preview(report))
    assert sc.INVALID not in json.dumps(report)


def test_the_note_is_the_only_text_the_user_typed_and_appears_verbatim(canary_world):
    report = builder.build_report("manual", context=canary_world,
                                  note="The card froze after I sent a photo.\x00\x1b[2J")
    assert report["note"] == "The card froze after I sent a photo.[2J"
    assert len(builder.build_report("manual", note="x" * 900)["note"]) == sc.NOTE_MAX
    assert "note" not in builder.build_report("manual", note="   ")


def test_the_preview_is_the_payload_byte_for_byte(canary_world):
    report = builder.build_report("manual", context=canary_world)
    preview = builder.render_preview(report)
    assert json.loads(preview) == report
    assert builder.render_preview(json.loads(preview)) == preview


# ---- the machine sources ---------------------------------------------------

def test_a_canary_in_a_machine_fact_becomes_other_or_nothing(machine, monkeypatch):
    machine.os_release.write_text(f"ID=cnryuser\nVERSION_ID=1.2-{CHAT}\n")
    monkeypatch.setattr(claude_resolve, "_memo", claude_resolve.ResolvedClaude(
        chosen=claude_resolve.ClaudeInstall(path=CWD, realpath=CWD, version=CWD)))
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda *a, **k: install_source.InstallSource(
                            kind=CWD, prefix=CWD, python=CWD, origin=TUNNEL))
    monkeypatch.setattr("platform.release", lambda: f"{CHAT}.{CHAT}-cnry")
    report = builder.build_report("manual")
    assert_no_leak(builder.render_preview(report))
    assert (report["os"]["distro"], report["os"]["distro_version"]) == ("other", None)
    assert report["claude_code"]["version"] is None
    assert report["os"]["kernel"] is None


@pytest.mark.parametrize(("distro", "version"), [
    ("debian", "12"), ("fedora", "40"), ("amzn", "2023"), ("ubuntu", "24.04"),
    ("rhel", "9.4")])
def test_a_distro_version_with_one_or_more_numbers_is_kept(machine, distro, version):
    machine.os_release.write_text(f'ID={distro}\nVERSION_ID="{version}"\n')
    report = builder.build_report("manual")
    assert (report["os"]["distro"], report["os"]["distro_version"]) == (distro, version)


def test_no_os_release_is_other_and_no_version(machine):
    report = builder.build_report("manual")
    assert (report["os"]["distro"], report["os"]["distro_version"]) == ("other", None)
    assert report["os"]["container"] == "none" and report["os"]["service"] == "none"


def test_a_canary_in_the_macos_version_is_dropped(machine, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.mac_ver", lambda: (CWD, ("", "", ""), "arm64"))
    report = builder.build_report("manual")
    assert report["os"]["distro_version"] is None
    assert sc.INVALID not in json.dumps(report)


def test_macos_is_named_with_its_own_service(machine, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.mac_ver", lambda: ("14.5", ("", "", ""), "arm64"))
    monkeypatch.setattr(service, "_platform", lambda: "macos")
    machine.plist.write_text("<plist/>")
    machine.unit.write_text("[Unit]\n")  # a stray unit file is not the macOS service
    report = builder.build_report("manual")
    assert report["os"]["system"] == "darwin"
    assert (report["os"]["distro"], report["os"]["distro_version"]) == ("macos", "14.5")
    assert report["os"]["service"] == "launchd"


def test_the_linux_service_is_the_unit_not_a_stray_plist(machine):
    machine.plist.write_text("<plist/>")
    assert builder.build_report("manual")["os"]["service"] == "none"
    machine.unit.write_text("[Unit]\n")
    assert builder.build_report("manual")["os"]["service"] == "systemd-user"


@pytest.mark.parametrize(("marker", "content", "expected"), [
    ("dockerenv", "", "docker"), ("containerenv", "", "podman"),
    ("systemd_container", "lxc-libvirt\n", "lxc"), ("systemd_container", "docker\n", "docker"),
    ("systemd_container", f"{USERNAME}\n", "other")])
def test_the_container_is_named_from_its_marker_only(machine, marker, content, expected):
    getattr(machine, marker).write_text(content)
    assert builder.build_report("manual")["os"]["container"] == expected


def test_live_counts_are_capped(machine):
    ctx = builder.ReportContext(sessions_live=10**9, sessions_busy=-4, unclean_exits_7d=True,
                                scopes=[SimpleNamespace(kind="dm")] * 500,
                                custom_role_names=[["x"], ROLE, "admin"],
                                flood_rows=[{"bans_7d": True}, {"bans_7d": 2}])
    report = builder.build_report("manual", context=ctx)
    assert report["runtime"]["sessions_live"] == 99
    assert report["runtime"]["sessions_busy"] == 0
    assert report["runtime"]["unclean_exits_7d"] == 0     # a bool is no count
    assert report["config"]["scopes_dm"] == 99
    assert report["config"]["custom_roles"] == 1
    assert report["flood"]["bans_7d"] == 2


def test_the_flood_load_is_the_highest_bucket_and_uptime_is_never_negative(machine):
    ctx = builder.ReportContext(uptime_seconds=-5, flood_rows=[
        {"hourly_used": 90, "hourly_budget": 100}, {"hourly_used": 10, "hourly_budget": 100},
        {"hourly_used": 60, "hourly_budget": 100}])
    report = builder.build_report("manual", context=ctx)
    assert report["flood"]["hour_load"] == "75-100%"
    assert report["runtime"]["uptime"] == "unknown"


def test_the_log_digest_keeps_the_15_busiest_sites(machine):
    rows = [{"site": f"aipager/state.py:{i}", "level": "ERROR", "n": i} for i in range(1, 31)]
    random.Random(7).shuffle(rows)
    digest = builder.build_report("manual", log_digest=rows)["log_digest_24h"]
    assert [r["n"] for r in digest] == list(range(30, 15, -1))


def test_the_builder_drops_unbudgeted_doctor_checks_itself():
    """Not only the validator: the builder never carries them either."""
    ctx = builder.ReportContext(doctor=[
        ("token_valid", SimpleNamespace(status="FAIL")), ("chat_reachable", "ok"),
        ("claude_auth", "warn"), ("config", SimpleNamespace(status="WARN"))])
    assert builder._doctor_facts(ctx) == {"config": "warn"}


def test_an_mcp_tool_is_named_mcp_and_never_by_its_server(machine):
    exc = _caught(_aipager_raiser(None), lambda: ValueError("x"))
    for tool, expected in (("Bash", "Bash"), ("mcp__cnryuser__send_invoice", "mcp"),
                           (f"{LABEL}_tool", "other"), (None, None)):
        entry = builder.error_entry(exc, where="hook", trigger="hook_error", tool=tool)
        assert entry["tool"] == expected


#: Every code point the note must drop between two letters, listed by hand
#: (not read from the rule, which would make the test agree with itself).
HIDDEN = [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200B, 0x2060, 0x2061, 0x2064,
          0x206A, 0x206F, 0xFEFF, 0x034F, 0x115F, 0x1160, 0x17B4, 0x180B, 0x180E, 0x3164,
          0xFFA0, 0xFFF0, 0xFFF9, 0xFFFB, 0x13430, 0x1BCA0, 0x1D173, 0xE0000, 0xE0001,
          0xE0041, 0xE007F, 0xE0100, 0xE01EF, 0xFE00, 0x7F, 0x85, 0x1B, 0x2028, 0x2029, 0xD800]


@pytest.mark.parametrize("code", HIDDEN, ids=hex)
def test_the_note_drops_each_hidden_code_point(machine, code):
    assert builder.build_report("manual", note=f"a{chr(code)}b")["note"] == "ab"


def _tagged(text: str) -> str:
    """*text* as tag characters: invisible, one per ASCII character."""
    return "".join(chr(0xE0000 + ord(c)) for c in text)


def _flag(subdivision: str) -> str:
    return "\U0001F3F4" + _tagged(subdivision) + "\U000E007F"


def test_the_note_drops_hidden_text_carried_in_tags_selectors_or_joiners(machine):
    secret = f"chat {CHAT} token {TOKEN_SECRET}"
    vs_bytes = "".join(chr(0xE0100 + b) for b in secret.encode())
    for smuggled, shown in [
            ("The card froze." + _tagged(secret), "The card froze."),
            ("\U0001F3F4" + _tagged(secret) + "\U000E007F x", "\U0001F3F4 x"),
            ("".join(_flag(secret[k:k + 7]) for k in range(0, len(secret), 7)),
             "\U0001F3F4" * len(range(0, len(secret), 7))),
            (_flag("chat777") + _flag("usca") + _flag("gb") + "!", "\U0001F3F4" * 3 + "!"),
            ("\U0001F600" + vs_bytes, "\U0001F600"),
            ("\u2764" + "\ufe0f" * 30, "\u2764\ufe0f"),
            ("a\ufe0fb\ufe0ec", "abc"),
            ("a" + "\u200c\u200d" * 40 + "b", "a\u200cb"),
            ("\u200c\u200fstart", "\u200fstart"),
            ("\u200e\u200f\u200e\u061cx", "\u200ex"),
            ("a\u200c\u200fb", "a\u200cb"),
            ("x \u200dy", "x y")]:
        note = builder.build_report("manual", note=smuggled)["note"]
        assert note == shown, f"{smuggled!r} -> {note!r}"
        assert not any(0xE0000 <= ord(ch) <= 0xE0FFF for ch in note.replace(
            "\U0001F3F4", "")), "a tag character outside a flag survived"


def test_the_note_keeps_what_real_writing_needs(machine):
    """Persian needs ZWNJ (and RLM/ALM between scripts), emoji sequences
    need ZWJ and VS16, keycaps VS16, subdivision flags their tags: all stay
    as typed, and the preview shows them as text, not as escapes."""
    persian = "\u0646\u0645\u06cc\u200c\u062f\u0627\u0646\u0645 \u200fOK\u200f \u061c2"
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    emoji = ("\u2764\ufe0f\u200d\U0001F525 \U0001F44D\U0001F3FD 1\ufe0f\u20e3 #\ufe0f\u20e3 \u00a9\ufe0f"
             " \u203c\ufe0f \u2049\ufe0f \u2139\ufe0f")
    flags = f"{_flag('gbeng')}{_flag('gbsct')}{_flag('gbwls')} \U0001F1EE\U0001F1F7"
    other = "co\u00adop \u4e2d\u6587 \u05e9\u05dc\u05d5\u05dd \u0645\u0631\u062d\u0628\u0627 \u0915\u094d\u0937"
    note = f"{persian} {family} {emoji} {flags} {other}"
    report = builder.build_report("manual", note=note)
    assert report["note"] == note
    assert note in builder.render_preview(report)


def test_a_tab_or_any_space_becomes_a_space_and_a_newline_stays(machine):
    assert builder.build_report("manual", note="col1\tcol2\nrow")["note"] == "col1 col2\nrow"
    spaced = "a\u00a0b\u2003c\u202fd\u3000e"
    assert builder.build_report("manual", note=spaced)["note"] == "a b c d e"


def test_a_flag_cut_at_the_cap_still_validates(machine):
    flag = _flag("gbeng")
    for pad in range(sc.NOTE_MAX - 8, sc.NOTE_MAX + 1):
        note = builder.build_report("manual", note="x" * pad + flag)["note"]
        # The whole flag when it fits, else its visible base (or nothing):
        # never its tags alone, and never the validator's "<invalid>".
        fits = pad + len(flag) <= sc.NOTE_MAX
        expected = "x" * pad + (flag if fits else "\U0001F3F4" if pad < sc.NOTE_MAX else "")
        assert note == expected


@pytest.mark.parametrize("seed", range(200))
def test_a_cleaned_note_is_stable_and_valid(seed):
    rng = random.Random(seed)
    alphabet = ["a", "\u0646", " ", "\n", "\t", "1", "#", "\u2764", "\U0001F600",
                "\U0001F3F4", "\u200c", "\u200d", "\u200f", "\u00ad", "\ufe0f", "\ufe0e",
                "\U000E0067", "\U000E007F", "\U000E0100", "\u202e", "\u200b", "\x1b",
                "\u200e", "\u061c", "\u3000", "\u00a0", "\r", "\u2028",
                _flag("gbeng"), _flag("usca")]
    note = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 700)))
    clean = sc.normalize_note(note)
    assert sc.normalize_note(clean) == clean
    if clean is not None:
        assert sc.NOTE.check(clean) == clean
        assert "\u200c\u200c" not in clean and "\ufe0f\ufe0f" not in clean
        assert "\u200f\u200f" not in clean and "\u200d\u200f" not in clean


def test_an_unshaped_aipager_version_falls_back(machine, monkeypatch):
    import aipager
    monkeypatch.setattr(aipager, "__version__", f"0.7.14.dev3+g1a2b.d{CHAT}")
    assert builder.build_report("manual")["aipager"]["version"] == "0.0.0+unknown"
