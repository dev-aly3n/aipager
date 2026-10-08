"""Sending a problem report (roadmap 8.112 step 2, design section 5).

Every request goes to a fake network (``httpx.MockTransport``): the key
file on GitHub and Sentry's envelope endpoint. Nothing here reaches the
real ones; an un-injected send is refused by ``_test_guard.check_network``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import stat
import threading
from pathlib import Path

import httpx
import pytest

from aipager import _test_guard, config
from aipager import private_file
from aipager.cli import report as cli_report
from aipager.report import builder, endpoint, send, store
from aipager.report import schema as sc
from tests.test_problem_report_privacy import (  # noqa: F401 - fixtures
    CANARIES, TOKEN_SECRET, _aipager_raiser, _caught, _encodings, assert_no_leak,
    canary_world, machine)

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = int(_dt.datetime(2026, 10, 8, 12, tzinfo=_dt.timezone.utc).timestamp())
DAY = 86400
KEY = "1718006caf2b98a09c45304f632720ed"
SENTRY_URL = "https://o4512218007339008.ingest.us.sentry.io/api/4512218016448512/envelope/"
OTHER_DSN = "https://" + "a" * 32 + "@o99.ingest.de.sentry.io/77"
OTHER_URL = "https://o99.ingest.de.sentry.io/api/77/envelope/"
FRAMES = [{"file": "aipager/state.py", "line": 30, "fn": "innermost"},
          {"file": "aipager/bot/notify.py", "line": 20, "fn": "middle"},
          {"file": "aipager/session_monitor.py", "line": 10, "fn": "outermost"}]


def _doc(**changes) -> bytes:
    doc = {"v": 1, "dsn": endpoint.FALLBACK_DSN, "enabled": True, "min_version": "0.7.20"}
    doc.update(changes)
    return json.dumps(doc).encode()


class FakeNet:
    """GitHub's key file and Sentry's envelope endpoint, recorded."""

    def __init__(self, doc: bytes | None = None, doc_status: int = 200,
                 sentry_status: int = 200, doc_error: Exception | None = None,
                 sentry_error: Exception | None = None, doc_headers=None, sentry_headers=None):
        self.doc = _doc() if doc is None else doc
        self.doc_status, self.sentry_status = doc_status, sentry_status
        self.doc_error, self.sentry_error = doc_error, sentry_error
        self.doc_headers, self.sentry_headers = doc_headers or {}, sentry_headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == endpoint.ENDPOINT_URL:
            if self.doc_error is not None:
                raise self.doc_error
            return httpx.Response(self.doc_status, content=self.doc, headers=self.doc_headers)
        if request.method == "POST":
            if self.sentry_error is not None:
                raise self.sentry_error
            return httpx.Response(self.sentry_status, json={"id": "x"},
                                  headers=self.sentry_headers)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def gets(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]

    @property
    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


def _untouchable(request):
    raise AssertionError(f"no request may be made here: {request.method} {request.url}")


NO_NET = httpx.MockTransport(_untouchable)


def _error_entry(frames=None) -> dict:
    exc = _caught(_aipager_raiser(None), lambda: KeyError("x"))
    entry = builder.error_entry(exc, where="daemon", trigger="log_exception",
                                logger="aipager.state")
    if frames is not None:
        entry["frames"] = frames
    return entry


def _report(errors=None, note=None, version="0.7.20", origin="index", install="pipx") -> dict:
    report = builder.build_report("manual", errors=errors, note=note)
    report["aipager"].update({"version": version, "origin": origin, "install": install})
    assert send.checked_preview(report) is not None
    return report


def parse_envelope(body: bytes):
    """The envelope back into (header, [(item header, payload bytes)]),
    honouring each item's byte length."""
    header_line, rest = body.split(b"\n", 1)
    items = []
    while rest:
        item_line, rest = rest.split(b"\n", 1)
        item = json.loads(item_line)
        payload, rest = rest[:item["length"]], rest[item["length"]:]
        assert rest[:1] == b"\n", "a payload must end with a newline"
        rest = rest[1:]
        items.append((item, payload))
    return json.loads(header_line), items


def _sent(report, net: FakeNet | None = None, now=NOW):
    net = net or FakeNet()
    return send.send(report, transport=net.transport, now=now), net


# ---- the key file -----------------------------------------------------------------

def test_the_key_file_in_the_repo_is_the_compiled_fallback():
    doc = endpoint.read_document((REPO_ROOT / "report-endpoint.json").read_bytes())
    assert doc is not None, "report-endpoint.json must be a document aipager accepts"
    assert doc["dsn"] == endpoint.FALLBACK_DSN and doc["enabled"] is True
    assert endpoint.parse_dsn(doc["dsn"]) == endpoint.Dsn(KEY, "o4512218007339008.ingest.us.sentry.io",
                                                          "4512218016448512")
    assert endpoint.release_tuple(doc["min_version"]) is not None


