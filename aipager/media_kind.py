"""What kind of file a file is, from its first bytes, and how Telegram
should get it.

Files Claude sends (SendUserFile) go to Telegram as a photo, an animation,
a video, an audio file or a document. The kind is read from the file's own
header, never its name: a PNG, JPEG or WebP is an image (with its width and
height, which Telegram's photo limits need), a GIF an animation, an MP4 a
video, an MP3 or M4A an audio file, anything else a document. Stdlib only,
the first 64 KB read (a JPEG's segments are then walked by their lengths,
up to Telegram's 10 MB photo limit), and a header that cannot be read is a
document.
"""

from __future__ import annotations

import struct

from aipager.send_check import (
    FILE_MAX_BYTES, PHOTO_MAX_BYTES, PHOTO_MAX_RATIO, PHOTO_MAX_SIDE_SUM,
)

KIND_IMAGE = "image"
KIND_ANIMATION = "animation"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_DOCUMENT = "document"

METHOD_PHOTO = "photo"
METHOD_ANIMATION = "animation"
METHOD_VIDEO = "video"
METHOD_AUDIO = "audio"
METHOD_DOCUMENT = "document"

_HEAD_BYTES = 64 * 1024
#: A JPEG's frame header (SOF) may follow large EXIF and ICC segments, past
#: the first 64 KB: walk at most this many segments, never past this byte.
_JPEG_MAX_SEGMENTS = 512
_JPEG_MAX_OFFSET = PHOTO_MAX_BYTES

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_VIDEO_BRANDS = frozenset({b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41",
                           b"mp42", b"avc1", b"M4V ", b"dash"})
_AUDIO_BRANDS = frozenset({b"M4A ", b"M4B "})
# JPEG markers with no SOF layout after them although they sit in the
# 0xC0-0xCF range: DHT, JPG and DAC.
_JPEG_NOT_SOF = frozenset({0xC4, 0xC8, 0xCC})


def _png(head: bytes) -> tuple[int, int] | None:
    if len(head) < 24 or not head.startswith(_PNG_SIG) or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def _jpeg(head: bytes) -> tuple[int, int] | None:
    if not head.startswith(b"\xff\xd8"):
        return None
    i = 2
    n = len(head)
    while i + 4 <= n:
        if head[i] != 0xFF:
            return None
        marker = head[i + 1]
        if marker == 0xFF:          # fill byte
            i += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2                  # standalone marker
            continue
        if marker in (0xD9, 0xDA):  # end of image, start of scan
            return None
        length = struct.unpack(">H", head[i + 2:i + 4])[0]
        if length < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in _JPEG_NOT_SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", head[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def _jpeg_file(fh) -> tuple[int, int] | None:
    """A JPEG's ``(width, height)`` read from its open file *fh*, walking
    the segments by their lengths with ``seek`` (each segment's 4-byte
    header and, for the frame header, 5 more bytes are read): a phone
    photo's EXIF thumbnail and ICC profile can push the frame header well
    past 64 KB. None when it is not found within the bounds."""
    fh.seek(0)
    if fh.read(2) != b"\xff\xd8":
        return None
    pos = 2
    for _ in range(_JPEG_MAX_SEGMENTS):
        if pos > _JPEG_MAX_OFFSET:
            return None
        fh.seek(pos)
        b = fh.read(2)
        if len(b) < 2 or b[0] != 0xFF:
            return None
        marker = b[1]
        if marker == 0xFF:          # fill byte
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2                # standalone marker
            continue
        if marker in (0xD9, 0xDA):  # end of image, start of scan
            return None
        raw = fh.read(2)
        if len(raw) < 2:
            return None
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in _JPEG_NOT_SOF:
            body = fh.read(5)
            if len(body) < 5:
                return None
            h, w = struct.unpack(">HH", body[1:5])
            return w, h
        pos += 2 + length
    return None


