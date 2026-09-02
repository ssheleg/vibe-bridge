"""Нить разговора — транспорт, а не память, и это ПРОВЕРЯЕТСЯ (U-2).

Мост держал `deque(maxlen=20)` и отправлял её мозгу, а SCN-008 шаг 3, оба
интерфейса и анти-визия говорили обратное: «bridge контекст не ведёт».
Интерфейс, сообщающий противоположное коду, — худший из двух возможных
исходов, потому что владелец строит на нём свои ожидания.

Путь «убрать историю» уже пробовали: мозг отвечал на сказанное час назад
вместо сказанного минуту назад (`robot.py:160`, жалоба владельца). Протокол
`/v1/chat/completions` по построению требует, чтобы тред нёс КЛИЕНТ.

Поэтому граница уточнена, и теперь она держится тестами, а не абзацем:
нить не переживает перезапуск, не пишется на диск, ограничена по длине и
обрывается кнопкой «Новый».
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import vibebridge

REPO = Path(vibebridge.__file__).resolve().parents[1]
WEBUI = Path(vibebridge.__file__).parent / "webui"
sys.path.insert(0, str(REPO))


def _web_source() -> str:
    return (Path(vibebridge.__file__).parent / "web.py").read_text(
        encoding="utf-8")


def test_the_thread_is_bounded():
    """Без потолка нить перестаёт быть нитью и становится памятью."""
    source = _web_source()
    found = re.search(r"chat_history\.setdefault\([^)]*deque\(maxlen=(\d+)\)",
                      source)
    assert found, "нить разговора больше не ограничена по длине"
    assert 0 < int(found.group(1)) <= 50, (
        f"потолок нити {found.group(1)} — это уже память, а не нить сессии")


def test_the_thread_never_reaches_the_disk():
    """Обещание «не переживает перезапуск» держится ровно на этом."""
    from dataclasses import fields

    from vibebridge.state import BridgeState
    имена = {f.name for f in fields(BridgeState)}
    for подозрительное in ("chat_history", "chat_thread", "history"):
        assert подозрительное not in имена, (
            f"нить разговора попала в состояние на диске («{подозрительное}») "
            f"— это уже память")
    saved = json.dumps({f.name: str(f.type) for f in fields(BridgeState)})
    assert "chat" not in saved.lower() or "chat_url" in saved, saved


def test_the_owner_can_cut_the_thread():
    """«Новый» обязан обрывать нить, иначе стереть её нечем."""
    source = _web_source()
    # Свойство: нить обрывается — и ТОЛЬКО своя. `clear()` стирал все сессии,
    # включая панельную (U-11), поэтому ассерт на него теперь означал бы
    # «верните дефект».
    assert "chat_history.pop(" in source, (
        "нить нечем оборвать — обещание владельцу не выполняется")
    assert "chat_history.clear()" not in source, (
        "«Новый» снова стирает чужую сессию вместе со своей")


def test_no_surface_claims_the_bridge_keeps_no_context():
    """Интерфейс, сообщающий противоположное коду, — это и была находка."""
    ложь = ("контекст разговора живёт у мозга робота, не на панели",
            "контекст разговора живёт у его мозга",
            "bridge контекст не ведёт")
    места = []
    for name in ("index.html", "mascot.html"):
        text = (WEBUI / name).read_text(encoding="utf-8")
        места += [f"{name}: «{s}»" for s in ложь if s in text]
    scenarios = (REPO / "docs" / "ux" / "scenarios.md").read_text(
        encoding="utf-8")
    места += [f"scenarios.md: «{s}»" for s in ложь if s in scenarios]
    assert not места, (
        "поверхность обещает то, чего код не делает: " + "; ".join(места))


def test_the_vision_states_the_boundary_rather_than_a_flat_ban():
    """Анти-визия запрещала память без оговорок, и код ей противоречил.
    Запрет, который нарушает собственный продукт, не защищает ничего."""
    vision = (REPO / "docs" / "ux" / "vision.md").read_text(encoding="utf-8")
    block = vision.split("Не второй мозг", 1)[1].split("- **", 1)[0]
    for обещание in ("не переживает перезапуск", "Новый"):
        assert обещание in block, (
            f"визия не называет границу «{обещание}» — значит запрет снова "
            f"шире того, что делает код")
