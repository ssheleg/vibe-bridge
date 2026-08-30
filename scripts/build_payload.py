#!/usr/bin/env python3
"""Build and sign a payload — the artefact the running bridge updates itself to.

The tarball holds exactly what ADR-0006 puts outside the bundle: our own
pure-Python package, plus a manifest naming the version and the oldest shell
that can run it. Dependencies are NOT here; they live in the signed .app, and
a payload that needs a newer one says so through `shell_min` instead of
failing at import time on the owner's machine.

    python scripts/build_payload.py                 # → dist/payload-<v>.tar.gz + .sig

Deterministic on purpose: fixed mtimes, sorted entries, uid/gid zeroed. Two
builds of the same commit produce the same bytes, so a signature can be
checked against a rebuild rather than trusted because it exists.
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EPOCH = 1735689600          # 2025-01-01, fixed: mtimes must not leak build time

# The oldest shell that can run this payload. Raise it in the SAME change that
# adds or bumps a third-party dependency — that is the moment the payload
# stops being runnable by shells already installed (ADR-0006).
SHELL_MIN = "0.1.0"


def _version() -> str:
    for line in (ROOT / "vibebridge" / "__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\' ')
    raise SystemExit("не нашёл __version__ в vibebridge/__init__.py")


def _members(version: str) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = [(
        "payload.json",
        json.dumps({"version": version, "shell_min": SHELL_MIN},
                   ensure_ascii=False, sort_keys=True).encode(),
    )]
    pkg = ROOT / "vibebridge"
    for path in sorted(pkg.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        files.append((str(path.relative_to(ROOT)), path.read_bytes()))
    return files


def build(version: str) -> bytes:
    buf = io.BytesIO()
    # mtime=0 on the gzip header too — otherwise the container leaks the clock
    # the tar was told to forget.
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT,
                      compresslevel=9) as tar:
        tar.gzip = None  # type: ignore[attr-defined]
        for name, data in _members(version):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = EPOCH
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    raw = bytearray(buf.getvalue())
    raw[4:8] = b"\x00\x00\x00\x00"        # gzip MTIME field → deterministic
    return bytes(raw)


def main() -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    from release_key import private_key

    version = _version()
    blob = build(version)
    signature = private_key().sign(blob)

    DIST.mkdir(exist_ok=True)
    tar_path = DIST / f"payload-{version}.tar.gz"
    tar_path.write_bytes(blob)
    (DIST / f"payload-{version}.tar.gz.sig").write_bytes(signature)

    print(f"payload {version}: {len(blob)} байт → {tar_path}")
    print(f"подпись: {tar_path}.sig ({len(signature)} байт)")

    # Prove the artefact against the same code the bridge will run.
    sys.path.insert(0, str(ROOT))
    from vibebridge.update import public_key_bytes, verify
    ok = verify(blob, signature, public_key_bytes(private_key().public_key()))
    print("самопроверка подписи:", "ok" if ok else "СБОЙ")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