def _webp(head: bytes) -> tuple[int, int] | None | bool:
    """``(w, h)`` for a still WebP, False for an animated one, None for
    anything that is not a WebP."""
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    chunk = head[12:16]
    if chunk == b"VP8 ":
        if head[23:26] != b"\x9d\x01\x2a":
            return None
        w, h = struct.unpack("<HH", head[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L":
        if head[20] != 0x2F:
            return None
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        if head[20] & 0x02:         # animation flag
            return False
        w = int.from_bytes(head[24:27], "little") + 1
        h = int.from_bytes(head[27:30], "little") + 1
        return w, h
    return None


def _ftyp(head: bytes) -> str | None:
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    size = struct.unpack(">I", head[:4])[0]
    major = head[8:12]
    brands = {major}
    end = min(size, len(head)) if size >= 16 else 16
    for j in range(16, end - 3, 4):
        brands.add(head[j:j + 4])
    if major in _AUDIO_BRANDS:
        return KIND_AUDIO
    if brands & _VIDEO_BRANDS:
        return KIND_VIDEO
    if brands & _AUDIO_BRANDS:
        return KIND_AUDIO
    return None


def _mp3(head: bytes) -> bool:
    if head.startswith(b"ID3"):
        return True
    if len(head) < 3 or head[0] != 0xFF:
        return False
    b1, b2 = head[1], head[2]
    return ((b1 & 0xE0) == 0xE0          # frame sync
            and (b1 & 0x18) != 0x08      # a real MPEG version
            and (b1 & 0x06) == 0x02      # layer III
            and (b2 & 0xF0) not in (0x00, 0xF0)   # a real bitrate
            and (b2 & 0x0C) != 0x0C)     # a real sample rate


def sniff(head: bytes) -> tuple[str, int | None, int | None]:
    """``(kind, width, height)`` of a file whose first bytes are *head*."""
    dims = _png(head) or _jpeg(head)
    if dims:
        return KIND_IMAGE, dims[0], dims[1]
    webp = _webp(head)
    if webp is False:
        return KIND_DOCUMENT, None, None
    if webp:
        return KIND_IMAGE, webp[0], webp[1]
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return KIND_ANIMATION, None, None
    kind = _ftyp(head)
    if kind:
        return kind, None, None
    if _mp3(head):
        return KIND_AUDIO, None, None
    return KIND_DOCUMENT, None, None


def probe(path: str) -> tuple[str, int | None, int | None]:
    """``(kind, width, height)`` of the file at *path* (a document when it
    cannot be read)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
            jpeg = _jpeg_file(fh) if head.startswith(b"\xff\xd8") else None
    except (OSError, struct.error, ValueError):
        return KIND_DOCUMENT, None, None
    if jpeg:
        return KIND_IMAGE, jpeg[0], jpeg[1]
    try:
        return sniff(head)
    except (struct.error, IndexError, ValueError):
        return KIND_DOCUMENT, None, None


def photo_fits(width: int | None, height: int | None, size: int) -> bool:
    """Telegram's photo limits: at most 10 MB, width plus height at most
    10,000, and the long side at most 20 times the short one."""
    if not width or not height or width <= 0 or height <= 0:
        return False
    if size > PHOTO_MAX_BYTES:
        return False
    if width + height > PHOTO_MAX_SIDE_SUM:
        return False
    return max(width, height) <= PHOTO_MAX_RATIO * min(width, height)


def choose_method(kind: str, width: int | None, height: int | None, size: int,
                  display: str | None) -> str:
    """How Telegram gets a file: ``display == "attach"`` (Claude asked for
    a plain attachment) is always a document; an image is a photo only
    within the photo limits; an animation, video or audio file goes as
    itself (the 50 MB limit was checked before)."""
    if display == "attach" or size > FILE_MAX_BYTES:
        return METHOD_DOCUMENT
    if kind == KIND_IMAGE:
        return METHOD_PHOTO if photo_fits(width, height, size) else METHOD_DOCUMENT
    if kind == KIND_ANIMATION:
        return METHOD_ANIMATION
    if kind == KIND_VIDEO:
        return METHOD_VIDEO
    if kind == KIND_AUDIO:
        return METHOD_AUDIO
    return METHOD_DOCUMENT
