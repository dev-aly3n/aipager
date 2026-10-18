"""Type mapping, album grouping and the caption (design.md criteria
"right method", "2-10 ... albums", "caption"; entrypoints.md media_kind,
outbox.plan_sends and outbox.caption_for)."""

from __future__ import annotations

import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

from aipager import media_kind, send_check
from aipager.bot import outbox

kit = sys.modules["_sftc_kit"]
MIB = 1024 * 1024


# ---- tiny headers --------------------------------------------------------------

def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(
        ">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    return b"\x89PNG\r\n\x1a\n" + chunk + iend


def jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp_vp8x(w, h):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + \
        (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def gif(w=4, h=4):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00;"


def ftyp(brand: bytes, compat=(b"isom",)):
    body = brand + b"\x00\x00\x02\x00" + b"".join(compat)
    return struct.pack(">I", 8 + len(body)) + b"ftyp" + body + b"\x00" * 16


def mp3_id3():
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 32


def mp3_frame():
    return b"\xff\xfb\x90\x64" + b"\x00" * 64


@pytest.fixture
def mk(tmp_path):
    def _mk(name, data):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)
    return _mk


@pytest.mark.parametrize("name,data,want", [
    ("a.png", png(640, 480), ("image", 640, 480)),
    ("a.jpg", jpeg(800, 600), ("image", 800, 600)),
    ("a.webp", webp_vp8x(300, 200), ("image", 300, 200)),
], ids=["png", "jpeg", "webp"])
def test_image_kind_and_dimensions(mk, name, data, want):
    assert media_kind.probe(mk(name, data)) == want


@pytest.mark.parametrize("name,data,kind", [
    ("a.gif", gif(), "animation"),
    ("a.mp4", ftyp(b"isom", (b"isom", b"avc1")), "video"),
    ("b.mp4", ftyp(b"mp42"), "video"),
    ("a.m4a", ftyp(b"M4A ", (b"M4A ", b"mp42")), "audio"),
    ("a.mp3", mp3_id3(), "audio"),
    ("b.mp3", mp3_frame(), "audio"),
    ("a.pdf", b"%PDF-1.4\n%...", "document"),
    ("a.txt", b"hello world\n", "document"),
    ("a.zip", b"PK\x03\x04" + b"\x00" * 30, "document"),
], ids=["gif", "mp4-isom", "mp4-mp42", "m4a", "mp3-id3", "mp3-frame", "pdf",
        "txt", "zip"])
def test_kind_by_content(mk, name, data, kind):
    assert media_kind.probe(mk(name, data))[0] == kind


def test_kind_follows_content_not_the_extension(mk):
    assert media_kind.probe(mk("photo.png", b"just text, not a png"))[0] == "document"


def test_png_content_with_a_wrong_extension_is_an_image(mk):
    assert media_kind.probe(mk("photo.dat", png(10, 20))) == ("image", 10, 20)


@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF\x00\x00",
                                  b"GIF8", b""],
                         ids=["png-sig-only", "jpeg-truncated", "riff-truncated",
                              "gif-truncated", "empty"])
def test_corrupt_header_is_a_document(mk, data):
    kind = media_kind.probe(mk("x.bin", data))[0]
    assert kind == "document"


# ---- choose_method -------------------------------------------------------------

P = send_check.PHOTO_MAX_BYTES


@pytest.mark.parametrize("w,h,size,want", [
    (640, 480, P, "photo"),
    (640, 480, P + 1, "document"),
    (5000, 5000, 1000, "photo"),
    (5000, 5001, 1000, "document"),
    (2000, 100, 1000, "photo"),
    (2001, 100, 1000, "document"),
    (100, 2000, 1000, "photo"),
    (100, 2001, 1000, "document"),
    (0, 100, 1000, "document"),
    (None, None, 1000, "document"),
], ids=["size-at-limit", "size-over", "sum-at-limit", "sum-over", "ratio-at-20",
        "ratio-over", "tall-ratio-at-20", "tall-ratio-over", "zero-side",
        "no-dimensions"])
