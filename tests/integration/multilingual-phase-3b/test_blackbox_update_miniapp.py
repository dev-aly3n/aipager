"""Black-box: the Mini App's update check answers in the viewer's
language, while a job it starts, and every job section, is written in the
job chat's language (phase 3b design, success criteria 7, 8, 11). The
installer, systemd and the network are the fakes of
``tests/integration/admin-self-update-dev/conftest.py`` (loaded by path:
its directory name is not importable)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status
from tests import i18n_bb3b as L

_spec = importlib.util.spec_from_file_location(
    "_bb3b_update_env",
    Path(__file__).resolve().parents[1] / "admin-self-update-dev" / "conftest.py")
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP = -100
ADMIN, MEMBER = 555, 777


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@pytest.fixture
def env(monkeypatch, tmp_path, mk_bot):
    return _harness.Env(monkeypatch, tmp_path, mk_bot)


@pytest.fixture
def run(env):
    def _run(coro_fn):
        async def _wrapped():
            try:
                return await coro_fn()
            finally:
                await env.close()
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_wrapped())
        finally:
            loop.close()
    return _run


def _init_data(user_id, language_code=None):
    user = {"id": user_id, "first_name": "T"}
    if language_code is not None:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, language_code=None):
    return {"X-Telegram-Init-Data": _init_data(user_id, language_code)}


def _scoped(env):
    env.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="user"),
    ))]
    env.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
    env.chat_id = GROUP


def _call(env, run, fn):
    srv = MiniAppServer(env.bot, env.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return run(scenario)


def _check(env, run, headers):
    async def fn(c):
        r = await c.post("/api/update/check", headers=headers)
        return r.status, await r.json()
    return _call(env, run, fn)


def _words(body):
    body = body["check"]
    out = list(body.get("lines") or [])
    out += [t for t in (body.get("summary"), body.get("restart")) if isinstance(t, str)]
    out += list(body.get("notes") or [])
    if body.get("offer"):
        out.append(body["offer"]["label"])
    return "\n".join(out)


# ---- POST /api/update/check: the viewer's language ------------------------------

def test_check_lines_follow_the_viewers_stored_persian(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    status, body = _check(env, run, _hdr(env.chat_id, "en"))
    assert (status, L.fa_problems(_words(body))) == (200, [])


def test_check_lines_follow_the_viewers_stored_russian(env, run):
    preferences.set_ui_language(env.chat_id, "ru")
    status, body = _check(env, run, _hdr(env.chat_id, "fa"))
    assert (status, L.ru_problems(_words(body))) == (200, [])


def test_a_viewer_with_nothing_stored_and_a_russian_app_reads_english(env, run):
    """Detection is off: an app language alone never switches the check."""
    _s, english = _check(env, run, _hdr(env.chat_id, "en"))
    _s, russian_app = _check(env, run, _hdr(env.chat_id, "ru"))
    assert _words(russian_app) == _words(english)


def test_the_offer_kind_and_source_do_not_change_with_the_viewer(env, run):
    _s, english = _check(env, run, _hdr(env.chat_id))
    preferences.set_ui_language(env.chat_id, "fa")
    _s, persian = _check(env, run, _hdr(env.chat_id))
    en, fa = english["check"], persian["check"]
    assert ((fa["offer"] or {}).get("kind"), fa["source"]) == \
        ((en["offer"] or {}).get("kind"), en["source"])


def test_the_version_numbers_in_the_payload_stay_data(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    _s, body = _check(env, run, _hdr(env.chat_id))
    assert (body["aipager"]["running"], body["claude"]["latest"]) == ("0.7.13", "2.1.290")


def test_persian_check_lines_keep_versions_latin(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    _s, body = _check(env, run, _hdr(env.chat_id))
    shown = L.bare(_words(body))
    assert all(v in shown for v in ("0.7.13", "0.7.14", "2.1.281", "2.1.290"))


def test_a_group_admins_check_follows_their_dm_not_the_group(env, run):
    _scoped(env)
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(ADMIN, "fa")
    status, body = _check(env, run, _hdr(ADMIN))
    assert (status, L.fa_problems(_words(body))) == (200, [])


def test_the_forbidden_error_id_is_unchanged_for_a_persian_member(env, run):
    _scoped(env)
    preferences.set_ui_language(MEMBER, "fa")
    preferences.set_ui_language(GROUP, "fa")

    async def fn(c):
        r = await c.post("/api/update/start", headers=_hdr(MEMBER, "fa"),
                         json={"kind": "aipager"})
        return r.status, await r.json()
    assert _call(env, run, fn) == (403, {"error": "forbidden"})


# ---- a job the Mini App starts posts in the chat's language ----------------------

def _start_from_app(env, run, viewer, kind="claude"):
    async def fn(c):
        await c.post("/api/update/check", headers=_hdr(viewer))
        r = await c.post("/api/update/start", headers=_hdr(viewer), json={"kind": kind})
        body = await r.json()
        await env.finish()
        return r.status, body
    return _call(env, run, fn)


def test_a_job_started_by_a_persian_viewer_posts_in_the_russian_groups_language(env, run):
    env.pypi_latest = env.running            # only Claude Code is newer
    _scoped(env)
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(ADMIN, "fa")
    status, _body = _start_from_app(env, run, ADMIN)
    texts = [m["text"] for m in env.sent] + [e["text"] for e in env.edits]
    assert (status, [m["chat_id"] for m in env.sent],
            [t for t in texts if L.ru_problems(t)]) == (202, [GROUP], [])


def test_the_started_jobs_kind_is_the_one_the_viewer_chose(env, run):
    env.pypi_latest = env.running
    preferences.set_ui_language(env.chat_id, "fa")
    status, body = _start_from_app(env, run, env.chat_id)
    assert (status, body["job"]["kind"]) == (202, "claude")


# ---- job sections, driven end to end in a Persian chat ---------------------------

def _claude_job(env, run):
    env.pypi_latest = env.running

    async def scenario():
        await env.manager.check()
        result = await env.start("claude")
        await env.finish()
        return result
    run(scenario)
    return env.last_text()


def test_a_finished_claude_update_reads_persian_in_a_persian_chat(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    text = _claude_job(env, run)
    assert L.fa_problems(text) == []


def test_a_finished_claude_update_keeps_its_versions_latin_in_persian(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    text = L.bare(_claude_job(env, run))
    assert ("2.1.281" in text, "2.1.290" in text) == (True, True)


def test_a_finished_claude_update_reads_russian_in_a_russian_chat(env, run):
    preferences.set_ui_language(env.chat_id, "ru")
    assert L.ru_problems(_claude_job(env, run)) == []


def test_a_failed_claude_update_shows_its_exit_code_latin_in_persian(env, run):
    preferences.set_ui_language(env.chat_id, "fa")
    env.claude_rc = 7
    text = _claude_job(env, run)
    assert ("7" in L.bare(text), L.fa_digits(7) in text) == (True, False)


def test_a_job_section_ignores_the_starters_own_dm_language(env, run):
    """The status message is shared: the job chat's language, even when
    the person who started it reads Russian in their DM."""
    _scoped(env)
    env.bot.scopes = list(env.bot.scopes) + [
        Scope(chat_id=ADMIN, kind="dm", label="ada DM",
              members=(Member(id=ADMIN, label="ada", role="admin"),))]
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(ADMIN, "ru")
    env.pypi_latest = env.running

    async def scenario():
        await env.manager.check()
        await env.manager.start("claude", chat_id=GROUP, user_id=ADMIN, origin="chat",
                                status_message=env.message)
        await env.finish()
    run(scenario)
    assert L.fa_problems(env.last_text()) == []


def test_an_aipager_update_waiting_on_a_busy_session_offers_restart_buttons_in_persian(
        env, run):
    """The restart gate's keyboard (Restart now / Cancel / Wait) is in the
    job chat's language and keeps its callback_data shape."""
    from aipager import self_update
    env.claude_latest = env.claude_version
    env.add_session("claude-dev", "dev", Status.BUSY, scope_chat_id=env.chat_id)
    preferences.set_ui_language(env.chat_id, "fa")
    env.monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)

    seen = {}

    async def scenario():
        await env.manager.check()
        await env.start("aipager")
        await env.until(lambda: any(d.startswith("_:up:wait:")
                                    for d in env.last_markup_data()), timeout=8)
        markup = env.edits[-1]["reply_markup"]
        seen["labels"] = [b.text for row in markup.inline_keyboard for b in row]
        seen["data"] = env.last_markup_data()
        seen["text"] = env.last_text()
    run(scenario)
    prefixes = sorted(d.rsplit(":", 1)[0] for d in seen["data"])
    assert (prefixes, [lb for lb in seen["labels"] if not L.is_fa(lb)],
            L.fa_problems(seen["text"])) == (["_:up:now", "_:up:stop", "_:up:wait"], [], [])

