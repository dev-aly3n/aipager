"""entrypoints.md HTTP routes: ``GET /api/sessions``,
``GET /api/sessions/{label}``, ``POST /api/sessions/{label}/stop`` for a
session waiting for its job's agents (``status: "tasks"``), and the
exported ``session_summary`` / ``session_actions`` / ``sort_for_display``.
Agents only (design-amend-5 A): no ``job_shells`` field, and a job with
only shells, or nothing left running, is ``idle`` as on main.

Driven through the Mini App's own aiohttp app with signed initData. The
dtach side (``inject.send_keys``, ``inject.kill_session``) and
``session_ops._sleep`` (the module's own seam) are the only fakes.
"""

from __future__ import annotations

import importlib
import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import sessions as ma
from aipager.state import Status

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")
L = kit.LABEL


def _waiting(server, **kw):
    params = dict(agents=2, job_shells=1, other_agents=1, other_shells=1)
    params.update(kw)
    return kit.miniapp_session(server, **params)


# ---- GET /api/sessions ---------------------------------------------------------

def test_row_status_is_tasks(miniapp, run_async):
    _waiting(miniapp)
    assert kit.miniapp_row(miniapp, run_async)["status"] == "tasks"


def test_row_carries_the_job_agent_count(miniapp, run_async):
    _waiting(miniapp)
    assert kit.miniapp_row(miniapp, run_async)["job_agents"] == 2


def test_row_carries_no_shell_count(miniapp, run_async):
    _waiting(miniapp)
    assert "job_shells" not in kit.miniapp_row(miniapp, run_async)


def test_row_count_is_an_int(miniapp, run_async):
    _waiting(miniapp)
    assert type(kit.miniapp_row(miniapp, run_async)["job_agents"]) is int


def test_row_with_only_job_shells_is_idle(miniapp, run_async):
    _waiting(miniapp, agents=0, job_shells=2)
    row = kit.miniapp_row(miniapp, run_async)
    assert (row["status"], row["job_agents"]) == ("idle", 0)


def test_row_grace_window_is_idle(miniapp, run_async):
    sess = _waiting(miniapp, agents=0, job_shells=0)
    sess.job_grace_until = time.monotonic() + 60
    row = kit.miniapp_row(miniapp, run_async)
    assert (row["status"], row["job_agents"]) == ("idle", 0)


@pytest.mark.parametrize("status, expected", [
    (Status.BUSY, "busy"), (Status.INTERACTIVE, "waiting"),
], ids=["busy", "interactive"])
def test_row_other_states_with_job_work_keep_their_status(miniapp, run_async, status, expected):
    sess = _waiting(miniapp, status=status)
    if status is Status.INTERACTIVE:
        sess.pending_permission = {"tool_summary": "Bash: ls"}
    assert kit.miniapp_row(miniapp, run_async)["status"] == expected


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE, Status.IDLE],
                         ids=["busy", "interactive", "idle"])
def test_row_count_is_zero_outside_tasks(miniapp, run_async, status):
    agents = 0 if status is Status.IDLE else 2
    sess = kit.miniapp_session(miniapp, status=status, agents=agents,
                               other_agents=3, other_shells=2)
    if status is Status.INTERACTIVE:
        sess.pending_permission = {"tool_summary": "Bash: ls"}
    assert kit.miniapp_row(miniapp, run_async)["job_agents"] == 0


def test_row_idle_with_only_non_job_work_is_idle(miniapp, run_async):
    kit.miniapp_session(miniapp, other_agents=2, other_shells=2)
    assert kit.miniapp_row(miniapp, run_async)["status"] == "idle"


def test_row_parked_session_with_an_open_job_stays_background(miniapp, run_async):
    """design.md guard 6: parking keeps priority over the waiting state."""
    sess = _waiting(miniapp)
    sess.parked_job = "ab12cd34"
    sess.parked_since = time.time() - 60
    assert kit.miniapp_row(miniapp, run_async)["status"] == "background"


def test_grid_ranks_tasks_with_busy_before_idle(miniapp, run_async):
    kit.miniapp_session(miniapp, name="claude-aa", label="aa")              # idle
    _waiting(miniapp, name="claude-zz", label="zz")                       # tasks
    kit.miniapp_session(miniapp, name="claude-mm", label="mm", status=Status.BUSY)
    order = [r["label"] for r in kit.miniapp_rows(miniapp, run_async)]
    assert order.index("zz") < order.index("aa"), order


def test_sort_for_display_puts_tasks_before_idle():
    now = time.monotonic()
    rows = [ma.session_summary(kit.make_session(name="claude-aa", label="aa"), now),
            ma.session_summary(kit.make_session(name="claude-zz", label="zz", agents=1), now)]
    assert [r["label"] for r in ma.sort_for_display(rows)][0] == "zz"


def test_session_summary_agrees_with_shown_state():
    sess = kit.make_session(agents=3, job_shells=2, other_shells=1)
    row = ma.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == tuple(sess.shown_state())


# ---- GET /api/sessions/{label} -----------------------------------------------------

def test_detail_status_and_count(miniapp, run_async):
    _waiting(miniapp)
    d = kit.miniapp_detail(miniapp, run_async)
    assert (d["status"], d["job_agents"]) == ("tasks", 2)
    assert "job_shells" not in d


