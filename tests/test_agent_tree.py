"""aipager.agent_tree: the still-running note, the parent link a PostToolUse
carries, and the state file's "agents" record (pure, clocks passed in)."""

from __future__ import annotations

import math

import pytest

from aipager import agent_tree as at

GAP = 1800.0
NOW_WALL = 1_800_000_000.0
NOW_MONO = 50_000.0

_NOTE_STILL_RUNNING = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>This agent stopped with background work of its own "
    "still running. It may resume on its own when that work completes or "
    "reports, and the same task-id notifies again if it does; the result "
    "below may be interim.</note>\n<result>Waiting.</result>\n"
    "</task-notification>")
_NOTE_DONE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>A task-notification fires each time this agent stops "
    "with no live background children of its own.</note>\n"
    "</task-notification>")


# ── the note parser (moved from hook_receiver) ─────────────────────────────

def test_the_still_running_note_names_its_task():
    assert at.still_running_task_ids(_NOTE_STILL_RUNNING.format(tid="a2d0")) \
        == ["a2d0"]


def test_the_final_note_names_nothing():
    assert at.still_running_task_ids(_NOTE_DONE.format(tid="a2d0")) == []


def test_a_result_quoting_the_note_is_not_the_note():
    text = _NOTE_DONE.format(tid="a2d0").replace(
        "</task-notification>",
        "<result>It says \"background work of its own still running\"."
        "</result>\n</task-notification>")
    assert at.still_running_task_ids(text) == []


def test_each_notification_is_read_on_its_own():
    shell = ("<task-notification>\n<task-id>bhvkhjl4x</task-id>\n<status>"
             "completed</status>\n</task-notification>\n")
    assert at.still_running_task_ids(
        shell + _NOTE_STILL_RUNNING.format(tid="a2d0")) == ["a2d0"]


def test_not_text_names_nothing():
    assert at.still_running_task_ids(None) == []  # type: ignore[arg-type]
    assert at.still_running_task_ids("no notification here") == []


# ── launched_child ──────────────────────────────────────────────────────────

def _post(**over):
    msg = {"hook_event_name": "PostToolUse", "agent_id": "aparent01",
           "tool_name": "Agent",
           "tool_response": {"isAsync": True, "status": "async_launched",
                             "agentId": "bchild001"}}
    msg.update(over)
    return msg


def test_an_agent_call_inside_an_agent_links_its_child():
    assert at.launched_child(_post()) == "bchild001"


def test_the_older_task_tool_links_too():
    assert at.launched_child(_post(tool_name="Task")) == "bchild001"


def test_another_status_still_links():
    msg = _post(tool_response={"agentId": "bchild001", "status": "completed"})
    assert at.launched_child(msg) == "bchild001"


_NO_LINK = [
    ("main loop call", _post(agent_id="")),
    ("no agent id", {k: v for k, v in _post().items() if k != "agent_id"}),
    ("bad parent id", _post(agent_id="a b")),
    ("non-dict response", _post(tool_response="launched bchild001")),
    ("list response", _post(tool_response=[{"agentId": "bchild001"}])),
    ("no agentId", _post(tool_response={"status": "async_launched"})),
    ("non-string agentId", _post(tool_response={"agentId": 12345})),
    ("bad agentId", _post(tool_response={"agentId": "../etc"})),
    ("too long agentId", _post(tool_response={"agentId": "a" * 65})),
    ("self link", _post(tool_response={"agentId": "aparent01"})),
    ("not an agent tool", _post(tool_name="Bash")),
    ("not a dict", "PostToolUse"),
]


@pytest.mark.parametrize("case, msg", _NO_LINK, ids=[c for c, _m in _NO_LINK])
def test_anything_else_links_nothing(case, msg):
    assert at.launched_child(msg) == ""


# ── the record ──────────────────────────────────────────────────────────────

def _running(**over):
    entry = {"id": "aparent01", "label": "pipeline-runner",
             "started_wall": NOW_WALL - 600, "waiting": True, "parent": "",
             "desc": "Run the pipeline", "prose": "Waiting for the review",
             "prose_msg": "msg_1", "tool": "Agent", "tool_arg": "ship-reviewer"}
    entry.update(over)
    return entry


def _job(**over):
    entry = {"id": "aparent01", "type": "pipeline-runner",
             "started_wall": NOW_WALL - 600, "tool_count": 7,
             "activity": "Agent: ship-reviewer"}
    entry.update(over)
    return entry


def _record(running=None, job=None, saved=NOW_WALL - 2):
    rec = at.encode_agents(
        running if running is not None else [_running()],
        job if job is not None else [_job()], saved)
    assert rec is not None
    return rec


