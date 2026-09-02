"""Onboarding engine — from a blank SD card to a paired robot, no SSH ever
(ADR-0001, spec §8, research-notes §B).

Everything here is FAT-only: the generators produce the files the wizard
drops onto the boot partition of a stock Raspberry Pi OS image, and the
firstrun mechanics are the OFFICIAL ones (Imager writes the same shape):

  cmdline.txt += systemd.run=/boot/firstrun.sh …reboot   (Bookworm path)
  firstrun.sh   — hostname, NetworkManager Wi-Fi profile, provision unit,
                  then self-delete and strip cmdline
  robot-provision.service — After=network-online.target: clone the robot
                  repo, run its installer, present the pairing token to
                  the bridge, then disable itself
  robot-pairing.json — one-shot token + the bridge's address; consumed and
                  shredded by the provision script (never world-readable
                  after первый бут)

Pairing protocol (the robot's provision script speaks it; M-ROBOT owns the
robot side):  POST {bridge}/pair  {token, name, base_url, chat_url}
           →  200 {robot_token, mcp_url}   — постоянный ключ взамен
           →  403 — токен неверен или уже погашен (одноразовость)
"""
from __future__ import annotations

import json
import secrets
import shlex
from pathlib import Path

ROBOT_REPO_DEFAULT = "https://github.com/ssheleg/rpi-ai-assistant.git"
TOKEN_FILENAME = "robot-pairing.json"


def pairing_token() -> str:
    return secrets.token_urlsafe(24)


def nm_profile(ssid: str, psk: str) -> str:
    """NetworkManager keyfile — wpa_supplicant.conf is dead on Bookworm
    (research-notes §B)."""
    return f"""[connection]
id=robot-wifi
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid={ssid}

[wifi-security]
key-mgmt=wpa-psk
psk={psk}

[ipv4]
method=auto

[ipv6]
method=auto
"""


def provision_unit(repo_url: str = ROBOT_REPO_DEFAULT) -> str:
    """One-shot unit installed into the rootfs by firstrun.sh. Runs WITH
    network (firstrun itself runs before the network exists)."""
    return f"""[Unit]
Description=vibe-bridge: первый запуск робота (клон + инсталлер + пейринг)
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/robot-provision.done

[Service]
Type=oneshot
TimeoutStartSec=3600
ExecStart=/usr/local/sbin/robot-provision.sh {shlex.quote(repo_url)}
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
"""


def provision_script(repo_url: str = ROBOT_REPO_DEFAULT) -> str:
    """The network-time half: clone → install → pair → shred the seed.

    Пейринг делает СОБСТВЕННЫЙ клиент робота (`scripts/bridge_pair.py`), а не
    curl отсюда. Причина не в красоте: свой адрес знает только робот, и
    `/pair` без `base_url` оставляет мост в состоянии «связан ✓, но не
    подключён» — владелец видит зелёную галочку и серый чат (A-5). Вторая
    реализация того же протокола здесь была бы третьей копией одной правды —
    класс, который проект уже разбирал на палитре и на таблице инструментов.

    Устанавливает `scripts/deploy.sh` робота. `pi-deploy.sh` — инструмент
    ДЕВ-МАШИНЫ: он собирает, подписывает и ОТПРАВЛЯЕТ дерево роботу через
    мост; запущенный на самой плате он пытался бы выкатить сам себе.

    Порядок fail-open: установка может не удаться, пейринг всё равно
    пробуется — владелец должен УВИДЕТЬ робота. Но закрывается юнит только
    после успеха: пока робот не связан, `provision.done` не появляется и
    следующая загрузка пробует снова (A-30 — Мак мог просто спать).
    """
    return f"""#!/bin/bash
# vibe-bridge robot-provision — runs once, with network, as root.
set -u
REPO="${{1:-{shlex.quote(repo_url)}}}"
# Корень путей. Юнит запускает скрипт без него (пусто = настоящая система);
# набор подставляет временный каталог и потому исполняет ЭТОТ скрипт целиком.
R="${{VB_PROVISION_ROOT:-}}"
TOK="$R/boot/firmware/{TOKEN_FILENAME}"
[ -f "$TOK" ] || TOK="$R/boot/{TOKEN_FILENAME}"
SEED="$R/var/lib/{TOKEN_FILENAME}"
ROBOT="$R/opt/robot"
PAIR="$ROBOT/scripts/bridge_pair.py"

# 1. Секрет с FAT — в 0600 немедленно, потом шредим оригинал (research B §5).
# Шредим оригинал ТОЛЬКО после удачной копии: `&&` здесь — не стиль, а
# единственный токен пейринга, который иначе исчезает совсем.
if [ -f "$TOK" ]; then
  mkdir -p "$(dirname "$SEED")"
  install -m 600 "$TOK" "$SEED" && {{ shred -u "$TOK" 2>/dev/null || rm -f "$TOK"; }}
fi

# 2. Стек робота: клон + его инсталлер НА УСТРОЙСТВЕ.
if [ ! -d "$ROBOT/.git" ]; then
  git clone --depth 1 "$REPO" "$ROBOT" || true
fi
if [ -f "$ROBOT/scripts/deploy.sh" ]; then
  (cd "$ROBOT" && bash scripts/deploy.sh) || true
fi

# 3. Пейринг клиентом робота. `--status` — его же ответ на вопрос «я уже
#    спарен?»: путь к creds принадлежит роботу, и мост его не повторяет.
PAIRED=0
python3 "$PAIR" --status >/dev/null 2>&1 && PAIRED=1
if [ "$PAIRED" = 0 ] && [ -f "$SEED" ] && [ -f "$PAIR" ]; then
  for _ in $(seq 1 60); do
    python3 "$PAIR" --from-file "$SEED" && {{ PAIRED=1; break; }}
    sleep 10
  done
fi

# 4. Закрываемся ТОЛЬКО связанными. Юнит робота гейтится наличием creds —
#    поднять его обязан тот, кто их создал.
if [ "$PAIRED" = 1 ]; then
  shred -u "$SEED" 2>/dev/null || rm -f "$SEED"
  systemctl enable --now bridge-api.service 2>/dev/null || true
  mkdir -p "$R/var/lib"
  touch "$R/var/lib/robot-provision.done"
  systemctl disable robot-provision.service 2>/dev/null || true
fi
exit 0
"""


