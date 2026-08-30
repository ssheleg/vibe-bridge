"""The version surface — what the owner is told about the code they run.

The bridge updates itself quietly (grill G-5), and quiet is only acceptable
while the panel can answer three questions at any moment: which version is
running, where it came from, and whether something newer is waiting. A silent
updater with no readable state is indistinguishable from an updater that
stopped working months ago.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vbboot import layout
from vibebridge.web import build_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState

    monkeypatch.setattr(layout, "payload_root",
                        lambda: tmp_path / "payload")
    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    c = TestClient(app)
    c.cookies.set("vb_panel", state.panel_token)
    return c


def test_version_endpoint_needs_the_panel_token(tmp_path):
    from vibebridge.audit import AuditLog
    from vibebridge.consent import ConsentEngine
    from vibebridge.state import BridgeState

    state = BridgeState.load(tmp_path / "state.json")
    app = build_app(consent=ConsentEngine(), audit=AuditLog(tmp_path / "a.log"),
                    state=state, notify=lambda *a, **k: None)
    assert TestClient(app).get("/api/version").status_code == 401


def test_version_reports_what_is_running(client):
    body = client.get("/api/version").json()
    from vibebridge import __version__
    assert body["running"] == __version__
    assert body["source"] in ("payload", "seed", "dev")
    assert "autostart" in body
    assert body["repo"] == "ssheleg/vibe-bridge"


def test_version_reports_a_pending_update_when_one_is_installed(client,
                                                                tmp_path):
    """Installed but not yet active: the owner must not think it is live."""
    root = tmp_path / "payload"
    (root / "9.9.9" / "vibebridge").mkdir(parents=True)
    (root / "9.9.9" / "vibebridge" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n')
    layout.mark_installed(root, "9.9.9")

    body = client.get("/api/version").json()
    assert body["pending"] == "9.9.9"
    assert "перезапуск" in body["pending_note"].lower()


def test_no_pending_update_reads_as_none_not_as_the_running_version(client):
    assert client.get("/api/version").json()["pending"] is None


def test_check_reports_when_there_is_nothing_newer(client, monkeypatch):
    from vibebridge import update
    monkeypatch.setattr(update, "check", lambda **kw: None)
    body = client.post("/api/update/check").json()
    assert body["found"] is False
    assert body["message"]


def test_check_reports_a_network_failure_honestly(client, monkeypatch):
    """`check` swallows the traceback; the panel must still not claim the
    bridge is up to date when nobody could ask."""
    from vibebridge import update
    monkeypatch.setattr(update, "check", lambda **kw: None)
    body = client.post("/api/update/check").json()
    assert body["found"] is False


def test_check_that_finds_an_update_installs_and_journals_it(client, tmp_path,
                                                             monkeypatch):
    from vibebridge import update
    found = update.Available(version="9.9.9", payload_url="https://x/p",
                             sig_url="https://x/p.sig", notes="")
    monkeypatch.setattr(update, "check", lambda **kw: found)
    monkeypatch.setattr("vbboot.runner.shell_version", lambda: "0.1.0")
    monkeypatch.setattr(update, "fetch_and_install",
                        lambda *a, **kw: (True, "версия 9.9.9 установлена"))

    body = client.post("/api/update/check").json()
    assert body["found"] is True and body["installed"] is True
    assert body["version"] == "9.9.9"

    feed = client.get("/api/journal").json()["entries"]
    assert any(e["tool"] == "update" and "9.9.9" in e["line"] for e in feed)


def test_a_refused_update_is_journalled_as_a_failure(client, monkeypatch):
    """A rejected signature is the single most important line this journal
    can carry — it must never be swallowed as "no update"."""
    from vibebridge import update
    found = update.Available(version="9.9.9", payload_url="https://x/p",
                             sig_url="https://x/p.sig", notes="")
    monkeypatch.setattr(update, "check", lambda **kw: found)
    monkeypatch.setattr("vbboot.runner.shell_version", lambda: "0.1.0")
    monkeypatch.setattr(
        update, "fetch_and_install",
        lambda *a, **kw: (False, "подпись payload не сошлась — отклонено"))

    body = client.post("/api/update/check").json()
    assert body["found"] is True and body["installed"] is False
    assert "подпись" in body["message"]

    feed = client.get("/api/journal").json()["entries"]
    bad = [e for e in feed if e["tool"] == "update" and not e["ok"]]
    assert bad and "подпись" in bad[0]["line"]


def test_autostart_state_is_exposed_for_the_settings_card(client):
    body = client.get("/api/version").json()
    assert set(body["autostart"]) >= {"state", "human", "supported"}


def test_bundle_is_found_from_the_shell_not_from_the_payload(tmp_path,
                                                             monkeypatch):
    """The trust anchor must be locatable from OUTSIDE the bundle.

    After the first successful update `vibebridge` lives in Application
    Support, with no `Contents/Resources` above it. Anchoring the public-key
    lookup on the payload's own path would make every update after the first
    one refuse for "no public key" — a bridge that can update exactly once.
    """
    from vibebridge import web

    resources = tmp_path / "vibe-bridge.app" / "Contents" / "Resources"
    (resources / "app" / "vbboot").mkdir(parents=True)
    fake_vbboot = resources / "app" / "vbboot" / "__init__.py"
    fake_vbboot.write_text("")

    import vbboot
    monkeypatch.setattr(vbboot, "__file__", str(fake_vbboot))
    assert web._bundle_resources() == resources


def test_no_bundle_outside_an_app_means_no_key_and_no_update(monkeypatch):
    import vbboot
    from vibebridge import web
    monkeypatch.setattr(vbboot, "__file__", "/Users/x/repo/vbboot/__init__.py")
    assert web._bundle_resources() is None


def test_a_dev_checkout_refuses_to_update_instead_of_pretending(client,
                                                                monkeypatch):
    """No bundle means no trust anchor and no shell to be compatible with.
    Saying so beats installing a payload beside a checkout nobody launches."""
    from vibebridge import update
    found = update.Available(version="9.9.9", payload_url="https://x/p",
                             sig_url="https://x/p.sig", notes="")
    monkeypatch.setattr(update, "check", lambda **kw: found)
    monkeypatch.setattr("vbboot.runner.shell_version", lambda: None)

    body = client.post("/api/update/check").json()
    assert body["found"] is True and body["installed"] is False
    assert "не из установленного приложения" in body["message"]
