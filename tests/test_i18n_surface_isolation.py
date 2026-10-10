"""``render_all`` renders every surface under its own monkeypatch context:
one surface pinning a value (a sibling phase's queue depth or restart
delay) must not change what a later surface shows, and the test
fixture's own patches (the conftest home and chat redirects) must
survive every surface."""

from aipager import config
from tests import i18n_surfaces


def test_one_surfaces_patches_never_reach_the_next(mk_bot, run_async, monkeypatch):
    fixture_chat = config.CHAT_ID

    def first(env):
        env.mp.setattr(config, "CHAT_ID", "pinned-by-the-first-surface")
        return [config.CHAT_ID]

    def second(env):
        return [config.CHAT_ID]

    monkeypatch.setattr(i18n_surfaces, "SURFACES", [("first", first), ("second", second)])
    out = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "en")
    assert out == {"first": ["pinned-by-the-first-surface"], "second": [fixture_chat]}
    assert config.CHAT_ID == fixture_chat