def _validate(rec, now_mono=NOW_MONO, now_wall=NOW_WALL):
    return at.validate_agents(rec, now_mono, now_wall, max_gap=GAP)


def test_nothing_running_is_no_record():
    assert at.encode_agents([], [], NOW_WALL) is None


def test_a_round_trip_places_each_start_on_this_boots_clock():
    child = _running(id="bchild001", label="ship-reviewer", waiting=False,
                     parent="aparent01", started_wall=NOW_WALL - 30)
    rec = _record(running=[_running(), child],
                  job=[_job(), _job(id="bchild001", type="ship-reviewer",
                                    started_wall=NOW_WALL - 30)])
    out = _validate(rec)
    assert out is not None
    parent, kid = out["running"]
    assert (parent["id"], parent["waiting"], parent["parent"]) == \
        ("aparent01", True, "")
    assert (kid["id"], kid["waiting"], kid["parent"]) == \
        ("bchild001", False, "aparent01")
    assert parent["started"] == pytest.approx(NOW_MONO - 600)
    assert kid["started"] == pytest.approx(NOW_MONO - 30)
    assert parent["clock_restarted"] is False
    assert (parent["desc"], parent["prose"], parent["prose_msg"],
            parent["tool"], parent["tool_arg"]) == (
        "Run the pipeline", "Waiting for the review", "msg_1", "Agent",
        "ship-reviewer")
    assert [j["id"] for j in out["job"]] == ["aparent01", "bchild001"]
    assert out["job"][0]["tool_count"] == 7
    assert out["job"][0]["activity"] == "Agent: ship-reviewer"
    assert out["job"][1]["started"] == pytest.approx(NOW_MONO - 30)


def test_a_wrong_version_drops_the_record():
    rec = _record()
    rec["v"] = 2
    assert _validate(rec) is None


@pytest.mark.parametrize("saved", [
    NOW_WALL - GAP - 1, NOW_WALL - GAP, NOW_WALL + 5, math.nan, math.inf,
    "now", None, True,
], ids=["too-old", "at-gap", "future", "nan", "inf", "str", "none", "bool"])
def test_a_saved_wall_outside_the_window_drops_the_record(saved):
    rec = _record()
    rec["saved_wall"] = saved
    assert _validate(rec) is None


def test_a_saved_wall_inside_the_window_is_kept():
    assert _validate(_record(saved=NOW_WALL - GAP + 1)) is not None


@pytest.mark.parametrize("raw", [None, [], "agents", 3],
                         ids=["none", "list", "str", "int"])
def test_a_non_dict_drops_the_record(raw):
    assert _validate(raw) is None


@pytest.mark.parametrize("key", ["running", "job"])
def test_a_list_of_the_wrong_type_drops_the_record(key):
    rec = _record()
    rec[key] = {"aparent01": {}}
    assert _validate(rec) is None


@pytest.mark.parametrize("over", [
    {"id": "a b"}, {"id": ""}, {"id": 5}, {"label": ""}, {"label": "  "},
    {"label": 3}, {"started_wall": NOW_WALL + 10}, {"started_wall": math.nan},
    {"started_wall": "x"}, {"started_wall": -1.0}, {"waiting": "yes"},
    {"parent": 3}, {"parent": "a b"}, {"parent": "abad00001"},
    {"desc": 3}, {"prose": ["x"]}, {"tool_arg": None},
], ids=lambda o: "-".join(f"{k}={v!r}"[:20] for k, v in o.items()))
def test_a_bad_running_entry_is_dropped_on_its_own(over):
    rec = _record(running=[_running(id="abad00001"),
                           _running(id="bgood0001", waiting=False)])
    assert [e["id"] for e in _validate(rec)["running"]] == \
        ["abad00001", "bgood0001"]
    rec["running"][0].update(over)
    out = _validate(rec)
    assert out is not None
    assert [e["id"] for e in out["running"]] == ["bgood0001"]


@pytest.mark.parametrize("over", [
    {"id": "a/b"}, {"type": ""}, {"type": None}, {"started_wall": NOW_WALL + 1},
    {"tool_count": -1}, {"tool_count": "7"}, {"tool_count": True},
    {"activity": 4},
], ids=lambda o: "-".join(f"{k}={v!r}"[:20] for k, v in o.items()))
def test_a_bad_job_entry_is_dropped_on_its_own(over):
    rec = _record(job=[_job(), _job(id="bgood0001")])
    assert len(_validate(rec)["job"]) == 2
    rec["job"][0].update(over)
    out = _validate(rec)
    assert out is not None
    assert [e["id"] for e in out["job"]] == ["bgood0001"]


