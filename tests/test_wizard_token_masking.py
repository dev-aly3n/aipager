"""The bot token's secret half must never reach the screen.

`aipager config` used to read the token with `questionary.text`, which
echoes it and leaves it in scrollback — that is how a live token reached
an assistant transcript, forcing a rotation. And the settings display cut
the token at a fixed 10 characters, which shows only the bot id for a
10-digit id and leaks 1-2 secret characters for a shorter one.

A token is `<bot_id>:<35-char secret>`. The bot id is not secret (it is
the bot's public identifier); everything after the colon is.
"""

from __future__ import annotations

import pytest

from aipager.wizard.display import mask_token

# Obviously fake. The two halves share no characters, so an assertion that
# "no secret character is shown" cannot pass by coincidence.
SECRET = "ZZZQQQWWWZZZQQQWWWZZZQQQWWWZZZQQQWWW"
LONG_ID = "1234567890"          # 10 digits — the operator's shape
SHORT_ID = "12345678"           # 8 digits — older bots


@pytest.mark.parametrize("bot_id", [LONG_ID, SHORT_ID, "1234567890123"])
def test_no_character_of_the_secret_is_ever_shown(bot_id):
    """The property that matters, across every id length."""
    shown = mask_token(f"{bot_id}:{SECRET}")

    leaked = set(shown) & set(SECRET)
    assert not leaked, (
        f"secret characters {sorted(leaked)} appear in {shown!r} — a "
        f"{len(bot_id)}-digit bot id must not shift the cut into the secret")


def test_the_short_id_case_the_old_slice_got_wrong():
    """Regression pin for the specific defect: `token[:10]` on an 8-digit
    id showed `12345678:Z` — the colon plus one secret character."""
    shown = mask_token(f"{SHORT_ID}:{SECRET}")

    assert not shown.startswith(f"{SHORT_ID}:Z"), "leaked the first secret char"
    assert SECRET[0] not in shown


def test_the_bot_id_stays_visible_so_the_operator_can_identify_the_bot():
    """Masking must not make the display useless — the id is public and is
    how you tell which bot is configured."""
    shown = mask_token(f"{LONG_ID}:{SECRET}")

    assert LONG_ID in shown


def test_a_token_with_no_colon_is_masked_entirely():
    """A malformed value has no identifiable public half, so nothing is
    safe to show. It must not fall back to printing the raw value."""
    shown = mask_token("thisisnotavalidtokenatall")

    assert "thisisnotavalid" not in shown


def test_empty_and_missing_values_do_not_crash_or_print_junk():
    for value in ("", None):
        shown = mask_token(value)
        assert isinstance(shown, str)
        assert SECRET not in shown


@pytest.mark.parametrize("bot_id", [LONG_ID, SHORT_ID])
def test_the_settings_screen_prints_no_secret_character(monkeypatch, capsys,
                                                        bot_id):
    """Drives the real renderer and reads what lands on the terminal.

    An earlier version of this test only grepped `display.py`'s source for
    `mask_token(`, which a reviewer showed was useless: printing the raw
    token ALONGSIDE the masked one left both this test and the
    pre-existing display tests green. Source containing the right call is
    not evidence that the wrong thing isn't also printed.
    """
    from aipager.wizard import display

    token = f"{bot_id}:{SECRET}"
    monkeypatch.setattr("aipager.wizard.scope_io.read_config",
                        lambda: ([], token))
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)

    display._show_current_config()
    out = capsys.readouterr().out

    leaked = set(out) & set(SECRET)
    assert not leaked, (
        f"settings screen printed secret character(s) {sorted(leaked)}")
    assert bot_id in out, "the bot id should still identify the configured bot"


# ---- the prompts must not echo -----------------------------------------

@pytest.mark.parametrize("module,func", [
    ("aipager.wizard.first_run", "_step_token"),
    ("aipager.wizard.edit_menu", None),
])
def test_token_prompts_use_a_masked_input(module, func):
    """`questionary.text` echoes; `questionary.password` does not. This
    reads the source rather than driving the prompt, because driving it
    needs a TTY the suite does not have."""
    import importlib

    mod = importlib.import_module(module)
    src = open(mod.__file__).read()

    # Find the token prompt and check what widget it uses.
    idx = src.find("Paste your bot token:")
    assert idx != -1, f"{module} no longer prompts for a token"
    window = src[max(0, idx - 200):idx]
    assert "questionary.password(" in window, (
        f"{module} still reads the token with an echoing widget")


