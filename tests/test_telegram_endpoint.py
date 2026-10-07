"""AIPAGER_TELEGRAM_API_BASE: every Bot API URL goes through one resolver.

Unset, every URL is byte-for-byte what it always was (PTB's own default
constants included). Set, every site uses it, and it must name this
machine unless AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1: a typo must never
send a bot token anywhere real.
"""

from __future__ import annotations

import inspect
import io
import json

import pytest
from telegram import Bot
from telegram.ext import ApplicationBuilder

from aipager import telegram_endpoint
from aipager.telegram_endpoint import TelegramApiBaseError

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
LOCAL = "http://127.0.0.1:41234"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(telegram_endpoint.BASE_ENV, raising=False)
    monkeypatch.delenv(telegram_endpoint.ALLOW_REMOTE_ENV, raising=False)


def _set(monkeypatch, base):
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)


# ---------------------------------------------------------------------------
# The resolver.
# ---------------------------------------------------------------------------

def test_defaults_match_telegram_and_ptb():
    assert telegram_endpoint.api_base() == "https://api.telegram.org"
    assert telegram_endpoint.method_url(TOKEN, "getMe") == \
        f"https://api.telegram.org/bot{TOKEN}/getMe"
    builder = ApplicationBuilder()
    assert telegram_endpoint.ptb_base_url() == builder._base_url.value
    assert telegram_endpoint.ptb_base_file_url() == builder._base_file_url.value
    params = inspect.signature(Bot.__init__).parameters
    assert telegram_endpoint.ptb_base_url() == params["base_url"].default
    assert telegram_endpoint.ptb_base_file_url() == params["base_file_url"].default
    assert telegram_endpoint.check() == []


@pytest.mark.parametrize("base,expected", [
    ("http://127.0.0.1:41234", "http://127.0.0.1:41234"),
    ("http://127.0.0.1:41234/", "http://127.0.0.1:41234"),
    ("  http://localhost:8081  ", "http://localhost:8081"),
    ("http://LOCALHOST", "http://LOCALHOST"),
    ("http://[::1]:9000", "http://[::1]:9000"),
    ("https://127.0.0.2", "https://127.0.0.2"),
    ("http://127.0.0.1:41234/prefix", "http://127.0.0.1:41234/prefix"),
])
def test_loopback_override_is_used(monkeypatch, base, expected):
    _set(monkeypatch, base)
    assert telegram_endpoint.api_base() == expected
    assert telegram_endpoint.method_url(TOKEN, "getMe") == f"{expected}/bot{TOKEN}/getMe"
    assert telegram_endpoint.ptb_base_url() == f"{expected}/bot"
    assert telegram_endpoint.ptb_base_file_url() == f"{expected}/file/bot"
    assert telegram_endpoint.check() == []


def test_blank_override_means_unset(monkeypatch):
    _set(monkeypatch, "   ")
    assert telegram_endpoint.api_base() == "https://api.telegram.org"


@pytest.mark.parametrize("base,host", [
    ("http://example.com", "example.com"),
    ("https://api.telegram.org", "api.telegram.org"),
    ("http://10.0.0.1:8080", "10.0.0.1"),
    ("http://127.0.0.1.evil.com", "127.0.0.1.evil.com"),
    ("http://localhost.evil.com", "localhost.evil.com"),
])
def test_remote_override_is_refused(monkeypatch, base, host):
    _set(monkeypatch, base)
    lines = [
        f"AIPAGER_TELEGRAM_API_BASE points at {host}, which is not this machine.",
        "Set AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1 to allow it.",
    ]
    with pytest.raises(TelegramApiBaseError) as exc:
        telegram_endpoint.api_base()
    assert exc.value.lines() == lines
    assert telegram_endpoint.check() == lines
    with pytest.raises(TelegramApiBaseError):
        telegram_endpoint.method_url(TOKEN, "getMe")
    with pytest.raises(TelegramApiBaseError):
        telegram_endpoint.ptb_base_url()


