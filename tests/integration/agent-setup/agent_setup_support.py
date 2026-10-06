"""Black-box helpers for ``aipager setup`` / ``setup detect-chat`` /
``doctor --json``.

Everything here is derived from entrypoints.md's "Seams to mock":

* Telegram HTTP is faked at the lowest seam,
  ``aipager.wizard.telegram_api.urllib.request.urlopen`` (getMe,
  sendMessage, getUpdates), and ``aipager.doctor._http_json`` for doctor.
* Deps resolve through ``shutil.which`` / ``claude_resolve`` to fake
  executables under ``tmp_path/input/bin``.
* Daemon detection, live reload and the service installer are spies.
* The no-prompt proof restores the REAL ``errors.require_interactive``
  (counting calls) and booby-traps questionary, ``input`` and getpass.
* Any socket connect and any subprocess is refused, so nothing here can
  reach the network, a daemon socket or a real program.

The token used is realistic so the token-shape scrubbers apply; it is
never printed in an assertion message (see ``assert_no_token``).
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import pytest

TOKEN = "987654321:AAFq1w2e3r4t5y6u7i8o9p0ZxCvBnM-_Kj"
SECRET = TOKEN.split(":", 1)[1]
OTHER_TOKEN = "123123123:BBGz9y8x7w6v5u4t3s2r1q0PoIuYtR_-Hg"
OTHER_SECRET = OTHER_TOKEN.split(":", 1)[1]
BOT = "example_bot"
CHAT = 424242424

SETUP_KEYS = (
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "dry_run", "bot_username", "chat_id", "role", "changed",
    "test_message", "deps", "settings_json", "daemon", "service",
    "warnings", "next_step",
)
DETECT_KEYS = (
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "bot_username", "source", "candidate", "other_candidates", "warnings",
    "next_step",
)
CHANGED_ORDER = ("migrated_v1", "bot_token", "owner_dm", "role",
                 "settings_json", "service")
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "Notification", "Stop", "StopFailure", "SubagentStart",
    "SubagentStop", "PreCompact", "PostCompact", "MessageDisplay",
    "PreModelSwitch",
)


def assert_no_secret(text: str, where: str, *, token: str = TOKEN) -> None:
    """Fail without ever echoing the token."""
    secret = token.split(":", 1)[1]
    if token in text or secret in text:
        pytest.fail(f"bot token leaked into {where}", pytrace=False)


# ── fake Telegram ─────────────────────────────────────────────────────

@dataclass
class Call:
    method: str
    url: str = field(repr=False)
    form: dict = field(repr=False)

    def __repr__(self):  # never show the token-bearing URL
        return f"Call({self.method!r}, query={self.query!r})"

    @property
    def query(self) -> str:
        return urllib.parse.urlsplit(self.url).query


def HTTP(code: int, description: str):
    return ("http", code, description)


def NET(msg: str = "Name or service not known"):
    return ("net", msg)


def ok(result):
    return {"ok": True, "result": result}


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self.code = status
        self.headers = {}

    def read(self, *a):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTelegram:
    def __init__(self):
        self.calls: list[Call] = []
        self.bots = {TOKEN: BOT, OTHER_TOKEN: "other_bot"}
        self.responses: dict = {
            "sendMessage": ok({"message_id": 1}),
            "getUpdates": ok([]),
        }

    def set(self, method, spec):
        self.responses[method] = spec

    def calls_to(self, method):
        return [c for c in self.calls if c.method == method]

    @property
    def sent(self):
        return self.calls_to("sendMessage")

    def __call__(self, req, data=None, timeout=None, *a, **kw):
        url = req if isinstance(req, str) else req.full_url
        body = data if data is not None else getattr(req, "data", None)
        if isinstance(body, bytes):
            body = body.decode()
        form = {k: v[0] for k, v in
                urllib.parse.parse_qs(body or "").items()}
        path = urllib.parse.urlsplit(url).path
        # /bot<token>/<method>
        tok_part, _, method = path.lstrip("/").partition("/")
        token = tok_part[3:] if tok_part.startswith("bot") else ""
        self.calls.append(Call(method, url, form))
        if method == "getMe" and "getMe" not in self.responses:
            if token in self.bots:
                spec = ok({"id": int(token.split(":")[0]), "is_bot": True,
                           "first_name": "Example",
                           "username": self.bots[token]})
            else:
                spec = HTTP(401, "Unauthorized")
        else:
            spec = self.responses.get(method, ok(True))
        if callable(spec):
            spec = spec(self.calls[-1])
        if isinstance(spec, BaseException):
            raise spec
        if isinstance(spec, tuple) and spec[0] == "http":
            _, code, desc = spec
            fp = io.BytesIO(json.dumps(
                {"ok": False, "error_code": code,
                 "description": desc}).encode())
            raise urllib.error.HTTPError(url, code, desc, {}, fp)
        if isinstance(spec, tuple) and spec[0] == "net":
            raise urllib.error.URLError(spec[1])
        return _Resp(json.dumps(spec).encode())


# ── stdin ─────────────────────────────────────────────────────────────

class FakeStdin(io.StringIO):
    def __init__(self, text: str = "", tty: bool = False, env=None):
        super().__init__(text)
        self._tty = tty
        self._env = env

    def isatty(self):
        return self._tty

    def read(self, *a):
        if self._tty and self._env is not None:
            self._env.traps.append("stdin read on a tty")
        return super().read(*a)

    def readline(self, *a):
        if self._tty and self._env is not None:
            self._env.traps.append("stdin readline on a tty")
        return super().readline(*a)


class RedactedDict(dict):
    """aipager.yaml as a dict whose repr hides bot_token, so a failing
    assertion can never print the token."""

    def __repr__(self):
        shown = {k: ("<bot_token>" if k == "bot_token" else v)
                 for k, v in self.items()}
        return repr(shown)

    __str__ = __repr__


# ── run result ────────────────────────────────────────────────────────

@dataclass
class Result:
    code: object
    out: str
    err: str
    argv: list
    log: str = ""

    @property
    def json(self) -> dict:
        return json.loads(self.out)


@dataclass
class Env:
    tmp: Path
    home: Path
    input_dir: Path
    bin_dir: Path
    tg: FakeTelegram
    monkeypatch: object
    capsys: object
    caplog: object
    traps: list = field(default_factory=list)
    interactive_calls: list = field(default_factory=list)
    spawned: list = field(default_factory=list)
    reloads: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    installs: list = field(default_factory=list)
    daemon_pid: object = None
    reload_outcome: tuple = ("reloaded", None)
    service_rc: int = 0
    missing: set = field(default_factory=set)

    # paths (read at call time: conftest redirects them per test)
    @property
    def config_path(self) -> Path:
        import aipager.scope as s
        return Path(s.CONFIG_PATH)

    @property
    def settings_path(self) -> Path:
        import aipager.wizard.settings_patch as sp
        return Path(sp.CLAUDE_SETTINGS)

    @property
    def audit_path(self) -> Path:
        import aipager.audit as a
        return Path(a.AUDIT_LOG_PATH)

    @property
    def config_env(self) -> Path:
        import aipager.wizard._constants as c
        return Path(c.CONFIG_ENV)

    @property
    def policy_path(self) -> Path:
        import aipager.policy as p
        return Path(p.POLICY_PATH)

    @property
    def unit_path(self) -> Path:
        import aipager.service as svc
        return Path(svc.LINUX_UNIT_PATH)

    def token_file(self, content: str | bytes = TOKEN + "\n",
                   name: str = "token.txt", mode: int = 0o600) -> Path:
        p = self.input_dir / name
        if isinstance(content, str):
            content = content.encode()
        p.write_bytes(content)
        os.chmod(p, mode)
        return p

    def config(self) -> dict:
        import yaml
        return RedactedDict(yaml.safe_load(self.config_path.read_text()))

    def audit(self) -> list[dict]:
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in
                self.audit_path.read_text().splitlines() if line.strip()]

    def write_config(self, doc: dict) -> None:
        import yaml
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(doc)
        doc.setdefault("schema_version", 3)
        self.config_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        os.chmod(self.config_path, 0o600)

    def snapshot(self) -> dict:
        """Every dir and file under tmp_path except the caller's input
        dir: relpath -> (kind, mode, bytes)."""
        snap = {}
        for root, dirs, files in os.walk(self.tmp):
            rp = Path(root)
            if rp == self.input_dir or self.input_dir in rp.parents:
                dirs[:] = []
                continue
            for d in dirs:
                p = rp / d
                if p == self.input_dir:
                    continue
                snap[str(p.relative_to(self.tmp))] = (
                    "d", stat.S_IMODE(os.lstat(p).st_mode), None)
            for f in files:
                p = rp / f
                st = os.lstat(p)
                data = p.read_bytes() if stat.S_ISREG(st.st_mode) else None
                snap[str(p.relative_to(self.tmp))] = (
                    "f", stat.S_IMODE(st.st_mode), data)
        return snap

    def files_except_input(self):
        for root, dirs, files in os.walk(self.tmp):
            rp = Path(root)
            if rp == self.input_dir or self.input_dir in rp.parents:
                dirs[:] = []
                continue
            for f in files:
                p = rp / f
                if p.is_file():
                    yield p

    def run(self, *args, stdin: str = "", tty: bool = False) -> Result:
        from aipager import cli
        argv = ["aipager", *[str(a) for a in args]]
        self.monkeypatch.setattr(sys, "argv", list(argv))
        self.monkeypatch.setattr(sys, "stdin",
                                 FakeStdin(stdin, tty=tty, env=self))
        self.monkeypatch.setattr(sys, "excepthook", sys.excepthook)
        self.capsys.readouterr()
        self.caplog.clear()
        code: object = "returned-without-SystemExit"
        try:
            cli.main()
        except SystemExit as e:
            code = 0 if e.code is None else e.code
        out, err = self.capsys.readouterr()
        return Result(code, out, err, argv, self.caplog.text)

    def setup(self, *extra, chat=CHAT, token_file=None, json_out=True,
              **kw) -> Result:
        tf = token_file if token_file is not None else self.token_file()
        args = ["setup", "--token-file", tf, "--chat-id", chat, *extra]
        if json_out:
            args.append("--json")
        return self.run(*args, **kw)