def test_photo_limits(w, h, size, want):
    assert media_kind.choose_method("image", w, h, size, None) == want


@pytest.mark.parametrize("kind,want", [("animation", "animation"), ("video", "video"),
                                       ("audio", "audio"), ("document", "document")])
def test_other_kinds_go_as_themselves(kind, want):
    assert media_kind.choose_method(kind, None, None, 40 * MIB, None) == want


@pytest.mark.parametrize("kind", ["image", "animation", "video", "audio", "document"])
def test_attach_sends_everything_as_document(kind):
    assert media_kind.choose_method(kind, 640, 480, 1000, "attach") == "document"


def test_display_other_than_attach_keeps_the_type():
    assert media_kind.choose_method("image", 640, 480, 1000, "inline") == "photo"


# ---- plan_sends ------------------------------------------------------------------

def item(name, method, size=1000):
    return SimpleNamespace(name=name, size=size, method=method, path=f"/x/{name}",
                           kind=method, width=None, height=None)


def methods(plan):
    return [(s.method, len(s.items)) for s in plan]


def photos(n, size=1000):
    return [item(f"p{i}.png", "photo", size) for i in range(n)]


def test_one_photo_goes_alone_as_a_photo():
    assert methods(outbox.plan_sends(photos(1), "frontend", None)) == [("photo", 1)]


@pytest.mark.parametrize("n", [2, 5, 10])
def test_two_to_ten_photos_go_as_one_album(n):
    assert methods(outbox.plan_sends(photos(n), "frontend", None)) == [("media_group", n)]


def test_eleven_photos_go_as_six_and_five():
    assert methods(outbox.plan_sends(photos(11), "frontend", None)) == [
        ("media_group", 6), ("media_group", 5)]


def test_twenty_photos_go_as_ten_and_ten():
    assert methods(outbox.plan_sends(photos(20), "frontend", None)) == [
        ("media_group", 10), ("media_group", 10)]


def test_twenty_one_photos_never_leave_a_chunk_of_one():
    sizes = [len(s.items) for s in outbox.plan_sends(photos(21), "frontend", None)]
    assert min(sizes) >= 2 and max(sizes) <= 10 and sum(sizes) == 21


def test_photos_and_videos_share_an_album():
    plan = outbox.plan_sends([item("a.png", "photo"), item("b.mp4", "video")],
                             "frontend", None)
    assert methods(plan) == [("media_group", 2)]


def test_documents_and_audio_never_mix_with_photos():
    plan = outbox.plan_sends([item("a.png", "photo"), item("b.pdf", "document"),
                              item("c.png", "photo"), item("d.mp3", "audio"),
                              item("e.pdf", "document"), item("f.mp3", "audio")],
                             "frontend", None)
    assert [sorted({i.method for i in s.items}) for s in plan] == [
        ["photo"], ["document"], ["audio"]]


def test_classes_go_in_the_order_of_their_first_item():
    plan = outbox.plan_sends([item("b.pdf", "document"), item("a.png", "photo"),
                              item("c.pdf", "document"), item("d.png", "photo")],
                             "frontend", None)
    assert [s.items[0].name for s in plan] == ["b.pdf", "a.png"]


def test_order_within_an_album_is_claudes_order():
    plan = outbox.plan_sends(photos(4), "frontend", None)
    assert [i.name for i in plan[0].items] == ["p0.png", "p1.png", "p2.png", "p3.png"]


def test_single_document_goes_as_a_document():
    assert methods(outbox.plan_sends([item("a.pdf", "document")], "frontend", None)) == [
        ("document", 1)]


def test_single_audio_and_single_video_use_their_own_method():
    plan = outbox.plan_sends([item("a.mp3", "audio"), item("b.mp4", "video")],
                             "frontend", None)
    assert sorted(methods(plan)) == [("audio", 1), ("video", 1)]