@pytest.mark.parametrize("base", [
    "ftp://127.0.0.1",
    "127.0.0.1:8080",
    "http://",
    "http://127.0.0.1@evil.com",
    "http://user:pw@127.0.0.1",
    "http://127.0.0.1/?x=1",
    "http://127.0.0.1/#frag",
    "http://127.0.0.1:notaport",
])
def test_invalid_override_is_refused(monkeypatch, base):
    _set(monkeypatch, base)
    with pytest.raises(TelegramApiBaseError) as exc:
        telegram_endpoint.api_base()
    assert exc.value.lines() == ["AIPAGER_TELEGRAM_API_BASE is not a valid http(s) URL."]


def test_allow_remote_accepts_a_remote_base(monkeypatch):
    _set(monkeypatch, "https://bots.example.com")
    monkeypatch.setenv(telegram_endpoint.ALLOW_REMOTE_ENV, "1")
    assert telegram_endpoint.check() == []
    assert telegram_endpoint.api_base() == "https://bots.example.com"


@pytest.mark.parametrize("value", ["0", "yes", "true", " 1"])
def test_allow_remote_needs_exactly_1(monkeypatch, value):
    _set(monkeypatch, "https://bots.example.com")
    monkeypatch.setenv(telegram_endpoint.ALLOW_REMOTE_ENV, value)
    with pytest.raises(TelegramApiBaseError):
        telegram_endpoint.api_base()


def test_allow_remote_still_refuses_an_invalid_url(monkeypatch):
    _set(monkeypatch, "ftp://bots.example.com")
    monkeypatch.setenv(telegram_endpoint.ALLOW_REMOTE_ENV, "1")
    with pytest.raises(TelegramApiBaseError):
        telegram_endpoint.api_base()


# ---------------------------------------------------------------------------
# Every call site: parity unset, override set.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_rich_message_url(monkeypatch, base):
    from aipager.bot import rich_message
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    if base:
        _set(monkeypatch, base)
    want = base or "https://api.telegram.org"
    assert rich_message._api_url("sendRichMessage") == f"{want}/bot{TOKEN}/sendRichMessage"


def _record_urlopen(monkeypatch, target: str, bodies: list[dict]):
    urls: list[str] = []
    it = iter(bodies)

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        urls.append(req if isinstance(req, str) else req.full_url)
        return _Resp(json.dumps(next(it)).encode())

    monkeypatch.setattr(target, _urlopen)
    return urls


@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_startup_preflight_urls(monkeypatch, base):
    from aipager.cli import daemon
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    monkeypatch.setattr("aipager.config.CHAT_ID", "900000001")
    if base:
        _set(monkeypatch, base)
    urls = _record_urlopen(monkeypatch, "aipager.cli.daemon.urllib.request.urlopen", [
        {"ok": True, "result": {"username": "b"}},
        {"ok": True, "result": {"id": 900000001}},
    ])
    assert daemon._telegram_preflight() == "b"
    want = base or "https://api.telegram.org"
    assert urls == [
        f"{want}/bot{TOKEN}/getMe",
        f"{want}/bot{TOKEN}/getChat?chat_id=900000001",
    ]


@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_lifecycle_builder_urls(monkeypatch, mk_bot, base):
    from aipager.bot import lifecycle
    monkeypatch.setattr(lifecycle, "BOT_TOKEN", TOKEN)
    if base:
        _set(monkeypatch, base)
    app = mk_bot()._make_builder().build()
    if base:
        assert app.bot.base_url == f"{base}/bot{TOKEN}"
        assert app.bot.base_file_url == f"{base}/file/bot{TOKEN}"
    else:
        defaults = ApplicationBuilder()
        assert app.bot.base_url == defaults._base_url.value + TOKEN
        assert app.bot.base_file_url == defaults._base_file_url.value + TOKEN


@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_observer_bot_urls(monkeypatch, run_async, base):
    from aipager.bot import observer
    if base:
        _set(monkeypatch, base)
    made = []

    class _Bot:
        def __init__(self, **kwargs):
            made.append(kwargs)

        async def initialize(self):
            return None

    monkeypatch.setattr(observer, "Bot", _Bot)
    run_async(observer.ObserverBroadcaster([(TOKEN, "1")]).start())
    params = inspect.signature(Bot.__init__).parameters
    want = (f"{base}/bot", f"{base}/file/bot") if base else (
        params["base_url"].default, params["base_file_url"].default)
    assert (made[0].get("base_url"), made[0].get("base_file_url")) == want
    assert made[0]["token"] == TOKEN


