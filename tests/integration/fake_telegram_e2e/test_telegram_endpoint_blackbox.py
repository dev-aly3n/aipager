"""Black-box: AIPAGER_TELEGRAM_API_BASE is loopback-only unless allowed.

Spec Part A: "The override must refuse a non-loopback base URL unless
explicitly allowed, so a typo cannot send a test token anywhere real", and
with the variable unset every URL is what it is today. The resolver reads the
env on every call, so these run in-process with monkeypatch.setenv.
"""

from __future__ import annotations

import pytest

from aipager import telegram_endpoint as te

BASE = "AIPAGER_TELEGRAM_API_BASE"
ALLOW = "AIPAGER_TELEGRAM_API_ALLOW_REMOTE"
TOKEN = "7000000001:SECRETxxxxxxxxxxxxxxxxxxxx"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(BASE, raising=False)
    monkeypatch.delenv(ALLOW, raising=False)


def _msg() -> str:
    """The refusal text: check()'s lines joined."""
    return "\n".join(te.check())


# ---- unset: today's URLs ---------------------------------------------------------------

def test_unset_api_base_is_telegram():
    assert te.api_base() == "https://api.telegram.org"


def test_unset_method_url():
    assert te.method_url(TOKEN, "getMe") == f"https://api.telegram.org/bot{TOKEN}/getMe"


def test_unset_ptb_base_url_matches_ptb_default():
    from telegram import Bot
    assert te.ptb_base_url() + TOKEN == Bot(TOKEN).base_url


def test_unset_ptb_base_file_url_matches_ptb_default():
    from telegram import Bot
    assert te.ptb_base_file_url() + TOKEN == Bot(TOKEN).base_file_url


def test_unset_check_is_empty():
    assert te.check() == []


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_override_is_unset(monkeypatch, blank):
    monkeypatch.setenv(BASE, blank)
    assert te.api_base() == "https://api.telegram.org"


# ---- loopback forms accepted -------------------------------------------------------------

LOOPBACK = [
    ("http://127.0.0.1:41234", "http://127.0.0.1:41234"),
    ("http://127.0.0.1", "http://127.0.0.1"),
    ("http://127.5.5.5:8081", "http://127.5.5.5:8081"),
    ("http://127.255.255.254:1", "http://127.255.255.254:1"),
    ("http://[::1]:8081", "http://[::1]:8081"),
    ("http://[::1]", "http://[::1]"),
    ("http://localhost:8081", "http://localhost:8081"),
    ("https://localhost", "https://localhost"),
    ("https://127.0.0.1:65535", "https://127.0.0.1:65535"),
    ("http://127.0.0.1:41234/", "http://127.0.0.1:41234"),
    ("  http://127.0.0.1:41234/  ", "http://127.0.0.1:41234"),
]


@pytest.mark.parametrize("raw,expected", LOOPBACK)
def test_loopback_override_used(monkeypatch, raw, expected):
    monkeypatch.setenv(BASE, raw)
    assert te.api_base() == expected


@pytest.mark.parametrize("raw,_", LOOPBACK)
def test_loopback_override_check_ok(monkeypatch, raw, _):
    monkeypatch.setenv(BASE, raw)
    assert te.check() == []


def test_override_method_url(monkeypatch):
    monkeypatch.setenv(BASE, "http://127.0.0.1:41234/")
    assert te.method_url(TOKEN, "sendMessage") == f"http://127.0.0.1:41234/bot{TOKEN}/sendMessage"


def test_override_ptb_base_url(monkeypatch):
    monkeypatch.setenv(BASE, "http://127.0.0.1:41234")
    assert te.ptb_base_url() == "http://127.0.0.1:41234/bot"


def test_override_ptb_base_file_url(monkeypatch):
    monkeypatch.setenv(BASE, "http://127.0.0.1:41234")
    assert te.ptb_base_file_url() == "http://127.0.0.1:41234/file/bot"


def test_override_read_at_call_time(monkeypatch):
    monkeypatch.setenv(BASE, "http://127.0.0.1:1111")
    first = te.api_base()
    monkeypatch.setenv(BASE, "http://127.0.0.1:2222")
    assert (first, te.api_base()) == ("http://127.0.0.1:1111", "http://127.0.0.1:2222")


def test_uppercase_localhost_is_this_machine(monkeypatch):
    # Hostnames are case-insensitive; LOCALHOST is still this machine.
    monkeypatch.setenv(BASE, "http://LOCALHOST:8081")
    assert te.check() == []


# ---- remote hosts refused ---------------------------------------------------------------

REMOTE = [
    "http://example.com",
    "https://api.telegram.org",
    "http://10.0.0.1:8081",
    "http://192.168.1.10",
    "http://0.0.0.0:8081",
    "http://[::]:8081",
    "http://127.0.0.1.evil.com",
    "http://localhost.evil.com",
    "http://evil.com/127.0.0.1",
    "http://128.0.0.1",
    "http://126.255.255.255",
]


