"""The Mini App's bundled fonts: the files, their licenses, the route that
serves them and the stylesheet that uses them.

Persian, Arabic and Vietnamese get the font the project site uses for
them (Vazirmatn, Noto Sans Arabic, Be Vietnam Pro), served by the daemon
at ``/fonts/<name>`` from a fixed list, never from another host.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from aipager.miniapp import fonts
from aipager.miniapp.fonts import FONT_FILES, FONTS_DIR
from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.static import index_html
from aipager.miniapp.static._styles import CSS
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

_SYS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'


def _family(name: str) -> str:
    """``be-vietnam-pro-1.002-400-latin.woff2`` -> ``be-vietnam-pro``."""
    m = re.match(r"^([a-z-]+?)-\d", name)
    assert m, name
    return m.group(1)


# ---- the files ------------------------------------------------------------------

def test_the_list_is_exactly_the_woff2_files_in_the_folder():
    on_disk = {p.name for p in FONTS_DIR.glob("*.woff2")}
    assert on_disk == set(FONT_FILES)
    assert len(FONT_FILES) == 10


@pytest.mark.parametrize("name", sorted(FONT_FILES))
def test_every_font_is_a_woff2_file(name):
    data = (FONTS_DIR / name).read_bytes()
    assert data[:4] == b"wOF2"
    # The length field of the WOFF2 header is the file's length.
    assert int.from_bytes(data[8:12], "big") == len(data)


@pytest.mark.parametrize("name", sorted(FONT_FILES))
def test_every_font_has_its_license_beside_it(name):
    lic = FONTS_DIR / (_family(name) + "-OFL.txt")
    assert lic.is_file(), f"{name} has no {lic.name} beside it"
    text = lic.read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in text
    assert text.startswith("Copyright ")


def test_every_license_belongs_to_a_shipped_font():
    families = {_family(n) for n in FONT_FILES}
    assert {p.name[:-len("-OFL.txt")] for p in FONTS_DIR.glob("*-OFL.txt")} == families
    assert families == {"vazirmatn", "noto-sans-arabic", "be-vietnam-pro"}


def test_the_folder_holds_only_fonts_licenses_and_the_readme():
    names = {p.name for p in FONTS_DIR.iterdir()}
    others = {n for n in names if not (n.endswith(".woff2") or n.endswith("-OFL.txt"))}
    assert others == {"README.md"}


def test_the_readme_names_every_file_with_its_size_and_sha256():
    readme = (FONTS_DIR / "README.md").read_text(encoding="utf-8")
    rows = dict((m.group(1), (int(m.group(2).replace(",", "")), m.group(3))) for m in re.finditer(
        r"^\| `([^`]+\.woff2)` \| ([\d,]+) \| `([0-9a-f]{64})` \|$", readme, re.M))
    assert set(rows) == set(FONT_FILES)
    for name, (size, sha) in rows.items():
        data = (FONTS_DIR / name).read_bytes()
        assert (len(data), hashlib.sha256(data).hexdigest()) == (size, sha), name


def test_the_fonts_stay_small():
    total = sum((FONTS_DIR / n).stat().st_size for n in FONT_FILES)
    assert total <= 300_000, total


# ---- the route ------------------------------------------------------------------

@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                  members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    return MiniAppServer(bot, registry, port=8765)


async def _get_all(server, paths):
    client = TestClient(TestServer(server._build_app()))
    await client.start_server()
    out = []
    try:
        for p in paths:
            resp = await client.get(URL(p, encoded=True))
            out.append((resp.status, resp.headers.get("Content-Type"),
                        resp.headers.get("Cache-Control"), await resp.read()))
    finally:
        await client.close()
    return out


def test_the_route_serves_every_listed_font_without_auth(server, run_async):
    """No initData header: a stylesheet fetches fonts without one."""
    names = sorted(FONT_FILES)
    got = run_async(_get_all(server, ["/fonts/" + n for n in names]))
    for name, (status, ctype, cache, body) in zip(names, got):
        assert status == 200, name
        assert ctype == "font/woff2", name
        assert cache == "public,max-age=31536000,immutable", name
        assert body == (FONTS_DIR / name).read_bytes(), name


_REFUSED = {
    "readme": "/fonts/README.md",                # in the folder, not on the list
    "license": "/fonts/vazirmatn-OFL.txt",
    "unknown": "/fonts/nope.woff2",
    "upper": "/fonts/VAZIRMATN-33.003-ARABIC.WOFF2",
    "suffix": "/fonts/vazirmatn-33.003-arabic.woff2.bak",
    "up1": "/fonts/..%2Ffonts.py",
    "up2": "/fonts/..%2F..%2Fserver.py",
    "up2-dots": "/fonts/%2e%2e%2f%2e%2e%2fserver.py",
    "backslash": "/fonts/..%5C..%5Cserver.py",
    "absolute": "/fonts/%2Fetc%2Fpasswd",
    "empty": "/fonts/",
    "plain-up": "/fonts/../server.py",
    "dot": "/fonts/./vazirmatn-OFL.txt",
}


@pytest.mark.parametrize("path", list(_REFUSED.values()), ids=list(_REFUSED))
def test_the_route_refuses_anything_not_on_the_list(server, run_async, path):
    [(status, _ctype, _cache, body)] = run_async(_get_all(server, [path]))
    assert status == 404
    assert b"wOF2" not in body and b"Copyright" not in body and b"import" not in body


def test_an_unlisted_name_never_reaches_the_cache():
    fonts._read.cache_clear()
    for i in range(50):
        assert fonts.font_bytes(f"x{i}.woff2") is None
    assert fonts._read.cache_info().currsize == 0
    assert fonts.font_bytes("vazirmatn-33.003-arabic.woff2")[:4] == b"wOF2"
    assert fonts._read.cache_info().currsize == 1


# ---- the stylesheet -------------------------------------------------------------

def _faces(css: str) -> list[dict]:
    out = []
    for body in re.findall(r"@font-face \{([^}]*)\}", css):
        decls = dict((k.strip(), v.strip()) for k, _, v in
                     (d.partition(":") for d in body.split(";") if ":" in d))
        out.append(decls)
    return out


def test_every_font_has_one_face_with_a_range_and_swap():
    faces = _faces(CSS)
    srcs = [re.fullmatch(r'url\("/fonts/([^"]+)"\) format\("woff2"\)', f["src"]).group(1)
            for f in faces]
    assert sorted(srcs) == sorted(FONT_FILES)
    for f in faces:
        assert f["font-display"] == "swap"
        assert re.fullmatch(r"U\+[0-9A-F]{4,5}(-[0-9A-F]{4,5})?(, U\+[0-9A-F]{4,5}(-[0-9A-F]{4,5})?)*",
                            f["unicode-range"]), f["unicode-range"]


def test_each_face_names_its_family_and_weight():
    want = {"vazirmatn": '"Vazirmatn"', "noto-sans-arabic": '"Noto Sans Arabic"',
            "be-vietnam-pro": '"Be Vietnam Pro"'}
    for f in _faces(CSS):
        name = re.search(r"/fonts/([^\"]+)", f["src"]).group(1)
        assert f["font-family"] == want[_family(name)], name
        w = re.search(r"-(\d00)-", name)
        assert f["font-weight"] == (w.group(1) if w else "400 700"), name


def test_the_arabic_faces_cover_the_persian_digits_and_signs():
    for f in _faces(CSS):
        if "arabic" in f["src"]:
            assert "U+0600-06FF" in f["unicode-range"]     # ۰-۹ ٫ ٬ ٪ ، ؟


def _lang_rule(code: str) -> dict:
    m = re.search(r":root:lang\(" + re.escape(code) + r"\) \{([^}]*)\}", CSS)
    assert m, f"no :lang({code}) rule"
    return dict((k.strip(), " ".join(v.split())) for k, _, v in
                (d.partition(":") for d in m.group(1).split(";") if ":" in d))


@pytest.mark.parametrize("code,first", [
    ("fa", '"Vazirmatn"'), ("ar", '"Noto Sans Arabic"'), ("vi", '"Be Vietnam Pro"'),
    ("ko", '"Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic"'),
    ("zh", '"PingFang SC", "Noto Sans SC", "Microsoft YaHei"'),
])
def test_each_script_gets_its_font_then_the_system_stack(code, first):
    assert _lang_rule(code)["--font"] == first + ", " + _SYS


@pytest.mark.parametrize("code,font", [("fa", '"Vazirmatn"'), ("ar", '"Noto Sans Arabic"')])
def test_code_in_an_arabic_script_page_keeps_the_script_font_behind_it(code, font):
    assert _lang_rule(code)["--mono"] == "ui-monospace, SFMono-Regular, Menlo, " + font + ", monospace"


def test_other_languages_keep_the_system_font():
    assert sorted(re.findall(r":lang\(([^)]+)\)", CSS)) == ["ar", "fa", "ko", "vi", "zh"]
    assert ('--font: ' + _SYS + ";") in CSS
    for code in ("ko", "zh", "vi"):
        assert "--mono" not in _lang_rule(code)


def test_the_page_carries_the_faces():
    page = index_html()
    assert page.count("@font-face") == len(FONT_FILES)
    assert "fonts.googleapis" not in page and "gstatic" not in page
