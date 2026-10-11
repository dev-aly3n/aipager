"""Route scenarios shared by the English byte-identity test and the
viewer/chat language tests.

Each scenario builds its sessions on a fresh ``env`` (conftest.py), makes one
request as ``actor`` and returns ``{"status", "body", "mirrors"}``, where
``mirrors`` are the texts posted into a chat by that request. Only APIs that
exist on the base branch are used, so the same module records today's English
on a ``git archive`` of the base (``P4_RECORD``) and checks the branch.

Kinds:
- ``detail``: a refusal whose body carries a ``detail`` sentence;
- ``mirror``: a write that posts one line into the group;
- ``payload``: a GET whose body carries server text (schema, facts, reasons);
- ``passthrough``: a detail produced by sibling code, passed on as it is.
"""

from __future__ import annotations

import re

GROUP = -1001
BOB = 2      # admin
DEV = 7      # ordinary member


def _do(env, method, path, actor, **kw):
    before = len(env.posted())
    r = env.call(method, path, user=actor, scope=GROUP, **kw)
    mirrors = [text for _chat, text in env.posted()[before:]]
    return {"status": r.status, "body": r.body, "mirrors": mirrors,
            "mirror_chats": [chat for chat, _t in env.posted()[before:]]}


def _create(body):
    def run(env, actor):
        env.session("taken")
        return _do(env, "POST", "/api/sessions", actor, json=body)
    return run


def _create_in_project(extra):
    def run(env, actor):
        return _do(env, "POST", "/api/sessions", actor,
                   json={"name": "made", "cwd": str(env.project), **extra})
    return run


def _directory(name):
    def run(env, actor):
        return _do(env, "POST", "/api/directories", actor,
                   json={"parent": str(env.project), "name": name})
    return run


def _action(path_tail, *, method="POST", status="idle", queued=0, skip_perms=False,
            transcript="", json=None, extra=None):
    def run(env, actor):
        env.session("dev", status=status, queued=queued, skip_perms=skip_perms,
                    claude_session_id=transcript)
        if extra:
            extra(env)
        kw = {"json": json} if json is not None else {}
        return _do(env, method, f"/api/sessions/dev{path_tail}", actor, **kw)
    return run


def _also_taken(env):
    env.session("taken")


def _launch_fails(env):
    from aipager.dtach import inject
    inject.launch_session.return_value = (False, "dtach broken")


def _get(path, *, status="idle", queued=0):
    def run(env, actor):
        env.session("dev", status=status, queued=queued, cwd="/srv/web2",
                    model="claude-opus-5")
        return _do(env, "GET", path, actor)
    return run


def _put_pref(field, value):
    def run(env, actor):
        return _do(env, "PUT", f"/api/preferences/{field}", actor, json={"value": value})
    return run


def _put_sess_pref(field, value, *, reset=False):
    def run(env, actor):
        env.session("dev")
        if reset:
            _do(env, "PUT", f"/api/sessions/dev/preferences/{field}", actor,
                json={"value": value})
            return _do(env, "DELETE", f"/api/sessions/dev/preferences/{field}", actor)
        return _do(env, "PUT", f"/api/sessions/dev/preferences/{field}", actor,
                   json={"value": value})
    return run