def test_a_repeated_id_keeps_its_first_entry():
    rec = _record(running=[_running(label="first"), _running(label="second")],
                  job=[_job(type="first"), _job(type="second")])
    out = _validate(rec)
    assert [e["label"] for e in out["running"]] == ["first"]
    assert [e["type"] for e in out["job"]] == ["first"]


def test_each_list_is_capped():
    many = [_running(id=f"a{i:08d}") for i in range(7)]
    rec = at.encode_agents(many, [], NOW_WALL - 1, cap=5)
    assert len(rec["running"]) == 5
    rec["running"] = [_running(id=f"a{i:08d}") for i in range(7)]
    out = at.validate_agents(rec, NOW_MONO, NOW_WALL, max_gap=GAP, cap=3)
    assert len(out["running"]) == 3


def test_strings_are_capped_both_ways():
    rec = _record(running=[_running(label="L" * 500, desc="d" * 500,
                                    prose="p" * 5000, prose_msg="m" * 500,
                                    tool_arg="t" * 500)],
                  job=[_job(type="T" * 500, activity="a" * 500)])
    r = rec["running"][0]
    assert (len(r["label"]), len(r["desc"]), len(r["prose"]),
            len(r["prose_msg"]), len(r["tool_arg"])) == (80, 200, 300, 100, 200)
    assert (len(rec["job"][0]["type"]), len(rec["job"][0]["activity"])) == \
        (80, 200)
    # A hand-made longer record is capped on the way in too.
    rec["running"][0]["prose"] = "p" * 5000
    out = _validate(rec)
    assert len(out["running"][0]["prose"]) == 300


def test_a_start_older_than_this_boots_clock_restarts_at_now():
    """A low boot clock (a reboot, CI): the stamp would be negative."""
    rec = _record()
    out = _validate(rec, now_mono=100.0)
    assert out["running"][0]["started"] == 100.0
    assert out["running"][0]["clock_restarted"] is True
    assert out["job"][0]["started"] == 100.0
    assert out["job"][0]["clock_restarted"] is True


def test_a_start_younger_than_this_boots_clock_is_placed():
    rec = _record()
    out = _validate(rec, now_mono=601.0)
    assert out["running"][0]["started"] == pytest.approx(1.0)
    assert out["running"][0]["clock_restarted"] is False


def test_a_missing_optional_string_reads_empty():
    rec = _record()
    for key in ("desc", "prose", "prose_msg", "tool", "tool_arg", "parent"):
        del rec["running"][0][key]
    del rec["job"][0]["activity"]
    out = _validate(rec)
    assert out["running"][0]["desc"] == "" and out["running"][0]["parent"] == ""
    assert out["job"][0]["activity"] == ""


# ---- walking the links (agents tree, 2026-10-14) ------------------------------------

def test_nearest_listed_is_the_parent_when_it_is_listed():
    assert at.nearest_listed({"c": "p"}, "c", {"p"}) == "p"


def test_nearest_listed_skips_ancestors_not_listed():
    parents = {"g": "c", "c": "p"}
    assert at.nearest_listed(parents, "g", {"p"}) == "p"
    assert at.nearest_listed(parents, "g", {"p", "c"}) == "c"


def test_nearest_listed_never_names_the_agent_itself():
    assert at.nearest_listed({"c": "p"}, "c", {"c"}) == ""


def test_nearest_listed_with_no_parent_is_nothing():
    assert at.nearest_listed({}, "c", {"c", "p"}) == ""
    assert at.nearest_listed({"c": "p"}, "c", set()) == ""


@pytest.mark.parametrize("parents", [
    {"a": "b", "b": "a"},
    {"a": "b", "b": "c", "c": "b"},
    {"a": "a"},
], ids=["two", "three-into-two", "self"])
def test_nearest_listed_ends_on_a_loop(parents):
    assert at.nearest_listed(parents, "a", {"z"}) == ""


def test_top_ancestor_is_the_end_of_the_chain():
    assert at.top_ancestor({"g": "c", "c": "p"}, "g") == "p"
    assert at.top_ancestor({"g": "c", "c": "p"}, "c") == "p"


def test_top_ancestor_of_a_top_agent_is_nothing():
    assert at.top_ancestor({"g": "c"}, "p") == ""


def test_top_ancestor_ends_on_a_loop():
    assert at.top_ancestor({"a": "b", "b": "a"}, "a") == "b"
    assert at.top_ancestor({"a": "b", "b": "c", "c": "b"}, "a") == "c"


def test_the_lineage_glyph():
    assert at.LINEAGE_SEP == " › "
