"""hook_spill's public functions against entrypoints.md: folder checks,
write, read refusals, remove, sweep and the pointer format."""

from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest

from aipager.dtach import hook_spill
from tests.integration.big_hook_events._kit import (
    SECRET,
    foreign_fstat,
    own_dir,
    plant,
    spill,
)

B = "/unused-base"  # spill_dir ignores its base under the autouse fixture


def _hex():
    return os.urandom(16).hex()


# ---- ensure_dir -----------------------------------------------------------

def test_ensure_creates(tmp_path):
    assert hook_spill.ensure_dir(B) == ""


def test_ensure_mode_0700(tmp_path):
    hook_spill.ensure_dir(B)
    assert stat.S_IMODE(os.lstat(spill(tmp_path)).st_mode) == 0o700


def test_ensure_tightens(tmp_path):
    spill(tmp_path).mkdir(mode=0o755)
    os.chmod(spill(tmp_path), 0o755)
    hook_spill.ensure_dir(B)
    assert stat.S_IMODE(os.lstat(spill(tmp_path)).st_mode) == 0o700


def test_ensure_foreign(tmp_path, monkeypatch):
    spill(tmp_path).mkdir(mode=0o700)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.ensure_dir(B) != ""


def test_ensure_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    os.chmod(real, 0o755)
    spill(tmp_path).symlink_to(real)
    assert hook_spill.ensure_dir(B) != ""


def test_ensure_symlink_target_kept(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    os.chmod(real, 0o755)
    spill(tmp_path).symlink_to(real)
    hook_spill.ensure_dir(B)
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o755


def test_ensure_regular_file(tmp_path):
    spill(tmp_path).write_text("x")
    assert hook_spill.ensure_dir(B) != ""


def test_ensure_no_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_spill, "spill_dir", lambda _b: tmp_path / "no" / "spill")
    assert hook_spill.ensure_dir(B) != ""


# ---- write_spill ----------------------------------------------------------

