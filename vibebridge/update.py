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
import re
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vbboot import layout

#: Defaults. The channel in force comes from settings — a fork that builds its
#: own .app signs payloads with ITS key, so pointing at this repository would
#: make every update fail the signature check instead of updating.
RELEASE_REPO = "ssheleg/vibe-bridge"
#: The releases Atom feed, NOT the REST API. The API caps unauthenticated
#: callers at 60 requests per hour PER IP — a cap every install behind one
#: address shares, and one that this repository's own testing hit within an
#: hour (2026-08-30). The feed carries what the check needs — the tags — and
#: asset URLs are derived from a tag rather than looked up.
RELEASE_FEED = f"https://github.com/{RELEASE_REPO}/releases.atom"
DOWNLOAD_BASE = f"https://github.com/{RELEASE_REPO}/releases/download"


def release_repo() -> str:
    from .config import load
    return load().release_repo


def feed_url(repo: str | None = None) -> str:
    return f"https://github.com/{repo or release_repo()}/releases.atom"


def download_base(repo: str | None = None) -> str:
    return f"https://github.com/{repo or release_repo()}/releases/download"
PAYLOAD_ASSET = "payload-{version}.tar.gz"
#: Payload tags look like `v0.2.0`. Shell releases are tagged `shell-v0.2.0`
#: and must never be offered as an update: they are a DMG a person installs,
#: not something the bridge can unpack. GitHub's own `latest` pointer does not
#: know the difference, which is exactly how a shell release once shadowed the
#: payload channel.
_PAYLOAD_TAG = re.compile(r"/(v\d+(?:\.\d+){1,3})\s*</id>")
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


#: Сколько версий payload держим на диске. Две: текущая и та, на которую
#: мост откатится, если текущая не переживёт свой запуск.
_KEEP_VERSIONS = 2


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
        # Уборка живёт ЗДЕСЬ, а не у вызывающих. Их двое — фоновый цикл и
        # кнопка «Проверить обновления», — и убирал только первый: панель
        # копила версии молча. Измерено 2026-09-02: пять версий при keep=2.
        # Путь, который забыл убрать, — это путь, который её не звал; из
        # успешной ветки установки забыть нельзя.
        layout.prune(root, keep=_KEEP_VERSIONS)
        return True, f"версия {version} установлена"
    except OSError as exc:
        return False, f"не удалось установить обновление: {exc}"
    finally:
        _rmtree(tmp)


@dataclass(frozen=True)
class Check:
    """The answer to "is there an update?", with the two negatives kept
    apart: `found is None` without `error` means we asked and there is
    nothing; `error` means we could not ask. Collapsing them into one None
    told the owner "обновлений нет" while the bundle was in fact failing to
    reach GitHub (2026-08-30) — the panel reported calm and the bridge was
    blind."""
    found: Available | None = None
    error: str = ""


def check(*, current: str, fetch=None, repo: str | None = None) -> Check:
    """Ask the release channel what the newest payload version is."""
    fetch = fetch or _fetch_text
    repo = repo or release_repo()
    try:
        feed = fetch(feed_url(repo))
    except (OSError, urllib.error.URLError) as exc:
        return Check(error=f"канал релизов недоступен: {exc}")

    now = layout.parse(current)
    if not now:
        return Check(error=f"непонятный номер текущей версии: {current!r}")

    versions = _payload_tags(feed)
    if versions is None:
        return Check(error="канал релизов ответил не фидом релизов")
    newer = [v for v in versions if (layout.parse(v) or ()) > now]
    if not newer:
        return Check()                       # asked, nothing newer

    tag = max(newer, key=lambda v: layout.parse(v))
    asset = PAYLOAD_ASSET.format(version=tag)
    url = f"{download_base(repo)}/v{tag}/{asset}"
    return Check(found=Available(version=tag, payload_url=url,
                                 sig_url=f"{url}.sig"))


def _payload_tags(feed: str) -> list[str] | None:
    """Payload versions in the feed, newest-first order irrelevant. None when
    the text is not a releases feed at all — distinct from a feed that simply
    holds no payload release."""
    if "<feed" not in feed and "<entry" not in feed:
        return None
    # Whole document, not line by line: GitHub's feed is pretty-printed but
    # nothing promises that, and a one-line feed parsed by lines finds nothing
    # while looking like an empty channel.
    return [m.lstrip("v") for m in _PAYLOAD_TAG.findall(feed)]


def download(url: str, *, opener=None) -> bytes | None:
    """Fetch an asset, capped. None on any failure — callers treat that as
    "no update this time", not as an error to surface."""
    opener = opener or _open_verified
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

def _open_verified(url: str, timeout: int = _TIMEOUT):
    return urllib.request.urlopen(url, timeout=timeout, context=ssl_context())


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


