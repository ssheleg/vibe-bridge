"""The background checker — the half that makes "обновляется сам" true.

Until this existed the bridge only updated when someone opened the panel and
pressed a button, while the README promised it updated itself. A claim in a
README is not a feature.

Two things it must not do, and both are about the journal. It must not write
a line every six hours saying nothing happened — a journal the owner learns
to scroll past is worse than no journal. And it must not repeat the same
failure forever while a laptop is closed: the first failure is news, the
twentieth identical one is noise.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from vbboot import layout
from vibebridge import update
from vibebridge.audit import AuditLog


class _State:
    def __init__(self, auto_update=True):
        self.auto_update = auto_update


@pytest.fixture()
def gear(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    audit = AuditLog(tmp_path / "journal.log")
    return root, audit


def _updater(root, audit, state=None, **kw):
    return update.AutoUpdater(
        root=root, audit=audit, state=state or _State(),
        pubkey=b"\x00" * 32, shell_version="0.1.0",
        current=lambda: "0.1.0", **kw)


def _entries(audit, tool="update"):
    return [e for e in audit.read_entries(limit=100)["entries"]
            if e["tool"] == tool]


# ------------------------------------------------------------- quiet success

def test_nothing_new_writes_nothing_to_the_journal(gear, monkeypatch):
    root, audit = gear
    monkeypatch.setattr(update, "check", lambda **kw: update.Check())
    _updater(root, audit).run_once()
    assert _entries(audit) == []


def test_an_installed_update_is_journalled(gear, monkeypatch):
    root, audit = gear
    found = update.Available("0.2.0", "https://x/p", "https://x/p.sig")
    monkeypatch.setattr(update, "check", lambda **kw: update.Check(found=found))
    monkeypatch.setattr(update, "fetch_and_install",
                        lambda *a, **kw: (True, "версия 0.2.0 установлена"))

    ok = _updater(root, audit).run_once()
    assert ok is True
    lines = _entries(audit)
    assert len(lines) == 1 and lines[0]["ok"] and "0.2.0" in lines[0]["line"]


def test_a_refused_update_is_journalled_as_a_failure(gear, monkeypatch):
    root, audit = gear
    found = update.Available("0.2.0", "https://x/p", "https://x/p.sig")
    monkeypatch.setattr(update, "check", lambda **kw: update.Check(found=found))
    monkeypatch.setattr(update, "fetch_and_install",
                        lambda *a, **kw: (False, "подпись не сошлась"))

    assert _updater(root, audit).run_once() is False
    lines = _entries(audit)
    assert len(lines) == 1 and not lines[0]["ok"]
    assert "подпись" in lines[0]["line"]


# ------------------------------------------------------- failures don't flood

def test_a_repeated_failure_is_reported_once_not_every_cycle(gear,
                                                             monkeypatch):
    """A closed laptop must not fill the journal with the same sentence."""
    root, audit = gear
    monkeypatch.setattr(update, "check",
                        lambda **kw: update.Check(error="канал недоступен"))
    up = _updater(root, audit)
    for _ in range(5):
        up.run_once()
    assert len(_entries(audit)) == 1


def test_a_different_failure_is_reported_again(gear, monkeypatch):
    root, audit = gear
    up = _updater(root, audit)
    monkeypatch.setattr(update, "check",
                        lambda **kw: update.Check(error="канал недоступен"))
    up.run_once()
    monkeypatch.setattr(update, "check",
                        lambda **kw: update.Check(error="ответ без подписи"))
    up.run_once()
    assert len(_entries(audit)) == 2


def test_recovery_is_reported_so_the_owner_knows_it_came_back(gear,
                                                              monkeypatch):
    root, audit = gear
    up = _updater(root, audit)
    monkeypatch.setattr(update, "check",
                        lambda **kw: update.Check(error="канал недоступен"))
    up.run_once()
    monkeypatch.setattr(update, "check", lambda **kw: update.Check())
    up.run_once()

    lines = _entries(audit)
    assert len(lines) == 2
    assert lines[0]["ok"] and "снова" in lines[0]["line"].lower()


# ------------------------------------------------------------------- housekeeping

def test_old_versions_are_pruned_after_a_successful_install(gear,
                                                            monkeypatch):
    """Versions accumulate forever otherwise — `prune` existed and nothing
    ever called it.

    Заглушается СКАЧИВАНИЕ, а не установка. Прежняя версия подменяла
    `fetch_and_install` целиком и проверяла, что уборку зовёт ВЫЗЫВАЮЩИЙ, —
    и тем закрепляла ровно ту развилку, из-за которой кнопка «Проверить
    обновления» ставила и не убирала. Уборка живёт в установке; здесь
    проверяется, что фоновый путь до неё доходит.
    """
    from tests.test_update import make_payload

    root, audit = gear
    for v in ("0.1.0", "0.2.0", "0.3.0", "0.4.0"):
        (root / v / "vibebridge").mkdir(parents=True)
        (root / v / "vibebridge" / "__init__.py").write_text("")
        layout.mark_installed(root, v)

    priv = Ed25519PrivateKey.generate()
    blob = make_payload("0.5.0")
    found = update.Available("0.5.0", "https://x/p", "https://x/p.sig")
    monkeypatch.setattr(update, "check", lambda **kw: update.Check(found=found))
    monkeypatch.setattr(update, "download",
                        lambda url, opener=None: (
                            blob if url.endswith("/p") else priv.sign(blob)))

    updater = update.AutoUpdater(
        root=root, audit=audit, state=_State(),
        pubkey=update.public_key_bytes(priv.public_key()),
        shell_version="9.9.9", current=lambda: "0.1.0")
    assert updater.run_once() is True
    assert layout.installed(root) == ["0.4.0", "0.5.0"]


def test_a_failed_install_prunes_nothing(gear, monkeypatch):
    root, audit = gear
    for v in ("0.1.0", "0.2.0", "0.3.0"):
        (root / v / "vibebridge").mkdir(parents=True)
        layout.mark_installed(root, v)
    found = update.Available("0.4.0", "https://x/p", "https://x/p.sig")
    monkeypatch.setattr(update, "check", lambda **kw: update.Check(found=found))
    monkeypatch.setattr(update, "fetch_and_install",
                        lambda *a, **kw: (False, "не сошлась"))

    _updater(root, audit).run_once()
    assert layout.installed(root) == ["0.1.0", "0.2.0", "0.3.0"]


# ----------------------------------------------------------------- the switch

def test_the_owner_can_turn_it_off(gear, monkeypatch):
    root, audit = gear
    called = []
    monkeypatch.setattr(update, "check",
                        lambda **kw: called.append(1) or update.Check())
    _updater(root, audit, state=_State(auto_update=False)).run_once()
    assert called == [] and _entries(audit) == []


# ------------------------------------------------------------------ never dies

def test_an_exploding_check_does_not_kill_the_loop(gear, monkeypatch):
    """This runs on a daemon thread inside the tray app. An exception here
    would silently end automatic updating and nothing would say so."""
    root, audit = gear

    def boom(**kw):
        raise RuntimeError("что-то совсем неожиданное")

    monkeypatch.setattr(update, "check", boom)
    assert _updater(root, audit).run_once() is False
    assert any("неожиданное" in e["line"] for e in _entries(audit))


def test_no_public_key_means_it_stays_quiet_instead_of_failing_hourly(gear,
                                                                      monkeypatch):
    """A development checkout has no bundle and no key. Retrying forever and
    journalling each attempt would make the journal useless there."""
    root, audit = gear
    called = []
    monkeypatch.setattr(update, "check",
                        lambda **kw: called.append(1) or update.Check())
    up = update.AutoUpdater(root=root, audit=audit, state=_State(),
                            pubkey=None, shell_version=None,
                            current=lambda: "0.1.0")
    assert up.run_once() is False
    assert called == [] and _entries(audit) == []


def test_the_updater_is_given_the_settings_the_owner_wrote(tmp_path,
                                                           monkeypatch):
    """A-16: `AutoUpdater` создавался без `interval_s` и без `settings`,
    поэтому `update.interval_hours` не действовал вовсе, а выключатель
    обновлений читался только из state. Совпадение умолчаний (6 ч) прятало
    это: настройка «работала», пока её не меняли."""
    from vibebridge import app as appmod
    from vibebridge.config import Settings
    from vibebridge.state import BridgeState

    seen: dict = {}

    class _Recorder:
        def __init__(self, **kw):
            seen.update(kw)

        def start(self):
            seen["started"] = True

    monkeypatch.setattr("vibebridge.update.AutoUpdater", _Recorder)
    monkeypatch.setattr("vibebridge.update.bundled_public_key",
                        lambda res: b"k")
    monkeypatch.setattr("vibebridge.web._bundle_resources", lambda: tmp_path)

    settings = Settings(update_interval_s=3600)
    state = BridgeState(path=tmp_path / "state.json", panel_token="t")
    appmod.start_autoupdate(state, AuditLog(tmp_path / "a.log"),
                            settings=settings)
    assert seen.get("started") is True
    assert seen["settings"] is settings
    assert seen["interval_s"] == 3600


def test_without_settings_the_updater_keeps_its_own_default(tmp_path,
                                                            monkeypatch):
    """Вызов без настроек остаётся законным — в наборе и в тестовой сборке
    их может не быть."""
    from vibebridge import app as appmod
    from vibebridge.state import BridgeState

    seen: dict = {}

    class _Recorder:
        def __init__(self, **kw):
            seen.update(kw)

        def start(self):
            pass

    monkeypatch.setattr("vibebridge.update.AutoUpdater", _Recorder)
    monkeypatch.setattr("vibebridge.update.bundled_public_key",
                        lambda res: b"k")
    monkeypatch.setattr("vibebridge.web._bundle_resources", lambda: tmp_path)
    appmod.start_autoupdate(BridgeState(path=tmp_path / "s.json",
                                        panel_token="t"),
                            AuditLog(tmp_path / "a.log"))
    assert seen["settings"] is None and seen["interval_s"] is None
