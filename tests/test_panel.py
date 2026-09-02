"""T-PANEL: the journal read path (filters, pagination, honest error) and
its HTTP surface — SCN-011's implementing seams.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.consent import ConsentEngine
from vibebridge.state import BridgeState
from vibebridge.web import build_app


def _fill(aud: AuditLog) -> None:
    aud.record(tool="open_url", tool_class="act", decision="allow", ok=True,
               line="открыть ссылку a")
    aud.record(tool="screenshot", tool_class="read", decision="auto", ok=True,
               line="смотрю на экран")
    aud.record(tool="open_app", tool_class="act", decision="deny", ok=False,
               line="открыть «X»")
    aud.record(tool="open_app", tool_class="act", decision="timeout", ok=False,
               line="открыть «Y»")
    aud.record(tool="list_apps", tool_class="read", decision="paused", ok=False,
               line="список приложений")


def test_read_entries_newest_first_and_pagination(tmp_path):
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    page = aud.read_entries(limit=2)
    assert page["total"] == 5
    assert [e["decision"] for e in page["entries"]] == ["paused", "timeout"]
    page2 = aud.read_entries(offset=2, limit=2)
    assert [e["decision"] for e in page2["entries"]] == ["deny", "auto"]


def test_read_entries_filter_refused_and_class(tmp_path):
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    refused = aud.read_entries(flt="refused")
    assert {e["decision"] for e in refused["entries"]} == {
        "deny", "timeout", "paused"}
    acts = aud.read_entries(flt="act")
    assert all(e["class"] == "act" for e in acts["entries"])
    assert acts["total"] == 3


def test_read_entries_missing_file_is_honest(tmp_path):
    aud = AuditLog(tmp_path / "nope" / "a.log")
    (tmp_path / "nope").rmdir() if (tmp_path / "nope").exists() else None
    page = aud.read_entries()
    assert page["entries"] == [] and page["total"] == 0


def test_journal_endpoint_filters(tmp_path):
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    aud = AuditLog(tmp_path / "a.log")
    _fill(aud)
    app = build_app(consent=ConsentEngine(), audit=aud, state=state,
                    capabilities={}, mcp_allowed_hosts=["testserver", "127.0.0.1:*"])
    with TestClient(app) as c:
        assert c.get("/api/journal").status_code == 401
        c.get("/?token=panel-secret")
        all_page = c.get("/api/journal?limit=3").json()
        assert all_page["total"] == 5 and len(all_page["entries"]) == 3
        refused = c.get("/api/journal?filter=refused").json()
        assert refused["total"] == 3
        reads = c.get("/api/journal?filter=read").json()
        assert {e["class"] for e in reads["entries"]} == {"read"}


def test_pwa_tile_serves_the_real_mark_not_the_placeholder(tmp_path):
    """The phone's home-screen tile is the app's face on a device the owner
    carries. It served a flat blue square until 2026-08-30."""
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import _solid_png, build_app

    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    r = TestClient(app).get("/icon-192.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert r.content != _solid_png(192)          # not the fallback square


def test_every_manifest_icon_size_is_served(tmp_path):
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    client = TestClient(app)
    for size in (180, 192, 512):
        assert client.get(f"/icon-{size}.png").status_code == 200
    assert client.get("/icon-999.png").status_code == 404


def test_the_panel_shows_the_robot_system_not_just_its_name(tmp_path):
    """«Сейчас то, что есть в Telegram-боте и настройки в панели, полностью
    разные. Нельзя этим заменить» — панель знала имя, версию и аптайм, и
    больше ничего."""
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    assert "/api/robot/system" in page
    for label in ("Температура CPU", "Память", "Диск", "Воздух PM2.5",
                  "Сервисы"):
        assert label in page
    # Bootstrapped, not just defined — the lesson from prune/migrate/top_up.
    assert "loadSystem();" in page.rsplit("</script>", 2)[-2]


def test_service_liveness_is_a_word_as_well_as_a_colour(tmp_path):
    from pathlib import Path

    import vibebridge
    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    assert "работает" in page and "не отвечает" in page


def test_the_system_endpoint_is_guarded(tmp_path):
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings())
    assert TestClient(app).get("/api/robot/system").status_code == 401


def test_the_panel_shows_times_in_the_owners_clock():
    """The journal stamps UTC (`audit.py` uses `datetime.now(UTC)`) and the
    panel used to slice that ISO string for display, dropping `+00:00` and
    presenting the result as the wall clock. On this machine the events card
    read 20:45:32 while the clock read 22:45:32 — off by a fixed two hours,
    off by a different amount for every reader, and confident about it.

    Checked mechanically because a wrong-but-plausible timestamp is exactly
    the kind of defect that survives review.
    """
    from pathlib import Path

    import vibebridge

    html = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    assert "function localTs(" in html
    # No surviving raw slice of a stamp into a time cell.
    assert 'esc((e.ts||"").slice' not in html.replace(" ", "")
    assert html.count("localTs(e.ts)") == 2        # the feed and the journal
    # …and the unparsable case degrades instead of printing "Invalid Date".
    fn = html.split("function localTs(", 1)[1].split("\n}", 1)[0]
    assert "isNaN" in fn


def test_the_system_card_tolerates_both_shapes_of_uptime():
    """Найдено аудитом 2026-09-01: робот отдаёт `uptime` СТРОКОЙ, панель
    читала `d.uptime.pretty` — и строки «Аптайм», «Нагрузка», «CPU» не
    рисовались никогда. Скриншот карточки это подтверждал, и отсутствие
    строки в скриншоте незаметно: сверять надо со списком ожидаемого, а не с
    тем, что видно.

    Проверка механическая: голое чтение `.pretty` у `d.uptime` не должно
    вернуться, а обработка строкового случая должна существовать.
    """
    import re
    from pathlib import Path

    import vibebridge

    html = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    # КОД, не текст файла: комментарий рядом называет старое чтение дословно,
    # чтобы объяснить фикс, и наивная проверка ловила бы его. Этот капкан в
    # проекте срабатывал трижды.
    code = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert 'typeof d.uptime === "string"' in code
    assert "d.uptime.pretty" not in code, "снова читаем .pretty у возможной строки"
    assert "d.uptime.load1" not in code and "d.uptime.cpu_pct" not in code



def test_the_consent_card_shows_that_silence_is_an_answer():
    """A-9/V-3: отказ по молчанию — политика по умолчанию, но три кнопки об
    этом не говорили. Пак задал таймер-бар до пикселя (4px, `--warn`, дренаж
    слева направо), и он не был построен. Смотрим на ПРАВИЛА — комментарии
    вырезаны, иначе тест зачтёт объяснение вместо кода."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

    rule = code.split(".consent .drain>i{", 1)
    assert len(rule) == 2, "таймер-бара согласия нет"
    body = rule[1].split("}", 1)[0].replace(" ", "")
    assert "background:var(--warn)" in body        # цвет из пака, не свой
    # Свойство, а не механизм: важно, что длина полосы управляется ДАННЫМИ,
    # а не то, каким CSS-свойством. Ассерт стоял на `width:var(--left` и
    # покраснел, когда бар переехал на `transform:scaleX` — то есть держал
    # реализацию, а не смысл (класс A-43). Чем именно двигают полосу,
    # проверяет моторный гейт: он же и запрещает `width`.
    assert "var(--left" in body                   # дренаж управляется данными
    bar = code.split(".consent .drain{", 1)[1].split("}", 1)[0].replace(" ", "")
    assert "height:4px" in bar                     # пак: ровно 4px
    # ...и разметка, и данные, которыми она живёт
    assert 'id="consentDrain"' in page and 'id="consentSecs"' in page
    assert "s.pending.left_s" in page and "s.pending.timeout_s" in page


