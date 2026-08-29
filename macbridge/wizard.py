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
    """The network-time half: clone → install → pair → shred the token.
    Fail-open ordering: pairing runs even if install had trouble, so the
    owner SEES the robot appear and gets an honest checklist instead of
    silence (SCN-015/016)."""
    return f"""#!/bin/bash
# vibe-bridge robot-provision — runs once, with network, as root.
set -u
REPO="${{1:-{shlex.quote(repo_url)}}}"
TOK=/boot/firmware/{TOKEN_FILENAME}
[ -f "$TOK" ] || TOK=/boot/{TOKEN_FILENAME}
STATE=/var/lib/robot-pairing.json

# 1. Секрет с FAT — в 0600 немедленно, потом шредим оригинал (research B §5).
if [ -f "$TOK" ]; then
  install -m 600 "$TOK" "$STATE"
  shred -u "$TOK" 2>/dev/null || rm -f "$TOK"
fi

# 2. Стек робота: клон + его собственный инсталлер (дальше он живёт своим
#    GitHub-таймером — fleet-канон, никакого SSH).
if [ ! -d /opt/robot ]; then
  git clone --depth 1 "$REPO" /opt/robot || true
fi
if [ -x /opt/robot/pi-deploy.sh ]; then
  (cd /opt/robot && ./pi-deploy.sh --unattended) || true
fi

# 3. Пейринг: предъявить одноразовый токен мосту, получить постоянный ключ.
if [ -f "$STATE" ]; then
  BRIDGE=$(python3 -c "import json;print(json.load(open('$STATE'))['bridge_url'])")
  TOKEN=$(python3 -c "import json;print(json.load(open('$STATE'))['token'])")
  NAME=$(python3 -c "import json;print(json.load(open('$STATE')).get('name','робот'))")
  for i in $(seq 1 30); do
    RESP=$(curl -fsS --max-time 10 -X POST "$BRIDGE/pair" \\
      -H 'Content-Type: application/json' \\
      -d "{{\\"token\\":\\"$TOKEN\\",\\"name\\":\\"$NAME\\"}}") && break
    sleep 10
  done
  if [ -n "${{RESP:-}}" ]; then
    printf '%s' "$RESP" > /var/lib/robot-bridge-credentials.json
    chmod 600 /var/lib/robot-bridge-credentials.json
    shred -u "$STATE" 2>/dev/null || rm -f "$STATE"
  fi
fi

touch /var/lib/robot-provision.done
systemctl disable robot-provision.service 2>/dev/null || true
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