@pytest.mark.parametrize("dsn", [
    endpoint.FALLBACK_DSN,
    "https://" + "0" * 32 + "@o1.ingest.sentry.io/1",
    "https://" + "f" * 32 + "@o42.ingest.de.sentry.io/9",
])
def test_a_sentry_ingest_key_is_accepted(dsn):
    assert endpoint.parse_dsn(dsn) is not None


@pytest.mark.parametrize("dsn", [
    "http://" + KEY + "@o4512218007339008.ingest.us.sentry.io/4512218016448512",
    "https://" + KEY.upper() + "@o4512218007339008.ingest.us.sentry.io/4512218016448512",
    "https://" + KEY[:-1] + "@o1.ingest.sentry.io/1",
    "https://" + KEY + ":secret@o1.ingest.sentry.io/1",
    "https://" + KEY + "@evil.example/1",
    "https://" + KEY + "@sentry.io.evil.example/1",
    "https://" + KEY + "@o1.ingest.sentry.io.evil.example/1",
    "https://" + KEY + "@evil.sentry.io/1",
    "https://" + KEY + "@o1.ingest.sentry.io:8443/1",
    "https://" + KEY + "@o1.ingest.sentry.io/prefix/1",
    "https://" + KEY + "@o1.ingest.sentry.io/1?x=1",
    "https://" + KEY + "@o1.ingest.sentry.io/1/",
    "https://" + KEY + "@o1.ingest.sentry.io/abc",
    "https://" + KEY + "@oX.ingest.sentry.io/1",
    "https://" + KEY + "@o1.ingest.usa.sentry.io/1",
    " https://" + KEY + "@o1.ingest.sentry.io/1",
    "https://" + KEY + "@o1.ingest.sentry.io/1\n",
    "", None, 5,
])
def test_anything_but_a_sentry_ingest_key_is_refused(dsn):
    assert endpoint.parse_dsn(dsn) is None


@pytest.mark.parametrize("body", [
    _doc(extra=1),
    json.dumps({"v": 1, "dsn": endpoint.FALLBACK_DSN, "enabled": True}).encode(),
    _doc(v=2), _doc(v=True), _doc(v="1"),
    _doc(enabled="true"), _doc(enabled=1),
    _doc(dsn=5), _doc(dsn=None),
    _doc(min_version="x"), _doc(min_version="1.2.3.4.5"), _doc(min_version=7),
    _doc(min_version="0.7.20\n"),
    b'{"v": 1, "v": 1, "dsn": "", "enabled": true, "min_version": "0.1"}',
    b'{"v": NaN, "dsn": "", "enabled": true, "min_version": "0.1"}',
    b"not json", b"\xff\xfe", b"[1, 2]", b"",
    _doc(dsn="x" * endpoint.MAX_BODY),
])
def test_a_key_file_aipager_does_not_understand_is_unusable(body):
    assert endpoint.read_document(body) is None


@pytest.mark.parametrize("version, floor, below", [
    ("0.7.20", "0.7.20", False), ("0.7.21", "0.7.20", False), ("0.10.0", "0.9.9", False),
    ("0.7.19", "0.7.20", True), ("0.7.20", "0.8", True), ("1.0", "0.99.99", False),
    ("0.8.0rc1", "0.8.0", False), ("0.8.0.dev3+g1a2b", "0.8.0", False),
    ("0.0.0+unknown", "0.0.1", True), ("0.0.0+unknown", "0.0", True),
    ("garbage", "0.1", True), (None, "0.1", True),
])
def test_min_version_compares_release_numbers(version, floor, below):
    assert endpoint._below(version, floor) is below


# ---- the decision table: which key a send uses ------------------------------------

@pytest.mark.parametrize("net", [
    FakeNet(doc_error=httpx.ConnectError("down")),
    FakeNet(doc_error=httpx.ReadTimeout("slow")),
    FakeNet(doc_status=404, doc=_doc(enabled=False)),   # a body that is not the file
    FakeNet(doc_status=500, doc=_doc(enabled=False)),
    FakeNet(doc=b"x" * (endpoint.MAX_BODY + 1)),
    FakeNet(doc=b"<html>not json</html>"),
    FakeNet(doc=_doc(extra=True)),
], ids=["offline", "timeout", "404", "500", "oversize", "not-json", "wrong-shape"])
def test_an_unusable_key_file_falls_back_to_the_compiled_key(net):
    result, net = _sent(_report(), net)
    assert result.outcome == send.SENT
    assert [str(r.url) for r in net.posts] == [SENTRY_URL]