def test_a_capability_that_needs_permission_offers_the_way_to_grant_it():
    """A-11 / SCN-020 шаг 2: статус «требует прав» был диагнозом без
    лечения. Панель обязана давать путь, не выходя из панели."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert 'needs-permission' in code and 'data-grant' in code, \
        "кнопки выдачи прав нет"
    fn = code.split("async function grantCap(", 1)[1].split("\n}", 1)[0]
    assert "/grant" in fn, "кнопка никуда не ходит"
    assert "capNote" in fn, "результат запроса прав не показывается"
    assert "Мост не ответил" in fn, "молчащая кнопка — тот же обман"


def test_the_attach_form_asks_for_the_chat_address_and_reports_the_probe():
    """A-12: `chat_url` в форме не было вовсе — вкладка «Чат» включалась, а
    чат отвечал «робот не подключён» рядом с карточкой «в сети». И «привязан
    ✓» печаталось без единого обращения к роботу."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert 'id="atChat"' in code, "адрес чата в форме не спрашивается"
    fn = code.split("async function attachRobot(", 1)[1].split("\n}", 1)[0]
    assert "chat_url" in fn, "адрес чата не отправляется"
    assert "d.reached" in fn, "успех печатается без проверки связи"
    assert "d.probe.error" in fn, "причина провала не показывается"