def test_animation_is_never_in_an_album():
    plan = outbox.plan_sends([item("a.png", "photo"), item("b.gif", "animation"),
                              item("c.png", "photo")], "frontend", None)
    assert sorted(methods(plan)) == [("animation", 1), ("media_group", 2)]


def test_two_animations_go_one_by_one():
    plan = outbox.plan_sends([item("a.gif", "animation"), item("b.gif", "animation")],
                             "frontend", None)
    assert methods(plan) == [("animation", 1), ("animation", 1)]


def test_album_total_stays_under_50_mib():
    plan = outbox.plan_sends(photos(6, size=9 * MIB), "frontend", None)
    assert all(sum(i.size for i in s.items) <= 50 * MIB for s in plan)


def test_album_byte_cap_keeps_every_item():
    plan = outbox.plan_sends(photos(6, size=9 * MIB), "frontend", None)
    assert sum(len(s.items) for s in plan) == 6


def test_album_of_exactly_50_mib_is_one_album():
    docs = [item(f"d{i}.pdf", "document", size=10 * MIB) for i in range(5)]
    assert methods(outbox.plan_sends(docs, "frontend", None)) == [("media_group", 5)]


def test_album_one_byte_over_50_mib_is_split():
    docs = [item(f"d{i}.pdf", "document", size=10 * MIB) for i in range(4)]
    docs.append(item("d4.pdf", "document", size=10 * MIB + 1))
    assert len(outbox.plan_sends(docs, "frontend", None)) == 2


# ---- captions --------------------------------------------------------------------

def test_caption_is_the_session_label():
    assert outbox.caption_for("frontend", None, True) == "💬 frontend"


def test_claude_caption_goes_on_the_next_line_of_the_first_send():
    assert outbox.caption_for("frontend", "the chart", True) == "💬 frontend\nthe chart"


def test_later_sends_carry_only_the_label():
    assert outbox.caption_for("frontend", "the chart", False) == "💬 frontend"


def test_empty_claude_caption_adds_nothing():
    assert outbox.caption_for("frontend", "", True) == "💬 frontend"


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def test_caption_of_exactly_1024_units_is_not_cut():
    head = "💬 frontend\n"
    body = "a" * (1024 - _u16(head))
    assert outbox.caption_for("frontend", body, True) == head + body


def test_caption_over_1024_units_is_cut_to_the_limit():
    out = outbox.caption_for("frontend", "word " * 400, True)
    assert _u16(out) <= 1024


def test_cut_caption_ends_with_an_ellipsis():
    assert outbox.caption_for("frontend", "word " * 400, True).endswith("…")


def test_caption_is_cut_at_a_word():
    out = outbox.caption_for("frontend", "word " * 400, True)
    assert out[:-1].rstrip().endswith("word")


def test_caption_of_1025_units_is_cut():
    head = "💬 frontend\n"
    body = "ab " * 400
    body = body[: 1025 - _u16(head)]
    assert outbox.caption_for("frontend", body, True) != head + body


def test_caption_counts_emoji_as_two_units():
    head = "💬 frontend\n"
    # 1024 - len(head) units of emoji pairs + spaces: 600 emoji alone is 1200 units
    out = outbox.caption_for("frontend", "🙂 " * 400, True)
    assert _u16(out) <= 1024 and out.startswith(head)


def test_plan_puts_the_caption_on_the_first_send_only():
    plan = outbox.plan_sends([item("a.png", "photo"), item("b.pdf", "document")],
                             "frontend", "here you go")
    assert [s.caption for s in plan] == ["💬 frontend\nhere you go", "💬 frontend"]


def test_album_caption_is_on_the_send():
    plan = outbox.plan_sends(photos(3), "frontend", "three")
    assert plan[0].caption == "💬 frontend\nthree"
