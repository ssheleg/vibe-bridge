"""`settings_view` — без HTTP-стека (F-2).

`build_app` — замыкание, вокруг которого выросли 49 обработчиков. Проверить
логику любого из них можно было только подняв Starlette, TestClient и весь
маршрутный слой; пять находок этого рана (A-11, A-13, A-16, A-21, A-41)
правились точечно ровно поэтому.

Здесь решение отделено от доставки: функция берёт настройки и отдаёт словарь.
Никакого сервера, никакого клиента — и потому видно, ЧТО именно она решает.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.config import Settings
from vibebridge.web import settings_view


def _view(**kw):
    base = dict(live=Settings(), on_disk=Settings(), bind_host="127.0.0.1",
                has_robot_token=False, gateway_ok=None,
                config_path_fn=lambda: Path("/tmp/config.toml"))
    base.update(kw)
    return settings_view(**base)


def test_the_switch_names_the_mode_it_goes_TO_not_the_current_one():
    """A-24 жил здесь: панель считала предупреждение от ЖИВОГО режима, то
    есть от того, который владелец уже покинул."""
    assert _view(live=Settings(mode="gateway"))["switch_to"] == "standalone"
    assert _view(live=Settings(mode="standalone"))["switch_to"] == "gateway"


def test_the_effect_describes_the_destination_boundary():
    to_standalone = _view(live=Settings(mode="gateway"))["switch_effect"]
    to_gateway = _view(live=Settings(mode="standalone"))["switch_effect"]
    # Свойство: названа граница, которая ПОЯВИТСЯ, и её цена. Слово «шлюз»
    # в первой строке стоит законно — как противопоставление («сам мост, а
    # не шлюз»), и запрещать его значило бы проверять формулировку вместо
    # смысла (класс A-43).
    assert to_standalone.startswith("standalone:") and "bearer" in to_standalone
    assert to_gateway.startswith("gateway:") and "agentgateway" in to_gateway
    assert "БЕЗ него" in to_gateway, "цена перехода в gateway не названа"


def test_listening_says_loopback_only_when_it_is_loopback():
    for host in ("127.0.0.1", "localhost", "::1"):
        assert "только этот компьютер" in _view(bind_host=host)["listens"]
    wide = _view(bind_host="0.0.0.0")["listens"]
    assert "все интерфейсы" in wide and "0.0.0.0" in wide


def test_a_gateway_that_is_not_answering_is_called_out():
    """В режиме gateway у `/mcp` нет проверки токена ВООБЩЕ: границей служит
    шлюз. Не отвечает шлюз — границы нет, и это не «предупреждение», это
    состояние продукта."""
    warned = _view(live=Settings(mode="gateway"), gateway_ok=False)
    assert "БЕЗ аутентификации" in warned["warning"]
    calm = _view(live=Settings(mode="gateway"), gateway_ok=True)
    assert "warning" not in calm
    assert calm["mcp_auth"] == "нет — границей служит agentgateway"


def test_standalone_never_pretends_to_know_about_the_gateway():
    """`gateway_ok=None` — это «не спрашивали», и выдумывать ответ нельзя."""
    view = _view(live=Settings(mode="standalone"))
    assert view["gateway_ok"] is None
    assert "warning" not in view
    assert "токен появится" in view["mcp_auth"]
    assert "bearer" in _view(live=Settings(mode="standalone"),
                             has_robot_token=True)["mcp_auth"]


def test_restart_is_owed_only_for_fields_that_need_one():
    """Панель обещает «применится после перезапуска» — и обязана называть
    ровно те поля, которые процесс уже не подхватит."""
    same = _view()
    assert same["pending"] == [] and same["restart_required"] is False

    moved = _view(live=Settings(port=48620), on_disk=Settings(port=51234))
    assert moved["pending"] == ["port"] and moved["restart_required"] is True

    # ...а поле, которое читается на лету, перезапуска не требует
    skin = _view(live=Settings(mascot_skin="vasya"),
                 on_disk=Settings(mascot_skin="другой"))
    assert skin["pending"] == []


def test_the_panel_is_told_the_port_in_force_not_the_one_in_the_file():
    """Иначе панель показывает порт, на котором мост НЕ слушает, — в момент,
    когда кто-то редактирует файл."""
    view = _view(live=Settings(port=48620), on_disk=Settings(port=51234))
    assert view["port"] == 48620
    assert "48620" in view["mcp_url"]


# ── решение о пейринге — тоже без стека (F-2) ──────────────────────────────

def test_the_pairing_verdict_tells_a_forgery_from_an_expiry():
    """A-22: истёкший токен и подделанный — разные новости. Владельцу в
    первом случае надо выдать новый в панели, во втором — задуматься, кто
    стучится. Раньше это была ветка внутри обработчика, и проверить её
    можно было только через HTTP."""
    from vibebridge.web import PairVerdict, pairing_verdict

    ok = pairing_verdict(offered="tok", expected="tok", issued_at=1000.0,
                         ttl_s=3600, now=1100.0)
    assert ok is PairVerdict.OK

    forged = pairing_verdict(offered="не тот", expected="tok",
                             issued_at=1000.0, ttl_s=3600, now=1100.0)
    assert forged is PairVerdict.BAD_TOKEN
    assert "неверным токеном" in forged.journal

    stale = pairing_verdict(offered="tok", expected="tok", issued_at=1000.0,
                            ttl_s=3600, now=1000.0 + 3601)
    assert stale is PairVerdict.EXPIRED
    assert "выдайте новый" in stale.journal and "возьмите новый" in stale.spoken


def test_a_burned_token_is_refused_before_the_clock_is_consulted():
    """Порядок важен: у погашенного токена (None) нет ни срока, ни возраста,
    и спрашивать часы про него бессмысленно."""
    from vibebridge.web import PairVerdict, pairing_verdict

    assert pairing_verdict(offered="что угодно", expected=None,
                           issued_at=None, ttl_s=3600) is PairVerdict.BAD_TOKEN
    assert pairing_verdict(offered="", expected="",
                           issued_at=None, ttl_s=3600) is PairVerdict.BAD_TOKEN


def test_a_zero_ttl_means_no_expiry_at_all():
    """Ноль в настройке — это «не протухает», а не «протухает мгновенно»."""
    from vibebridge.web import PairVerdict, pairing_verdict

    assert pairing_verdict(offered="t", expected="t", issued_at=0.0,
                           ttl_s=0, now=10**9) is PairVerdict.OK


def test_a_token_without_a_stamp_is_not_treated_as_ancient():
    """Установки, спаренные до появления отметки времени, не должны
    внезапно перестать пускать своего робота."""
    from vibebridge.web import PairVerdict, pairing_verdict

    assert pairing_verdict(offered="t", expected="t", issued_at=None,
                           ttl_s=3600, now=10**9) is PairVerdict.OK


def test_a_non_ascii_token_is_refused_rather_than_crashing():
    """`secrets.compare_digest` на СТРОКАХ бросает TypeError, если в них есть
    не-ASCII. Токен приходит из сети, где бывает что угодно, — и мост
    отвечал бы 500 вместо 403. Найдено тем, что решение стало вызываемым
    напрямую (F-2)."""
    from vibebridge.web import PairVerdict, pairing_verdict

    assert pairing_verdict(offered="не тот", expected="tok", issued_at=None,
                           ttl_s=3600) is PairVerdict.BAD_TOKEN
    assert pairing_verdict(offered="🤖", expected="tok", issued_at=None,
                           ttl_s=3600) is PairVerdict.BAD_TOKEN
    # ...и настоящий токен по-прежнему проходит
    assert pairing_verdict(offered="tok", expected="tok", issued_at=None,
                           ttl_s=3600) is PairVerdict.OK
