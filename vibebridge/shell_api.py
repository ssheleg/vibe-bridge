"""Шов «оболочка → payload»: что payload ТРЕБУЕТ от .app, в одном месте.

Оболочка и payload обновляются порознь (ADR-0006): payload прилетает сам,
оболочка — только новым подписанным .app. Значит payload всегда может
оказаться новее той оболочки, в которой запущен, и вопрос «а есть ли там то,
что я зову» — не теоретический.

До этого шов существовал только в головах: `vbboot.__all__` перечислял один
`layout`, хотя payload импортирует ещё и `runner.shell_version`, а
`SHELL_MIN` в сборщике был константой `"0.1.0"`, замороженной на первом
релизе. Константу, которую надо ВСПОМНИТЬ поднять, не поднимают: забытая, она
даёт `AttributeError` из фонового потока вместо честного «нужен новый .app».

Поэтому здесь:

* `REQUIRED` — список имён с версией оболочки, в которой имя появилось;
* `SHELL_MIN` СЧИТАЕТСЯ как максимум этих версий, а не помнится;
* `require_shell()` проверяет их наличие ДО работы и говорит словами.

Модуль намеренно ничего не импортирует из `vbboot` на уровне модуля и не
берёт `layout.parse`: он существует ровно для случая, когда оболочка старая
или неполная, и падать на импорте того, что проверяешь, — то же самое, что
не проверять.
"""
from __future__ import annotations

import importlib
import sys

#: Имя → версия ОБОЛОЧКИ, начиная с которой имя в ней есть. Только то, что
#: зовёт PAYLOAD: `layout.installed` живёт в самой оболочке
#: (`runner.py`), и требовать его отсюда — поднимать пол за чужой код.
#: Добавляя обращение к новому символу оболочки, добавляйте строку СЮДА —
#: `tests/test_shell_seam.py` сверяет этот список с тем, что код реально
#: зовёт, и не даст ему разойтись молча.
REQUIRED: dict[str, dict[str, str]] = {
    "vbboot.layout": {
        "active_version": "0.1.0",
        "mark_installed": "0.1.0",
        "parse": "0.1.0",
        "payload_root": "0.1.0",
        "prune": "0.1.0",
    },
    "vbboot.runner": {
        "shell_version": "0.1.0",
    },
}


#: ОБРАТНОЕ направление шва: что оболочка ПЕРЕДАЁТ payload, и во что
#: превращается работа моста, если не передала. Версий здесь нет намеренно:
#: релизов оболочки с тегом не было ни одного (`git tag -l 'shell-v*'` пуст),
#: а знать наверняка можно и без версии — payload видит сам ФАКТ получения.
#:
#: Направление важно различать, потому что лечится оно иначе. Когда payload
#: ЗОВЁТ отсутствующее — это `AttributeError`, и правильный ответ отказать
#: (`SHELL_MIN`). Когда payload не ПОЛУЧИЛ переданное — ничего не падает, и
#: отказывать нельзя: поднять пол ради косметики значит отсечь исправные
#: оболочки от всего payload. Правильный ответ — сказать вслух (B-45).
SHELL_PROVIDES: dict[str, str] = {
    "chosen": ("источник кода и откат показываются по догадке, а не по "
               "ответу оболочки"),
}


def not_provided(**received: object) -> list[str]:
    """Что из `SHELL_PROVIDES` оболочка не передала. Ключи — имена оттуда."""
    unknown = set(received) - set(SHELL_PROVIDES)
    if unknown:
        raise KeyError(f"не объявлено в SHELL_PROVIDES: {sorted(unknown)}")
    return [name for name in SHELL_PROVIDES if received.get(name) is None]


def degradation(names: list[str], have: str | None) -> str:
    """Фраза про то, ЧТО именно теперь неточно, а не «что-то не так»."""
    shell = f"оболочка {have}" if have else "оболочка"
    effects = "; ".join(SHELL_PROVIDES[name] for name in names)
    return (f"{shell} не передаёт: {', '.join(names)} — {effects}. "
            f"Лечится новым .app; мост работает")


def _parse(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(version).split("."))
    except (TypeError, ValueError):
        return None