def firstrun_script(*, hostname: str, ssid: str, psk: str,
                    repo_url: str = ROBOT_REPO_DEFAULT) -> str:
    """The pre-network half, exactly the Imager mechanism: runs from
    kernel-command-line.target with rootfs rw, installs everything the
    network-time half needs, self-deletes, strips cmdline, reboots."""
    nm = nm_profile(ssid, psk)
    unit = provision_unit(repo_url)
    script = provision_script(repo_url)
    return f"""#!/bin/bash
set +e
# hostname
echo {shlex.quote(hostname)} > /etc/hostname
sed -i "s/127.0.1.1.*/127.0.1.1\\t{hostname}/g" /etc/hosts

# Wi-Fi: NetworkManager keyfile (Bookworm; wpa_supplicant.conf мёртв)
install -d -m 700 /etc/NetworkManager/system-connections
cat > /etc/NetworkManager/system-connections/robot-wifi.nmconnection <<'NMEOF'
{nm}NMEOF
chmod 600 /etc/NetworkManager/system-connections/robot-wifi.nmconnection

# network-time половина: скрипт + oneshot-юнит
cat > /usr/local/sbin/robot-provision.sh <<'PROVEOF'
{script}PROVEOF
chmod 755 /usr/local/sbin/robot-provision.sh
cat > /etc/systemd/system/robot-provision.service <<'UNITEOF'
{unit}UNITEOF
systemctl enable robot-provision.service

# самоудаление — как у официального Imager (customization_generator.cpp)
rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh
sed -i 's| systemd.run.*||g' /boot/cmdline.txt 2>/dev/null
sed -i 's| systemd.run.*||g' /boot/firmware/cmdline.txt 2>/dev/null
exit 0
"""


def cmdline_patch(existing: str) -> str:
    """Append the official systemd.run trigger once; idempotent."""
    line = existing.strip()
    if "systemd.run=" in line:
        return existing
    return (line + " systemd.run=/boot/firstrun.sh "
            "systemd.run_success_action=reboot "
            "systemd.unit=kernel-command-line.target\n")


def pairing_file(*, token: str, bridge_url: str, name: str) -> str:
    return json.dumps({"token": token, "bridge_url": bridge_url.rstrip("/"),
                       "name": name}, ensure_ascii=False, indent=2)


def prepare_boot_partition(mount: Path, *, hostname: str, ssid: str,
                           psk: str, token: str, bridge_url: str,
                           name: str,
                           repo_url: str = ROBOT_REPO_DEFAULT) -> list[str]:
    """Drop the FAT-side files onto a mounted boot partition. Returns the
    list of files written (the wizard shows it). Windows-safe: FAT only."""
    written = []
    fr = mount / "firstrun.sh"
    fr.write_text(firstrun_script(hostname=hostname, ssid=ssid, psk=psk,
                                  repo_url=repo_url), encoding="utf-8")
    written.append(fr.name)
    cl = mount / "cmdline.txt"
    if cl.exists():
        cl.write_text(cmdline_patch(cl.read_text(encoding="utf-8")),
                      encoding="utf-8")
        written.append(cl.name)
    tf = mount / TOKEN_FILENAME
    tf.write_text(pairing_file(token=token, bridge_url=bridge_url,
                               name=name), encoding="utf-8")
    written.append(tf.name)
    return written


def find_boot_volumes() -> list[str]:
    """Mounted Raspberry Pi OS boot partitions (macOS: /Volumes/*): the
    state a card is in right after the official Imager finishes. Identified
    by cmdline.txt — the file our patch targets."""
    import sys
    roots = []
    if sys.platform == "darwin":
        roots = list(Path("/Volumes").glob("*"))
    else:  # pragma: no cover - Linux media mounts, refined in M-PLATFORM
        import os
        user = os.environ.get("USER", "")
        roots = list(Path("/media").glob(f"{user}/*")) + \
            list(Path("/run/media").glob(f"{user}/*"))
    return [str(p) for p in roots
            if (p / "cmdline.txt").exists() and not (p / "mach_kernel").exists()]


def list_removable_disks() -> list[dict]:
    """macOS: external physical disks via diskutil. The system disk can
    never appear here — `external physical` excludes it by construction.
    (Win/Linux enumeration lands with M-PLATFORM.)"""
    import plistlib
    import subprocess
    try:
        out = subprocess.run(
            ["diskutil", "list", "-plist", "external", "physical"],
            capture_output=True, timeout=10)
        data = plistlib.loads(out.stdout)
    except Exception:
        return []
    disks = []
    for d in data.get("AllDisksAndPartitions", []):
        disks.append({
            "device": f"/dev/{d.get('DeviceIdentifier', '')}",
            "size_bytes": d.get("Size", 0),
            "name": (d.get("VolumesFromDisks") or [d.get("Content", "")])[0]
            if d.get("VolumesFromDisks") else d.get("Content", ""),
        })
    return disks
