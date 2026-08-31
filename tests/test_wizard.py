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


def test_provision_script_shreds_token_and_pairs():
    s = wiz.provision_script()
    assert "shred -u" in s
    assert "/pair" in s and "curl -fsS" in s
    assert "chmod 600 /var/lib/robot-bridge-credentials.json" in s
    assert "systemctl disable robot-provision.service" in s


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
