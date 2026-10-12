"""Coverage gaps left open in iteration 1 (tester-iter1-005):

- The Mini App side of an in-place switch: the detail payload's
  ``mode_pending`` is true after a "sent" switch and false once the next
  main-agent hook confirms it (entrypoints: GET /api/sessions/{label}).
- The docs and the CHANGELOG [1.1.0] entry: the three modes by their
  shown names and flags, Auto as the default, Full access for admins, plain
  words with no em dashes (spec.md)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aipager import perm_modes
from aipager.dtach.hook_receiver import HookReceiver

from _tm3 import FLAGS, NEVER, hook, mode_only_notify, settle_tasks
from test_tm3_miniapp import MEMBER, _call, _sess, server  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[3]


# ---- Mini App: mode_pending follows the confirmation --------------------------

@pytest.fixture
def no_settle(monkeypatch):
    monkeypatch.setattr(perm_modes, "MODE_SETTLE_SECONDS", 0.0)


def _sent(server, run_async):  # noqa: F811
    s = _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "auto"})
    return s


def _report(server, run_async, s, raw, **kw):  # noqa: F811
    recv = HookReceiver(server.registry, mode_only_notify(server.bot))

    async def _go():
        await recv._on_datagram(hook(s.name, mode=raw, **kw))
        await settle_tasks()
    run_async(_go())


def test_miniapp_detail_is_pending_after_a_sent_switch(server, run_async, pty,  # noqa: F811
                                                       no_settle):
    _sent(server, run_async)
    _st, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert data.get("mode_pending") is True, data


def test_miniapp_detail_shows_the_target_while_pending(server, run_async, pty,  # noqa: F811
                                                       no_settle):
    _sent(server, run_async)
    _st, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert data.get("mode") == "auto", data


def test_miniapp_detail_is_not_pending_after_the_confirming_hook(server, run_async,  # noqa: F811
                                                                 pty, no_settle):
    s = _sent(server, run_async)
    _report(server, run_async, s, "auto")
    _st, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert (data.get("mode_pending"), data.get("mode")) == (False, "auto"), data


def test_miniapp_detail_stays_pending_after_a_subagent_hook(server, run_async,  # noqa: F811
                                                            pty, no_settle):
    s = _sent(server, run_async)
    _report(server, run_async, s, "auto", agent_id="a-1")
    _st, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert data.get("mode_pending") is True, data


def test_miniapp_detail_current_entry_after_confirmation(server, run_async, pty,  # noqa: F811
                                                         no_settle):
    s = _sent(server, run_async)
    _report(server, run_async, s, "auto")
    _st, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    current = [m.get("id") for m in data.get("modes", []) if m.get("current")]
    assert current == ["auto"], data.get("modes")


# ---- docs and CHANGELOG --------------------------------------------------------

DOCS = ["docs/commands.md", "docs/security.md", "docs/groups.md"]
DOC_IDS = ["commands", "security", "groups"]


def _unreleased() -> str:
    """The release the three modes shipped in (it was [Unreleased])."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[1\.1\.0\][^\n]*\n(.*?)(?=^## \[)", text, re.S | re.M)
    assert m, "CHANGELOG has no [1.1.0] section"
    return m.group(1)


def _mode_lines(text: str) -> list[str]:
    keys = ("Full access", "Auto mode", "Ask mode", "--permission-mode", "/mode")
    return [ln for ln in text.splitlines() if any(k in ln for k in keys)]


@pytest.mark.parametrize("doc", DOCS, ids=DOC_IDS)
def test_doc_names_full_access(doc):
    assert "Full access" in (ROOT / doc).read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", DOCS, ids=DOC_IDS)
def test_doc_mode_lines_have_no_em_dash(doc):
    lines = _mode_lines((ROOT / doc).read_text(encoding="utf-8"))
    assert [ln for ln in lines if "—" in ln] == []


@pytest.mark.parametrize("doc", DOCS, ids=DOC_IDS)
def test_doc_never_recommends_the_allow_flag(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert f"`{NEVER}`" not in text


@pytest.mark.parametrize("mode", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_docs_or_changelog_name_each_launch_flag(mode):
    corpus = "\n".join((ROOT / d).read_text(encoding="utf-8") for d in DOCS) + _unreleased()
    assert FLAGS[mode] in " ".join(corpus.split())


@pytest.mark.parametrize("label", ["💬 Ask mode", "🤖 Auto mode", "🔓 Full access mode"],
                         ids=["ask", "auto", "full"])
def test_commands_doc_shows_each_mode_label(label):
    assert label in (ROOT / "docs/commands.md").read_text(encoding="utf-8")


def test_commands_doc_says_auto_is_the_default():
    text = " ".join((ROOT / "docs/commands.md").read_text(encoding="utf-8").split())
    assert "🤖 Auto mode** (the default)" in text


@pytest.mark.parametrize("label", ["💬 Ask mode", "🤖 Auto mode", "🔓 Full access mode"],
                         ids=["ask", "auto", "full"])
def test_changelog_unreleased_names_each_mode(label):
    assert label in _unreleased()


def test_changelog_unreleased_says_new_sessions_start_in_auto():
    assert "new sessions start in Auto mode" in _unreleased()


def test_changelog_unreleased_says_full_access_is_for_admins():
    assert "Full access is for admins" in _unreleased()


def test_changelog_unreleased_mode_lines_have_no_em_dash():
    assert [ln for ln in _mode_lines(_unreleased()) if "—" in ln] == []
