"""Reason ids from cloudflared's own files (spec requirement 3; design.md
success criterion "Reasons"; entrypoints.md ``classify`` /
``classify_unreachable``).

Equivalence classes: each recognised error (in the log's shutdown line,
in a plain error line, in stderr only), an unrecognised error, garbage,
missing files. Boundaries: the shutdown line versus a later unrelated
error line, an earlier harmless error line, an empty stderr line at the
end, a huge log. Every call must return an id and never raise.
"""
from __future__ import annotations

import pytest

from aipager.miniapp import tunnel_state as ts

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    DNS_INCOMPLETE_TEXT, DNS_LOOKUP_TEXT, EDGE_DIAL_TEXT, QUICK_TUNNEL_TEXT, log_line,
)


def _files(tmp_path, log_lines=None, err_text=None):
    log = tmp_path / "aipager-cloudflared.log"
    err = tmp_path / "aipager-cloudflared.err"
    if log_lines is not None:
        log.write_text("".join(f"{line}\n" for line in log_lines))
    if err_text is not None:
        err.write_text(err_text)
    return log, err


def test_reason_ids_are_the_documented_strings():
    assert (ts.DNS_INCOMPLETE, ts.DNS_LOOKUP_FAILED, ts.QUICK_TUNNEL, ts.EDGE_UNREACHABLE,
            ts.BINARY_MISSING, ts.UNREACHABLE, ts.OTHER) == (
        "dns_incomplete", "dns_lookup_failed", "quick_tunnel", "edge_unreachable",
        "binary_missing", "unreachable", "other")


def test_phase_ids_are_the_documented_strings():
    assert (ts.STARTING, ts.WORKING, ts.RETRYING, ts.SLOW) == (
        "starting", "working", "retrying", "slow")


def test_the_incident_log_is_dns_incomplete(tmp_path):
    """The exact line the incident's log held, plus the two harmless
    ICMP-proxy warnings it also had."""
    log, err = _files(tmp_path, [
        log_line("The user running cloudflared process has a GID (group ID) that is "
                 "not within ping_group_range.", level="warn"),
        log_line("ICMP proxy feature is disabled", error="cannot create ICMPv4 proxy",
                 level="warn"),
        log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT),
    ])
    assert ts.classify(log, err) == "dns_incomplete"


def test_dns_incomplete_with_another_region_count(tmp_path):
    """The format string is "... but SRV only returned %v": a 0 matches too."""
    log, err = _files(tmp_path, [log_line(
        "Initiating shutdown",
        error="expected at least 2 Cloudflare Regions regions, but SRV only returned 0")])
    assert ts.classify(log, err) == "dns_incomplete"


def test_dns_lookup_failure_is_dns_lookup_failed(tmp_path):
    log, err = _files(tmp_path, [log_line("Initiating shutdown", error=DNS_LOOKUP_TEXT)])
    assert ts.classify(log, err) == "dns_lookup_failed"


def test_quick_tunnel_failure_in_stderr_with_an_empty_log(tmp_path):
    log, err = _files(tmp_path, [], f"{QUICK_TUNNEL_TEXT}\n")
    assert ts.classify(log, err) == "quick_tunnel"


def test_quick_tunnel_failure_in_stderr_with_no_log_file(tmp_path):
    log, err = _files(tmp_path, None, f"{QUICK_TUNNEL_TEXT}\n")
    assert ts.classify(log, err) == "quick_tunnel"


def test_quick_tunnel_in_stderr_followed_by_blank_lines(tmp_path):
    """The stderr file's last NON-EMPTY line counts (boundary)."""
    log, err = _files(tmp_path, [], f"{QUICK_TUNNEL_TEXT}\n\n\n")
    assert ts.classify(log, err) == "quick_tunnel"


def test_shutdown_line_wins_over_a_later_unrelated_error_line(tmp_path):
    log, err = _files(tmp_path, [
        log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT),
        log_line("Metrics server stopped", error="http: Server closed"),
    ])
    assert ts.classify(log, err) == "dns_incomplete"


def test_an_earlier_features_error_does_not_hide_the_shutdown_cause(tmp_path):
    """design.md: a ``Failed to fetch features`` line with an "error"
    field comes earlier in the log than the shutdown line."""
    log, err = _files(tmp_path, [
        log_line("Failed to fetch features, default to disable",
                 error="lookup cfd-features.argotunnel.com on 127.0.0.53:53: "
                       "server misbehaving"),
        log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT),
    ])
    assert ts.classify(log, err) == "dns_incomplete"


def test_a_plain_error_line_is_used_when_there_is_no_shutdown_line(tmp_path):
    log, err = _files(tmp_path, [log_line("edge discovery", error=DNS_LOOKUP_TEXT)])
    assert ts.classify(log, err) == "dns_lookup_failed"