def test_a_huge_key_file_is_not_read_to_the_end():
    pulled = []

    def chunks():
        for i in range(10_000):
            pulled.append(i)
            yield b"x" * 1024

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, content=chunks())
        return httpx.Response(200, json={"id": "x"})

    result = send.send(_report(), transport=httpx.MockTransport(handler), now=NOW)
    assert result.outcome == send.SENT, "an unusable file falls back"
    assert len(pulled) <= endpoint.MAX_BODY // 1024 + 2, "the download stops at the cap"


def test_a_key_file_with_a_key_that_is_not_sentry_is_ignored():
    evil = "https://" + KEY + "@evil.example/1"
    result, net = _sent(_report(), FakeNet(doc=_doc(dsn=evil)))
    assert result.outcome == send.SENT
    assert [str(r.url) for r in net.posts] == [SENTRY_URL]
    assert all("evil" not in str(r.url) for r in net.requests)


def test_the_key_file_wins_over_the_compiled_key():
    result, net = _sent(_report(), FakeNet(doc=_doc(dsn=OTHER_DSN)))
    assert result.outcome == send.SENT
    assert [str(r.url) for r in net.posts] == [OTHER_URL]
    assert ("sentry_key=" + "a" * 32) in net.posts[0].headers["X-Sentry-Auth"]


@pytest.mark.parametrize("doc", [_doc(enabled=False), _doc(dsn=""),
                                 _doc(enabled=False, dsn="")],
                         ids=["off", "empty-key", "both"])
def test_the_kill_switch_wins_over_the_fallback(doc):
    result, net = _sent(_report(), FakeNet(doc=doc))
    assert result.outcome == send.DISABLED
    assert net.posts == []
    assert send.sends_today(send._day(NOW)) == 0, "a refused send uses no slot"


def test_a_release_below_min_version_does_not_send():
    result, net = _sent(_report(version="0.7.19"), FakeNet(doc=_doc(min_version="0.7.20")))
    assert result.outcome == send.TOO_OLD and net.posts == []
    result, net = _sent(_report(version="0.0.0+unknown"), FakeNet())
    assert result.outcome == send.TOO_OLD and net.posts == []
    assert send.sends_today(send._day(NOW)) == 0


def test_a_valid_key_file_is_kept_for_a_day_and_a_failure_is_not():
    net = FakeNet()
    for offset in (0, 60, DAY - 1):
        assert _sent(_report(), net, now=NOW + offset)[0].outcome == send.SENT
    assert len(net.gets) == 1, "fetched once, then kept"
    assert _sent(_report(), net, now=NOW + DAY)[0].outcome == send.SENT
    assert len(net.gets) == 2, "a day later it is fetched again"

    endpoint.reset_cache()
    down = FakeNet(doc_error=httpx.ConnectError("down"))
    for offset in (0, 60):
        assert _sent(_report(), down, now=NOW + 2 * DAY + offset)[0].outcome == send.SENT
    assert len(down.gets) == 2, "a failed fetch is tried again on the next send"


def test_a_kept_kill_switch_holds_for_the_day():
    off = FakeNet(doc=_doc(enabled=False))
    assert _sent(_report(), off)[0].outcome == send.DISABLED
    assert _sent(_report(), FakeNet(), now=NOW + 60)[0].outcome == send.DISABLED


def test_a_clock_that_went_back_fetches_again():
    net = FakeNet()
    _sent(_report(), net, now=NOW)
    _sent(_report(), net, now=NOW - 60)
    assert len(net.gets) == 2


def test_the_key_file_is_fetched_only_by_a_send():
    users = sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "aipager").rglob("*.py")
                   if "endpoint.resolve(" in p.read_text())
    assert users == ["aipager/report/send.py"]


# ---- the envelope ------------------------------------------------------------------

FORBIDDEN_EVENT_KEYS = {"user", "server_name", "request", "breadcrumbs", "extra", "modules",
                        "contexts", "sdk", "logentry", "threads", "debug_meta", "dist"}


