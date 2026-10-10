"""Stop and resume keep working whatever language the chat is in (entrypoints.md
"Lines posted into a chat": /stop with "N queued messages discarded" in plural
forms, /resume with the "in Ask" variant; design.md success criteria "Lines
the Mini App posts into a chat are in that chat's language, whoever acted"
and "Russian with correct plural forms (1/2/5/11/21/22/25)").

Partitions:
- chat language en / fa / ru, plus the pseudo codes qps and qps-rtl that a
  test may switch on through ``aipager.i18n.locales._TEST_ENABLED``
  (entrypoints.md: "only when a test adds them");
- queued count 0 (no count clause), 1, 2, 5 and the Russian CLDR boundaries
  11, 21, 22, 25 (QUEUE_CAP is 50, so all fit);
- resume plain, and resume by a non-admin of an Auto session (the Ask
  variant, which also carries a ``detail``).
Oracle: the route still answers its English status body (status codes and
``status`` / ``label`` / ``dropped`` / ``mode`` fields are not text), and
exactly one line reaches the session's group.
"""

from __future__ import annotations

import re

import pytest

GROUP = -1001
BOB = 2      # admin
DEV = 7      # ordinary member
CODES = ["en", "fa", "ru", "qps", "qps-rtl"]
PSEUDO_OPEN = "\u27e6"


@pytest.fixture
def pseudo_on(monkeypatch):
    monkeypatch.setattr("aipager.i18n.locales._TEST_ENABLED", frozenset({"qps", "qps-rtl"}))


def _chat(p4, code):
    if code != "en":
        p4.set_lang(p4.GROUP, code)


def _stop(env, p4, code, queued, label="dev"):
    _chat(p4, code)
    env.session(label, status="busy", queued=queued)
    before = len(env.posted())
    r = env.call("POST", f"/api/sessions/{label}/stop", user=BOB, scope=GROUP)
    return r, env.posted()[before:]


def _resume(env, p4, code, *, actor=BOB, skip_perms=False, label="dev"):
    _chat(p4, code)
    env.session(label, status="gone", skip_perms=skip_perms, claude_session_id="abc")
    before = len(env.posted())
    r = env.call("POST", f"/api/sessions/{label}/resume", user=actor, scope=GROUP)
    return r, env.posted()[before:]


# ---- stop succeeds in every chat language ----------------------------------------------

@pytest.mark.parametrize("queued", [0, 1, 2, 5])
@pytest.mark.parametrize("code", CODES)
def test_stop_answers_stopped_in_any_chat_language(env, p4, pseudo_on, code, queued):
    r, _lines = _stop(env, p4, code, queued)
    assert (r.status, r.body) == (200, {"dropped": queued, "label": "dev", "status": "stopped"})


@pytest.mark.parametrize("queued", [0, 1, 2, 5])
@pytest.mark.parametrize("code", CODES)
def test_stop_posts_one_line_to_the_group_in_any_chat_language(env, p4, pseudo_on, code, queued):
    _r, lines = _stop(env, p4, code, queued)
    assert [chat for chat, _t in lines] == [GROUP]


@pytest.mark.parametrize("code", ["qps", "qps-rtl"])
def test_a_pseudo_chat_gets_a_pseudo_stop_line(env, p4, pseudo_on, code):
    _r, lines = _stop(env, p4, code, 2)
    assert PSEUDO_OPEN in lines[0][1]


# ---- resume succeeds in every chat language ---------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_resume_answers_resumed_in_any_chat_language(env, p4, pseudo_on, code):
    r, _lines = _resume(env, p4, code)
    assert (r.status, r.body) == (200, {"label": "dev", "status": "resumed"})


@pytest.mark.parametrize("code", CODES)
def test_resume_posts_one_line_to_the_group_in_any_chat_language(env, p4, pseudo_on, code):
    _r, lines = _resume(env, p4, code)
    assert [chat for chat, _t in lines] == [GROUP]


@pytest.mark.parametrize("code", CODES)
def test_resume_in_ask_answers_resumed_in_any_chat_language(env, p4, pseudo_on, code):
    r, _lines = _resume(env, p4, code, actor=DEV, skip_perms=True)
    assert (r.status, r.body["status"], r.body["mode"]) == (200, "resumed", "ask")


@pytest.mark.parametrize("code", CODES)
def test_resume_in_ask_posts_one_line_to_the_group_in_any_chat_language(env, p4, pseudo_on, code):
    _r, lines = _resume(env, p4, code, actor=DEV, skip_perms=True)
    assert [chat for chat, _t in lines] == [GROUP]


def test_resume_in_ask_line_is_in_the_chats_language(env, p4):
    _r, lines = _resume(env, p4, "fa", actor=DEV, skip_perms=True)
    assert p4.has_arabic_script(lines[0][1])


def test_resume_in_ask_line_differs_from_a_plain_resume_in_persian(env, p4):
    _r, plain = _resume(env, p4, "fa", label="aa")
    _r, ask = _resume(env, p4, "fa", actor=DEV, skip_perms=True, label="bb")
    assert plain[0][1].replace("aa", "L") != ask[0][1].replace("bb", "L")


def test_resume_in_ask_detail_follows_the_viewer_not_the_chat(env, p4):
    p4.set_lang(DEV, "ru")
    r, _lines = _resume(env, p4, "fa", actor=DEV, skip_perms=True)
    assert p4.has_cyrillic(r.body["detail"]) and not p4.has_arabic_script(r.body["detail"])


# ---- Russian plural forms on the route --------------------------------------------------

def _shape(line, label):
    """The line with its count and session name replaced, so lines of one
    plural form compare equal."""
    return re.sub(r"[0-9]+", "N", line.replace(label, "L"))


def _ru_stop_line(env, p4, n, label="dev"):
    _r, lines = _stop(env, p4, "ru", n, label=label)
    return lines[0][1]


@pytest.mark.parametrize("a,b", [(1, 21), (2, 22), (5, 11), (5, 25), (2, 4)])
def test_russian_stop_counts_of_one_category_share_a_form(env, p4, a, b):
    lines = [_shape(_ru_stop_line(env, p4, n, label), label)
             for n, label in ((a, "aa"), (b, "bb"))]
    assert lines[0] == lines[1]


@pytest.mark.parametrize("a,b", [(1, 2), (2, 5), (1, 5), (21, 22), (22, 25), (1, 11)])
def test_russian_stop_counts_of_different_categories_differ(env, p4, a, b):
    lines = [_shape(_ru_stop_line(env, p4, n, label), label)
             for n, label in ((a, "aa"), (b, "bb"))]
    assert lines[0] != lines[1]


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25])
def test_a_russian_stop_line_shows_its_count(env, p4, n):
    assert re.search(r"(?<![0-9])" + str(n) + r"(?![0-9])", _ru_stop_line(env, p4, n))


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21])
def test_a_persian_stop_line_shows_its_count_in_persian_digits(env, p4, n):
    _r, lines = _stop(env, p4, "fa", n)
    want = "".join(p4.FA_DIGITS[int(d)] for d in str(n))
    assert want in lines[0][1]


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21])
def test_a_persian_stop_line_has_no_latin_digit(env, p4, n):
    _r, lines = _stop(env, p4, "fa", n)
    assert not re.search("[0-9]", lines[0][1])
