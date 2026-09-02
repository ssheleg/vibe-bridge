"""What the shell decides before any of our code runs.

Two decisions live here and both are about not lying. `choose_payload` picks
between an installed version and the seed that shipped in the bundle — and
when it falls back it must SAY it fell back, because "running the version you
installed" and "running the version from the DMG" look identical from the
tray. `guard_single_instance` refuses the second copy: two bridges on port
48620 is one bridge answering the robot and one silently dead, which is worse
than a startup that says the port is taken.
"""
from __future__ import annotations

import socket

import pytest

from vbboot import layout, runner


@pytest.fixture()
def bundle(tmp_path):
    seed = tmp_path / "seed"
    (seed / "vibebridge").mkdir(parents=True)
    (seed / "vibebridge" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    return seed


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "payload"
    r.mkdir()
    return r


def _install(root, version):
    d = root / version
    (d / "vibebridge").mkdir(parents=True)
    (d / "vibebridge" / "__init__.py").write_text(
        f'__version__ = "{version}"\n')
    layout.mark_installed(root, version)


# ------------------------------------------------------------ which code runs

def test_seed_runs_when_nothing_is_installed(bundle, root):
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.path == bundle
    assert chosen.version == "0.1.0"
    assert chosen.source == "seed"


def test_installed_version_wins_over_the_seed(bundle, root):
    _install(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.path == root / "0.2.0"
    assert chosen.version == "0.2.0"
    assert chosen.source == "payload"


def test_seed_wins_when_it_is_newer_than_everything_installed(bundle, root):
    """A freshly installed .app carries a newer seed than the payload left by
    the previous install — taking the older payload would silently downgrade
    the owner right after they dragged a new build to Applications."""
    _install(root, "0.0.9")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed" and chosen.version == "0.1.0"


def test_a_crashed_version_is_skipped_on_the_next_launch(bundle, root):
    _install(root, "0.2.0")
    _install(root, "0.3.0")
    layout.begin_launch(root, "0.3.0")            # crash: marker survives
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.version == "0.2.0"
    assert layout.is_quarantined(root, "0.3.0")


def test_falling_all_the_way_back_to_the_seed_is_reported_not_hidden(bundle,
                                                                    root):
    _install(root, "0.2.0")
    layout.begin_launch(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed"
    assert chosen.fell_back is True               # the panel says so


def test_a_payload_missing_its_package_is_not_chosen(bundle, root):
    """Stamped complete but empty — disk corruption, or a release built
    wrong. The seed is a working bridge; this is not."""
    d = root / "0.2.0"
    d.mkdir()
    layout.mark_installed(root, "0.2.0")
    chosen = runner.choose_payload(root, seed=bundle)
    assert chosen.source == "seed"


# --------------------------------------------------------------- one instance

def test_guard_allows_a_free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert runner.guard_single_instance(free) is None


def test_guard_reports_a_taken_port_instead_of_racing_for_it():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        why = runner.guard_single_instance(taken)
    assert why is not None
    assert str(taken) in why


# ------------------------------------------------------- the shell's own version

def test_shell_version_comes_from_the_bundle_not_the_payload(tmp_path,
                                                             monkeypatch):
    """ADR-0006 splits the two on purpose: dependencies live in the shell,
    our code lives in the payload. If the shell reported the PAYLOAD's
    version, then updating to 0.2.0 would make the shell claim to be 0.2.0 —
    and a later payload declaring `shell_min = 0.2.0` would install against a
    shell that is still 0.1.0 and cannot import what it needs.
    """
    resources = tmp_path / "vibe-bridge.app" / "Contents" / "Resources"
    (resources / "app" / "vbboot").mkdir(parents=True)
    (resources / "app" / "vbboot" / "__init__.py").write_text("")
    (tmp_path / "vibe-bridge.app" / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        '<key>CFBundleShortVersionString</key><string>0.1.0</string>'
        "</dict></plist>\n")

    import vbboot
    monkeypatch.setattr(vbboot, "__file__",
                        str(resources / "app" / "vbboot" / "__init__.py"))
    assert runner.shell_version() == "0.1.0"


def test_shell_version_outside_a_bundle_is_reported_as_unknown(monkeypatch):
    """A development checkout has no shell at all. Returning a real-looking
    number would let a payload's compatibility check pass on nothing."""
    import vbboot
    monkeypatch.setattr(vbboot, "__file__", "/Users/x/repo/vbboot/__init__.py")
    assert runner.shell_version() is None


# ------------------------------------------- a broken payload must not win

def test_a_payload_that_cannot_be_imported_falls_back_in_the_same_launch(
        tmp_path, bundle, root, monkeypatch):
    """Live on 2026-08-30 a payload raising on import took the bridge down
    until someone launched it a second time: the marker mechanism only rolls
    back on the NEXT boot. For an app that starts at login that means the
    owner's bridge is simply absent until they notice. The fallback happens
    now, in this process."""
    _install(root, "9.9.9")
    attempts = []

    def loader(chosen):
        attempts.append(chosen.version)
        if chosen.version == "9.9.9":
            raise ImportError("подсаженный дефект")
        return f"ran {chosen.version}"

    result, chosen = runner.run_payload(root, seed=bundle, loader=loader)
    assert result == "ran 0.1.0"                 # the seed, in one launch
    assert attempts == ["9.9.9", "0.1.0"]
    assert layout.is_quarantined(root, "9.9.9")  # and never offered again
    assert chosen.source == "seed" and chosen.fell_back


def test_a_healthy_payload_is_loaded_once_and_kept(bundle, root):
    _install(root, "0.2.0")
    calls = []
    result, chosen = runner.run_payload(
        root, seed=bundle, loader=lambda c: calls.append(c.version) or "ok")
    assert result == "ok" and calls == ["0.2.0"]
    assert chosen.source == "payload"
    assert not layout.is_quarantined(root, "0.2.0")


def test_fallback_stops_rather_than_looping_when_nothing_loads(bundle, root):
    """The seed failing too is the end of the line — say so, do not spin."""
    _install(root, "0.2.0")

    def always_broken(chosen):
        raise ImportError("всё сломано")

    with pytest.raises(ImportError):
        runner.run_payload(root, seed=bundle, loader=always_broken)


def test_the_port_message_points_at_the_setting_that_changes_it():
    """`architecture.md` promised "порт занят → выбрать свободный". That is
    wrong for THIS port: the robot reaches the bridge at a fixed address —
    through an agentgateway route or its own configuration — so silently
    moving would leave a bridge that runs and a robot that cannot find it.
    Refusing is right; refusing without a way out is not."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        why = runner.guard_single_instance(held.getsockname()[1])
    assert why is not None
    assert "config.toml" in why          # where to change it
    assert "port" in why


def test_the_message_names_who_is_holding_the_port_when_it_can():
    """"Занято" is not actionable; "занято вот этим" is."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        why = runner.guard_single_instance(port)
    # Our own process holds it in this test, so the name must appear.
    assert "python" in why.lower() or "pid" in why.lower()


# ── защита от второго экземпляра, измеренная против обоих видов бинда ──────


def test_the_guard_sees_a_wildcard_listener_too():
    """ИЗМЕРЕНО аудитом 2026-09-01 и перепроверено: проба шла на
    `127.0.0.1` с `SO_REUSEADDR`, и когда standalone (дефолт дистрибуции) стал
    биндить `0.0.0.0`, гвард перестал работать вовсе — на BSD-стеке точечный
    бинд поверх wildcard с `SO_REUSEADDR` разрешён. Второй экземпляр стартовал
    МОЛЧА: два трея, два питомца, консент только в одном процессе.
    """
    import socket

    from vbboot.runner import guard_single_instance

    for host in ("0.0.0.0", "127.0.0.1"):    # noqa: S104 - это и есть проверка
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        try:
            busy = guard_single_instance(port)
            assert busy, f"слушатель на {host} не замечен гвардом"
            assert str(port) in busy          # и назван номер
        finally:
            srv.close()


def test_a_free_port_is_not_reported_as_busy():
    """Обратная сторона: гвард, который всегда говорит «занято», это гвард,
    который не даёт мосту запуститься."""
    import socket

    from vbboot.runner import guard_single_instance

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert guard_single_instance(port) is None


def test_the_shell_guards_the_port_the_payload_will_take(tmp_path, monkeypatch):
    """Оболочка читала `VIBE_BRIDGE_PORT` или константу и `config.toml` не
    открывала никогда — то есть при изменённом порте охраняла чужой номер, а
    совет в её же сообщении («поменяйте port в config.toml») не работал."""
    from vbboot import __main__ as boot
    from vbboot import layout

    monkeypatch.delenv("VIBE_BRIDGE_PORT", raising=False)
    (tmp_path / "config.toml").write_text('port = 51234\n', encoding="utf-8")
    monkeypatch.setattr(layout, "support_dir", lambda: tmp_path)
    assert boot._configured_port() == 51234

    monkeypatch.setenv("VIBE_BRIDGE_PORT", "52000")
    assert boot._configured_port() == 52000        # env всё ещё старше файла

    monkeypatch.delenv("VIBE_BRIDGE_PORT")
    (tmp_path / "config.toml").write_text("port = 'нет'\n", encoding="utf-8")
    assert boot._configured_port() == 48620        # мусор → умолчание



def test_the_guard_is_actually_given_the_configured_port(monkeypatch):
    """A-29: `_configured_port` написан и протестирован — но проверялся В
    ОДИНОЧКУ. Класс «написано, протестировано, не вызвано» стоил проекту уже
    трёх случаев (A-37), и шов между настройкой и гвардом ровно такой же:
    сломай вызов, и все тесты порта останутся зелёными."""
    from vbboot import __main__ as boot

    seen: list[int] = []
    monkeypatch.setattr(boot, "_configured_port", lambda: 51777)
    monkeypatch.setattr("vbboot.runner.guard_single_instance",
                        lambda port: seen.append(port) or "порт занят")
    monkeypatch.setattr(boot, "_complain", lambda msg: None)

    assert boot.main() == 1          # занят → выходим с кодом
    assert seen == [51777], "гвард получил не тот порт, который займёт payload"


def test_the_busy_message_names_the_real_config_path(monkeypatch, tmp_path):
    """Совет читают на всех платформах, а путь `~/Library/...` верен только
    на macOS."""
    import socket

    from vbboot import layout, runner

    monkeypatch.setattr(layout, "support_dir", lambda: tmp_path)
    held = socket.socket()
    held.bind(("0.0.0.0", 0))
    held.listen(1)
    try:
        why = runner.guard_single_instance(held.getsockname()[1])
    finally:
        held.close()
    assert why and str(tmp_path / "config.toml") in why
    assert "~/Library" not in why


def test_the_shell_hands_its_choice_to_the_bridge(monkeypatch, tmp_path):
    """F-5: `Chosen(path, version, source, fell_back)` строился и НЕ
    передавался. Оболочка знала, какой код выбрала, и выбрасывала ответ —
    после чего панель переугадывала источник по наличию каталога."""
    from vbboot import __main__ as boot
    from vbboot.runner import Chosen

    handed = []
    chosen = Chosen(tmp_path / "0.9.0", "0.9.0", "payload", fell_back=True)
    monkeypatch.setattr(boot, "_configured_port", lambda: 48620)
    monkeypatch.setattr("vbboot.runner.guard_single_instance", lambda p: None)
    monkeypatch.setattr("vbboot.runner.run_payload",
                        lambda root, seed, loader: (handed.append, chosen))
    monkeypatch.setattr(boot, "_complain", lambda msg: None)

    assert boot.main() == 0
    assert handed == [chosen], "мост не получил решение оболочки"


def test_the_panel_reports_the_answer_not_a_guess(tmp_path):
    """Догадка «есть каталог с такой версией → payload» врала ровно в том
    случае, ради которого источник и показывают: версия seed совпадает с
    установленным payload, каталог есть, а работает seed."""
    import vibebridge.web as web
    from vbboot.runner import Chosen

    root = tmp_path / "payload"
    (root / "0.9.0").mkdir(parents=True)      # каталог ЕСТЬ

    web.set_chosen(None)
    try:
        # догадка: каталог есть → «payload», хотя это может быть seed
        assert web._payload_source(root, "0.9.0") in ("payload", "dev")
        web.set_chosen(Chosen(root / "0.9.0", "0.9.0", "seed"))
        assert web._payload_source(root, "0.9.0") == "seed"
    finally:
        web.set_chosen(None)


def test_the_fallback_toast_points_at_a_line_that_now_exists():
    """«Подробности в журнале» было обещанием, которого никто не выполнял:
    vbboot по построению без зависимостей и без журнала. Записать может
    только мост — и он это делает, получив `chosen`."""
    import re
    from pathlib import Path

    import vibebridge

    # 1. Оболочка называет ВЕРСИЮ, а не просто «обновление».
    boot_src = (Path(vibebridge.__file__).resolve().parents[1]
                / "vbboot" / "__main__.py").read_text()
    # Комментарии вырезаны: срез по первой скобке иначе попадает в «(F-5)»
    # и проверяет объяснение вместо кода — класс A-32.
    boot_src = re.sub(r"^\s*#.*$", "", boot_src, flags=re.M)
    toast = boot_src.split("if chosen.fell_back:", 1)[1].split("return", 1)[0]
    assert "chosen.version" in toast

    # 2. Мост ПИШЕТ строку про откат, получив решение оболочки.
    app_src = (Path(vibebridge.__file__).parent / "app.py").read_text()
    app_src = re.sub(r"^\s*#.*$", "", app_src, flags=re.M)
    branch = app_src.split("if chosen is not None:", 1)[1].split("settings =", 1)[0]
    assert "audit.record" in branch, "мост не пишет, откуда взят код"
    assert "chosen.fell_back" in branch, "откат не попадает в журнал"
    assert "chosen.source" in branch and "chosen.version" in branch