def test_the_envelope_is_an_event_and_the_exact_preview():
    report = _report(errors=[_error_entry(FRAMES)], note="Ça gèle après une photo 日本")
    result, net = _sent(report)
    assert result.outcome == send.SENT
    (post,) = net.posts
    header, items = parse_envelope(post.content)
    assert set(header) == {"event_id", "sent_at"}
    assert len(header["event_id"]) == 32 and int(header["event_id"], 16) >= 0
    assert header["sent_at"] == "2026-10-08T12:00:00Z"
    [(event_header, event_bytes), (att_header, att_bytes)] = items
    assert event_header == {"type": "event", "length": len(event_bytes),
                            "content_type": "application/json"}
    assert att_header == {"type": "attachment", "length": len(att_bytes),
                          "filename": "report.json", "content_type": "application/json",
                          "attachment_type": "event.attachment"}
    preview = builder.render_preview(report).encode("utf-8")
    assert att_bytes == preview, "the attachment is exactly what the user previewed"
    assert len(preview) != len(builder.render_preview(report)), "lengths are bytes"
    event = json.loads(event_bytes)
    assert set(event) == {"event_id", "timestamp", "platform", "level", "release",
                          "environment", "tags", "fingerprint", "exception"}
    assert not FORBIDDEN_EVENT_KEYS & set(event)
    assert event["event_id"] == header["event_id"] and event["timestamp"] == NOW
    assert (event["platform"], event["level"]) == ("python", "error")
    assert event["release"] == "aipager@0.7.20" and event["environment"] == "production"
    assert event["fingerprint"] == [report["errors"][0]["fingerprint"]]
    assert result.reference == report["errors"][0]["fingerprint"]
    assert event["tags"] == {"aipager": "0.7.20", "install": "pipx",
                             "os": report["os"]["system"],
                             "python": ".".join(report["python"]["version"].split(".")[:2]),
                             "claude_code": report["claude_code"]["version"] or "unknown",
                             "trigger": "manual"}
    (value,) = event["exception"]["values"]
    assert value == {"type": "KeyError", "module": "builtins", "stacktrace": {"frames": [
        {"filename": "aipager/session_monitor.py", "function": "outermost", "lineno": 10,
         "in_app": True},
        {"filename": "aipager/bot/notify.py", "function": "middle", "lineno": 20, "in_app": True},
        {"filename": "aipager/state.py", "function": "innermost", "lineno": 30, "in_app": True},
    ]}}


def test_a_report_without_an_error_has_a_fixed_title():
    result, net = _sent(_report())
    _header, [(_h, event_bytes), _att] = parse_envelope(net.posts[0].content)
    event = json.loads(event_bytes)
    assert event["fingerprint"] == [send.NO_ERROR_FINGERPRINT]
    assert event["message"] == {"formatted": "aipager problem report (manual)"}
    assert "exception" not in event and not FORBIDDEN_EVENT_KEYS & set(event)
    assert result.reference == event["event_id"][:12]


def test_the_request_is_one_authenticated_post():
    result, net = _sent(_report())
    (post,) = net.posts
    assert str(post.url) == SENTRY_URL
    assert post.headers["Content-Type"] == "application/x-sentry-envelope"
    assert post.headers["X-Sentry-Auth"] == (
        "Sentry sentry_version=7, sentry_key=" + KEY + ", sentry_client=aipager/0.7.20")
    assert b'"dsn"' not in post.content.split(b"\n", 1)[0]


#: Design section 7: no header but these goes out. httpx adds the transfer
#: ones; the only identity is aipager's own version.
POST_HEADERS = {"host", "accept", "accept-encoding", "connection", "content-length",
                "user-agent", "content-type", "x-sentry-auth"}
GET_HEADERS = {"host", "accept", "accept-encoding", "connection", "user-agent"}


def test_no_header_beyond_the_listed_ones_goes_out():
    _result, net = _sent(_report())
    (get,), (post,) = net.gets, net.posts
    assert {k.lower() for k in post.headers} == POST_HEADERS
    assert {k.lower() for k in get.headers} == GET_HEADERS
    for request in (get, post):
        assert request.headers["user-agent"] == "aipager/0.7.20"
        assert request.headers["accept"] == "*/*"
        assert request.headers["connection"] == "keep-alive"
    assert get.headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("net", [
    FakeNet(sentry_status=307, sentry_headers={"Location": "https://evil.example/api/1/envelope/"}),
    FakeNet(sentry_status=308, sentry_headers={"Location": "https://evil.example/api/1/envelope/"}),
    FakeNet(doc_status=301, doc_headers={"Location": "https://evil.example/report-endpoint.json"}),
    FakeNet(doc_status=302, doc_headers={"Location": "https://evil.example/report-endpoint.json"}),
], ids=["post-307", "post-308", "get-301", "get-302"])
def test_a_redirect_is_never_followed(net):
    result, net = _sent(_report(), net)
    assert all(r.url.host != "evil.example" for r in net.requests)
    assert len(net.posts) == 1 and str(net.posts[0].url) == SENTRY_URL
    expected = send.OFFLINE if net.sentry_status != 200 else send.SENT
    assert result.outcome == expected


