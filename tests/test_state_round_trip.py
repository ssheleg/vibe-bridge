"""Состояние переживает рестарт ЦЕЛИКОМ, а не по списку из четырнадцати имён.

`save()` пишет `asdict(self)` — все поля. `load()` до 2026-09-02 перечисляла
их руками, и пятнадцатое поле писалось на диск и молча терялось при каждом
рестарте: файл правильный, состояние пустое, ошибки никакой (F-10).

Проверка здесь — на КЛАСС: любое поле, добавленное завтра, обязано пережить
круг. Тест, перечисляющий поля, повторил бы ту же ошибку одним слоем выше.
"""
from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibebridge.state import BridgeState  # noqa: E402

#: Значение, которое ОТЛИЧАЕТСЯ от умолчания, — иначе круг «сохранили и
#: прочитали» пройдёт даже у поля, которое `load` не читает вовсе.
#: Аннотации в dataclass — СТРОКИ (`from __future__ import annotations`),
#: поэтому и ключи здесь строки. Первая версия писала сюда типы Python и
#: не совпала ни с одним полем: проверка прошла бы вхолостую, если бы не
#: явный ассерт «поле не покрыто».
SAMPLES = {
    "str": "не-умолчание",
    "str | None": "не-умолчание",
    "bool": True,
    "int": 7,
    "float": 12345.0,
    "float | None": 12345.0,
    "list": [{"проба": 1}],
    "list | None": [{"проба": 1}],
}


def _sample(field):
    return SAMPLES.get(str(field.type))


def test_every_field_survives_a_restart(tmp_path):
    """Круг save → load для КАЖДОГО поля, со значением, отличным от
    умолчания."""
    path = tmp_path / "state.json"
    state = BridgeState(path=path, panel_token="ключ-панели")
    подставлено = {}
    for f in fields(BridgeState):
        if f.name == "path":
            continue
        value = _sample(f)
        assert value is not None, (
            f"поле «{f.name}» типа {f.type} не покрыто: допишите образец в "
            f"SAMPLES, иначе проверка молча его пропустит")
        if f.name == "mode":
            value = "standalone"          # осмысленное не-умолчание
        if f.name == "panel_token":
            value = "ключ-панели"
        setattr(state, f.name, value)
        подставлено[f.name] = value
    state.save()

    снова = BridgeState.load(path)
    потеряно = {name: (value, getattr(снова, name))
                for name, value in подставлено.items()
                if getattr(снова, name) != value}
    assert not потеряно, (
        "поля не пережили рестарт: " +
        "; ".join(f"{n}: записали {w!r}, прочитали {g!r}"
                  for n, (w, g) in потеряно.items()))


def test_a_field_added_tomorrow_is_read_without_touching_load():
    """Свойство, ради которого список убран: `load` не содержит перечисления.

    Ассерт на ИСХОДНИК здесь уместен ровно потому, что проверяется отсутствие
    ручного списка, а его иначе не увидеть.
    """
    source = (Path(BridgeState.__module__.replace(".", "/") + ".py")
              if False else
              Path(__file__).resolve().parents[1] / "vibebridge" / "state.py")
    text = source.read_text(encoding="utf-8")
    body = text.split("def load(", 1)[1].split("def ", 1)[0]
    for name in ("robot_base_url", "vapid_private", "pet_pos"):
        assert f"{name}=data.get" not in body.replace(" ", ""), (
            f"в `load` снова ручной список полей (нашлось «{name}»)")


def test_a_file_from_a_newer_bridge_does_not_break_an_older_one(tmp_path):
    """Откат обязан работать (ADR-0006): файл с незнакомым ключом читается,
    а не роняет мост."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "panel_token": "ключ", "mode": "standalone",
        "поле_из_будущего": {"что-то": [1, 2, 3]},
    }, ensure_ascii=False), encoding="utf-8")
    state = BridgeState.load(path)
    assert state.panel_token == "ключ"
    assert state.mode == "standalone"


def test_a_state_file_without_a_panel_token_fails_loudly(tmp_path):
    """Молчаливый новый токен означал бы, что все открытые панели разлогинены
    и никто не сказал почему."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mode": "standalone"}), encoding="utf-8")
    with pytest.raises(TypeError):
        BridgeState.load(path)
