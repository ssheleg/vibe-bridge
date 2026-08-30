"""Settings — the layer that did not exist until 2026-08-30.

Everything the bridge does was a module constant: the port, the mode, the
release channel, every timeout. A user could change nothing without editing
source, and `docs/spec/architecture.md` had promised for months that the two
network modes are "выбираются конфигом".

Three rules the tests below hold to:

* **A bad config never stops the bridge.** Unreadable, malformed, wrong types,
  from the future — each is reported and then the default is used. A remote
  control that refuses to start because of a typo in a settings file is worse
  than one running last week's settings.
* **Precedence is stated, not discovered:** environment over file over default.
* **What the panel shows is what the bridge does.** The value the settings API
  returns is the value in force, never the file's wish.
"""
from __future__ import annotations

import pytest

from vibebridge import config as cfg


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.delenv("VIBE_BRIDGE_PORT", raising=False)
    monkeypatch.delenv("VIBE_BRIDGE_MODE", raising=False)
    return tmp_path


def write(home, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ defaults

def test_defaults_apply_with_no_file_at_all(home):
    s = cfg.load()
    assert s.port == 48620
    assert s.mode == "standalone"
    assert s.release_repo == "ssheleg/vibe-bridge"
    assert s.update_enabled is True
    assert s.problems == []


def test_the_distribution_default_is_standalone(home):
    """`architecture.md` §2 calls standalone the distribution default, and it
    is the only mode that authenticates /mcp. The code shipped `gateway` —
    which disables the bearer guard — to every fresh install for months."""
    assert cfg.load().mode == "standalone"


def test_a_first_run_writes_a_commented_file_the_owner_can_edit(home):
    cfg.load(create=True)
    text = (home / "config.toml").read_text()
    assert "port" in text and "mode" in text
    assert "#" in text                      # explained, not just values
    # …and reading it back changes nothing.
    assert cfg.load().port == 48620


# ---------------------------------------------------------------- the file

def test_values_from_the_file_win_over_defaults(home):
    write(home, 'port = 9000\nmode = "gateway"\n')
    s = cfg.load()
    assert s.port == 9000 and s.mode == "gateway"


def test_nested_sections_are_read(home):
    write(home, '[update]\nenabled = false\ninterval_hours = 24\n'
                '[consent]\nask_timeout_s = 30\n')
    s = cfg.load()
    assert s.update_enabled is False
    assert s.update_interval_s == 24 * 3600
    assert s.ask_timeout_s == 30


def test_an_unknown_key_is_reported_but_not_fatal(home):
    """A typo must be visible. Silently ignoring it is how someone spends an
    hour wondering why `prot = 9000` did nothing."""
    write(home, 'prot = 9000\n')
    s = cfg.load()
    assert s.port == 48620
    assert any("prot" in p for p in s.problems)


# ------------------------------------------------------- refusing to break

def test_malformed_toml_falls_back_to_defaults_and_says_so(home):
    write(home, 'port = = = 9000\n')
    s = cfg.load()
    assert s.port == 48620
    assert s.problems and "config.toml" in s.problems[0]


def test_a_wrong_type_is_rejected_per_key_not_wholesale(home):
    """One bad value must not throw away the good ones beside it."""
    write(home, 'port = "сорок восемь тысяч"\nmode = "gateway"\n')
    s = cfg.load()
    assert s.port == 48620                  # rejected
    assert s.mode == "gateway"              # kept
    assert any("port" in p for p in s.problems)


def test_an_impossible_port_is_refused(home):
    write(home, "port = 99999\n")
    s = cfg.load()
    assert s.port == 48620 and s.problems


def test_an_unknown_mode_is_refused_rather_than_bound_blindly(home):
    write(home, 'mode = "sideways"\n')
    s = cfg.load()
    assert s.mode == "standalone"
    assert any("sideways" in p for p in s.problems)


def test_a_config_from_a_newer_version_is_named_not_guessed(home):
    """SCN-017 promised an honest message on a config-version conflict; there
    was no version field at all."""
    write(home, f"version = {cfg.VERSION + 1}\nport = 9000\n")
    s = cfg.load()
    assert s.port == 48620
    assert any("новее" in p for p in s.problems)


def test_an_unreadable_file_is_survivable(home, monkeypatch):
    write(home, "port = 9000\n")
    (home / "config.toml").chmod(0o000)
    try:
        s = cfg.load()
        assert s.port == 48620 and s.problems
    finally:
        (home / "config.toml").chmod(0o600)


# ------------------------------------------------------------ environment

def test_environment_beats_the_file(home, monkeypatch):
    write(home, "port = 9000\n")
    monkeypatch.setenv("VIBE_BRIDGE_PORT", "7000")
    assert cfg.load().port == 7000


def test_a_bad_environment_value_is_reported_not_obeyed(home, monkeypatch):
    monkeypatch.setenv("VIBE_BRIDGE_PORT", "не число")
    s = cfg.load()
    assert s.port == 48620
    assert any("VIBE_BRIDGE_PORT" in p for p in s.problems)


def test_mode_can_come_from_the_environment(home, monkeypatch):
    monkeypatch.setenv("VIBE_BRIDGE_MODE", "gateway")
    assert cfg.load().mode == "gateway"


# ------------------------------------------------------------------ writing

def test_a_setting_changed_from_the_panel_survives_a_reload(home):
    cfg.load(create=True)
    cfg.update({"mode": "gateway"})
    assert cfg.load().mode == "gateway"


def test_writing_one_setting_keeps_the_others(home):
    write(home, 'port = 9000\n[update]\ninterval_hours = 12\n')
    cfg.update({"mode": "gateway"})
    s = cfg.load()
    assert s.port == 9000 and s.update_interval_s == 12 * 3600
    assert s.mode == "gateway"


def test_writing_refuses_a_value_it_would_then_reject_on_read(home):
    cfg.load(create=True)
    with pytest.raises(ValueError):
        cfg.update({"mode": "sideways"})


def test_writing_a_sectioned_key_lands_under_its_own_header(home):
    write(home, 'port = 9000\n\n[update]\nenabled = true\n\n'
                '[consent]\nask_timeout_s = 60\n')
    cfg.update({"update_interval_s": 12})
    s = cfg.load()
    assert s.update_interval_s == 12 * 3600
    assert s.ask_timeout_s == 60 and s.port == 9000
    body = (home / "config.toml").read_text()
    # …and it must sit inside [update], not after [consent].
    assert body.index("interval_hours") < body.index("[consent]")


def test_writing_keeps_the_comments_that_explain_the_file(home):
    """The template's comments are the only documentation most owners read;
    a round-trip through a TOML writer would delete every one of them."""
    cfg.load(create=True)
    before = (home / "config.toml").read_text().count("#")
    cfg.update({"mode": "gateway", "update_enabled": False})
    after = (home / "config.toml").read_text()
    assert after.count("#") == before
    assert 'mode = "gateway"' in after and "enabled = false" in after


def test_a_key_added_to_a_section_that_exists_but_lacks_it(home):
    write(home, "[update]\nenabled = true\n")
    cfg.update({"update_interval_s": 3})
    assert cfg.load().update_interval_s == 3 * 3600


# ------------------------------------------------------------- migration

def test_an_existing_install_keeps_the_mode_it_was_running(home):
    """The default flips to `standalone` for new installs. A machine already
    running `gateway` — its robot arriving through an agentgateway on
    loopback — must NOT be switched by an update: standalone binds a different
    interface and demands a bearer the gateway does not send, so the robot
    would simply stop reaching the bridge."""
    class _State:
        mode = "gateway"

    cfg.migrate_from_state(_State())
    s = cfg.load()
    assert s.mode == "gateway"
    assert "mode" in (home / "config.toml").read_text()


def test_migration_does_not_overwrite_a_config_the_owner_already_wrote(home):
    write(home, 'mode = "standalone"\n')

    class _State:
        mode = "gateway"

    cfg.migrate_from_state(_State())
    assert cfg.load().mode == "standalone"


def test_migration_is_a_no_op_for_a_fresh_install(home):
    class _State:
        mode = "standalone"

    cfg.migrate_from_state(_State())
    assert cfg.load().mode == "standalone"