def test_the_feed_marks_an_event_that_only_mirrors_telegram():
    """A-18: робот кладёт `channel` в событие ИМЕННО для ленты — с
    комментарием «без этого лента не отличает показ на компьютере от зеркала
    телеграма». Мост поле выбрасывал, панель о нём не знала."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "e.channel" in code, "лента не смотрит на канал события"
    assert "из телеграма" in code, "зеркало телеграма никак не помечено"


def test_the_robot_card_does_not_scare_or_lie_when_nothing_is_paired():
    """A-23: обе половины строки были неверны. «Подключение появится с
    визардом» — визард отгружен в T-WIZARD; «робот уже может действовать на
    этом компьютере» читалось как «неизвестный робот уже имеет руки на моём
    Маке». Правда проще: пейринг это ЮЖНЫЙ канал, а вызов инструментов идёт
    своим путём."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "появится с визардом" not in code, "визард всё ещё «появится»"
    assert "уже может действовать на этом компьютере" not in code
    # Фрагмент внутри ОДНОГО литерала: строка собирается
    # конкатенацией, и проверка через её шов ломалась бы от
    # переноса, а не от смены смысла (класс A-43).
    assert "Панель ещё не знает вашего робота" in code
    assert "Инструменты — отдельный канал" in code
    # ...и у карточки есть ПУТЬ, а не только диагноз
    assert "goConnectRobot" in code
    fn = code.split("function goConnectRobot(", 1)[1].split("\n}", 1)[0]
    assert 'data-view="settings"' in fn and "attachCard" in fn


def test_switching_the_security_boundary_explains_itself_first():
    """A-24: граница безопасности переключалась ОДНИМ кликом, а последствие
    владелец узнавал после перезапуска — предупреждение считалось от ЖИВОГО
    режима, то есть от того, который он уже покинул."""
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text()
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    fn = code.split("async function switchMode(", 1)[1].split("\n}", 1)[0]
    assert "armed" in fn, "переключение всё ещё с первого клика"
    assert "alert(" not in fn, "alert блокирует страницу и не объясняет"
    assert "Мост не ответил" in fn, "провал переключения снова беззвучен"
    assert "switch_effect" in code, "последствие не показывается ДО клика"
    assert "s.listens" in code, "панель не говорит, что мост слушает"


def test_an_unavailable_control_is_disabled_not_absent():
    """SCN-007 шаг 2: недоступное действие ОТКЛЮЧЕНО с причиной.

    Офлайн-ветка карточки робота возвращалась раньше отрисовки кнопки, и
    контрол просто исчезал. Исчезнувший контрол — не «отключено с причиной»,
    а «его нет»: владелец учится, что такого действия не существует (U-6).
    """
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    кнопки = re.findall(r'<button[^>]*id="updBtn"[^>]*>', code)
    assert len(кнопки) == 2, (
        f"кнопка «Обновить робота» найдена {len(кнопки)} раз — она обязана "
        f"существовать в ОБЕИХ ветках: живой и офлайн")
    отключённая = [b for b in кнопки if "disabled" in b]
    assert len(отключённая) == 1, "офлайн-кнопка не отключена"
    assert "title=" in отключённая[0], (
        "кнопка отключена молча — причина не названа")