@pytest.mark.parametrize("origin, install, environment", [
    ("index", "pipx", "production"), ("index", "uv", "production"),
    ("unknown", "pip", "production"),
    ("local", "pipx", "dev"), ("vcs", "uv", "dev"), ("index", "editable", "dev"),
])
def test_a_developer_install_reports_as_dev(origin, install, environment):
    _result, net = _sent(_report(origin=origin, install=install))
    _header, [(_h, event_bytes), _att] = parse_envelope(net.posts[0].content)
    assert json.loads(event_bytes)["environment"] == environment


def test_the_whole_request_from_the_canary_world_holds_no_canary(canary_world):  # noqa: F811
    exc = _caught(_aipager_raiser(None), lambda: OSError(13, "denied", "/home/cnryuser/x"))
    entry = builder.error_entry(exc, where="daemon", trigger="log_exception",
                                logger="aipager.state")
    report = builder.build_report("auto", errors=[entry], context=canary_world)
    report["aipager"]["version"] = "0.7.20"
    result, net = _sent(report)
    assert result.outcome == send.SENT
    (post,) = net.posts
    wire = (str(post.url) + "\n" + "\n".join(f"{k}: {v}" for k, v in post.headers.items())
            + "\n").encode() + post.content
    text = wire.decode("utf-8")
    for canary in CANARIES:
        for form in _encodings(canary):
            assert form not in text, f"canary {canary!r} went out as {form!r}"
    for i in range(len(TOKEN_SECRET) - 5):
        assert TOKEN_SECRET[i:i + 6] not in text
    # The event and the attachment pass the report's own leak check once
    # the typed fields that are digits or carry "@" by design are set aside.
    header, [(_h, event_bytes), (_a, att_bytes)] = parse_envelope(post.content)
    event = json.loads(event_bytes)
    assert event.pop("release") == "aipager@0.7.20"
    for key in ("event_id", "timestamp"):
        event.pop(key)
    assert_no_leak(json.dumps(event))
    assert_no_leak(att_bytes.decode("utf-8"))


# ---- what is refused before any network --------------------------------------------

def _broken_reports():
    yield "extra key", lambda r: r.update({"chat_id": 777000123456})
    yield "nested extra key", lambda r: r["aipager"].update({"home": "/home/x"})
    yield "missing errors", lambda r: r.pop("errors")
    yield "missing nested", lambda r: r["aipager"].pop("origin")
    yield "bool for int", lambda r: r["runtime"].update({"sessions_live": True})
    yield "int for bool", lambda r: r["aipager"].update({"upgradable": 1})
    yield "free text", lambda r: r["os"].update({"distro": "cnry-host"})
    yield "too many errors", lambda r: r.update({"errors": [_error_entry()] * 11})
    yield "a tuple", lambda r: r.update({"errors": ()})
    yield "a float", lambda r: r["runtime"].update({"sessions_busy": 1.0})


@pytest.mark.parametrize("name, breaks", list(_broken_reports()), ids=lambda v: v if isinstance(v, str) else "")
def test_a_report_that_is_not_allow_listed_is_never_sent(name, breaks):
    report = _report()
    breaks(report)
    result = send.send(report, transport=NO_NET, now=NOW)
    assert result.outcome == send.INVALID


@pytest.mark.parametrize("report", [None, "x", [], {}])
def test_a_non_report_is_never_sent(report):
    assert send.send(report, transport=NO_NET, now=NOW).outcome == send.INVALID


def test_an_oversized_report_is_never_sent(monkeypatch):
    report = _report()
    monkeypatch.setattr(send, "MAX_PREVIEW_BYTES", len(builder.render_preview(report)) - 1)
    assert send.send(report, transport=NO_NET, now=NOW).outcome == send.INVALID


def test_a_report_with_an_invalid_marker_is_still_sendable():
    # build_report marks a bad leaf "<invalid>"; that report is what the
    # user saw, and validating it again leaves it unchanged.
    report = _report()
    report["os"]["distro"] = sc.INVALID
    assert _sent(report)[0].outcome == send.SENT


# ---- the outcome of a send ----------------------------------------------------------

@pytest.mark.parametrize("status, outcome", [
    (200, send.SENT), (429, send.RATE_LIMITED), (400, send.REJECTED), (401, send.REJECTED),
    (403, send.REJECTED), (413, send.REJECTED), (500, send.OFFLINE), (502, send.OFFLINE),
    (503, send.OFFLINE), (302, send.OFFLINE),
])
def test_each_answer_maps_to_one_outcome_and_is_never_retried(status, outcome):
    result, net = _sent(_report(), FakeNet(sentry_status=status))
    assert result.outcome == outcome
    assert len(net.posts) == 1
    assert (result.reference is not None) is (outcome == send.SENT)


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"),
                                   httpx.RemoteProtocolError("bad")])
