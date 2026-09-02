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

    # Здесь стояло убеждение «поле, которое читается на лету, перезапуска не
    # требует», и в качестве примера — `mascot_skin`. Измерение его
    # опровергает: `api_settings_save` пишет ТОЛЬКО файл, а обработчики
    # читают объект `Settings`, захваченный при сборке приложения
    # (`web.py:1281` — `settings.mascot_skin` из замыкания). Живых полей нет
    # ни одного, и список молчал про обе кнопки, которые владелец нажимает
    # чаще всего (U-5).
    skin = _view(live=Settings(mascot_skin="vasya"),
                 on_disk=Settings(mascot_skin="другой"))
    assert skin["pending"] == ["mascot_skin"], (
        "изменение скина не показывается как ждущее перезапуска, хотя "
        "процесс держит старое значение до рестарта")

    ask = _view(live=Settings(ask_for_read=False),
                on_disk=Settings(ask_for_read=True))
    assert ask["pending"] == ["ask_for_read"], (
        "кнопка «Спрашивать перед чтением» снова не даёт обратной связи")


def test_the_restart_list_covers_every_setting_there_is():
    """Список выводится из `Settings`, а не перечисляется. Перечисленный
    молчал про четыре поля из десяти — включая обе кнопки панели."""
    from dataclasses import fields

    from vibebridge.web import restart_fields
    покрыто = set(restart_fields(Settings()))
    все = {f.name for f in fields(Settings)} - {"problems"}
    assert покрыто == все, (
        f"поля вне списка перезапуска: {sorted(все - покрыто)}; "
        f"лишние: {sorted(покрыто - все)}")


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


# ── лента и привязка — тоже без стека (F-2) ────────────────────────────────

def test_the_event_normaliser_keeps_the_channel_and_the_media():
    """Обе находки о ленте случились здесь: `channel` терялся при пересборке
    словаря (A-18), вторая копия события появлялась строкой ниже (A-13).
    Проверить это можно было только подняв приложение и дождавшись SSE."""
    from vibebridge.web import normalise_robot_event

    ev = normalise_robot_event(
        {"ts": "2026-09-02T10:00:00", "kind": "media", "text": "кадр",
         "channel": "computer", "media": {"name": "a.jpg", "type": "image"}},
        now_iso="2026-09-02T11:00:00")
    assert ev["channel"] == "computer"
    assert ev["media"] == {"name": "a.jpg", "type": "image"}
    # Стрелки робота НЕ переводим — 10:00:00 остаётся 10:00:00, — но зону
    # дописываем. Наивная метка это не «время робота»: это строка, которую JS
    # читателя толкует как СВОЁ местное время, и в поездке владельца каждое
    # событие сдвигалось на разницу зон (B-42). Робот теперь шлёт зону сам;
    # старые прошивки на флоте обновятся не сразу, поэтому мост дописывает
    # СВОЮ — допущение «робот и мост дома в одной зоне» записано в коде, а
    # не спрятано, как раньше делал браузер.
    assert ev["ts"].startswith("2026-09-02T10:00:00")
    import datetime as _dt
    assert _dt.datetime.fromisoformat(ev["ts"]).tzinfo is not None, (
        "мост отдал наивную метку — читатель в другой зоне увидит не то время")


def test_an_event_without_a_stamp_gets_ours():
    from vibebridge.web import normalise_robot_event

    ev = normalise_robot_event({"text": "привет"}, now_iso="СЕЙЧАС")
    assert ev["ts"] == "СЕЙЧАС" and ev["kind"] == "event"
    assert ev["channel"] is None and ev["media"] is None


def test_a_robot_that_sends_a_novel_does_not_get_it_into_the_feed():
    """Лента — это фразы, а не документы. Чей-то лог, попавший не туда, не
    должен вытеснить всё остальное."""
    from vibebridge.web import EVENT_TEXT_MAX, normalise_robot_event

    ev = normalise_robot_event({"text": "я" * 5000}, now_iso="x")
    assert len(ev["text"]) == EVENT_TEXT_MAX