def test_detail_actions_are_exactly_the_tasks_set_in_order(miniapp, run_async):
    _waiting(miniapp)
    assert list(kit.miniapp_detail(miniapp, run_async)["actions"]) == [
        "stop", "compact", "rename", "kill", "perms", "restart"]


def test_detail_stop_is_available_to_the_admin(miniapp, run_async):
    _waiting(miniapp)
    assert kit.miniapp_detail(miniapp, run_async)["actions"]["stop"]["available"] is True


def test_detail_model_switch_is_available(miniapp, run_async):
    _waiting(miniapp)
    assert kit.miniapp_detail(miniapp, run_async)["model_switch"]["available"] is True


def test_detail_has_no_answer(miniapp, run_async):
    _waiting(miniapp)
    assert kit.miniapp_detail(miniapp, run_async)["answer"] is None


def test_plain_idle_detail_offers_no_stop(miniapp, run_async):
    """Control: Stop is the waiting state's, not every idle session's."""
    kit.miniapp_session(miniapp, other_shells=1)
    assert "stop" not in kit.miniapp_detail(miniapp, run_async)["actions"]


def test_session_actions_for_tasks_offers_stop():
    acts = ma.session_actions("tasks", resumable=False, can_act=True)
    assert list(acts) == ["stop", "compact", "rename", "kill", "perms", "restart"]


# ---- POST /api/sessions/{label}/stop ----------------------------------------------

@pytest.fixture
def dtach(monkeypatch):
    keys = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.session_ops._sleep", AsyncMock())
    return keys


def test_stop_succeeds_for_a_tasks_session(miniapp, run_async, dtach):
    _waiting(miniapp)
    status, body = kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/stop")
    assert (status, body["status"]) == (200, "stopped"), body


def test_after_stop_the_row_is_idle(miniapp, run_async, dtach):
    _waiting(miniapp)
    kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/stop")
    assert kit.miniapp_row(miniapp, run_async)["status"] == "idle"


def test_after_stop_the_row_count_is_zero(miniapp, run_async, dtach):
    _waiting(miniapp)
    kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/stop")
    assert kit.miniapp_row(miniapp, run_async)["job_agents"] == 0


def test_after_stop_the_session_shows_idle(miniapp, run_async, dtach):
    sess = _waiting(miniapp)
    kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/stop")
    assert sess.shown_state().state == "idle"


def test_stop_by_a_read_only_member_is_refused_and_keeps_waiting(miniapp, run_async, dtach):
    sess = _waiting(miniapp)
    status, _ = kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/stop",
                                 user=kit.READONLY)
    assert (status, sess.shown_state().state) == (403, "tasks")


def test_kill_removes_a_tasks_session_from_the_grid(miniapp, run_async, dtach):
    _waiting(miniapp)
    status, _ = kit.miniapp_call(miniapp, run_async, "post", f"/api/sessions/{L}/kill")
    labels = [r["label"] for r in kit.miniapp_rows(miniapp, run_async)
              if r["status"] != "gone"]
    assert (status, L in labels) == (200, False)


# ---- the page's words (entrypoints.md "Mini App page keys") -------------------------

PAGE_KEYS = ("ui.state.tasks_agents", "ui.state_line.tasks_agents")
PLURAL_PAGE_KEYS = PAGE_KEYS
#: Removed with the shells (design-amend-5 A): in no catalog, not served.
GONE_PAGE_KEYS = ("ui.count.agents", "ui.count.shells", "ui.state.tasks_shells",
                  "ui.state.tasks_both", "ui.state.tasks_finishing",
                  "ui.state_line.tasks_shells", "ui.state_line.tasks_both",
                  "ui.state_line.tasks_finishing")


@pytest.mark.parametrize("key", PAGE_KEYS)
def test_every_catalog_carries_the_page_key(key):
    from aipager.i18n import core
    from aipager.i18n.locales import LOCALES
    missing = [c for c in LOCALES if c != "en" and key not in core._catalog(c)]
    assert missing == []


@pytest.mark.parametrize("key", GONE_PAGE_KEYS)
def test_no_catalog_keeps_a_removed_page_key(key):
    from aipager.i18n import core
    from aipager.i18n.locales import LOCALES
    assert [c for c in LOCALES if key in core._catalog(c)] == []


@pytest.mark.parametrize("key", PLURAL_PAGE_KEYS)
def test_every_catalog_has_the_plural_forms_its_language_needs(key):
    from aipager.i18n import core, plurals
    from aipager.i18n.locales import LOCALES
    gaps = {}
    for code in LOCALES:
        if code == "en":
            continue
        forms = core._catalog(code).get(key)
        need = {plurals.category(code, n) for n in (0, 1, 2, 3, 5, 11, 21, 22, 100)}
        if not isinstance(forms, dict) or not need <= set(forms):
            gaps[code] = forms
    assert gaps == {}


def test_the_served_russian_catalog_carries_the_page_keys(miniapp, run_async, monkeypatch):
    status, body = kit.miniapp_call(miniapp, run_async, "get", "/api/i18n/ru.json")
    assert status == 200, body
    flat = json.dumps(body)
    assert [k for k in PAGE_KEYS if f'"{k}"' not in flat] == []
    assert [k for k in GONE_PAGE_KEYS if f'"{k}"' in flat] == []
