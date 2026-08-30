"""The updater — what it accepts, what it refuses, and how it fails.

The payload runs with the shell's TCC grants: the screen, the clipboard, Apple
Events. So the signature check is not a nicety around the download, it IS the
download's admission test, and every test here that says "refused" is a test
about code that would otherwise run with those rights.

The other half is honesty under failure. Every network path in this module
returns a verdict instead of raising: an update that cannot happen must leave
the bridge running and say why, exactly like `push.send` never raises.
"""
from __future__ import annotations

import io
import json
import tarfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vbboot import layout
from vibebridge import update


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "payload"
    r.mkdir()
    return r


@pytest.fixture()
def keys():
    priv = Ed25519PrivateKey.generate()
    return priv, update.public_key_bytes(priv.public_key())


def make_payload(version: str = "0.2.0", *, shell_min: str = "0.1.0",
                 extra: dict | None = None) -> bytes:
    """A payload tarball shaped exactly like the release script builds one."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest = json.dumps(
            {"version": version, "shell_min": shell_min}).encode()
        info = tarfile.TarInfo("payload.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))

        code = f'__version__ = "{version}"\n'.encode()
        info = tarfile.TarInfo("vibebridge/__init__.py")
        info.size = len(code)
        tar.addfile(info, io.BytesIO(code))

        for name, body in (extra or {}).items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ------------------------------------------------------------- signatures

def test_correctly_signed_payload_installs(root, keys):
    priv, pub = keys
    blob = make_payload("0.2.0")
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root,
                             pubkey=pub, shell_version="0.1.0")
    assert ok, why
    assert layout.active_version(root) == "0.2.0"
    assert (root / "0.2.0" / "vibebridge" / "__init__.py").exists()


def test_unsigned_payload_is_refused(root, keys):
    _, pub = keys
    blob = make_payload()
    ok, why = update.install(blob, b"", "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok and "подпис" in why.lower()
    assert layout.installed(root) == []


def test_payload_signed_by_the_wrong_key_is_refused(root, keys):
    _, pub = keys
    blob = make_payload()
    ok, why = update.install(blob, Ed25519PrivateKey.generate().sign(blob),
                             "0.2.0", root, pubkey=pub, shell_version="0.1.0")
    assert not ok and "подпис" in why.lower()
    assert layout.installed(root) == []


def test_tampered_payload_is_refused(root, keys):
    """The signature covers the bytes; one flipped byte must not pass."""
    priv, pub = keys
    blob = make_payload()
    sig = priv.sign(blob)
    tampered = bytearray(blob)
    tampered[-1] ^= 0x01
    ok, why = update.install(bytes(tampered), sig, "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok and "подпис" in why.lower()
    assert layout.installed(root) == []


def test_truncated_download_is_refused_before_extraction(root, keys):
    priv, pub = keys
    blob = make_payload()
    ok, why = update.install(blob[: len(blob) // 2], priv.sign(blob), "0.2.0",
                             root, pubkey=pub, shell_version="0.1.0")
    assert not ok
    assert layout.installed(root) == []


def test_missing_public_key_refuses_rather_than_trusting(root, keys):
    """No key in the bundle is not "skip the check" — it is "install nothing"."""
    priv, _ = keys
    blob = make_payload()
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root,
                             pubkey=None, shell_version="0.1.0")
    assert not ok and "ключ" in why.lower()
    assert layout.installed(root) == []


# ------------------------------------------------------------ tar contents

def test_path_traversal_in_the_tar_is_refused(root, keys):
    """A signed release is still parsed defensively: the key could leak."""
    priv, pub = keys
    blob = make_payload(extra={"../escaped.py": "pwned"})
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok
    assert not (root.parent / "escaped.py").exists()


def test_version_mismatch_between_tag_and_manifest_is_refused(root, keys):
    priv, pub = keys
    blob = make_payload("0.3.0")
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok and "верси" in why.lower()


def test_payload_needing_a_newer_shell_is_refused_with_a_reason(root, keys):
    """ADR-0006: dependencies live in the signed shell, so a payload may
    outgrow it. Saying so is the whole point — installing it would be a
    bridge that cannot import its own libraries."""
    priv, pub = keys
    blob = make_payload("0.2.0", shell_min="0.9.0")
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok and "оболочк" in why.lower()
    assert layout.installed(root) == []


def test_payload_without_a_manifest_is_refused(root, keys):
    priv, pub = keys
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"x = 1\n"
        info = tarfile.TarInfo("vibebridge/__init__.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    ok, why = update.install(blob, priv.sign(blob), "0.2.0", root, pubkey=pub,
                             shell_version="0.1.0")
    assert not ok


def test_failed_install_leaves_no_half_written_version(root, keys):
    priv, pub = keys
    update.install(make_payload("0.1.0"), priv.sign(make_payload("0.1.0")),
                   "0.1.0", root, pubkey=pub, shell_version="0.1.0")
    bad = make_payload("0.2.0", extra={"../escaped.py": "pwned"})
    update.install(bad, priv.sign(bad), "0.2.0", root, pubkey=pub,
                   shell_version="0.1.0")
    assert layout.installed(root) == ["0.1.0"]          # untouched
    assert not any(p.name.startswith("0.2.0") for p in root.iterdir())


# ----------------------------------------------------------------- checking

def _release(tag="0.2.0", *, assets=("payload-0.2.0.tar.gz",
                                     "payload-0.2.0.tar.gz.sig")):
    return {
        "tag_name": f"v{tag}",
        "body": "заметки к релизу",
        "assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                   for n in assets],
    }


def test_check_reports_a_newer_release(root):
    found = update.check(current="0.1.0", fetch=lambda url: _release()).found
    assert found is not None
    assert found.version == "0.2.0"
    assert found.payload_url.endswith("payload-0.2.0.tar.gz")
    assert found.sig_url.endswith(".sig")


def test_check_is_quiet_when_already_current(root):
    res = update.check(current="0.2.0", fetch=lambda url: _release())
    assert res.found is None and res.up_to_date


def test_check_ignores_an_older_release(root):
    res = update.check(current="0.3.0", fetch=lambda url: _release())
    assert res.found is None and res.up_to_date


def test_unreachable_channel_is_not_reported_as_up_to_date(root):
    """The distinction this whole type exists for. Live on 2026-08-30 the
    packaged app could not reach GitHub and the panel said "обновлений нет":
    a blind bridge reporting calm."""
    def boom(url):
        raise OSError("сеть недоступна")

    res = update.check(current="0.1.0", fetch=boom)
    assert res.found is None
    assert not res.up_to_date
    assert "недоступен" in res.error and "сеть недоступна" in res.error


def test_garbage_json_is_an_error_not_silence(root):
    res = update.check(current="0.1.0", fetch=lambda url: {"nope": 1})
    assert res.found is None and not res.up_to_date and res.error


def test_release_without_a_signature_asset_is_refused_out_loud(root):
    rel = _release(assets=("payload-0.2.0.tar.gz",))
    res = update.check(current="0.1.0", fetch=lambda url: rel)
    assert res.found is None and not res.up_to_date
    assert "подписи" in res.error


# ------------------------------------------------------------- trust store

def test_updater_carries_its_own_ca_roots():
    """A bundled Python has no system CA store. Without certifi the first
    packaged build reported CERTIFICATE_VERIFY_FAILED for every check, and
    the panel rendered that as "обновлений нет"."""
    import certifi
    ctx = update.ssl_context()
    assert ctx.verify_mode.name == "CERT_REQUIRED"
    assert ctx.check_hostname is True
    assert certifi.where()


def test_trust_store_is_never_downgraded_to_unverified(monkeypatch):
    """Even with certifi missing, verification stays on: an updater that
    turns off certificate checks to keep working has thrown away the reason
    to trust the channel."""
    import builtins
    real_import = builtins.__import__

    def no_certifi(name, *a, **kw):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    ctx = update.ssl_context()
    assert ctx.verify_mode.name == "CERT_REQUIRED"
    assert ctx.check_hostname is True
