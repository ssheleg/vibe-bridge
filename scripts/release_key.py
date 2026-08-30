#!/usr/bin/env python3
"""The release key — generate it once, keep it in the keychain, never in git.

This key is the only thing standing between a compromised release channel and
code running with the owner's screen and clipboard (ADR-0006). Two rules the
code enforces rather than documents:

* the private half is written to the login keychain and nowhere else — never
  printed, never a file in the repository;
* generation REFUSES when a key already exists, because overwriting it would
  silently orphan every release signed with the old one.

    python scripts/release_key.py generate     # once, ever
    python scripts/release_key.py public       # the 32 bytes for the bundle
"""
from __future__ import annotations

import base64
import subprocess
import sys

SERVICE = "vibe-bridge-release"
ACCOUNT = "ed25519"


def _keychain_read() -> bytes | None:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT,
         "-w"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return base64.b64decode(out.stdout.strip())


def _keychain_write(private: bytes) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-s", SERVICE, "-a", ACCOUNT,
         "-w", base64.b64encode(private).decode(), "-U",
         "-T", "/usr/bin/security"],
        check=True, capture_output=True)


def generate() -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    if _keychain_read() is not None:
        print("Ключ релизов уже существует в keychain — генерация отменена.\n"
              "Перезапись осиротила бы все подписанные им релизы. Ротация: "
              "см. docs/spec/packaging.md.", file=sys.stderr)
        return 1

    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw,
                                serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    _keychain_write(raw)
    print("Приватный ключ записан в keychain "
          f"(service={SERVICE}). Значение не печатается намеренно.")
    print("Публичный ключ (32 байта, base64) — он уезжает в бандл:")
    print(base64.b64encode(public_bytes()).decode())
    return 0


def private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    raw = _keychain_read()
    if raw is None:
        raise SystemExit(
            "нет ключа релизов в keychain — сначала "
            "`python scripts/release_key.py generate`")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_bytes() -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "generate":
        return generate()
    if cmd == "public":
        sys.stdout.buffer.write(public_bytes())
        return 0
    if cmd == "public-b64":
        print(base64.b64encode(public_bytes()).decode())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