def test_the_chat_input_is_not_locked_by_a_cached_status():
    """Канон противоречил сам себе: SCN-007 требовал отключить ввод,
    SCN-009 — отправить с пометкой «не доставлено».

    Спор решает факт: статус робота — кэш последнего опроса. Блокировать ввод
    по устаревшему «недоступен» значит запретить работающий чат.
    """
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    поле = [ln for ln in page.splitlines() if 'id="chatInput"' in ln]
    assert поле, "поле ввода чата пропало"
    assert "disabled" not in поле[0], (
        "ввод чата отключён по статусу — а статус это кэш опроса")

    scenarios = (Path(vibebridge.__file__).parents[1] / "docs" / "ux"
                 / "scenarios.md").read_text(encoding="utf-8")
    assert "система отключает ввод с причиной «робот недоступен»" not in scenarios, (
        "канон снова требует отключать ввод — это противоречит SCN-009")


def test_no_two_controls_share_one_id():
    """`getElementById` отдаёт ПЕРВЫЙ в порядке документа.

    `updBtn` жил на двух разных кнопках — у робота на дашборде и у моста в
    настройках, — и «Проверить обновления» переписывало чужой контрол,
    оставляя свой без обратной связи. Найдено гейтом U-6, а не отчётом.
    """
    import collections
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    ids = re.findall(r'\bid="([A-Za-z][\w-]*)"', page)
    дубли = {i: n for i, n in collections.Counter(ids).items() if n > 1}
    # `updBtn` у робота стоит дважды НАМЕРЕННО — это одна кнопка в двух
    # взаимоисключающих ветках отрисовки (живой и офлайн), в документе она
    # всегда одна.
    дубли.pop("updBtn", None)
    assert not дубли, f"один id на разных контролах: {дубли}"


def test_the_journal_row_shows_the_class_it_can_be_filtered_by():
    """SCN-011 обещает в строке журнала класс и краткий результат. Запись их
    несёт, разметка не рисовала — а фильтры ACT/READ стоят в сорока пикселях:
    владелец фильтровал по признаку, которого не видел (U-17).

    Имя поля проверяется отдельно: в API оно `class`, и первая версия читала
    `tool_class`, молча рисуя пустоту.
    """
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    строка = code.split("function rowHtml", 1)
    assert len(строка) == 2, "функция строки журнала пропала"
    тело = строка[1].split("\n}", 1)[0]
    assert 'e["class"]' in тело, (
        "строка журнала не рисует класс записи — или читает не то поле")

    # ...и запись действительно его несёт, под этим именем
    import inspect

    from vibebridge.audit import AuditLog
    src = inspect.getsource(AuditLog.record)
    assert '"class": tool_class' in src, (
        "имя поля в записи изменилось — разметка станет читать пустоту")


def test_a_closed_request_is_not_guessed_at():
    """Мост различает «истёк» и «решён с другой поверхности» (A-10). Запасная
    строка виджета утверждала второе — именно тогда, когда не знает."""
    from pathlib import Path

    import vibebridge

    window = (Path(vibebridge.__file__).parent / "webui" / "mascot.html").read_text(
        encoding="utf-8")
    запасная = window.split("let why = ", 1)[1].split(";", 1)[0]
    assert "уже решён с другой поверхности" not in запасная, (
        f"виджет угадывает причину закрытия: {запасная}")
    assert "не сказал" in запасная, запасная


def test_the_journal_does_not_blame_the_owner_for_the_boundary():
    """Записи класса SYS решает не человек: это граница MCP, обновление,
    канал уведомлений. `DW.deny` подписывал их «отказ владельца» — то есть
    приписывал владельцу отказ, которого тот не видел.

    Измерено на живом мосту 2026-09-02: 9 таких записей из последних 60
    (B-47). Найдено живой проверкой, а не отчётом.
    """
    import re
    from pathlib import Path

    import vibebridge

    page = (Path(vibebridge.__file__).parent / "webui" / "index.html").read_text(
        encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    тело = code.split("function rowHtml", 1)[1].split("\n}", 1)[0]
    assert '"SYS"' in тело, (
        "строка журнала снова подписывает решением ВСЕ записи, включая те, "
        "где решения человека не было")