def ssl_context():
    """A trust store the packaged app actually has.

    A Python bundled inside a .app has no system CA store to fall back on:
    the first packaged build failed every release check with
    `CERTIFICATE_VERIFY_FAILED … unable to get local issuer certificate`
    (2026-08-30), and it looked exactly like "no updates". `httpx` and
    `requests` — the bridge's other HTTP callers — already carry `certifi`;
    this gives the same roots to the stdlib client used here.

    Never falls back to an unverified context: an updater that drops
    certificate checking to keep working has removed the reason to trust the
    channel at all.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"Accept": "application/atom+xml",
                      "User-Agent": "vibe-bridge-updater"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                context=ssl_context()) as resp:
        return resp.read(1024 * 1024).decode("utf-8", "replace")


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class AutoUpdater:
    """The background half of self-updating (SCN-021).

    A bridge that only updates when someone opens the panel and presses a
    button is not a bridge that updates itself, and the README said it did.

    Journal policy is deliberate and is most of the design here. Success that
    changed nothing writes NOTHING: a line every six hours saying "проверил,
    ничего нет" trains the owner to scroll past the journal, and the journal
    is where consent decisions live. A failure is written once and then held
    until the reason changes or the channel comes back — a laptop closed for a
    week must not produce twenty-eight identical sentences.
    """

    #: Between checks. Long: releases are rare and the owner can always press
    #: the button. Short enough that a security fix lands the same day.
    INTERVAL_S = 6 * 60 * 60
    #: After startup, before the first check. The bridge's job at launch is to
    #: answer the robot, not to talk to GitHub.
    FIRST_DELAY_S = 5 * 60

    def __init__(self, *, root: Path, audit, state, pubkey: bytes | None,
                 shell_version: str | None, current,
                 interval_s: int | None = None,
                 first_delay_s: int | None = None, settings=None) -> None:
        self._root = root
        self._audit = audit
        self._state = state
        self._settings = settings
        self._pubkey = pubkey
        self._shell_version = shell_version
        self._current = current
        self._interval = interval_s or self.INTERVAL_S
        self._first_delay = (self.FIRST_DELAY_S if first_delay_s is None
                             else first_delay_s)
        self._last_error: str | None = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- cycle

    def run_once(self) -> bool:
        """One check. True only when a new version was installed. Never
        raises — this runs on a daemon thread inside the tray app, and an
        exception here would end automatic updating with nobody told."""
        if not self._enabled():
            return False
        if not self._pubkey or not self._shell_version:
            # No bundle, no trust anchor: a development checkout. Retrying
            # every cycle and journalling it would drown the journal in a
            # situation where updating was never possible.
            return False
        try:
            return self._cycle()
        except Exception as exc:                      # noqa: BLE001
            self._report_failure(f"проверка обновлений сорвалась: {exc}")
            return False

    def _enabled(self) -> bool:
        """The switch lives in settings; `state.auto_update` is honoured for
        installs that set it before settings existed."""
        if self._settings is not None:
            return bool(self._settings.update_enabled)
        from .config import load
        return bool(load().update_enabled
                    and getattr(self._state, "auto_update", True))

    def _cycle(self) -> bool:
        result = check(current=self._current())
        if result.error:
            self._report_failure(result.error)
            return False

        self._report_recovery()
        if result.found is None:
            return False                              # nothing new: stay quiet

        ok, why = fetch_and_install(
            result.found, self._root, pubkey=self._pubkey,
            shell_version=self._shell_version)
        self._audit.record(
            tool="update", tool_class="SYS",
            decision="auto" if ok else "unavailable", ok=ok,
            line=f"обновление {result.found.version}: {why}", detail=why)
        # Уборка старых версий делается внутри `install` — одна на оба пути.
        return ok

    # --------------------------------------------------------- journal policy

    def _report_failure(self, reason: str) -> None:
        if reason == self._last_error:
            return                                    # already said, once
        self._last_error = reason
        self._audit.record(tool="update", tool_class="SYS",
                           decision="unavailable", ok=False,
                           line=f"проверка обновлений: {reason}",
                           detail=reason)

    def _report_recovery(self) -> None:
        if self._last_error is None:
            return
        self._last_error = None
        self._audit.record(tool="update", tool_class="SYS", decision="auto",
                           ok=True,
                           line="канал обновлений снова доступен", detail="")

    # ----------------------------------------------------------------- thread

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._loop, name="vibe-bridge-update",
                                  daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:  # pragma: no cover - timing, exercised live
        if self._stop.wait(self._first_delay):
            return
        while True:
            self.run_once()
            if self._stop.wait(self._interval):
                return