# name -> (kind, actor, runner)
SCENARIOS = {
    # POST /api/sessions refusals
    "create_empty_name": ("detail", BOB, _create({"name": ""})),
    "create_long_name": ("detail", BOB, _create({"name": "a" * 65})),
    "create_bad_name": ("detail", BOB, _create({"name": "bad name"})),
    "create_reserved_name": ("detail", BOB, _create({"name": "new"})),
    "create_name_taken": ("detail", BOB, _create({"name": "taken"})),
    "create_outside_folder": ("detail", BOB, _create({"name": "dev", "cwd": "/etc"})),
    "create_auto_needs_admin": ("detail", DEV, _create({"name": "dev", "skip_perms": True})),
    "create_bad_model": ("detail", BOB, _create({"name": "dev", "model": "bad model!"})),
    # POST /api/directories refusals and creation
    "dir_empty_name": ("detail", BOB, _directory("")),
    "dir_bad_name": ("detail", BOB, _directory("a b")),
    "dir_created": ("mirror", BOB, _directory("web")),
    # session routes: refusals
    "stop_not_busy": ("detail", BOB, _action("/stop")),
    "clearqueue_empty": ("detail", BOB, _action("/clearqueue", status="busy")),
    "perms_auto_needs_admin": ("detail", DEV, _action("/perms")),
    "compact_not_running": ("detail", BOB, _action("/compact", status="gone")),
    "model_busy": ("detail", BOB, _action("/model", status="busy", json={"model": "opus"})),
    "model_prompt_open": ("detail", BOB, _action("/model", status="interactive",
                                                 json={"model": "opus"})),
    "model_unknown": ("detail", BOB, _action("/model", status="unknown",
                                             json={"model": "opus"})),
    "model_not_live": ("detail", BOB, _action("/model", status="gone",
                                              json={"model": "opus"})),
    "rename_taken": ("detail", BOB, _action("/rename", json={"label": "taken"},
                                            extra=_also_taken)),
    "rename_bad": ("detail", BOB, _action("/rename", json={"label": "bad name!"})),
    "rename_reserved": ("detail", BOB, _action("/rename", json={"label": "help"})),
    "resume_no_transcript": ("detail", BOB, _action("/resume", status="gone")),
    "resume_running": ("detail", BOB, _action("/resume", status="busy",
                                              transcript="abc")),
    "delete_running": ("detail", BOB, _action("", method="DELETE", status="busy")),
    "model_unconfirmed": ("detail", BOB, _action("/model", json={"model": "claude-opus-5-5"})),
    "perms_launch_failed": ("passthrough", BOB, _action("/perms", extra=_launch_fails)),
    "resume_auto_by_member": ("detail", DEV, _action("/resume", status="gone",
                                                     skip_perms=True, transcript="abc")),
    "kill_gone": ("mirror", BOB, _action("/kill", status="gone")),
    # session routes: lines posted into the group
    "stop_busy_0": ("mirror", BOB, _action("/stop", status="busy")),
    "stop_busy_1": ("mirror", BOB, _action("/stop", status="busy", queued=1)),
    "stop_busy_2": ("mirror", BOB, _action("/stop", status="busy", queued=2)),
    "clearqueue_1": ("mirror", BOB, _action("/clearqueue", status="busy", queued=1)),
    "clearqueue_2": ("mirror", BOB, _action("/clearqueue", status="busy", queued=2)),
    "clearqueue_5": ("mirror", BOB, _action("/clearqueue", status="busy", queued=5)),
    "perms_to_auto": ("mirror", BOB, _action("/perms")),
    "perms_to_ask": ("mirror", BOB, _action("/perms", skip_perms=True)),
    "compact_idle": ("mirror", BOB, _action("/compact")),
    "restart_idle": ("mirror", BOB, _action("/restart", transcript="abc")),
    "rename_ok": ("mirror", BOB, _action("/rename", json={"label": "web2"})),
    "kill_idle": ("mirror", BOB, _action("/kill")),
    "resume_gone": ("mirror", BOB, _action("/resume", status="gone", transcript="abc")),
    "delete_gone": ("mirror", BOB, _action("", method="DELETE", status="gone")),
    "pref_changed": ("mirror", BOB, _put_pref("answer_length", "short")),
    "sess_pref_set": ("mirror", BOB, _put_sess_pref("answer_length", "short")),
    "sess_pref_reset": ("mirror", BOB, _put_sess_pref("answer_length", "short", reset=True)),
    "create_ok": ("mirror", BOB, _create_in_project({})),
    "create_ok_auto": ("mirror", BOB, _create_in_project({"skip_perms": True})),
    # GETs with server text
    "get_detail_idle": ("payload", BOB, _get("/api/sessions/dev")),
    "get_detail_busy": ("payload", BOB, _get("/api/sessions/dev", status="busy")),
    "get_detail_viewer": ("payload", 3, _get("/api/sessions/dev")),
    "get_preferences": ("payload", BOB, _get("/api/preferences")),
    "get_session_options": ("payload", BOB, _get("/api/session-options")),
    "get_session_preferences": ("payload", BOB, _get("/api/sessions/dev/preferences")),
}

_VOLATILE_KEY = re.compile(r"(seconds|_at$|^uptime|^version$|directories|default_directory|"
                           r"^cwd$|^path$|^session_name$)")


def normalize(obj, project=""):
    """Drop what changes from run to run (clocks, ages, tmp paths) and the one
    field entrypoints.md documents as new on facts (``id``)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _VOLATILE_KEY.search(k):
                continue
            out[k] = normalize(v, project)
        if set(out) >= {"label", "value"} and "id" in out:
            out.pop("id")
        return out
    if isinstance(obj, list):
        items = [normalize(v, project) for v in obj]
        return [i for i in items if not (isinstance(i, dict) and set(i) <= {"label", "value"}
                                         and _is_volatile_fact(i))]
    if isinstance(obj, str) and project:
        return obj.replace(project, "<proj>")
    return obj


def _is_volatile_fact(fact):
    value = str(fact.get("value", ""))
    return "ago" in value or re.search(r"\d+:\d\d", value) is not None or "/" in value