# ---- the error path must stay unable to echo the URL --------------------

def test_an_http_failure_never_reports_the_token_bearing_url(monkeypatch):
    """`_http_json` is called with `https://api.telegram.org/bot<TOKEN>/getMe`,
    so anything it puts in its error string is a candidate leak.

    Today it uses `str(e)`, which is just "HTTP Error 418: Teapot" — safe.
    But `HTTPError.filename` holds the full URL, token and all, so a
    later `repr(e)`, `e.filename`, or logging the exception object would
    start leaking silently. This pins the observable behaviour rather
    than the current implementation.
    """
    import urllib.error

    from aipager.wizard import telegram_api

    url = f"https://api.telegram.org/bot{LONG_ID}:{SECRET}/getMe"

    def boom(*a, **kw):
        raise urllib.error.HTTPError(url, 418, "Teapot", {}, None)

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", boom)
    _body, code, err = telegram_api._http_json(url)

    assert code == 418
    assert not (set(err) & set(SECRET)), f"error text leaks the token: {err!r}"
    assert telegram_api._explain_http_error(code, err).count(SECRET[:6]) == 0


def test_a_network_failure_never_reports_the_token_bearing_url(monkeypatch):
    import urllib.error

    from aipager.wizard import telegram_api

    url = f"https://api.telegram.org/bot{LONG_ID}:{SECRET}/getMe"

    def boom(*a, **kw):
        raise urllib.error.URLError(OSError(f"cannot reach {url}"))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", boom)
    _body, _code, err = telegram_api._http_json(url)

    assert SECRET not in err, (
        f"a network error surfaced the token-bearing URL: {err!r}")


def test_the_send_probe_error_path_is_redacted_too(monkeypatch):
    """`_test_send` posts to the same token-bearing URL and has its own
    three error returns — scrubbing only `_http_json` would leave this
    sibling leaking."""
    import urllib.error

    from aipager.wizard import telegram_api

    url = f"https://api.telegram.org/bot{LONG_ID}:{SECRET}/sendMessage"

    def boom(*a, **kw):
        raise urllib.error.URLError(OSError(f"cannot reach {url}"))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", boom)
    ok, err = telegram_api._test_send(f"{LONG_ID}:{SECRET}", 12345)

    assert ok is False
    assert SECRET not in err, f"send probe leaked the token: {err!r}"


# ---- the same try/except shape exists three times in the tree ----------
#
# The wizard's copy was the one this change started from. A reviewer found
# two more, both reachable by the operator: `aipager doctor` and the
# preflight `aipager start` runs. Patching one and leaving two is how a
# credential leak survives a security fix, so each is pinned separately.

def test_doctor_never_reports_the_token_bearing_url(monkeypatch):
    """`aipager doctor` prints these strings straight to the terminal."""
    import urllib.error

    from aipager import doctor

    url = f"https://api.telegram.org/bot{LONG_ID}:{SECRET}/getMe"

    def boom(*a, **kw):
        raise urllib.error.URLError(OSError(f"cannot reach {url}"))

    monkeypatch.setattr(doctor.urllib.request, "urlopen", boom)
    _body, err = doctor._http_json(url)

    assert SECRET not in err, f"doctor leaked the token: {err!r}"


def test_the_startup_preflight_never_reports_the_token_bearing_url(
        monkeypatch, capsys):
    """`aipager start`'s preflight prints through `friendly_error`."""
    import urllib.error
    import urllib.request

    from aipager.cli import daemon as cli_daemon

    token = f"{LONG_ID}:{SECRET}"
    url = f"https://api.telegram.org/bot{token}/getMe"

    monkeypatch.setattr("aipager.config.BOT_TOKEN", token, raising=False)
    monkeypatch.setattr("aipager.config.CHAT_ID", 12345, raising=False)

    def boom(*a, **kw):
        raise urllib.error.URLError(OSError(f"cannot reach {url}"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    try:
        cli_daemon._telegram_preflight()
    except SystemExit:
        pass                       # exits 2 on a user-fixable failure

    printed = capsys.readouterr()
    combined = printed.out + printed.err
    assert SECRET not in combined, (
        f"the startup preflight printed the token: {combined!r}")