@pytest.mark.parametrize("raw", REMOTE)
def test_remote_override_raises(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    with pytest.raises(te.TelegramApiBaseError):
        te.api_base()


@pytest.mark.parametrize("raw", REMOTE)
def test_remote_override_check_names_allow_remote(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    assert "Set AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1 to allow it." in _msg()


def test_remote_message_names_host(monkeypatch):
    monkeypatch.setenv(BASE, "http://example.com:8081")
    assert ("AIPAGER_TELEGRAM_API_BASE points at example.com, which is not this machine."
            in _msg())


@pytest.mark.parametrize("fn", ["ptb_base_url", "ptb_base_file_url"])
def test_remote_override_never_falls_back(monkeypatch, fn):
    monkeypatch.setenv(BASE, "http://example.com")
    with pytest.raises(te.TelegramApiBaseError):
        getattr(te, fn)()


def test_remote_method_url_raises(monkeypatch):
    monkeypatch.setenv(BASE, "http://example.com")
    with pytest.raises(te.TelegramApiBaseError):
        te.method_url(TOKEN, "getMe")


def test_refusal_never_carries_the_token(monkeypatch):
    monkeypatch.setenv(BASE, "http://example.com")
    try:
        te.method_url(TOKEN, "getMe")
    except te.TelegramApiBaseError as exc:
        assert "SECRET" not in str(exc)
    else:
        pytest.fail("remote base accepted")


def test_error_is_a_value_error():
    assert issubclass(te.TelegramApiBaseError, ValueError)


# ---- invalid URLs refused --------------------------------------------------------------

INVALID = [
    "127.0.0.1:8081",
    "localhost",
    "ftp://127.0.0.1",
    "file:///tmp/x",
    "ws://127.0.0.1:8081",
    "http://",
    "http:///bot",
    "http://127.0.0.1:8081/?x=1",
    "http://127.0.0.1:8081#frag",
    "http://user:pw@127.0.0.1:8081",
    "http://127.0.0.1@evil.com",
    "http://127.0.0.1:notaport",
    "http://127.0.0.1:99999",
]


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_override_raises(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    with pytest.raises(te.TelegramApiBaseError):
        te.api_base()


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_override_check_refuses(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    assert te.check() != []


@pytest.mark.parametrize("raw", ["ftp://127.0.0.1", "127.0.0.1:8081", "http://"])
def test_invalid_message(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    assert "AIPAGER_TELEGRAM_API_BASE is not a valid http(s) URL." in _msg()


@pytest.mark.parametrize("raw", INVALID + REMOTE)
def test_check_never_raises(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    assert te.check()


def test_check_returns_lines_per_contract(monkeypatch):
    # entrypoints.md: `check() -> list[str]`: the refusal lines, empty
    # when the base is acceptable.
    monkeypatch.setenv(BASE, "http://example.com")
    out = te.check()
    assert isinstance(out, list) and all(isinstance(line, str) for line in out)
    assert out == [
        "AIPAGER_TELEGRAM_API_BASE points at example.com, which is not this machine.",
        "Set AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1 to allow it.",
    ]
    monkeypatch.setenv(BASE, "http://127.0.0.1:41234")
    assert te.check() == []
    monkeypatch.delenv(BASE)
    assert te.check() == []


# ---- allow-remote: only the exact value "1" ----------------------------------------------

def test_allow_remote_one_accepts_remote(monkeypatch):
    monkeypatch.setenv(BASE, "https://bots.example.com")
    monkeypatch.setenv(ALLOW, "1")
    assert te.api_base() == "https://bots.example.com"


def test_allow_remote_one_check_ok(monkeypatch):
    monkeypatch.setenv(BASE, "https://bots.example.com")
    monkeypatch.setenv(ALLOW, "1")
    assert te.check() == []


@pytest.mark.parametrize("val", ["0", "true", "yes", "TRUE", "", "2", "01", "on"])
def test_allow_remote_other_values_refuse(monkeypatch, val):
    monkeypatch.setenv(BASE, "https://bots.example.com")
    monkeypatch.setenv(ALLOW, val)
    with pytest.raises(te.TelegramApiBaseError):
        te.api_base()


def test_allow_remote_padded_one_refuses(monkeypatch):
    # design.md: accepted only when the variable equals "1".
    monkeypatch.setenv(BASE, "https://bots.example.com")
    monkeypatch.setenv(ALLOW, " 1 ")
    with pytest.raises(te.TelegramApiBaseError):
        te.api_base()


@pytest.mark.parametrize("raw", ["ftp://bots.example.com", "http://u:p@bots.example.com",
                                 "bots.example.com", "https://bots.example.com/?q=1"])
def test_allow_remote_still_requires_valid_url(monkeypatch, raw):
    monkeypatch.setenv(BASE, raw)
    monkeypatch.setenv(ALLOW, "1")
    with pytest.raises(te.TelegramApiBaseError):
        te.api_base()


def test_allow_remote_without_override_keeps_telegram(monkeypatch):
    monkeypatch.setenv(ALLOW, "1")
    assert te.api_base() == "https://api.telegram.org"