def test_an_unrecognised_shutdown_error_falls_back_to_stderr(tmp_path):
    """Candidates are tried in order and the first RECOGNISED one wins."""
    log, err = _files(tmp_path, [log_line("Initiating shutdown", error="context canceled")],
                      f"{QUICK_TUNNEL_TEXT}\n")
    assert ts.classify(log, err) == "quick_tunnel"


def test_an_unknown_error_is_other(tmp_path):
    log, err = _files(tmp_path, [log_line("Initiating shutdown",
                                          error="zebra-marker-7781 failed for no reason")],
                      "zebra-marker-7781\n")
    assert ts.classify(log, err) == "other"


def test_no_files_at_all_is_other(tmp_path):
    log, err = _files(tmp_path)
    assert ts.classify(log, err) == "other"


def test_empty_files_are_other(tmp_path):
    log, err = _files(tmp_path, [], "")
    assert ts.classify(log, err) == "other"


@pytest.mark.parametrize("garbage", [
    "not json at all\n",
    "{\"level\": \"error\", \"error\": \n",          # truncated JSON
    "[1, 2, 3]\n\"just a string\"\n42\n",            # JSON, not objects
    '{"level":"error","error":17,"message":"Initiating shutdown"}\n',  # non-string error
    "\x00\xff\xfe binary \x01\n",
], ids=["text", "truncated", "non_object", "error_not_a_string", "binary"])
def test_garbage_log_is_other_and_never_raises(tmp_path, garbage):
    log = tmp_path / "aipager-cloudflared.log"
    err = tmp_path / "aipager-cloudflared.err"
    log.write_bytes(garbage.encode("latin-1"))
    assert ts.classify(log, err) == "other"


def test_the_dns_text_inside_a_non_json_line_is_not_taken_by_position(tmp_path):
    """Lines are parsed as JSON by key, never by position: a recognised
    error inside garbage still is not a JSON "error" field. It may only
    match through stderr, which is empty here."""
    log, err = _files(tmp_path, [f"garbage {DNS_INCOMPLETE_TEXT}"], "")
    assert ts.classify(log, err) == "other"


def test_a_directory_where_the_log_should_be_is_other(tmp_path):
    log = tmp_path / "aipager-cloudflared.log"
    log.mkdir()
    err = tmp_path / "aipager-cloudflared.err"
    err.mkdir()
    assert ts.classify(log, err) == "other"


def test_a_huge_log_still_finds_the_shutdown_line_at_its_end(tmp_path):
    """Only the log's tail is read (64 KiB per design): the cause sits at
    the end, after megabytes of warnings."""
    filler = log_line("Retrying connection", level="warn")
    lines = [filler] * 40000 + [log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)]
    log, err = _files(tmp_path, lines)
    assert ts.classify(log, err) == "dns_incomplete"


def test_failed_dial_to_edge_is_edge_unreachable(tmp_path):
    log, _err = _files(tmp_path, [
        log_line("Failed to dial a quic connection", error=EDGE_DIAL_TEXT),
        log_line("Retrying connection in up to 2s", error=EDGE_DIAL_TEXT),
    ])
    assert ts.classify_unreachable(log) == "edge_unreachable"


def test_a_live_tunnel_with_no_dial_error_is_unreachable(tmp_path):
    log, _err = _files(tmp_path, [log_line("Registered tunnel connection", level="info")])
    assert ts.classify_unreachable(log) == "unreachable"


def test_dial_error_not_last_is_unreachable(tmp_path):
    """The LAST error decides (boundary): a dial error followed by a
    different error is not edge_unreachable."""
    log, _err = _files(tmp_path, [
        log_line("x", error=EDGE_DIAL_TEXT),
        log_line("y", error="some other later error"),
    ])
    assert ts.classify_unreachable(log) == "unreachable"


def test_dial_text_not_at_the_start_is_unreachable(tmp_path):
    """"starts with failed to dial to edge" (boundary: contained, not leading)."""
    log, _err = _files(tmp_path, [log_line("x", error=f"wrapped: {EDGE_DIAL_TEXT}")])
    assert ts.classify_unreachable(log) == "unreachable"


def test_classify_unreachable_with_no_log_is_unreachable(tmp_path):
    assert ts.classify_unreachable(tmp_path / "missing.log") == "unreachable"


def test_classify_unreachable_with_garbage_never_raises(tmp_path):
    log = tmp_path / "aipager-cloudflared.log"
    log.write_bytes(b"\xff\xfe{{{ not json\n")
    assert ts.classify_unreachable(log) == "unreachable"
