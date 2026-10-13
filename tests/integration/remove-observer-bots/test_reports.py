"""Problem reports: an old report naming the retired ``observers`` feature
stays valid and sendable; a new report never names it, even with the
old variable still set.

design.md success criterion 5; entrypoints.md ``schema.FEATURES``,
``schema.validate``, ``send.checked_preview``, ``builder.build_report``,
``report_flow.report_context``.
"""

from __future__ import annotations

import json

import pytest

from aipager.bot import report_flow
from aipager.report import builder, schema, send


def _built():
    """A report as it travels: built, then through JSON."""
    return json.loads(json.dumps(builder.build_report("manual")))


#: Old-shape feature lists (boundaries: alone, first, last, among others).
OLD_FEATURE_LISTS = {
    "alone": ["observers"],
    "after_another": ["miniapp", "observers"],
    "before_another": ["observers", "miniapp"],
}


# ---- the retired id stays accepted -------------------------------------------

def test_the_retired_id_is_still_a_known_feature():
    assert "observers" in schema.FEATURES


@pytest.mark.parametrize("features", list(OLD_FEATURE_LISTS.values()),
                         ids=list(OLD_FEATURE_LISTS))
def test_an_old_report_validates_unchanged(features):
    report = _built()
    report["config"]["features"] = features
    assert schema.validate(report) == (report, [])


@pytest.mark.parametrize("features", list(OLD_FEATURE_LISTS.values()),
                         ids=list(OLD_FEATURE_LISTS))
def test_an_old_report_passes_the_send_check(features):
    report = _built()
    report["config"]["features"] = features
    assert isinstance(send.checked_preview(report), bytes)


def test_an_old_report_keeps_the_retired_id_after_validation():
    report = _built()
    report["config"]["features"] = ["miniapp", "observers"]
    clean, _problems = schema.validate(report)
    assert clean["config"]["features"] == ["miniapp", "observers"]


def test_an_unknown_feature_id_is_still_refused():
    """Contrast class: keeping one retired id did not open the list."""
    report = _built()
    report["config"]["features"] = ["miniapp", "observer_bots"]
    assert schema.validate(report) != (report, [])


# ---- new reports -------------------------------------------------------------

def test_a_new_report_validates_as_built():
    report = _built()
    assert schema.validate(report) == (report, [])


def test_a_new_report_passes_the_send_check():
    assert isinstance(send.checked_preview(_built()), bytes)


@pytest.mark.parametrize("value", ["123456:AAx:42", "1:a:2,3:b:4", "garbage", " x "],
                         ids=["token_shaped", "multi_entry", "malformed", "padded"])
def test_report_context_never_names_observers_with_the_variable_set(
        mk_bot, monkeypatch, value):
    monkeypatch.setenv("OBSERVER_BOTS", value)
    assert "observers" not in report_flow.report_context(mk_bot()).features


def test_report_context_never_names_observers_with_the_variable_unset(mk_bot, monkeypatch):
    monkeypatch.delenv("OBSERVER_BOTS", raising=False)
    assert "observers" not in report_flow.report_context(mk_bot()).features


def test_report_context_still_names_other_features(mk_bot, monkeypatch):
    """The removal did not empty the feature list (Mini App on)."""
    monkeypatch.setenv("OBSERVER_BOTS", "123456:AAx:42")
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    assert "miniapp" in report_flow.report_context(mk_bot()).features


def test_a_report_built_from_the_context_never_names_observers(mk_bot, monkeypatch):
    monkeypatch.setenv("OBSERVER_BOTS", "123456:AAx:42")
    ctx = report_flow.report_context(mk_bot())
    report = builder.build_report("manual", context=ctx)
    assert "observers" not in report["config"]["features"]


def test_a_report_built_from_the_context_validates_as_built(mk_bot, monkeypatch):
    monkeypatch.setenv("OBSERVER_BOTS", "123456:AAx:42")
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    ctx = report_flow.report_context(mk_bot())
    report = json.loads(json.dumps(builder.build_report("manual", context=ctx)))
    assert schema.validate(report) == (report, [])


def test_a_report_built_with_the_variable_set_never_carries_its_value(
        mk_bot, monkeypatch):
    monkeypatch.setenv("OBSERVER_BOTS", "123456:AAcanaryTOKEN:42")
    ctx = report_flow.report_context(mk_bot())
    text = json.dumps(builder.build_report("manual", context=ctx))
    if "AAcanaryTOKEN" in text:
        pytest.fail("the OBSERVER_BOTS value reached the report", pytrace=False)