def test_attach_refuses_what_is_not_an_address():
    from vibebridge.web import attach_request

    for bad in ("", "   ", "robot.local", "ftp://robot", "//robot"):
        wanted, why = attach_request({"base_url": bad})
        assert wanted is None and why, bad

    wanted, why = attach_request({"base_url": "https://robot.ts.net/",
                                  "chat_url": " https://robot.ts.net:8642 ",
                                  "key": " k ", "name": "  Вася "})
    assert why == ""
    assert wanted["base_url"] == "https://robot.ts.net"     # хвостовой слэш снят
    assert wanted["chat_url"] == "https://robot.ts.net:8642"
    assert wanted["key"] == "k" and wanted["name"] == "Вася"


def test_a_nameless_robot_is_still_called_something():
    from vibebridge.web import attach_request

    wanted, _ = attach_request({"base_url": "http://r", "name": "   "})
    assert wanted["name"] == "робот"


def test_attach_words_never_claim_a_connection_that_was_not_made():
    """A-12: «связан ✓» без ответа робота — обещание, которого мост не может
    выполнить: панель рядом скажет «не подключён»."""
    from vibebridge.web import attach_words

    line, toast = attach_words("Вася", {"ok": True})
    assert "отвечает" in line and "✓" in toast

    line, toast = attach_words("Вася", {"ok": False,
                                        "error": "ключ не подошёл"})
    assert "не отвечает" in line and "ключ не подошёл" in line
    assert "✓" not in toast and "не отвечает" in toast


def test_the_reachable_address_never_falls_back_to_a_constant_port():
    """A-21 нашёл этот расчёт в ТРЁХ местах с константой 48620 вместо
    действующего порта: команда `tailscale serve` вела не туда, а `mcp_url`
    в кредах робота указывал в пустоту."""
    from vibebridge.web import bridge_base_url, mcp_url

    assert bridge_base_url(dns=None, https_ok=False, port=51234) == \
        "http://127.0.0.1:51234"
    assert mcp_url(dns=None, https_ok=False, port=51234) == \
        "http://127.0.0.1:51234/mcp"

    # есть MagicDNS, но HTTPS ещё не включён — идём по имени и порту
    assert bridge_base_url(dns="mac.ts.net", https_ok=False, port=51234) == \
        "http://mac.ts.net:51234"

    # HTTPS живой — порт не нужен вовсе, и его отсутствие это свойство
    assert bridge_base_url(dns="mac.ts.net", https_ok=True, port=51234) == \
        "https://mac.ts.net"
    assert mcp_url(dns="mac.ts.net", https_ok=True, port=51234) == \
        "https://mac.ts.net/mcp"


def test_https_without_a_name_is_not_a_thing():
    """`serve_active` может ответить True, когда имени нет: тогда адреса
    по HTTPS не существует, и выдумывать его нельзя."""
    from vibebridge.web import bridge_base_url, mcp_url

    assert bridge_base_url(dns=None, https_ok=True, port=48620) == \
        "http://127.0.0.1:48620"
    assert mcp_url(dns=None, https_ok=True, port=48620) == \
        "http://127.0.0.1:48620/mcp"


def test_a_robot_that_already_sends_a_zone_is_left_alone():
    """Новый робот шлёт зону сам — переписывать её значило бы врать о том,
    где он находится."""
    from vibebridge.web import normalise_robot_event

    ev = normalise_robot_event({"ts": "2026-09-02T10:00:00+00:00",
                                "kind": "e", "text": "x"},
                               now_iso="2026-09-02T12:00:00+00:00")
    assert ev["ts"] == "2026-09-02T10:00:00+00:00"


def test_a_timestamp_we_cannot_read_is_passed_through_untouched():
    """Чужой формат — не наше дело чинить: выдумывать за него время хуже,
    чем показать как есть."""
    from vibebridge.web import normalise_robot_event

    ev = normalise_robot_event({"ts": "вчера вечером", "kind": "e", "text": "x"},
                               now_iso="2026-09-02T12:00:00+00:00")
    assert ev["ts"] == "вчера вечером"
