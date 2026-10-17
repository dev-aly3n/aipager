"""File kinds from their first bytes and the Telegram method for each
(aipager/media_kind.py)."""

from __future__ import annotations

import struct

import pytest

from aipager import media_kind as mk
from aipager.send_check import PHOTO_MAX_BYTES


def png(w=640, h=480):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00" + b"\x00" * 16)


def jpeg(w=800, h=600):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    dht = b"\xff\xc4" + struct.pack(">H", 4) + b"\x00\x00"
    sof = b"\xff\xc2" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + dht + sof + b"\xff\xda" + b"\x00" * 10


def webp_vp8(w=300, h=200):
    body = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(body)) + b"WEBP" + b"VP8 " + struct.pack("<I", len(body)) + body


def webp_vp8l(w=300, h=200):
    bits = (w - 1) | ((h - 1) << 14)
    body = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 8
    return b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8L" + struct.pack("<I", len(body)) + body


def webp_vp8x(w=300, h=200, animated=False):
    flags = 0x02 if animated else 0x00
    body = bytes([flags]) + b"\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8X" + struct.pack("<I", len(body)) + body


def ftyp(major, *compat):
    brands = major + b"\x00\x00\x00\x00" + b"".join(compat)
    return struct.pack(">I", 8 + len(brands)) + b"ftyp" + brands + b"\x00" * 16


@pytest.mark.parametrize("head, kind, dims", [
    (png(640, 480), "image", (640, 480)),
    (jpeg(800, 600), "image", (800, 600)),
    (webp_vp8(300, 200), "image", (300, 200)),
    (webp_vp8l(301, 201), "image", (301, 201)),
    (webp_vp8x(302, 202), "image", (302, 202)),
    (webp_vp8x(animated=True), "document", (None, None)),
    (b"GIF89a" + b"\x00" * 20, "animation", (None, None)),
    (b"GIF87a" + b"\x00" * 20, "animation", (None, None)),
    (ftyp(b"isom", b"iso2", b"avc1", b"mp41"), "video", (None, None)),
    (ftyp(b"mp42"), "video", (None, None)),
    (ftyp(b"M4A ", b"mp42", b"isom"), "audio", (None, None)),
    (ftyp(b"qt  "), "document", (None, None)),
    (b"ID3\x04\x00" + b"\x00" * 20, "audio", (None, None)),
    (b"\xff\xfb\x90\x64" + b"\x00" * 20, "audio", (None, None)),
    (b"%PDF-1.7\n" + b"\x00" * 20, "document", (None, None)),
    (b"", "document", (None, None)),
], ids=["png", "jpeg", "webp-vp8", "webp-vp8l", "webp-vp8x", "webp-animated",
        "gif89", "gif87", "mp4", "mp42", "m4a", "mov", "id3", "mp3-sync", "pdf",
        "empty"])
def test_sniff(head, kind, dims):
    assert mk.sniff(head) == (kind, *dims)


@pytest.mark.parametrize("head", [
    png()[:20], b"\xff\xd8\xff\xe0\x00", b"\xff\xd8\x00\x00\x00\x00",
    webp_vp8()[:24], b"RIFF\x00\x00\x00\x00WEBPVP8 \x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
    b"\xff\xd8" + b"\xff\xc0\x00\x01",
], ids=["png-short", "jpeg-short", "jpeg-no-marker", "webp-short", "webp-bad-code",
        "jpeg-bad-length"])
def test_corrupt_headers_are_documents(head, tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(head)
    assert mk.probe(str(p))[0] == "document"


def test_probe_reads_a_file_and_a_missing_one(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(png(10, 20))
    assert mk.probe(str(p)) == ("image", 10, 20)
    assert mk.probe(str(tmp_path / "gone")) == ("document", None, None)


def test_a_jpeg_that_is_not_a_photo_by_name_still_reads_by_content(tmp_path):
    p = tmp_path / "report.txt"
    p.write_bytes(jpeg(100, 50))
    assert mk.probe(str(p)) == ("image", 100, 50)


@pytest.mark.parametrize("w, h, size, method", [
    (1000, 800, 1000, "photo"),
    (1000, 800, PHOTO_MAX_BYTES, "photo"),
    (1000, 800, PHOTO_MAX_BYTES + 1, "document"),
    (5000, 5000, 1000, "photo"),
    (5001, 5000, 1000, "document"),
    (2000, 100, 1000, "photo"),
    (2001, 100, 1000, "document"),
    (0, 100, 1000, "document"),
], ids=["ok", "size-edge", "size", "sum-edge", "sum", "ratio-edge", "ratio", "zero"])
def test_photo_limits(w, h, size, method):
    assert mk.choose_method("image", w, h, size, None) == method


@pytest.mark.parametrize("kind", ["image", "animation", "video", "audio", "document"])
def test_attach_sends_everything_as_document(kind):
    assert mk.choose_method(kind, 100, 100, 1000, "attach") == "document"


def test_kinds_go_as_themselves():
    assert mk.choose_method("animation", None, None, 1000, "render") == "animation"
    assert mk.choose_method("video", None, None, 1000, None) == "video"
    assert mk.choose_method("audio", None, None, 1000, None) == "audio"
    assert mk.choose_method("document", None, None, 1000, None) == "document"


def _big_app_segments(total: int) -> bytes:
    """APP1 (EXIF) and APP2 (ICC) segments, as a phone writes them, of
    about *total* bytes: each segment at most 65,535 bytes long."""
    out = b""
    marker = 0xE1
    while len(out) < total:
        body = b"\x00" * 65000
        out += bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body
        marker = 0xE2
    return out


def test_a_jpeg_whose_frame_header_is_past_64_kb_is_a_photo(tmp_path):
    """EXIF and ICC segments push the frame header (SOF) to about 200 KB in:
    the segments are walked by their lengths, so it still reads as a
    photo with its size."""
    w, h = 1200, 900
    sof = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w)
           + b"\x03" + b"\x00" * 9)
    data = b"\xff\xd8" + _big_app_segments(200_000) + sof + b"\xff\xda" + b"\x00" * 10
    assert data.index(b"\xff\xc0") > 64 * 1024
    p = tmp_path / "phone.jpg"
    p.write_bytes(data)
    assert mk.probe(str(p)) == (mk.KIND_IMAGE, w, h)
    assert mk.choose_method(*mk.probe(str(p)), len(data), None) == "photo"


def test_a_jpeg_walk_stops_at_its_bounds(tmp_path, monkeypatch):
    """No frame header within the walk's bounds: a document, never a hang."""
    p = tmp_path / "endless.jpg"
    p.write_bytes(b"\xff\xd8" + _big_app_segments(300_000))
    assert mk.probe(str(p))[0] == mk.KIND_DOCUMENT
    monkeypatch.setattr(mk, "_JPEG_MAX_SEGMENTS", 2)
    sof = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 9, 9)
           + b"\x03" + b"\x00" * 9)
    p.write_bytes(b"\xff\xd8" + _big_app_segments(200_000) + sof)
    assert mk.probe(str(p))[0] == mk.KIND_DOCUMENT
