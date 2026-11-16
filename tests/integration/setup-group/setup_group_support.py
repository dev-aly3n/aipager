"""Black-box helpers for ``aipager setup group`` / ``setup detect-group``.

Derived only from entrypoints.md:

* The CLI runs in-process through ``aipager.cli.main()``.
* Telegram is faked at the lowest seam, ``urllib.request.urlopen`` as
  ``aipager.wizard.telegram_api`` reaches it, so the real
  ``telegram_api._http_json`` parses every answer (error bodies and
  ``parameters.migrate_to_chat_id`` included) and every Bot API request,
  ``sendMessage`` included, is recorded whatever helper sends it.
* Daemon detection and the live reload are the documented seams
  ``daemon_io._detect_daemon_running`` / ``daemon_io._live_reload``.
* Files are real, under ``tmp_path`` (conftest redirects
  ``scope.CONFIG_PATH``, ``policy.POLICY_PATH``, ``policy.POLICY_D_DIR``,
  ``audit.AUDIT_LOG_PATH`` and ``team.PENDING_USERS_PATH``), and ``HOME``
  points at ``tmp_path/home``.

The token is realistic so the token-shape scrubbers apply; it is never
printed in an assertion message (see ``assert_no_secret``).
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
BOT_ID = 987654321
BOT = "example_bot"

OWNER = 111          # the DM owner of the install
GROUP = -1001234567890
GROUP_LABEL = "group-1001234567890"

GROUP_KEYS = (
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "dry_run", "force", "bot_username", "chat_id", "scope_chat_id",
    "label", "root", "block_edits", "members", "changed",
    "member_changes", "force_reasons", "pinned_roots", "telegram",
    "daemon", "warnings", "next_step",
)
DETECT_KEYS = (
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "bot_username", "source", "candidate", "other_candidates", "warnings",
    "next_step",
)
CHANGED_ORDER = ("group", "label", "root", "deny_tools", "member_added",
                 "member_relabeled", "role", "member_removed")


def assert_no_secret(text: str, where: str) -> None:
    """Fail without ever echoing the token."""
    if TOKEN in text or SECRET in text:
        pytest.fail(f"bot token leaked into {where}", pytrace=False)


# ---- fake Telegram ---------------------------------------------------------

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

    @property
    def params(self) -> dict:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.query).items()}


def HTTP(code: int, description: str, parameters: dict | None = None):
    return ("http", code, description, parameters)


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


def admin_member(can_pin=True):
    return ok({"status": "administrator", "user": {"id": BOT_ID,
               "is_bot": True}, "can_pin_messages": can_pin})


class FakeTelegram:
    """Default answers: the bot exists, the group is a supergroup, the bot
    is an admin that may pin. ``set(method, spec)`` overrides one method;
    a callable spec gets the Call and returns a spec."""

    def __init__(self):
        self.calls: list[Call] = []
        self.responses: dict = {
            "getMe": ok({"id": BOT_ID, "is_bot": True,
                         "first_name": "Example", "username": BOT,
                         "can_read_all_group_messages": False}),
            "getChat": ok({"id": GROUP, "type": "supergroup",
                           "title": "Team"}),
            "getChatMember": admin_member(True),
            "getUpdates": ok([]),
            "sendMessage": ok({"message_id": 1}),
        }

    def set(self, method, spec):
        self.responses[method] = spec

    def calls_to(self, method):
        return [c for c in self.calls if c.method == method]

    @property
    def methods(self):
        return [c.method for c in self.calls]

    def __call__(self, req, data=None, timeout=None, *a, **kw):
        url = req if isinstance(req, str) else req.full_url
        body = data if data is not None else getattr(req, "data", None)
        if isinstance(body, bytes):
            body = body.decode()
        form = {k: v[0] for k, v in
                urllib.parse.parse_qs(body or "").items()}
        path = urllib.parse.urlsplit(url).path
        method = path.rstrip("/").rpartition("/")[2]
        self.calls.append(Call(method, url, form))
        spec = self.responses.get(method, ok(True))
        if callable(spec):
            spec = spec(self.calls[-1])
        if isinstance(spec, BaseException):
            raise spec
        if isinstance(spec, bytes):  # a body that is not JSON
            return _Resp(spec)
        if isinstance(spec, tuple) and spec[0] == "http":
            _, code, desc, params = spec
            doc = {"ok": False, "error_code": code, "description": desc}
            if params:
                doc["parameters"] = params
            fp = io.BytesIO(json.dumps(doc).encode())
            raise urllib.error.HTTPError(url, code, desc, {}, fp)
        if isinstance(spec, tuple) and spec[0] == "net":
            raise urllib.error.URLError(spec[1])
        return _Resp(json.dumps(spec).encode())


# ---- results and env -------------------------------------------------------

class RedactedDict(dict):
    """aipager.yaml as a dict whose repr hides bot_token."""

    def __repr__(self):
        return repr({k: ("<bot_token>" if k == "bot_token" else v)
                     for k, v in self.items()})

    __str__ = __repr__


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

    def warning_codes(self) -> list:
        return [w["code"] for w in self.json["warnings"]]


@dataclass
class Env:
    tmp: Path
    home: Path
    tg: FakeTelegram
    monkeypatch: object
    capsys: object
    caplog: object
    reloads: list = field(default_factory=list)
    detects: list = field(default_factory=list)
    daemon_pid: object = None
    reload_outcome: tuple = ("reloaded", None)

    @property
    def config_path(self) -> Path:
        import aipager.scope as s
        return Path(s.CONFIG_PATH)

    @property
    def policy_path(self) -> Path:
        import aipager.policy as p
        return Path(p.POLICY_PATH)

    @property
    def audit_path(self) -> Path:
        import aipager.audit as a
        return Path(a.AUDIT_LOG_PATH)

    @property
    def config_env(self) -> Path:
        import aipager.wizard._constants as c
        return Path(c.CONFIG_ENV)

    def config(self) -> dict:
        import yaml
        return RedactedDict(yaml.safe_load(self.config_path.read_text()))

    def raw(self) -> bytes:
        return self.config_path.read_bytes()

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

    def install(self, *, scopes=None, extra=None) -> None:
        """An install set up with a DM scope whose owner is OWNER."""
        if scopes is None:
            scopes = [dm_scope()]
        doc = {"bot_token": TOKEN, "scopes": scopes}
        doc.update(extra or {})
        self.write_config(doc)

    def write_policy(self, text: str) -> None:
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(text)

    def group(self, chat_id=GROUP) -> dict | None:
        for s in self.config().get("scopes") or []:
            if s.get("chat_id") == chat_id:
                return s
        return None

    def snapshot(self) -> dict:
        """Every file under tmp_path: relpath -> (mode, bytes)."""
        snap = {}
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                p = Path(root) / f
                st = os.lstat(p)
                if "__pycache__" in p.parts:
                    continue
                data = p.read_bytes() if stat.S_ISREG(st.st_mode) else None
                snap[str(p.relative_to(self.tmp))] = (
                    stat.S_IMODE(st.st_mode), data)
        return snap

    def files(self):
        for root, _dirs, files in os.walk(self.tmp):
            for f in files:
                p = Path(root) / f
                if p.is_file():
                    yield p

    def run(self, *args) -> Result:
        from aipager import cli
        argv = ["aipager", *[str(a) for a in args]]
        self.monkeypatch.setattr(sys, "argv", list(argv))
        self.monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        self.capsys.readouterr()
        self.caplog.clear()
        code: object = "returned-without-SystemExit"
        try:
            cli.main()
        except SystemExit as e:
            code = 0 if e.code is None else e.code
        out, err = self.capsys.readouterr()
        return Result(code, out, err, argv, self.caplog.text)

    def group_cmd(self, *extra, chat=GROUP, json_out=True) -> Result:
        args = ["setup", "group", "--chat-id", chat, *extra]
        if json_out:
            args.append("--json")
        return self.run(*args)


def dm_scope(uid=OWNER, label="owner DM", role="owner", **kw) -> dict:
    s = {"kind": "dm", "chat_id": uid, "label": label,
         "members": [{"id": uid, "label": "owner", "role": role}]}
    s.update(kw)
    return s


def group_scope(members, chat_id=GROUP, label="team", **kw) -> dict:
    s = {"kind": "group", "chat_id": chat_id, "label": label,
         "members": [{"id": i, "label": lab, "role": r}
                     for i, lab, r in members]}
    s.update(kw)
    return s


def m(uid, label, role) -> str:
    return f"--member={uid}:{label}:{role}"