def _shell_min() -> str:
    """Самая старая оболочка, где есть ВСЁ требуемое."""
    best, text = (0,), "0.0.0"
    for names in REQUIRED.values():
        for since in names.values():
            parsed = _parse(since)
            if parsed and parsed > best:
                best, text = parsed, since
    return text


#: Считается из `REQUIRED`, а не пишется рядом с ним. Сборщик payload берёт
#: ЭТО значение — забыть поднять «свою» копию больше негде.
SHELL_MIN = _shell_min()


class ShellTooOld(RuntimeError):
    """Оболочка не даёт того, что payload зовёт. Текст — для владельца."""


def missing(importer=importlib.import_module) -> list[str]:
    """Чего из `REQUIRED` нет в установленной оболочке.

    `importer` подменяем в тестах: настоящую старую оболочку в тесте не
    поставить, а проверять надо именно её.
    """
    gaps: list[str] = []
    for module_name, names in REQUIRED.items():
        try:
            module = importer(module_name)
        except Exception:                   # noqa: BLE001
            gaps.extend(f"{module_name}.{name}" for name in sorted(names))
            continue
        gaps.extend(f"{module_name}.{name}" for name in sorted(names)
                    if not hasattr(module, name))
    return gaps


def refusal(gaps: list[str], have: str | None) -> str:
    """Одна фраза, из которой владельцу понятно, что делать."""
    installed = f"установлена {have}" if have else "версия оболочки неизвестна"
    return (f"этому мосту нужна оболочка {SHELL_MIN} или новее, {installed} — "
            f"нужен новый .app; не хватает: {', '.join(gaps)}")


def require_shell(importer=importlib.import_module) -> None:
    """Поднять `ShellTooOld`, если шов не выполнен. Иначе — молча вернуться."""
    gaps = missing(importer)
    if not gaps:
        return
    have = None
    try:
        have = importer("vbboot.runner").shell_version()
    except Exception:                       # noqa: BLE001
        # молчим: мы уже в ветке «оболочка не та», и версия здесь —
        # уточнение в тексте, а не условие. Уронить проверку совместимости на
        # попытке узнать версию — значит вернуть тот самый AttributeError.
        pass
    raise ShellTooOld(refusal(gaps, have))


def complain(message: str) -> None:
    """Сказать владельцу там, где он увидит: stderr и уведомление macOS.

    Своя копия, а не вызов `vbboot.__main__._complain`: этот код бежит ровно
    тогда, когда на оболочку полагаться нельзя, — и приватного помощника в
    старой оболочке может не быть вовсе.
    """
    print(f"vibe-bridge: {message}", file=sys.stderr)
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        text = (message.replace("\\", "\\\\").replace('"', '\\"')
                       .replace("\n", " ").replace("\r", " "))
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "vibe-bridge"'],
            check=False, timeout=10)
    except Exception:                       # noqa: BLE001
        # молчим: это САМА жалоба. Ронять процесс из-за неудавшегося
        # уведомления — оставить владельца вообще без объяснения.
        pass


def bundle_resources():
    """`Contents/Resources` подписанного .app — или None вне бандла.

    Живёт ЗДЕСЬ, а не в веб-слое. Это факт про устройство оболочки, а не про
    HTTP: якорь доверия обновлений доставался через приватный
    `web._bundle_resources`, то есть ветка автообновления зависела от детали
    модуля маршрутов, а трей писал тот же обход `parents` заново (F-8).

    Опора — `vbboot`, никогда не этот файл. `vbboot` — оболочка и бандл не
    покидает; `vibebridge` — payload и переезжает в Application Support при
    первом же обновлении. Опершись на себя, функция нашла бы бандл ровно
    один раз, на свежей установке, а каждое следующее обновление отказывало
    бы себе за неимением публичного ключа.

    None — это то, что получает разработческий чекаут, и `install` из-за
    этого отказывает НАМЕРЕННО: якорь доверия — подписанный бандл, и код вне
    его не имеет канала, которому вправе верить.
    """
    from pathlib import Path

    import vbboot
    for parent in Path(vbboot.__file__).resolve().parents:
        if parent.name == "Resources" and parent.parent.name == "Contents":
            return parent
    return None
