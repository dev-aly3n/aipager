"""Server texts in the viewer's language, chat lines in the chat's language
(design.md C and D; entrypoints.md HTTP routes and "Lines posted into a
chat").

Partitions:
- viewer language en / fa / ru, chat language en / fa / ru, crossed (check
  12: a Russian viewer acting in a Persian group);
- text kinds: refusal ``detail``, chat line, payload text (schema, reasons);
- numbers in sentences (64, queue counts), names that hold digits (web2),
  model ids (claude-opus-5), commands (/new, /help).
The English of every scenario is pinned by test_p4_english_bytes.py; here the
oracle is "the same error code, a different sentence in the right script".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


def _load_scenarios():
    import importlib.util
    import sys
    name = "p4_scenarios"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("_scenarios.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SC = _load_scenarios()
GOLDEN = json.loads(Path(__file__).with_name("base_english.json").read_text(encoding="utf-8"))
DETAILS = sorted(n for n, (k, _a, _r) in SC.SCENARIOS.items() if k == "detail")
MIRRORS = sorted(n for n, (k, _a, _r) in SC.SCENARIOS.items() if k == "mirror")
SCRIPT = {"fa": "has_arabic_script", "ru": "has_cyrillic"}


def _run(env, p4, name, *, viewer=None, chat=None):
    _kind, actor, runner = SC.SCENARIOS[name]
    if viewer:
        p4.set_lang(actor, viewer)
    if chat:
        p4.set_lang(p4.GROUP, chat)
    return runner(env, actor)


# ---- details follow the viewer --------------------------------------------------------

@pytest.mark.parametrize("name", DETAILS)
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_detail_is_in_the_viewers_language(env, p4, name, code):
    out = _run(env, p4, name, viewer=code)
    assert getattr(p4, SCRIPT[code])(out["body"]["detail"])


@pytest.mark.parametrize("name", DETAILS)
def test_a_detail_keeps_its_status_and_code_in_another_language(env, p4, name):
    out = _run(env, p4, name, viewer="fa")
    want = {k: v for k, v in GOLDEN[name]["body"].items() if k != "detail"}
    got = {k: v for k, v in SC.normalize(out["body"], str(env.project)).items() if k != "detail"}
    assert (out["status"], got) == (GOLDEN[name]["status"], want)


@pytest.mark.parametrize("name", DETAILS)
def test_the_groups_language_does_not_change_a_detail(env, p4, name):
    out = _run(env, p4, name, chat="fa")
    assert out["body"]["detail"] == GOLDEN[name]["body"]["detail"]


def test_a_detail_from_sibling_code_passes_through(env, p4):
    out = _run(env, p4, "perms_launch_failed", viewer="fa")
    assert out["body"] == {"error": "launch_failed", "detail": "dtach broken"}


def test_persian_detail_numbers_use_persian_digits(env, p4):
    detail = _run(env, p4, "create_long_name", viewer="fa")["body"]["detail"]
    assert "\u06f6\u06f4" in detail


def test_persian_detail_has_no_latin_number(env, p4):
    detail = _run(env, p4, "create_long_name", viewer="fa")["body"]["detail"]
    assert "64" not in detail


def test_russian_detail_numbers_stay_latin(env, p4):
    detail = _run(env, p4, "create_long_name", viewer="ru")["body"]["detail"]
    assert "64" in detail


@pytest.mark.parametrize("name,command", [("create_reserved_name", "/new"),
                                          ("rename_reserved", "/help")])
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_command_in_a_detail_stays_whole(env, p4, name, command, code):
    detail = _run(env, p4, name, viewer=code)["body"]["detail"]
    assert command in detail


@pytest.mark.parametrize("name", ["create_reserved_name", "rename_reserved"])
def test_no_direction_mark_inside_a_command(env, p4, name):
    detail = _run(env, p4, name, viewer="fa")["body"]["detail"]
    # The command's own characters: "/" then letters, up to the first
    # character that is not part of a command (an isolate around it is fine).
    after_slash = [detail[i + 1] for i, c in enumerate(detail) if c == "/" and i + 1 < len(detail)]
    assert after_slash and not any(p4.has_bidi(c) for c in after_slash)


def test_a_model_id_in_a_persian_detail_keeps_latin_digits(env, p4):
    detail = _run(env, p4, "create_bad_model", viewer="fa")["body"]["detail"]
    assert "claude-opus-5" in detail


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_session_name_in_a_detail_is_never_translated(env, p4, code):
    detail = _run(env, p4, "create_name_taken", viewer=code)["body"]["detail"]
    assert "taken" in detail


@pytest.mark.parametrize("name", DETAILS)
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_em_dash_in_a_translated_detail(env, p4, name, code):
    assert "\u2014" not in _run(env, p4, name, viewer=code)["body"]["detail"]


def test_resumed_in_ask_is_in_the_viewers_language(env, p4):
    detail = _run(env, p4, "resume_auto_by_member", viewer="ru")["body"]["detail"]
    assert p4.has_cyrillic(detail) and "dev" in detail


def test_an_unconfirmed_model_switch_is_explained_in_the_viewers_language(env, p4):
    detail = _run(env, p4, "model_unconfirmed", viewer="fa")["body"]["detail"]
    assert p4.has_arabic_script(detail)


# ---- chat lines follow the chat ------------------------------------------------------

@pytest.mark.parametrize("name", MIRRORS)
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_chat_line_is_in_the_chats_language(env, p4, name, code):
    out = _run(env, p4, name, chat=code)
    assert out["mirrors"] and all(getattr(p4, SCRIPT[code])(m) for m in out["mirrors"])


@pytest.mark.parametrize("name", MIRRORS)
def test_the_viewers_language_never_changes_a_chat_line(env, p4, name):
    out = _run(env, p4, name, viewer="fa")
    project = str(env.project)
    assert [m.replace(project, "<proj>") for m in out["mirrors"]] == GOLDEN[name]["mirrors"]


@pytest.mark.parametrize("name", MIRRORS)
def test_a_chat_line_goes_to_the_sessions_group(env, p4, name):
    out = _run(env, p4, name, chat="fa", viewer="ru")
    assert out["mirror_chats"] == [p4.GROUP] * len(out["mirrors"])


def test_check_12_ru_viewer_in_a_fa_group_gets_a_russian_detail(env, p4):
    out = _run(env, p4, "stop_not_busy", viewer="ru", chat="fa")
    assert p4.has_cyrillic(out["body"]["detail"])


def test_check_12_ru_viewer_in_a_fa_group_posts_persian(env, p4):
    out = _run(env, p4, "stop_busy_2", viewer="ru", chat="fa")
    assert p4.has_arabic_script(out["mirrors"][0]) and not p4.has_cyrillic(out["mirrors"][0])


def test_check_12_fa_viewer_in_a_ru_group_posts_russian(env, p4):
    out = _run(env, p4, "clearqueue_2", viewer="fa", chat="ru")
    assert p4.has_cyrillic(out["mirrors"][0]) and not p4.has_arabic_script(out["mirrors"][0])


@pytest.mark.parametrize("name", MIRRORS)
def test_every_persian_line_starts_with_a_right_to_left_mark(env, p4, name):
    out = _run(env, p4, name, chat="fa")
    lines = [ln for m in out["mirrors"] for ln in m.split("\n")]
    assert lines and all(ln.startswith(p4.RLM) for ln in lines)


@pytest.mark.parametrize("name", MIRRORS)
def test_a_russian_line_carries_no_direction_control(env, p4, name):
    out = _run(env, p4, name, chat="ru")
    assert not any(p4.has_bidi(m) for m in out["mirrors"])


@pytest.mark.parametrize("name,needle", [
    ("stop_busy_0", "dev"), ("kill_idle", "dev"), ("rename_ok", "web2"),
    ("perms_to_auto", "dev"), ("dir_created", "web"), ("create_ok", "made"),
    ("model_unconfirmed", "claude-opus-5-5"),
])
def test_names_and_ids_in_a_persian_line_sit_in_an_isolate(env, p4, name, needle):
    line = _run(env, p4, name, chat="fa")["mirrors"][0]
    spans = re.findall(p4.FSI + "([^" + p4.PDI + "]*)" + p4.PDI, line)
    assert any(needle in s for s in spans)


def test_a_session_name_with_digits_keeps_latin_digits_in_persian(env, p4):
    line = _run(env, p4, "rename_ok", chat="fa")["mirrors"][0]
    assert "web2" in line


def test_a_model_id_keeps_latin_digits_in_persian(env, p4):
    line = _run(env, p4, "model_unconfirmed", chat="fa")["mirrors"][0]
    assert "claude-opus-5-5" in line


def test_a_count_in_a_persian_line_uses_persian_digits(env, p4):
    line = _run(env, p4, "clearqueue_5", chat="fa")["mirrors"][0]
    assert "\u06f5" in line and "5" not in line


@pytest.mark.parametrize("name", ["pref_changed", "sess_pref_set", "model_unconfirmed"])
def test_a_persian_arrow_points_left(env, p4, name):
    line = _run(env, p4, name, chat="fa")["mirrors"][0]
    assert "\u2190" in line and "\u2192" not in line


def test_a_persian_folder_line_keeps_the_path_whole(env, p4):
    line = _run(env, p4, "dir_created", chat="fa")["mirrors"][0]
    assert str(env.project) + "/web" in line


def test_the_persian_mode_word_differs_by_mode(env, p4):
    ask = _run(env, p4, "perms_to_ask", chat="fa")["mirrors"][0]
    auto = _run(env, p4, "perms_to_auto", chat="fa")["mirrors"][0]
    assert ask.replace("\U0001f4ac", "") != auto.replace("\U0001f916", "")


# ---- payload texts follow the viewer ---------------------------------------------------

@pytest.mark.parametrize("name", ["get_preferences", "get_session_options",
                                  "get_session_preferences"])
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_schema_titles_are_in_the_viewers_language(env, p4, name, code):
    schema = _run(env, p4, name, viewer=code)["body"]["schema"]
    assert schema and all(getattr(p4, SCRIPT[code])(g["title"]) for g in schema)


@pytest.mark.parametrize("name", ["get_preferences", "get_session_options",
                                  "get_session_preferences"])
def test_schema_ids_and_values_are_unchanged(env, p4, name):
    schema = _run(env, p4, name, viewer="fa")["body"]["schema"]

    def ids(s):
        return [(g["section"], g["field"], [o["value"] for o in g["options"]]) for g in s]
    assert ids(schema) == ids(GOLDEN[name]["body"]["schema"])


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_schema_option_labels_are_translated(env, p4, code):
    schema = _run(env, p4, "get_preferences", viewer=code)["body"]["schema"]
    labels = [o["label"] for g in schema for o in g["options"]]
    assert sum(getattr(p4, SCRIPT[code])(x) for x in labels) == len(labels)


def test_the_groups_language_never_changes_the_schema(env, p4):
    body = _run(env, p4, "get_preferences", chat="fa")["body"]
    assert body["schema"] == GOLDEN["get_preferences"]["body"]["schema"]


def test_model_hints_are_never_translated(env, p4):
    body = _run(env, p4, "get_session_options", viewer="fa")["body"]
    assert body["models"] == GOLDEN["get_session_options"]["body"]["models"]


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_action_reasons_are_in_the_viewers_language(env, p4, code):
    body = _run(env, p4, "get_detail_viewer", viewer=code)["body"]
    reasons = [a["reason"] for a in body["actions"].values() if a and a.get("reason")]
    assert reasons and all(getattr(p4, SCRIPT[code])(r) for r in reasons)


def test_the_model_switch_reason_is_in_the_viewers_language(env, p4):
    body = _run(env, p4, "get_detail_busy", viewer="ru")["body"]
    assert p4.has_cyrillic(body["model_switch"]["reason"])


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_fact_labels_are_in_the_viewers_language(env, p4, code):
    facts = _run(env, p4, "get_detail_idle", viewer=code)["body"]["facts"]
    assert facts and all(getattr(p4, SCRIPT[code])(f["label"]) for f in facts)


def test_every_fact_carries_a_documented_id(env, p4):
    facts = _run(env, p4, "get_detail_idle", viewer="fa")["body"]["facts"]
    assert {f["id"] for f in facts} <= {"model", "context", "cost", "working",
                                        "last_active", "cwd"}


def test_the_model_and_folder_values_are_as_stored(env, p4):
    facts = _run(env, p4, "get_detail_idle", viewer="fa")["body"]["facts"]
    by_id = {f["id"]: f["value"] for f in facts}
    assert (by_id.get("model"), by_id.get("cwd")) == ("claude-opus-5", "/srv/web2")
