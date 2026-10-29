"""The banned real names (as hashes) and the rename that removed them.

``tests/test_no_real_names.py`` fails on any of :data:`BANNED` in the
tree. Tests that compare a file with its bytes at an old commit read the
old bytes through :func:`as_renamed`, which applies the rename the tree
got (the owner's handle became ``alice``), so they stay byte-exact on
everything else. Neither spells a name out: the handle is recovered from
its hash.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import re
import string

#: sha256 of each banned name, lower case, two-word names joined with "-"
#: (the last two: those two-word names written as one word).
BANNED = frozenset({
    "f789c4d98a4ee924f6611a858aed07c30b6c903ad874dd6a1961b42e8c9792b5",
    "ff0b1c3627ee1305e09ac07700b6347c049411fd1931552e61098d0ee4a2d870",
    "9c1880f9229fe5e242a6c3e3794387c41c8bad966a4d0a03176860287a1c4237",
    "638f96b24c779c96de111d6facd55befdea861f2765b57cdf11ff5570db0696a",
    "fa340114498cfb0f5dfedaf79ddcf3af3e3211a852438d087a77c1a4af98c42e",
    "6b5cd2cd0bbe67956f7f3223ee714f017d2020ff229dfcf15c12fcce5b3a0442",
    "2ed120d72259eca99dc0391676aabf3464f4b2c97a7dfe8f8577fc1afbc54375",
    "4e48a499bf852d5055801be0791e918752b2db8365bd6d9855c5a6c2667798f0",
    "fbad85d1a813aff2b7d6fa1d23243ce5022720d4708998165315ddc217b0716c",
    "22a63c82a55462b7d8b566ef087b9183afcac4de622f4ddfff689294161ef0f0",
    "25630c01be4b235eb15306b30862382c048cb199e27d8d8f2d84ddb08f390c0e",
    "0ad5ecd59defc4a95abab9e6865d274d2fdb6852f03c65954dfd847e97b01243",
    "00271b8622d8d4e662be8bf34d5886d4a2f9641463989d21fbbd4698091d1c8e",
})


def sha(word: str) -> str:
    return hashlib.sha256(word.lower().encode()).hexdigest()


@functools.lru_cache(maxsize=1)
def owner_handle() -> str:
    """The one three-letter banned word (the owner's old handle), brute
    forced from its hash."""
    found = ["".join(t) for t in itertools.product(string.ascii_lowercase, repeat=3)
             if sha("".join(t)) in BANNED]
    assert len(found) == 1, len(found)
    return found[0]


def as_renamed(old: bytes) -> bytes:
    """*old* (a file's bytes at an earlier commit) with the owner's handle
    renamed to ``alice`` as the tree's rename did: the whole lower-case
    word, with letters and digits as the only word characters. (The goldens
    these tests compare hold only that form.)"""
    w = owner_handle().encode()
    return re.sub(rb"(?<![A-Za-z0-9])" + w + rb"(?![A-Za-z0-9])", b"alice", old)
