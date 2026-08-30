"""Self-update — fetch a payload, prove it is ours, put it in place.

The bridge already holds the owner's screen, clipboard and Apple Events. A
payload installed here inherits every one of those on the next launch, so this
module's real subject is not downloading — it is refusing. The order is
deliberate and never rearranged: verify the signature over the exact bytes
received, THEN look inside the archive, THEN write anything to disk. Reading a
tar header before checking the signature would mean parsing an attacker's data
with an attacker's structure.

Trust anchor: the Ed25519 public key ships inside the signed .app (ADR-0006).
Its absence is refusal, never a skipped check.

Nothing here raises. An update is optional work on a running bridge; a network
blip must not become a traceback in the tray.
"""
from __future__ import annotations

import json
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vbboot import layout

RELEASE_REPO = "ssheleg/vibe-bridge"
RELEASE_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
PAYLOAD_ASSET = "payload-{version}.tar.gz"
MANIFEST = "payload.json"
_TIMEOUT = 30
_MAX_PAYLOAD = 64 * 1024 * 1024          # a payload is our .py files, not a VM


@dataclass(frozen=True)
class Available:
    """A release newer than what is running, with everything needed to take
    it. Built only when both assets are present — a payload without its
    signature is not an offer, it is an incomplete publish."""
    version: str
    payload_url: str
    sig_url: str
    notes: str = ""


def public_key_bytes(key: Ed25519PublicKey) -> bytes:
    """The 32 raw bytes we ship in the bundle — no PEM, no parsing surface."""
    return key.public_bytes(serialization.Encoding.Raw,
                            serialization.PublicFormat.Raw)


def bundled_public_key(resources: Path | None = None) -> bytes | None:
    """Read the release key from the signed bundle. None when absent, and
    None means *refuse*, not *trust* — see `install`."""
    if resources is None:
        return None
    try:
        raw = (resources / "release_pubkey.raw").read_bytes()
    except OSError:
        return None
    return raw if len(raw) == 32 else None


def verify(data: bytes, signature: bytes, pubkey: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def install(blob: bytes, signature: bytes, version: str, root: Path, *,
            pubkey: bytes | None, shell_version: str) -> tuple[bool, str]:
    """Install `blob` as `version`. Returns (ok, reason) and never raises.

    On any refusal the payload directory is left exactly as it was: the
    version is extracted to a scratch directory and only stamped complete
    once every check has passed, so `layout.installed` cannot see a partial
    install even if this process dies mid-write.
    """
    if not pubkey:
        return False, "нет публичного ключа релизов — обновление отклонено"
    if not signature or not verify(blob, signature, pubkey):
        return False, "подпись payload не сошлась — обновление отклонено"

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"каталог payload недоступен: {exc}"

    tmp = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=root))
    try:
        ok, why = _extract(blob, tmp)
        if not ok:
            return False, why

        manifest = _read_manifest(tmp)
        if manifest is None:
            return False, "в payload нет payload.json — обновление отклонено"
        if manifest.get("version") != version:
            return False, (f"версия в payload ({manifest.get('version')}) "
                           f"не совпадает с релизом ({version})")
        if not _shell_supports(manifest.get("shell_min", "0.0.0"),
                               shell_version):
            return False, (f"payload требует оболочку "
                           f"{manifest.get('shell_min')}, установлена "
                           f"{shell_version} — нужен новый .app")
        if not (tmp / "vibebridge" / "__init__.py").exists():
            return False, "в payload нет пакета vibebridge — отклонено"

        final = root / version
        if final.exists():
            _rmtree(final)
        tmp.rename(final)
        layout.mark_installed(root, version)   # stamped LAST — see layout.py
        return True, f"версия {version} установлена"
    except OSError as exc:
        return False, f"не удалось установить обновление: {exc}"
    finally:
        _rmtree(tmp)


def check(*, current: str, fetch=None) -> Available | None:
    """Ask the release channel what the newest version is. None means "no
    update to take" for every reason there is — current, older, unreachable,
    malformed, or published without a signature."""
    fetch = fetch or _fetch_json
    try:
        data = fetch(RELEASE_API)
        tag = str(data["tag_name"]).lstrip("vV")
        assets = {a["name"]: a["browser_download_url"]
                  for a in data.get("assets", [])}
    except (KeyError, TypeError, ValueError, OSError,
            urllib.error.URLError):
        return None

    new, now = layout.parse(tag), layout.parse(current)
    if not new or not now or new <= now:
        return None

    payload = PAYLOAD_ASSET.format(version=tag)
    if payload not in assets or f"{payload}.sig" not in assets:
        return None
    return Available(version=tag, payload_url=assets[payload],
                     sig_url=assets[f"{payload}.sig"],
                     notes=str(data.get("body") or ""))


def download(url: str, *, opener=None) -> bytes | None:
    """Fetch an asset, capped. None on any failure — callers treat that as
    "no update this time", not as an error to surface."""
    opener = opener or urllib.request.urlopen
    try:
        with opener(url, timeout=_TIMEOUT) as resp:
            blob = resp.read(_MAX_PAYLOAD + 1)
    except Exception:                       # noqa: BLE001 - never raises
        return None
    return None if len(blob) > _MAX_PAYLOAD else blob


def fetch_and_install(found: Available, root: Path, *, pubkey: bytes | None,
                      shell_version: str, opener=None) -> tuple[bool, str]:
    """The whole take-an-update path, from two URLs to a stamped version."""
    blob = download(found.payload_url, opener=opener)
    if blob is None:
        return False, "не удалось скачать payload"
    sig = download(found.sig_url, opener=opener)
    if sig is None:
        return False, "не удалось скачать подпись payload"
    return install(blob, sig, found.version, root, pubkey=pubkey,
                   shell_version=shell_version)


# ------------------------------------------------------------------ helpers

def _extract(blob: bytes, dest: Path) -> tuple[bool, str]:
    """Unpack with `filter="data"`: absolute paths, `..` and device nodes are
    rejected by the stdlib rather than by our own review of the tar."""
    try:
        with tarfile.open(fileobj=_reader(blob), mode="r:gz") as tar:
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        return False, f"payload не распаковался: {exc}"
    return True, ""


def _reader(blob: bytes):
    import io
    return io.BytesIO(blob)


def _read_manifest(root: Path) -> dict | None:
    try:
        data = json.loads((root / MANIFEST).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _shell_supports(required: str, have: str) -> bool:
    need, got = layout.parse(str(required)), layout.parse(str(have))
    return bool(need and got and got >= need)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "vibe-bridge-updater"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read(2 * 1024 * 1024))


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
