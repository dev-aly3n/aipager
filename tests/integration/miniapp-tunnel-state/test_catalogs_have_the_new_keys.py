"""Every one of the 14 catalogs carries the 8 new /app keys, translated
against the current English (spec requirement 4; entrypoints.md English
texts). Read from the shipped files, the way a reader of the catalog
would: a key is current when its ``src`` is the first 8 hex digits of
the sha1 of the English (``tools/i18n.py``'s documented ``src_hash``).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LOCALES = ROOT / "aipager" / "i18n" / "locales"
SOURCE = ROOT / "tools" / "i18n" / "source.jsonl"
CODES = ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru", "tr", "uk", "vi",
         "zh-Hans"]

ENGLISH = {
    "appcmd.tunnel.dns_incomplete":
        "📱 The Mini App can't connect: this machine's DNS server gave an incomplete answer "
        "when cloudflared looked up Cloudflare's servers. Set the machine to use another DNS "
        "server, for example 1.1.1.1. aipager keeps trying by itself.",
    "appcmd.tunnel.dns_lookup_failed":
        "📱 The Mini App can't connect: this machine could not look up Cloudflare's servers "
        "in DNS. Check the machine's internet connection and its DNS server, for example by "
        "switching to 1.1.1.1. aipager keeps trying by itself.",
    "appcmd.tunnel.quick_tunnel":
        "📱 The Mini App can't connect: cloudflared could not get a link from Cloudflare. "
        "Check that this machine can reach the internet. aipager keeps trying by itself.",
    "appcmd.tunnel.edge_unreachable":
        "📱 The Mini App can't connect: this machine can't reach Cloudflare's servers on "
        "port 7844 (UDP), which cloudflared needs. Check the machine's firewall or network. "
        "aipager keeps trying by itself.",
    "appcmd.tunnel.binary_missing":
        "📱 The Mini App can't connect: aipager could not download or start cloudflared, the "
        "program that makes its link. Check the machine's internet connection. aipager keeps "
        "trying by itself.",
    "appcmd.tunnel.unreachable":
        "📱 The Mini App can't connect: cloudflared is running, but its link never answered "
        "from the internet. Check the machine's network. aipager keeps trying by itself.",
    "appcmd.tunnel.other":
        "📱 The Mini App isn't working right now. Run {command} on this machine to see why. "
        "aipager keeps trying by itself.",
    "appcmd.tunnel.unavailable":
        "📱 The Mini App isn't available right now. Try /app again later.",
}
KEYS = list(ENGLISH)


def _load(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["k"]] = rec
    return out


@pytest.fixture(scope="module")
def source():
    return _load(SOURCE)


@pytest.fixture(scope="module")
def catalogs():
    return {code: _load(LOCALES / f"{code}.jsonl") for code in CODES}


def test_there_are_exactly_14_catalogs():
    assert sorted(p.stem for p in LOCALES.glob("*.jsonl")) == sorted(CODES)


@pytest.mark.parametrize("key", KEYS)
def test_source_has_the_key(source, key):
    assert key in source


@pytest.mark.parametrize("key", KEYS)
def test_source_english_is_the_documented_sentence(source, key):
    """The English may style 1.1.1.1 as code; the words are the spec's."""
    en = re.sub(r"</?code>", "", source[key]["en"])
    assert en == ENGLISH[key]


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_has_the_key(catalogs, code, key):
    assert key in catalogs[code]


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_translation_is_current(catalogs, source, code, key):
    expected = hashlib.sha1(source[key]["en"].encode("utf-8")).hexdigest()[:8]
    assert catalogs[code][key]["src"] == expected


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_translation_is_not_the_english(catalogs, source, code, key):
    value = catalogs[code][key]["v"]
    assert isinstance(value, str) and value.strip() and value != source[key]["en"]


@pytest.mark.parametrize("code", CODES)
def test_other_keeps_its_command_placeholder(catalogs, code):
    assert catalogs[code]["appcmd.tunnel.other"]["v"].count("{command}") == 1


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_translation_has_no_em_dash(catalogs, code, key):
    assert "—" not in catalogs[code][key]["v"]


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", KEYS)
def test_catalog_translation_has_no_forbidden_instruction(catalogs, code, key):
    low = catalogs[code][key]["v"].lower()
    assert [w for w in ("tailscale", "sudo", "curl", "apt-get", "brew install", "| sh")
            if w in low] == []


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("key", [k for k in KEYS if "1.1.1.1" in ENGLISH[k]])
def test_catalog_dns_translation_keeps_the_dns_server_address(catalogs, code, key):
    value = catalogs[code][key]["v"]
    assert any(a in value for a in ("1.1.1.1", "۱.۱.۱.۱", "١.١.١.١")), value


@pytest.mark.parametrize("code", CODES)
def test_catalog_edge_translation_keeps_the_port(catalogs, code):
    value = catalogs[code]["appcmd.tunnel.edge_unreachable"]["v"]
    assert "7844" in value or "۷۸۴۴" in value or "٧٨٤٤" in value
