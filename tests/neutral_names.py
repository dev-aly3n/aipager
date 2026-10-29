"""The banned real names (as hashes) and the rename that removed them.

``tests/test_no_real_names.py`` fails on any of :data:`BANNED` in the
tree. Tests that compare a file with its bytes at an old commit read the
old bytes through :func:`as_renamed`, which applies the rename the tree
got (the owner's handle became ``alice``, the real chat ids became the
fakes in :data:`_ID_RENAMES`), so they stay byte-exact on everything
else. Neither spells a name or an id out: the handle is recovered from its
hash, an id is matched by the hash of its digits.

:data:`BANNED_IDS` also arms the fake-Telegram harness's tripwire
(:func:`is_real_chat_id`): no id it builds may be the operator's real
chat.
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
    "4dd473371e8ff72c7d987bb33a31752fc1cdc5a57c34495501c8b9d34f4e8f16",
    "43fb4fb14165a5d15ce7d8001809e2793e80eab9979b7e1165978e352f4c42b7",
    "5845f9ea46a1fdd2e22bc4fd1563a20790d6017dfd886f6a46dab531b3aac604",
    "e3b961ce4b33d0782ee8e1a6c55f375b9e4a983507cce87b547c06630914d225",
    "6911a816c3a7e3978f7ed16a7fad9a1e8101fe788f9ec059a462e45100eb722c",
})

#: sha256 of the decimal digits (no sign) of each real chat id.
#: ``tests/test_no_real_names.py`` fails on any run of digits with one of
#: these hashes, wherever it stands (``d…``/``g…`` scope suffixes,
#: ``group-…`` folders, a negative group id, a ``-100…`` supergroup form).
#: The first two (:data:`OPERATOR_IDS`) are the operator's DM and a group of
#: theirs; the other three were copied from live installs into regression
#: tests (two DM ids in session names, a member's id in a config).
OPERATOR_IDS = (
    "c6caa4de7936d103e9d79f847ff315bc1910585bb068d137dcc9e9ea9c7c186c",
    "e3cc2cf570331bcde5b112228b3f40f8d0c5bbdd5095bc0832cbda5e53c87af2",
)
BANNED_IDS = frozenset({
    *OPERATOR_IDS,
    "501671d977b66d669a6b7b2559ab571125cc4ab2bb207bc4f8c8bdb20a0992d4",
    "1273dd7710b51a2b2d583c8917a60b99e2c153fdf037164ae302b6cc5a696a5d",
    "c87f4d682c3f5a5704c92f69ad1b3674318e4f82e9c8d67b6c9081eaf5de8887",
})

#: The neutral id (same digit count) that replaced each banned one.
_ID_RENAMES = {
    "c6caa4de7936d103e9d79f847ff315bc1910585bb068d137dcc9e9ea9c7c186c": b"222333444",
    "e3cc2cf570331bcde5b112228b3f40f8d0c5bbdd5095bc0832cbda5e53c87af2": b"4100000001",
    "501671d977b66d669a6b7b2559ab571125cc4ab2bb207bc4f8c8bdb20a0992d4": b"333444555",
    "1273dd7710b51a2b2d583c8917a60b99e2c153fdf037164ae302b6cc5a696a5d": b"3334445556",
    "c87f4d682c3f5a5704c92f69ad1b3674318e4f82e9c8d67b6c9081eaf5de8887": b"111333555",
}

_DIGITS = re.compile(rb"(?<![0-9])[0-9]+(?![0-9])")


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


def id_forms(digits: str) -> tuple[str, ...]:
    """The ids a run of digits can stand for: itself and, after a leading
    ``100`` (a supergroup's ``-100…`` form of a group id), the rest."""
    if digits.startswith("100") and len(digits) > 3:
        return (digits, digits[3:])
    return (digits,)


def is_real_chat_id(chat_id: int | str) -> bool:
    """Whether *chat_id* (an int or its decimal text, any sign, a group id
    also in its ``-100…`` supergroup form) is one of the real chat ids in
    :data:`BANNED_IDS`."""
    digits = str(chat_id).strip().lstrip("-")
    return any(sha(d) in BANNED_IDS for d in id_forms(digits))


def _rename_id(m: re.Match) -> bytes:
    return _ID_RENAMES.get(hashlib.sha256(m.group()).hexdigest(), m.group())


def as_renamed(old: bytes) -> bytes:
    """*old* (a file's bytes at an earlier commit) renamed as the tree was:
    the owner's handle to ``alice`` (the whole lower-case word, with
    letters and digits as the only word characters; the goldens these
    tests compare hold only that form), and each real chat id, as a whole
    run of digits, to its neutral one."""
    w = owner_handle().encode()
    old = re.sub(rb"(?<![A-Za-z0-9])" + w + rb"(?![A-Za-z0-9])", b"alice", old)
    return _DIGITS.sub(_rename_id, old)
