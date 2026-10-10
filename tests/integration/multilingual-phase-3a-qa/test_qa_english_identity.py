"""Success criterion 2 (and the "English byte-identical" hard requirement):
every scenario, run in an English chat, shows exactly the bytes the base
tree showed. The base's outputs are recorded in ``golden_base.json``
(re-record with ``AIPAGER_RECORD_QA_GOLDEN=1``, see conftest.py); the
integration branch was checked equal to the base when the file was
recorded, so one golden stands for both."""

from __future__ import annotations

import pytest

import qa_p3a_scenarios as S

NAMES = sorted(S.SCENARIOS)


def test_the_golden_has_every_scenario(golden):
    assert golden["base"] == S.BASE
    assert sorted(golden["scenarios"]) == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_an_english_chat_reads_what_it_read_before(golden, scenario_out, name):
    assert scenario_out(name, "en") == golden["scenarios"][name]


def test_the_exported_constants_are_the_same_set(golden):
    assert sorted(S.constants()) == sorted(golden["constants"])


@pytest.mark.parametrize("name", sorted(S.constants()))
def test_an_exported_constant_equals_todays_english(golden, name):
    value = S.constants()[name]
    assert value == golden["constants"][name]
    assert str(value) == golden["constants"][name]