@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_doctor_urls(monkeypatch, base):
    from aipager import doctor
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    monkeypatch.setattr("aipager.config.CHAT_ID", "900000001")
    if base:
        _set(monkeypatch, base)
    urls = []
    monkeypatch.setattr(doctor, "_http_json",
                        lambda url, timeout=10.0: (urls.append(url) or
                                                   ({"ok": True, "result": {}}, "")))
    doctor.check_token_valid()
    doctor.check_chat_reachable()
    want = base or "https://api.telegram.org"
    assert urls == [f"{want}/bot{TOKEN}/getMe",
                    f"{want}/bot{TOKEN}/getChat?chat_id=900000001"]


def test_doctor_reports_a_refused_base_without_calling(monkeypatch):
    from aipager import doctor
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    monkeypatch.setattr("aipager.config.CHAT_ID", "900000001")
    _set(monkeypatch, "http://example.com")
    monkeypatch.setattr(doctor, "_http_json",
                        lambda *a, **k: pytest.fail("doctor called the network"))
    for check in (doctor.check_token_valid, doctor.check_chat_reachable):
        r = check()
        assert r.status == doctor.FAIL
        assert r.detail[0] == ("AIPAGER_TELEGRAM_API_BASE points at example.com, "
                               "which is not this machine.")


@pytest.mark.parametrize("base", [None, LOCAL], ids=["unset", "override"])
def test_wizard_urls(monkeypatch, base):
    from aipager.wizard import team_setup, telegram_api
    if base:
        _set(monkeypatch, base)
    urls: list[str] = []

    def _rec(url):
        urls.append(url)
        return None, None, ""

    monkeypatch.setattr(telegram_api, "_http_json", _rec)
    monkeypatch.setattr(team_setup, "_http_json", _rec)
    telegram_api._get_me(TOKEN)
    telegram_api._newest_private_chat(TOKEN, not_before=0)
    telegram_api._fetch_id_from_updates(TOKEN, want="dm")
    team_setup._resolve_user(TOKEN, "@somebody")
    sent = _record_urlopen(monkeypatch, "aipager.wizard.telegram_api.urllib.request.urlopen",
                           [{"ok": True, "result": {}}])
    telegram_api._send_message(TOKEN, 900000001, "hi")
    want = base or "https://api.telegram.org"
    assert urls == [
        f"{want}/bot{TOKEN}/getMe",
        f"{want}/bot{TOKEN}/getUpdates",
        f"{want}/bot{TOKEN}/getUpdates",
        f"{want}/bot{TOKEN}/getChat?chat_id=@somebody",
        f"{want}/bot{TOKEN}/getUpdates",
    ]
    assert sent == [f"{want}/bot{TOKEN}/sendMessage"]


def test_cli_turns_a_refused_base_into_exit_2(monkeypatch, capsys):
    """A wizard or doctor call that meets a bad base exits 2 with the
    message, not a traceback."""
    import aipager.cli as cli
    _set(monkeypatch, "http://example.com")

    def _boom(args):
        telegram_endpoint.api_base()
        return 0

    monkeypatch.setattr(cli, "_cmd_version", _boom)
    monkeypatch.setattr("sys.argv", ["aipager", "version"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "which is not this machine" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `aipager start` refuses before any network.
# ---------------------------------------------------------------------------

def test_cmd_start_refuses_a_remote_base_before_any_network(monkeypatch, capsys):
    import urllib.request

    import httpx

    from aipager import preflight
    from aipager.cli import daemon
    monkeypatch.delenv("AIPAGER_INSTANCE_DIR", raising=False)
    _set(monkeypatch, "http://example.com")

    def _no_net(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    monkeypatch.setattr(httpx.Client, "send", _no_net)
    monkeypatch.setattr(httpx.AsyncClient, "send", _no_net)
    called = []

    def _require():
        # Stop right here: a regressed check must not run the rest of
        # start-up inside the test process.
        called.append(1)
        raise SystemExit(99)

    monkeypatch.setattr(preflight, "require_config", _require)
    with pytest.raises(SystemExit) as exc:
        daemon._cmd_start(None)
    assert exc.value.code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "AIPAGER_TELEGRAM_API_BASE points at example.com" in err
    assert "AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1" in err
