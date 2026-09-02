"""Имя робота — его собственное, а обещания канона — выполнимые (U-14…U-16).

U-15: «Вася» — имя робота ОДНОГО владельца, зашитое в шести местах, при том
что `status.name` есть в снимке и в компаньоне используется правильно. Для
любого другого владельца продукт звал его робота чужим именем.

U-14: канал уведомлений знал только журнал, а от подписок панель показывала
счётчик — без списка и без отзыва. «Подписок: 2» не отвечает на вопрос
«какие устройства получают мои согласия».

U-16: `foundation.md` обещал, что согласие спрашивается у владельца, «а не у
того, кто ближе к экрану», — при том что мост никого не опознаёт.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEBUI = Path(vibebridge.__file__).parent / "webui"
sys.path.insert(0, str(REPO))


def _code(name: str) -> str:
    text = (WEBUI / name).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_no_surface_calls_the_robot_by_someone_elses_name():
    """Имя приходит от робота. Зашитое — это имя чужого робота."""
    места = []
    for name in ("index.html", "mascot.html", "mascot.js"):
        for line in _code(name).splitlines():
            if "Вася" in line:
                места.append(f"{name}: {line.strip()[:70]}")
    assert not места, "имя робота снова зашито: " + "; ".join(места)


def test_the_forms_do_not_prefill_a_name_the_owner_did_not_choose():
    """`value="Вася"` в форме — это не подсказка, а чужое имя, которое
    владелец случайно подтвердит."""
    page = _code("index.html")
    for поле in ("atName", "wzName"):
        строка = next(ln for ln in page.splitlines() if f'id="{поле}"' in ln)
        assert "value=" not in строка, (
            f"{поле} подставляет имя за владельца: {строка.strip()[:80]}")
        assert "placeholder=" in строка, f"{поле} остался без подсказки"


def test_the_widget_has_one_source_of_truth_for_the_name():
    js = _code("mascot.html")
    assert "ROBOT_NAME" in js and "robotName()" in js, (
        "имя робота в виджете снова размазано по вызовам")
    assert "s.name ||" in js, "имя не берётся из статуса робота"


def test_the_panel_says_which_channel_shows_notifications():
    """Владелец, у которого тосты не приходят, должен узнать чем их
    показывают — а не искать это в журнале."""
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    assert '"notify_backend"' in web, "канал уведомлений не доезжает до панели"
    assert "notify_backend" in _code("index.html"), "панель его не рисует"


def test_subscribers_are_listed_and_can_be_revoked():
    """Список без отзыва — опись, а не контроль."""
    web = (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")
    assert '"subscribers"' in web, "панель видит только счётчик подписок"
    assert "api_push_forget" in web, "подписку нечем отозвать"
    assert "/api/phone/forget" in _code("index.html"), (
        "в панели нет кнопки отзыва")


def test_a_subscriber_is_shown_by_host_not_by_its_endpoint():
    """Эндпоинт — ключ доставки. Хост отличает телефон от ноутбука и не
    является секретом."""
    from vibebridge.web import _subscriber_where
    где = _subscriber_where({"endpoint":
                             "https://fcm.googleapis.com/fcm/send/AAA-secret"})
    assert где == "fcm.googleapis.com", где
    assert "secret" not in где
    assert _subscriber_where({}) == "неизвестное устройство"


def test_the_canon_no_longer_promises_an_identity_check():
    """Обещание, которого продукт не выполняет, опаснее отсутствия обещания:
    владелец строит на нём поведение."""
    foundation = (REPO / "docs" / "ux" / "foundation.md").read_text(
        encoding="utf-8")
    p03 = foundation.split("### P-03", 1)[1].split("###", 1)[0]
    # Утверждающая часть — ДО блока поправки: сама поправка ЦИТИРУЕТ старое
    # обещание, объясняя, почему оно неверно. Первая версия этой проверки
    # покраснела именно на цитате — шестой за сессию случай «проза прочитана
    # как код». Граница между утверждением и цитатой здесь структурная, и
    # проверять надо по ней.
    утверждение = p03.split("**Формулировка исправлена", 1)[0]
    assert "а не у того, кто ближе к экрану" not in утверждение, (
        "канон снова обещает опознание, которого мост не делает")
    assert "экран блокировки" in p03, "истинная граница не названа"
    assert "мост никого не опознаёт" in p03, (
        "не сказано главное: продукт не различает людей за одной клавиатурой")


def test_the_family_member_finally_has_a_scenario():
    """Персона, объявленная и не прожитая ни разу, — это допущение, выданное
    за требование."""
    scenarios = (REPO / "docs" / "ux" / "scenarios.md").read_text(
        encoding="utf-8")
    строки = [ln for ln in scenarios.splitlines()
              if ln.startswith("| SCN-") and "| P-03 |" in ln]
    assert строки, "ни один сценарий не относится к P-03"
    assert "### SCN-029" in scenarios
