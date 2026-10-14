"""Black-box: English, a left-to-right language and the left-to-right twin
of fa/ar/qps-rtl (``tr.directed(False)``) render byte-identical to the
previous release (design.md success criterion "English golden unchanged;
left-to-right twin output byte-identical").

``ltr_bytes_main_8c96963.json`` was recorded by running main at 8c96963
(before this change) in a separate interpreter over the same cases: each
row is a language, an output kind, a pattern (or a plural count) and its
parameters, with what main rendered."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import i18n
from aipager.i18n import Command, Plural, Safe, Span

_ROWS = json.loads((Path(__file__).parent / "ltr_bytes_main_8c96963.json")
                   .read_text(encoding="utf-8"))
_PLURAL = Plural("zz.agents", one="{n} agent on {s}", other="{n} agents on {s}\nmore")
_TYPES = {"str": str, "int": int, "safe": Safe, "span": Span, "command": Command}


def _tr(lang):
    if lang.endswith("@ltr"):
        return i18n.get(lang.removesuffix("@ltr")).directed(False)
    return i18n.get(lang)


def _params(row):
    return {k: _TYPES[t](v) for k, (t, v) in row["params"].items()}


def _render(row):
    tr = _tr(row["lang"])
    if "plural" in row:
        return str(tr.n(_PLURAL, row["plural"], kind=row["kind"], **_params(row)))
    return str(getattr(tr, row["kind"])("zz.case", row["pattern"], **_params(row)))


def _id(i, row):
    return f"{i}-{row['lang']}-{row['kind']}"


def test_the_recording_covers_every_left_to_right_rendering():
    assert {r["lang"] for r in _ROWS} == {"en", "ru", "fa@ltr", "ar@ltr", "qps-rtl@ltr"}


@pytest.mark.parametrize("row", _ROWS, ids=[_id(i, r) for i, r in enumerate(_ROWS)])
def test_left_to_right_rendering_is_byte_identical_to_main(row):
    assert _render(row) == row["out"]


@pytest.mark.parametrize("code", ("fa", "ar", "qps-rtl"))
def test_the_twin_never_ends_a_line_with_a_mark(code):
    out = i18n.get(code).directed(False).text("zz.case", "x\ny {name}\n\nz", name="x1")
    assert not any(line.endswith("\u200f") for line in out.split("\n"))


@pytest.mark.parametrize("code", ("en", "ru", "de"))
def test_a_left_to_right_language_never_holds_a_mark(code):
    out = i18n.get(code).html("zz.case", "x\ny {name}\n<pre>p</pre>\n- z", name="x1")
    assert "\u200f" not in out and "\u200e" not in out
