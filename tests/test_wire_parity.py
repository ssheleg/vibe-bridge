"""Wire-парность — гейт вместо прозы.

Правило «`mcp` бампается только вместе с `HERMES_VERSION` робота» жило в
четырёх документах и ни в одной проверке, а сам пин стоял в ДВУХ местах —
`pyproject.toml` и `scripts/build_app.sh`, — которые ничто не связывало
(A-15). Парность оболочки и payload при этом гейтится скриптом сборки:
шаблон в проекте был, эта пара его пропустила.

Здесь три проверки, и они отвечают на разные вопросы:

  1. пин существует в ОДНОМ месте — сборка его читает, а не повторяет;
  2. объявленная парность не разошлась с настоящей зависимостью;
  3. объявленная версия Hermes совпадает с той, что пинит робот.

Третья — кросс-репозиторная. Дерева робота на чужой машине нет, и это
названо вслух: тест пропускается С ПРИЧИНОЙ, а не молча зеленеет.
"""
from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _wire() -> dict:
    wire = _pyproject()["tool"]["vibebridge"]["wire"]
    assert wire.get("mcp") and wire.get("hermes"), \
        "секция [tool.vibebridge.wire] обязана называть обе стороны пары"
    return wire


def test_the_pin_is_declared_once_and_the_build_reads_it():
    """Второй литерал пина в скрипте сборки — это вторая правда, которая
    расходится молча: `pyproject` бампают, `.sh` забывают, и в DMG уезжает
    не та генерация протокола."""
    script = (ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in script.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert not re.search(r'mcp==\d', code), \
        "скрипт сборки снова держит собственный пин mcp"
    assert "vibebridge" in code and "wire" in code, \
        "скрипт сборки не читает пин из pyproject"


def test_the_declared_pin_matches_the_actual_dependency():
    """Объявление, которое можно забыть обновить, — это тоже вторая копия.
    Гейт держит его сцепленным с настоящей зависимостью."""
    wire = _wire()
    deps = _pyproject()["project"]["dependencies"]
    pinned = [d for d in deps if d.replace(" ", "").startswith("mcp==")]
    assert len(pinned) == 1, f"ожидался ровно один пин mcp, найдено: {pinned}"
    assert pinned[0].replace(" ", "") == f"mcp=={wire['mcp']}", (
        f"[tool.vibebridge.wire].mcp = {wire['mcp']}, "
        f"а зависимость — {pinned[0]}")


def _robot_repo() -> Path | None:
    """Дерево робота, если оно на этой машине есть."""
    env = os.environ.get("VIBE_BRIDGE_ROBOT_REPO")
    candidates = [Path(env)] if env else []
    candidates.append(Path.home() / "DATA" / "microcontrollers"
                      / "robot-vibecoder")
    for path in candidates:
        if (path / "requirements" / "orchestrators.env").is_file():
            return path
    return None


def test_the_pin_matches_the_hermes_the_robot_runs():
    """Настоящая парность: пин выбран ПОД конкретный мозг, и когда робот
    бампает `HERMES_VERSION`, мост обязан узнать об этом здесь, а не на
    живом вызове."""
    repo = _robot_repo()
    if repo is None:
        pytest.skip("дерева робота на этой машине нет — парность с "
                    "HERMES_VERSION НЕ проверена (задайте "
                    "VIBE_BRIDGE_ROBOT_REPO)")
    env = (repo / "requirements" / "orchestrators.env").read_text(
        encoding="utf-8")
    match = re.search(r"^HERMES_VERSION=(.+)$", env, flags=re.M)
    assert match, "у робота не нашёлся HERMES_VERSION"
    theirs = match.group(1).strip()
    ours = _wire()["hermes"]
    assert theirs == ours, (
        f"мост собран под Hermes {ours}, робот пинит {theirs} — "
        f"бампайте `mcp` и [tool.vibebridge.wire] вместе с ним")