def test_no_answer_is_offline_and_never_retried(error):
    result, net = _sent(_report(), FakeNet(sentry_error=error))
    assert result.outcome == send.OFFLINE and len(net.posts) == 1


def test_every_outcome_is_named():
    assert set(cli_report.OUTCOME_LINES) == set(send.OUTCOMES)


# ---- the daily cap -------------------------------------------------------------------

def test_five_sends_a_day_then_none_until_the_next_utc_day():
    net = FakeNet()
    outcomes = [_sent(_report(), net, now=NOW + i)[0].outcome for i in range(send.SENDS_PER_DAY)]
    assert outcomes == [send.SENT] * send.SENDS_PER_DAY
    assert send.SENDS_PER_DAY == 5
    blocked = send.send(_report(), transport=NO_NET, now=NOW + 60)
    assert blocked.outcome == send.DAILY_CAP, "the 6th never touches the network"
    midnight = NOW - NOW % DAY + DAY
    assert send.send(_report(), transport=NO_NET, now=midnight - 1).outcome == send.DAILY_CAP
    assert _sent(_report(), net, now=midnight)[0].outcome == send.SENT


def test_an_attempt_counts_even_when_sentry_refuses_it():
    for _ in range(send.SENDS_PER_DAY):
        assert _sent(_report(), FakeNet(sentry_status=500))[0].outcome == send.OFFLINE
    assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP


@pytest.mark.parametrize("content", [
    b"not json", b"[]", b'{"day": "2026-10-08"}', b'{"day": "2026-10-08", "count": -1}',
    b'{"day": "2026-10-08", "count": true}', b'{"day": "yesterday", "count": 0}',
    b'{"day": "2026-10-08", "count": 0, "extra": 1}', b"\xff",
])
def test_a_count_file_that_is_not_ours_closes_today_and_opens_tomorrow(content):
    path = Path(config.REPORT_SENDS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP
    assert json.loads(path.read_text()) == {"day": "2026-10-08", "count": send.SENDS_PER_DAY}
    assert _sent(_report(), now=NOW + DAY)[0].outcome == send.SENT


@pytest.mark.parametrize("day", ["2026-10-07", "2026-10-09"], ids=["yesterday", "tomorrow"])
def test_a_count_from_another_day_is_a_fresh_day(day):
    # A day ahead (a clock that went back) is a fresh day too: failing
    # closed on it could hold sends off until that date.
    path = Path(config.REPORT_SENDS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": day, "count": 99}))
    assert _sent(_report())[0].outcome == send.SENT
    assert json.loads(path.read_text()) == {"day": "2026-10-08", "count": 1}


def test_a_count_path_that_is_not_a_plain_file_closes_today():
    # A pipe would block a send forever: it is never read. The writer
    # below makes the check observable (without it, the read would see a
    # zero count) and is always released, so nothing hangs.
    path = Path(config.REPORT_SENDS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)

    def _writer():
        with open(path, "w") as pipe:
            pipe.write(json.dumps({"day": "2026-10-08", "count": 0}))

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    try:
        assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP
    finally:
        reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            writer.join(5)
        finally:
            os.close(reader)
    assert not writer.is_alive()


def test_a_count_file_too_big_to_be_ours_closes_today():
    path = Path(config.REPORT_SENDS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": "2026-10-08", "count": 0}) + " " * send.MAX_COUNT_BYTES)
    assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP


def test_taking_a_send_counts_it_and_stops_at_the_cap():
    today = send._day(NOW)
    assert [send._take_send(today) for _ in range(send.SENDS_PER_DAY + 1)] == (
        [None] * send.SENDS_PER_DAY + [send.DAILY_CAP])
    assert send.sends_today(today) == send.SENDS_PER_DAY


def test_an_unreadable_count_file_closes_today():
    Path(config.REPORT_SENDS_FILE).mkdir(parents=True)
    assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file")
def test_a_count_file_that_cannot_be_read_closes_today():
    path = Path(config.REPORT_SENDS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": "2026-10-08", "count": 0}))
    os.chmod(path, 0)
    try:
        assert send.send(_report(), transport=NO_NET, now=NOW).outcome == send.DAILY_CAP
    finally:
        os.chmod(path, 0o600)


def test_a_count_that_cannot_be_written_sends_nothing(monkeypatch):
    def _full(*a, **k):
        raise OSError(28, "full")

    monkeypatch.setattr(send, "write_private", _full)
    result, net = _sent(_report())
    assert result.outcome == send.OFFLINE and net.posts == []


def test_the_count_file_is_private_in_a_private_folder():
    _sent(_report())
    path = Path(config.REPORT_SENDS_FILE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


# ---- the network guard -----------------------------------------------------------------

def test_a_send_without_a_fake_network_is_refused_under_pytest(real_home_refusals_expected):
    _test_guard.refusals.clear()
    with pytest.raises(_test_guard.LiveNetworkError):
        send.send(_report(), now=NOW)
    assert len(_test_guard.refusals) == 1 and "network" in _test_guard.refusals[0]
    _test_guard.refusals.clear()
    assert send.sends_today(send._day(NOW)) == 0


def test_an_environment_proxy_never_bypasses_the_fake_network(monkeypatch):
    # httpx itself ignores the environment's proxies once a transport is
    # given; this pins that the fake network stays the only way out.
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    result, net = _sent(_report())
    assert result.outcome == send.SENT and len(net.gets) == 1 and len(net.posts) == 1


def test_the_key_file_fetch_without_a_fake_network_is_refused(real_home_refusals_expected):
    _test_guard.refusals.clear()
    with pytest.raises(_test_guard.LiveNetworkError):
        endpoint.resolve("0.7.20", None, NOW)
    assert len(_test_guard.refusals) == 1 and "key file" in _test_guard.refusals[0]
    _test_guard.refusals.clear()


@pytest.mark.parametrize("env", [
    {"HTTPS_PROXY": "ftp://127.0.0.1:9", "HTTP_PROXY": "ftp://127.0.0.1:9"},
    {"SSL_CERT_FILE": "/nonexistent/aipager-test-ca.pem", "HTTPS_PROXY": "http://127.0.0.1:9"},
], ids=["proxy-scheme", "certificate-file"])
def test_a_client_httpx_cannot_build_sends_nothing_and_uses_no_slot(env, monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
                 "all_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # Only here is the guard off: httpx refuses to build the client, and if
    # it ever did build one, the proxy is a closed local port.
    monkeypatch.setattr(_test_guard, "check_network", lambda what: None)
    assert endpoint.client(None, "0.7.20", 1.0, "probe") is None, "httpx must refuse here"
    assert send.send(_report(), now=NOW).outcome == send.OFFLINE
    assert send.sends_today(send._day(NOW)) == 0


def test_the_network_guard_is_a_no_op_outside_pytest(monkeypatch):
    monkeypatch.setattr(_test_guard, "under_pytest", lambda: False)
    _test_guard.check_network("x")


# ---- the command line ----------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, capsys):
    """``aipager report`` with a chosen terminal and answer."""
    answers: list[str] = []
    state = {"tty": True}

    def _input(prompt):
        assert state["tty"], "nothing may be asked without a terminal"
        print(prompt, end="")
        if not answers:
            raise EOFError
        return answers.pop(0)

    monkeypatch.setattr(cli_report, "_at_a_terminal", lambda: state["tty"])
    monkeypatch.setattr("builtins.input", _input)

    def run(answer=None, tty=True, net=None, note=None):
        state["tty"] = tty
        answers[:] = [] if answer is None else [answer]
        net = net or FakeNet()
        args = type("Args", (), {"note": note})()
        code = cli_report.cmd_report(args, transport=net.transport)
        return code, capsys.readouterr().out, net

    return run


@pytest.fixture(autouse=False)
def released(monkeypatch):
    real = cli_report.build_report

    def _released(note):
        report = real(note)
        report["aipager"].update({"version": "0.7.20", "origin": "index", "install": "pipx"})
        return report

    monkeypatch.setattr(cli_report, "build_report", _released)


def test_without_a_terminal_the_report_is_shown_and_never_sent(cli, released):
    code, out, net = cli(answer="y", tty=False)
    assert code == 0 and net.requests == []
    assert cli_report.INTRO.strip() in out and cli_report.NO_TERMINAL in out
    assert cli_report.QUESTION not in out
    assert '"schema": "aipager-report/1"' in out


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
def test_yes_at_a_terminal_sends_what_was_shown(cli, released, answer):
    code, out, net = cli(answer=answer, note="the card froze")
    assert code == 0
    (post,) = net.posts
    _header, [_event, (_a, att_bytes)] = parse_envelope(post.content)
    assert att_bytes.decode("utf-8") in out, "the bytes sent were printed first"
    assert json.loads(att_bytes)["note"] == "the card froze"
    assert "Sent. Reference " in out


@pytest.mark.parametrize("answer", [None, "", "n", "no", "yes please", "ok", "y y"])
def test_anything_but_yes_sends_nothing(cli, released, answer):
    code, out, net = cli(answer=answer)
    assert code == 0 and net.requests == [] and cli_report.NOT_SENT in out


@pytest.mark.parametrize("net, line", [
    (FakeNet(sentry_status=503), cli_report.TRY_LATER),
    (FakeNet(sentry_status=429), cli_report.TRY_LATER),
    (FakeNet(doc=_doc(enabled=False)), cli_report.OUTCOME_LINES["disabled"]),
])
def test_a_confirmed_send_that_fails_says_so_and_exits_1(cli, released, net, line):
    code, out, _net = cli(answer="y", net=net)
    assert code == 1 and line in out


def test_the_command_line_never_writes_the_store(cli, released, monkeypatch):
    exc = _caught(_aipager_raiser(None), lambda: KeyError("x"))
    store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    assert store.save_if_dirty(force=True)
    path = Path(config.REPORTS_FILE)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    store.reset()

    def _no_save(*a, **k):
        raise AssertionError("aipager report must never write reports.json")

    monkeypatch.setattr(store, "save_if_dirty", _no_save)
    code, out, net = cli(answer="y")
    assert code == 0 and len(net.posts) == 1 and '"errors": [' in out
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_the_terminal_check_needs_both_ends(monkeypatch):
    class _Stream:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    for stdin, stdout, expected in ((True, True, True), (True, False, False),
                                    (False, True, False), (False, False, False)):
        monkeypatch.setattr("sys.stdin", _Stream(stdin))
        monkeypatch.setattr("sys.stdout", _Stream(stdout))
        assert cli_report._at_a_terminal() is expected
    monkeypatch.setattr("sys.stdin", None)
    assert cli_report._at_a_terminal() is False


def test_aipager_report_is_wired(monkeypatch):
    from aipager import cli

    seen = []
    monkeypatch.setattr(cli_report, "cmd_report", lambda args: seen.append(args.note) or 0)
    monkeypatch.setattr("sys.argv", ["aipager", "report", "--note", "hello"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0 and seen == ["hello"]


def test_the_command_line_text_has_no_em_dash():
    texts = [cli_report.INTRO, cli_report.QUESTION, cli_report.NO_TERMINAL,
             cli_report.NOT_SENT, *cli_report.OUTCOME_LINES.values()]
    assert all("—" not in t and "–" not in t for t in texts)


# ---- files written owner-only from the first byte (roadmap 8.115) ------------------------

def test_a_private_file_is_0600_whatever_the_umask(tmp_path):
    target = tmp_path / "d" / "f.json"
    old = os.umask(0)
    try:
        private_file.write_private(target, "{}", dir_mode=0o700)
    finally:
        os.umask(old)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert target.read_text() == "{}"


def test_a_leftover_temporary_file_is_replaced_not_reused(tmp_path):
    target = tmp_path / "f.json"
    leftover = tmp_path / "f.json.tmp"
    leftover.write_text("old")
    os.chmod(leftover, 0o666)
    private_file.write_private(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600 and target.read_text() == "new"
    assert not leftover.exists()


def test_a_link_planted_as_the_temporary_file_is_not_followed(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / "f.json.tmp").symlink_to(victim)
    private_file.write_private(tmp_path / "f.json", "new")
    assert victim.read_text() == "keep"
    assert (tmp_path / "f.json").read_text() == "new"


def test_a_link_planted_after_the_cleanup_is_refused(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    real_unlink = os.unlink

    def _race(path, *a, **k):  # another process wins the race after the cleanup
        try:
            real_unlink(path, *a, **k)
        finally:
            if str(path).endswith(".tmp") and not os.path.lexists(path):
                os.symlink(victim, path)

    monkeypatch.setattr(private_file.os, "unlink", _race)
    with pytest.raises(OSError):
        private_file.write_private(tmp_path / "f.json", "new")
    assert victim.read_text() == "keep"


def test_a_failed_rename_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    target = tmp_path / "f.json"
    target.write_text("old")

    def _fails(*a, **k):
        raise OSError(28, "full")

    monkeypatch.setattr(private_file.os, "replace", _fails)
    with pytest.raises(OSError):
        private_file.write_private(target, "new")
    assert target.read_text() == "old" and not (tmp_path / "f.json.tmp").exists()


def test_an_existing_folder_keeps_its_mode(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    os.chmod(folder, 0o755)
    private_file.write_private(folder / "f", "x", dir_mode=0o700)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755


def test_a_private_write_is_checked_against_the_real_home(tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(private_file, "check_write", checked.append)
    private_file.write_private(tmp_path / "f", "x")
    assert checked == [tmp_path / "f"]


def test_the_store_folder_is_private(monkeypatch):
    exc = _caught(_aipager_raiser(None), lambda: KeyError("x"))
    store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    assert store.save_if_dirty(force=True)
    path = Path(config.REPORTS_FILE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
