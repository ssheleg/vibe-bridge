"""T-WIZARD (core slice): the FAT-side generators and the pairing protocol
— SCN-013's file mechanics and SCN-015's «найден → связан» seam.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from starlette.testclient import TestClient

from vibebridge import wizard as wiz
from vibebridge.audit import AuditLog
from vibebridge.consent import ConsentEngine
from vibebridge.robot import RobotClient
from vibebridge.state import BridgeState
from vibebridge.web import build_app

# ── generators ──────────────────────────────────────────────────────────────

def test_firstrun_carries_wifi_hostname_and_self_delete():
    s = wiz.firstrun_script(hostname="robot-vasya", ssid="Home-5G",
                            psk="secret pass")
    assert "robot-vasya" in s
    assert "ssid=Home-5G" in s and "psk=secret pass" in s
    assert "system-connections/robot-wifi.nmconnection" in s
    assert "rm -f /boot/firstrun.sh" in s          # self-delete, Imager-style
    assert "sed -i 's| systemd.run.*||g'" in s
    assert "robot-provision.service" in s


def test_cmdline_patch_appends_once():
    base = "console=serial0,115200 root=PARTUUID=x rootwait\n"
    once = wiz.cmdline_patch(base)
    assert "systemd.run=/boot/firstrun.sh" in once
    assert "systemd.run_success_action=reboot" in once
    assert wiz.cmdline_patch(once) == once          # idempotent


def _run_provision(tmp_path, *, pair_exit: int, with_client: bool = True,
                   already_paired: bool = False) -> dict:
    """Выполнить НАСТОЯЩИЙ скрипт провижининга под временным корнем.

    Скрипт с карты — единственный код проекта, который до сих пор никогда не
    исполнялся набором (A-39 про тот же класс в JS). Здесь он запускается
    целиком: `git`, `systemctl`, `sleep` и клиент пейринга робота подменены
    заглушками на PATH, каждая пишет свой вызов в журнал.
    """
    import os
    import subprocess

    root = tmp_path / "root"
    (root / "boot" / "firmware").mkdir(parents=True)
    (root / "var" / "lib").mkdir(parents=True)
    (root / "boot" / "firmware" / wiz.TOKEN_FILENAME).write_text(
        wiz.pairing_file(token="tok-1", bridge_url="https://mac.ts.net",
                         name="Вася"), encoding="utf-8")
    calls = root / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def stub(name: str, body: str) -> None:
        p = bin_dir / name
        p.write_text(f"#!/bin/bash\necho \"{name} $*\" >> {calls}\n{body}\n")
        p.chmod(0o755)

    stub("git", 'mkdir -p "$3/.git" "$3/scripts"; exit 0')
    stub("systemctl", "exit 0")
    stub("sleep", "exit 0")            # иначе провал ждал бы десять минут
    stub("shred", 'rm -f "${@: -1}"; exit 0')

    robot = root / "opt" / "robot" / "scripts"
    robot.mkdir(parents=True)
    (root / "opt" / "robot" / ".git").mkdir(parents=True)
    (robot / "deploy.sh").write_text(f'#!/bin/bash\necho "deploy.sh $*" >> {calls}\n')
    if with_client:
        # Клиент робота: --status говорит «спарен?», --from-file пейрит.
        (robot / "bridge_pair.py").write_text(
            f'import sys\n'
            f'open({str(calls)!r}, "a").write("bridge_pair " + " ".join(sys.argv[1:]) + "\\n")\n'
            f'sys.exit(0 if ("--status" in sys.argv and {already_paired!r}) '
            f'else ({pair_exit} if "--from-file" in sys.argv else 1))\n')

    script = tmp_path / "provision.sh"
    script.write_text(wiz.provision_script(), encoding="utf-8")
    env = dict(os.environ,
               PATH=f"{bin_dir}:{os.environ['PATH']}",
               VB_PROVISION_ROOT=str(root))
    proc = subprocess.run(["bash", str(script)], env=env, timeout=120,
                          capture_output=True, text=True)
    return {"rc": proc.returncode, "root": root,
            "calls": calls.read_text(encoding="utf-8") if calls.exists() else "",
            "stderr": proc.stderr}


def test_provision_pairs_through_the_robots_own_client(tmp_path):
    """A-5: карта POST'ила в /pair только {token, name}. Адрес робота знает
    ТОЛЬКО робот, и без него мост говорит «связан ✓», а панель — «не
    подключён». Протокол не должен существовать во второй реализации:
    пейринг делает `scripts/bridge_pair.py` робота."""
    got = _run_provision(tmp_path, pair_exit=0)
    assert "bridge_pair --from-file" in got["calls"]
    seed = got["root"] / "var" / "lib" / "robot-pairing.json"
    assert f"--from-file {seed}" in got["calls"]
    # Никакого второго клиента протокола в скрипте нет.
    src = wiz.provision_script()
    assert "curl" not in src and "/pair" not in src


def test_provision_installs_with_the_on_device_installer(tmp_path):
    """`pi-deploy.sh` — инструмент ДЕВ-МАШИНЫ: он отправляет сборку роботу
    через мост. На самом роботе устанавливает `scripts/deploy.sh`."""
    got = _run_provision(tmp_path, pair_exit=0)
    assert "deploy.sh" in got["calls"]
    assert "pi-deploy.sh" not in wiz.provision_script()


def test_provision_starts_the_bridge_api_after_pairing(tmp_path):
    """Юнит робота гейтится наличием creds — до пейринга он не может
    стартовать. Значит поднять его обязан тот, кто создал creds."""
    got = _run_provision(tmp_path, pair_exit=0)
    assert "systemctl enable --now bridge-api.service" in got["calls"]


def test_provision_closes_itself_only_when_pairing_succeeded(tmp_path):
    """A-30: `provision.done` и `disable` выполнялись безусловно — спящий
    Мак стоил владельцу всей карты. Провал обязан оставлять юнит включённым."""
    ok = _run_provision(tmp_path, pair_exit=0)
    assert (ok["root"] / "var" / "lib" / "robot-provision.done").exists()
    assert "systemctl disable robot-provision.service" in ok["calls"]

    bad = _run_provision(tmp_path / "second", pair_exit=1)
    assert not (bad["root"] / "var" / "lib" / "robot-provision.done").exists()
    assert "disable robot-provision.service" not in bad["calls"]
    # Семя пейринга уцелело — следующая загрузка попробует им же.
    assert (bad["root"] / "var" / "lib" / "robot-pairing.json").exists()


def test_provision_shreds_the_seed_only_after_success(tmp_path):
    ok = _run_provision(tmp_path, pair_exit=0)
    assert not (ok["root"] / "var" / "lib" / "robot-pairing.json").exists()
    # ...а с FAT секрет уходит сразу, до всякой сети (research B §5).
    assert not (ok["root"] / "boot" / "firmware" / wiz.TOKEN_FILENAME).exists()


def test_provision_does_not_repair_an_already_paired_robot(tmp_path):
    """Идемпотентность: юнит может отработать второй раз (done потерян,
    карта переставлена) — повторный пейринг сжёг бы рабочий токен."""
    got = _run_provision(tmp_path, pair_exit=0, already_paired=True)
    assert "--from-file" not in got["calls"]
    assert (got["root"] / "var" / "lib" / "robot-provision.done").exists()


def test_prepare_boot_partition_writes_files(tmp_path):
    (tmp_path / "cmdline.txt").write_text("console=tty1 root=x rootwait\n")
    written = wiz.prepare_boot_partition(
        tmp_path, hostname="robot-vasya", ssid="Home", psk="pass123",
        token="tok-1", bridge_url="https://mac.ts.net/", name="Вася")
    assert set(written) == {"firstrun.sh", "cmdline.txt",
                            wiz.TOKEN_FILENAME}
    assert "systemd.run=" in (tmp_path / "cmdline.txt").read_text()
    pairing = json.loads((tmp_path / wiz.TOKEN_FILENAME).read_text())
    assert pairing == {"token": "tok-1",
                       "bridge_url": "https://mac.ts.net", "name": "Вася"}


# ── pairing protocol over HTTP ──────────────────────────────────────────────

def _app(tmp_path):
    state = BridgeState(path=tmp_path / "s.json", panel_token="panel-secret")
    robot = RobotClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"version": "v9"}))))
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, capabilities={}, robot=robot,
                    mcp_allowed_hosts=["testserver", "127.0.0.1:*"])
    return app, state, robot


def test_pair_flow_one_shot(tmp_path):
    app, state, robot = _app(tmp_path)
    with TestClient(app) as c:
        # без токена панели старт запрещён
        assert c.post("/api/wizard/pairing/start").status_code == 401
        c.get("/?token=panel-secret")
        start = c.post("/api/wizard/pairing/start").json()
        assert start["token"] and start["bridge_url"]
        # робот приходит с токеном
        r = c.post("/pair", json={
            "token": start["token"], "name": "Вася",
            "base_url": "http://100.123.65.23:8630",
            "chat_url": "http://100.123.65.23:8642",
            "chat_key": "hermes-key"})
        assert r.status_code == 200
        got = r.json()
        assert got["robot_token"] and "/mcp" in got["mcp_url"]
        # состояние сохранено и клиент перенастроен на лету
        assert state.robot_name == "Вася"
        assert state.robot_base_url == "http://100.123.65.23:8630"
        assert robot.configured and robot.name == "Вася"
        # одноразовость: повтор того же токена — 403
        again = c.post("/pair", json={"token": start["token"], "name": "x"})
        assert again.status_code == 403
        # журнал видел и связку
        j = c.get("/api/journal?limit=3").json()
        assert any("связан" in e.get("line", "") for e in j["entries"])


def test_pair_wrong_token_is_403_and_audited(tmp_path):
    app, state, _ = _app(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        c.post("/api/wizard/pairing/start")
        r = c.post("/pair", json={"token": "wrong", "name": "x"})
        assert r.status_code == 403
        j = c.get("/api/journal?limit=2").json()
        assert any("неверным токеном" in e.get("line", "")
                   for e in j["entries"])
        assert state.robot_name is None            # ничего не записано


def test_wizard_prepare_endpoint(tmp_path):
    app, state, _ = _app(tmp_path)
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "cmdline.txt").write_text("console=tty1 root=x rootwait\n")
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        bad = c.post("/api/wizard/prepare", json={
            "mount_path": str(tmp_path), "ssid": "s", "psk": "p",
            "name": "Вася"})
        assert bad.status_code == 400              # не boot-раздел
        ok = c.post("/api/wizard/prepare", json={
            "mount_path": str(boot), "ssid": "Home", "psk": "pass",
            "name": "Вася"}).json()
        assert ok["ok"] is True
        assert wiz.TOKEN_FILENAME in ok["written"]
        assert state.pending_pair_token            # armed
        pairing = json.loads((boot / wiz.TOKEN_FILENAME).read_text())
        assert pairing["token"] == state.pending_pair_token


def test_disks_endpoint_returns_list(tmp_path):
    app, *_ = _app(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        disks = c.get("/api/wizard/disks").json()["disks"]
        assert isinstance(disks, list)             # live smoke on macOS


def test_pair_without_chat_key_defaults_to_robot_token(tmp_path):
    """bridge_api робота авторизует чат тем же robot_token — мост обязан
    использовать его как чат-ключ, когда отдельный не назван."""
    app, state, robot = _app(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        start = c.post("/api/wizard/pairing/start").json()
        r = c.post("/pair", json={
            "token": start["token"], "name": "Вася",
            "base_url": "http://100.123.65.23:8630",
            "chat_url": "http://100.123.65.23:8630"})
        assert r.status_code == 200
        assert state.robot_chat_key == r.json()["robot_token"]
        assert robot.chat_key == state.robot_chat_key


def test_the_card_carries_the_configured_robot_repository(tmp_path):
    """`prepare_boot_partition` took `repo_url` from the first day and the
    panel never passed it, so every card cloned the hardcoded default — a
    fork's robot would be provisioned from somebody else's repository."""
    from starlette.testclient import TestClient

    from vibebridge.audit import AuditLog
    from vibebridge.config import Settings
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState
    from vibebridge.web import build_app

    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "cmdline.txt").write_text("console=serial0\n")

    state = BridgeState(path=tmp_path / "state.json", panel_token="pt")
    app = build_app(consent=ConsentEngine(),
                    audit=AuditLog(tmp_path / "a.log"), state=state,
                    settings=Settings(robot_repo="https://example.org/mine.git"))
    c = TestClient(app)
    c.cookies.set("vb_panel", "pt")
    r = c.post("/api/wizard/prepare", json={
        "mount_path": str(boot), "ssid": "wifi", "psk": "pw", "name": "Вася"})
    assert r.status_code == 200, r.text

    written = "\n".join(p.read_text() for p in boot.rglob("*")
                        if p.is_file() and p.suffix in ("", ".sh", ".txt"))
    assert "example.org/mine.git" in written
    assert "ssheleg/rpi-ai-assistant" not in written


def test_pair_without_an_address_does_not_claim_a_connection(tmp_path):
    """A-5, вторая половина: токен принят, адрес не назван — мост обязан
    сказать это, а не показать «связан ✓» рядом с «не подключён»."""
    app, state, robot = _app(tmp_path)
    with TestClient(app) as c:
        c.get("/?token=panel-secret")
        start = c.post("/api/wizard/pairing/start").json()
        r = c.post("/pair", json={"token": start["token"], "name": "Вася"})
        assert r.status_code == 200                # токен настоящий
        assert robot.configured is False           # но подключения нет
        j = c.get("/api/journal?limit=3").json()
        lines = [e.get("line", "") for e in j["entries"]]
        assert any("не назвал свой адрес" in ln for ln in lines)
        assert not any("связан с мостом" == ln.strip() for ln in lines)
