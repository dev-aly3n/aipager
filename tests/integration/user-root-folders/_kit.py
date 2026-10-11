"""Constants and pure helpers for the members' root folder tests, loaded
by path (the folder name is not a Python package name)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

OWNER, ADMIN, SARA, SARA2, BOB, CY, HAL, BOSS = 1, 2, 3, 4, 5, 6, 8, 9
GROUP = -100
TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
SESSION = "claude-proj"
CLAUDE_SID = "5b1d7363-aaaa-bbbb-cccc-0000000000aa"

def init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)

def texts_of(*mocks) -> list[str]:
    """Every string argument any of *mocks* was awaited/called with."""
    out: list[str] = []
    for m in mocks:
        for c in list(getattr(m, "await_args_list", [])) + list(getattr(m, "call_args_list", [])):
            for a in c.args:
                if isinstance(a, str):
                    out.append(a)
            for k in ("text", "message"):
                v = c.kwargs.get(k)
                if isinstance(v, str):
                    out.append(v)
    return out

def short(path) -> str:
    """``path`` with the home folder written ``~``."""
    h = os.path.expanduser("~")
    p = str(path)
    return "~" + p[len(h):] if p == h or p.startswith(h + "/") else p

def outside_root_msg(root) -> str:
    return (f"Your role can only start sessions in your folder {short(root)} "
            "or a folder inside it.")

def session_outside_msg(root) -> str:
    return (f"This session works outside your folder {short(root)}, so your "
            "role can't resume, restart or switch it. Ask an admin.")

def realp(p) -> str:
    return os.path.realpath(str(p))


def load():
    """This module, by path, once."""
    return __import__("sys").modules[__name__]