def test_write_name_pattern(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.NAME_RE.match(hook_spill.write_spill(B, b"{}"))


def test_write_body(tmp_path):
    own_dir(tmp_path)
    name = hook_spill.write_spill(B, b'{"a": 1}')
    assert (spill(tmp_path) / name).read_bytes() == b'{"a": 1}'


def test_write_mode_0600(tmp_path):
    own_dir(tmp_path)
    name = hook_spill.write_spill(B, b"{}")
    assert stat.S_IMODE(os.lstat(spill(tmp_path) / name).st_mode) == 0o600


def test_write_names_differ(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.write_spill(B, b"{}") != hook_spill.write_spill(B, b"{}")


def test_write_no_dir(tmp_path):
    assert hook_spill.write_spill(B, b"{}") is None


def test_write_no_dir_not_made(tmp_path):
    hook_spill.write_spill(B, b"{}")
    assert not os.path.lexists(spill(tmp_path))


def test_write_foreign_dir(tmp_path, monkeypatch):
    own_dir(tmp_path)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.write_spill(B, b"{}") is None


@pytest.mark.parametrize("mode", [0o750, 0o710, 0o701, 0o702], ids=["g5", "g1", "o1", "o2"])
def test_write_loose_dir(tmp_path, mode):
    own_dir(tmp_path)
    os.chmod(spill(tmp_path), mode)
    assert hook_spill.write_spill(B, b"{}") is None


def test_write_loose_dir_empty(tmp_path):
    own_dir(tmp_path)
    os.chmod(spill(tmp_path), 0o750)
    hook_spill.write_spill(B, b"{}")
    assert os.listdir(spill(tmp_path)) == []


def test_write_symlink_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    spill(tmp_path).symlink_to(real)
    assert hook_spill.write_spill(B, b"{}") is None


def test_write_symlink_dir_empty(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    spill(tmp_path).symlink_to(real)
    hook_spill.write_spill(B, b"{}")
    assert os.listdir(real) == []


def test_write_at_cap(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.write_spill(B, b"x" * hook_spill.SPILL_MAX_BYTES)


def test_write_over_cap(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.write_spill(B, b"x" * (hook_spill.SPILL_MAX_BYTES + 1)) is None


def test_write_over_cap_no_file(tmp_path):
    own_dir(tmp_path)
    hook_spill.write_spill(B, b"x" * (hook_spill.SPILL_MAX_BYTES + 1))
    assert os.listdir(spill(tmp_path)) == []


def test_write_planted(tmp_path, monkeypatch):
    name = plant(tmp_path, b"planted")
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    assert hook_spill.write_spill(B, b"{}") is None


def test_write_planted_kept(tmp_path, monkeypatch):
    name = plant(tmp_path, b"planted")
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    hook_spill.write_spill(B, b"{}")
    assert (spill(tmp_path) / name).read_bytes() == b"planted"


def test_write_planted_link(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(b"orig")
    own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    (spill(tmp_path) / name).symlink_to(target)
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    hook_spill.write_spill(B, b"{}")
    assert target.read_bytes() == b"orig"


def test_write_error_none(tmp_path, monkeypatch):
    own_dir(tmp_path)

    def boom(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(hook_spill, "_write", boom)
    assert hook_spill.write_spill(B, b"{}") is None


def test_write_error_cleans(tmp_path, monkeypatch):
    own_dir(tmp_path)

    def boom(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(hook_spill, "_write", boom)
    hook_spill.write_spill(B, b"{}")
    assert os.listdir(spill(tmp_path)) == []


def test_write_error_midway_cleans(tmp_path, monkeypatch):
    own_dir(tmp_path)
    calls = []

    def second_fails(fd, data):
        calls.append(1)
        if len(calls) > 1:
            raise OSError(28, "No space left on device")
        return os.write(fd, bytes(data[:100]))

    monkeypatch.setattr(hook_spill, "_write", second_fails)
    hook_spill.write_spill(B, b"y" * 1000)
    assert os.listdir(spill(tmp_path)) == []


def test_write_short_writes(tmp_path, monkeypatch):
    """Error guessing: a write that takes fewer bytes than offered."""
    own_dir(tmp_path)
    monkeypatch.setattr(hook_spill, "_write",
                        lambda fd, data: os.write(fd, bytes(data[:777])))
    body = bytes(range(256)) * 40
    name = hook_spill.write_spill(B, body)
    assert (spill(tmp_path) / name).read_bytes() == body


# ---- pointer / parse_pointer ---------------------------------------------

def test_pointer_shape(tmp_path):
    name = "ev-" + _hex() + ".json"
    assert json.loads(hook_spill.pointer(name)) == {"type": "aipager_spill", "name": name}


def test_pointer_small(tmp_path):
    assert len(hook_spill.pointer("ev-" + _hex() + ".json")) <= 512


def test_parse_roundtrip(tmp_path):
    name = "ev-" + _hex() + ".json"
    assert hook_spill.parse_pointer(hook_spill.pointer(name)) == name


def test_parse_event_none(tmp_path):
    data = json.dumps({"hook_event_name": "Stop", "session": "claude-x"}).encode()
    assert hook_spill.parse_pointer(data) is None


def test_parse_mentions_type_none(tmp_path):
    data = json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                       "msg": "aipager_spill"}).encode()
    assert hook_spill.parse_pointer(data) is None


def test_parse_bad_json(tmp_path):
    assert hook_spill.parse_pointer(b'{"type": "aipager_spill", ') is None


def test_parse_not_dict(tmp_path):
    assert hook_spill.parse_pointer(b'["aipager_spill"]') is None


def test_parse_over_512(tmp_path):
    data = json.dumps({"type": "aipager_spill", "name": "ev-" + _hex() + ".json",
                       "pad": "x" * 600}).encode()
    assert hook_spill.parse_pointer(data) is None


def test_parse_non_str_name(tmp_path):
    assert hook_spill.parse_pointer(b'{"type": "aipager_spill", "name": 7}') == 7


def test_parse_binary(tmp_path):
    assert hook_spill.parse_pointer(b"\xff\xfeaipager_spill\x00") is None


# ---- read_spill: success --------------------------------------------------

def test_read_ok(tmp_path):
    name = plant(tmp_path, b'{"k": "v"}')
    assert hook_spill.read_spill(B, name) == (b'{"k": "v"}', "")


def test_read_deletes(tmp_path):
    name = plant(tmp_path, b"{}")
    hook_spill.read_spill(B, name)
    assert not os.path.lexists(spill(tmp_path) / name)


def test_read_at_cap(tmp_path):
    body = b"z" * hook_spill.SPILL_MAX_BYTES
    name = plant(tmp_path, body)
    assert hook_spill.read_spill(B, name)[0] == body


def test_read_empty_file(tmp_path):
    name = plant(tmp_path, b"")
    assert hook_spill.read_spill(B, name)[0] in (b"", None)


# ---- read_spill: refusals -------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../x.json", "ev-" + "0" * 32 + ".json/..", "EV-" + "0" * 32 + ".json",
    "ev-" + "A" * 32 + ".json", "ev-" + "0" * 31 + ".json", "ev-" + "0" * 33 + ".json",
    "ev-" + "0" * 32 + ".JSON", "ev-" + "0" * 32 + ".json.x", "x-ev-" + "0" * 32 + ".json",
    "", "ev-" + "g" * 32 + ".json",
], ids=["dotdot", "trail", "upper-ev", "upper-hex", "hex31", "hex33", "ext", "suffix",
        "prefix", "empty", "nonhex"])
def test_read_bad_name(tmp_path, bad):
    own_dir(tmp_path)
    assert hook_spill.read_spill(B, bad)[0] is None


@pytest.mark.parametrize("bad", [None, 7, ["x"], {"a": 1}, b"ev-x.json", 1.5, True],
                         ids=["none", "int", "list", "dict", "bytes", "float", "bool"])
def test_read_non_str(tmp_path, bad):
    own_dir(tmp_path)
    assert hook_spill.read_spill(B, bad)[0] is None


def test_read_bad_name_reason(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.read_spill(B, "../x.json")[1] != ""


def test_read_dotdot_outside_kept(tmp_path):
    own_dir(tmp_path)
    (tmp_path / "x.json").write_text(SECRET)
    hook_spill.read_spill(B, "../x.json")
    assert (tmp_path / "x.json").read_text() == SECRET


def test_read_abs_path(tmp_path):
    own_dir(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    victim = out / ("ev-" + _hex() + ".json")
    victim.write_text(SECRET)
    assert hook_spill.read_spill(B, str(victim))[0] is None


def test_read_abs_path_kept(tmp_path):
    own_dir(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    victim = out / ("ev-" + _hex() + ".json")
    victim.write_text(SECRET)
    hook_spill.read_spill(B, str(victim))
    assert victim.read_text() == SECRET


def test_read_outside_same_name(tmp_path):
    """A valid name present only outside the folder is not read from there."""
    own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    (tmp_path / name).write_text(SECRET)
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_outside_same_name_kept(tmp_path):
    own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    (tmp_path / name).write_text(SECRET)
    hook_spill.read_spill(B, name)
    assert (tmp_path / name).read_text() == SECRET


def test_read_bad_name_file_stays(tmp_path):
    """A non-matching name is never acted on, even inside the folder."""
    d = own_dir(tmp_path)
    (d / "notes.json").write_text("x")
    hook_spill.read_spill(B, "notes.json")
    assert (d / "notes.json").exists()


def test_read_missing(tmp_path):
    own_dir(tmp_path)
    assert hook_spill.read_spill(B, "ev-" + _hex() + ".json")[0] is None


def test_read_no_dir(tmp_path):
    assert hook_spill.read_spill(B, "ev-" + _hex() + ".json")[0] is None


def _link(tmp_path):
    d = own_dir(tmp_path)
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"session": "claude-x", "s": SECRET}))
    name = "ev-" + _hex() + ".json"
    (d / name).symlink_to(target)
    return d / name, target


def test_read_symlink(tmp_path):
    link, _t = _link(tmp_path)
    assert hook_spill.read_spill(B, link.name)[0] is None


def test_read_symlink_deleted(tmp_path):
    link, _t = _link(tmp_path)
    hook_spill.read_spill(B, link.name)
    assert not os.path.lexists(link)


def test_read_symlink_target_kept(tmp_path):
    link, target = _link(tmp_path)
    before = target.read_bytes()
    hook_spill.read_spill(B, link.name)
    assert target.read_bytes() == before


def test_read_dangling_symlink_deleted(tmp_path):
    d = own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    (d / name).symlink_to(tmp_path / "nowhere")
    hook_spill.read_spill(B, name)
    assert not os.path.lexists(d / name)


def _fifo(tmp_path):
    d = own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    os.mkfifo(d / name, 0o600)
    return d / name


def _read_with_deadline(name, path):
    """read_spill on a FIFO must not block; if it does, unblock it and fail."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", hook_spill.read_spill(B, name)),
                         daemon=True)
    t.start()
    t.join(5)
    if t.is_alive():
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        t.join(5)
        pytest.fail("read_spill blocked on a FIFO")
    return out["r"]


def test_read_fifo(tmp_path):
    p = _fifo(tmp_path)
    assert _read_with_deadline(p.name, p)[0] is None


def test_read_fifo_deleted(tmp_path):
    p = _fifo(tmp_path)
    _read_with_deadline(p.name, p)
    assert not os.path.lexists(p)


def test_read_subdir(tmp_path):
    d = own_dir(tmp_path)
    name = "ev-" + _hex() + ".json"
    (d / name).mkdir()
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_foreign_file(tmp_path, monkeypatch):
    name = plant(tmp_path, SECRET.encode())
    foreign_fstat(monkeypatch)
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_foreign_file_deleted(tmp_path, monkeypatch):
    name = plant(tmp_path, SECRET.encode())
    foreign_fstat(monkeypatch)
    hook_spill.read_spill(B, name)
    assert not os.path.lexists(spill(tmp_path) / name)


def test_read_over_cap_small(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    name = plant(tmp_path, b"x" * 1025)
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_at_small_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    name = plant(tmp_path, b"x" * 1024)
    assert hook_spill.read_spill(B, name)[0] == b"x" * 1024


def test_read_over_cap(tmp_path):
    name = plant(tmp_path, b"x" * (hook_spill.SPILL_MAX_BYTES + 1))
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_over_cap_deleted(tmp_path):
    name = plant(tmp_path, b"x" * (hook_spill.SPILL_MAX_BYTES + 1))
    hook_spill.read_spill(B, name)
    assert not os.path.lexists(spill(tmp_path) / name)


def test_read_foreign_dir(tmp_path, monkeypatch):
    name = plant(tmp_path, b"{}")
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_foreign_dir_untouched(tmp_path, monkeypatch):
    name = plant(tmp_path, b"{}")
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    hook_spill.read_spill(B, name)
    assert (spill(tmp_path) / name).exists()


def test_read_loose_dir(tmp_path):
    name = plant(tmp_path, b"{}")
    os.chmod(spill(tmp_path), 0o750)
    assert hook_spill.read_spill(B, name)[0] is None


def test_read_loose_dir_untouched(tmp_path):
    name = plant(tmp_path, b"{}")
    os.chmod(spill(tmp_path), 0o750)
    hook_spill.read_spill(B, name)
    assert (spill(tmp_path) / name).exists()


def _linked_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    name = "ev-" + _hex() + ".json"
    (real / name).write_text("{}")
    spill(tmp_path).symlink_to(real)
    return real / name


def test_read_symlinked_dir(tmp_path):
    f = _linked_dir(tmp_path)
    assert hook_spill.read_spill(B, f.name)[0] is None


def test_read_symlinked_dir_kept(tmp_path):
    f = _linked_dir(tmp_path)
    hook_spill.read_spill(B, f.name)
    assert f.exists()


# ---- remove_spill ---------------------------------------------------------

def test_remove_match(tmp_path):
    name = plant(tmp_path, b"{}")
    hook_spill.remove_spill(B, name)
    assert not os.path.lexists(spill(tmp_path) / name)


def test_remove_non_match_kept(tmp_path):
    d = own_dir(tmp_path)
    (d / "keep.txt").write_text("x")
    hook_spill.remove_spill(B, "keep.txt")
    assert (d / "keep.txt").exists()


def test_remove_dotdot_kept(tmp_path):
    own_dir(tmp_path)
    (tmp_path / "x.json").write_text("x")
    hook_spill.remove_spill(B, "../x.json")
    assert (tmp_path / "x.json").exists()


def test_remove_missing_no_raise(tmp_path):
    own_dir(tmp_path)
    hook_spill.remove_spill(B, "ev-" + _hex() + ".json")


def test_remove_no_dir_no_raise(tmp_path):
    hook_spill.remove_spill(B, "ev-" + _hex() + ".json")


# ---- sweep ----------------------------------------------------------------

def _aged(tmp_path, age, name=None, body=b"{}"):
    name = plant(tmp_path, body, name)
    t = time.time() - age
    os.utime(spill(tmp_path) / name, (t, t))
    return spill(tmp_path) / name


def test_sweep_old(tmp_path):
    p = _aged(tmp_path, 301)
    hook_spill.sweep(B)
    assert not p.exists()


def test_sweep_fresh(tmp_path):
    p = _aged(tmp_path, 299)
    hook_spill.sweep(B)
    assert p.exists()


def test_sweep_now_arg(tmp_path):
    p = _aged(tmp_path, 0)
    hook_spill.sweep(B, now=time.time() + 400)
    assert not p.exists()


def test_sweep_non_match_kept(tmp_path):
    p = _aged(tmp_path, 3600, name="other.json")
    hook_spill.sweep(B)
    assert p.exists()


def test_sweep_count(tmp_path):
    _aged(tmp_path, 3600)
    _aged(tmp_path, 3600)
    _aged(tmp_path, 10)
    assert hook_spill.sweep(B) == 2


def test_sweep_old_link(tmp_path):
    d = own_dir(tmp_path)
    target = tmp_path / "t"
    target.write_text("keep")
    name = "ev-" + _hex() + ".json"
    (d / name).symlink_to(target)
    t = time.time() - 3600
    os.utime(d / name, (t, t), follow_symlinks=False)
    hook_spill.sweep(B)
    assert not os.path.lexists(d / name)


def test_sweep_link_target_kept(tmp_path):
    d = own_dir(tmp_path)
    target = tmp_path / "t"
    target.write_text("keep")
    t = time.time() - 3600
    os.utime(target, (t, t))
    name = "ev-" + _hex() + ".json"
    (d / name).symlink_to(target)
    hook_spill.sweep(B)
    assert target.read_text() == "keep"


def test_sweep_fresh_fifo(tmp_path):
    p = _fifo(tmp_path)
    hook_spill.sweep(B)
    assert not os.path.lexists(p)


def test_sweep_no_dir(tmp_path):
    assert hook_spill.sweep(B) == 0

