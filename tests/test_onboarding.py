"""Getting from "installed" to "working" — the path that did not exist.

`SCN-017` promised a welcome with two doors: «Подключить робота» and «У меня
уже есть связка». The bridge shipped neither. The only way to attach a robot
was the SD-card wizard: a person whose robot was already running had no
supported path at all, and the panel's own robot card told them pairing
"появится с визардом" — which had shipped two blocks above it.

The other half is the door itself. Opening the panel address without a token
answered `{"error":"unauthorized"}` as raw JSON: correct, and useless to the
person reading it.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vibebridge.audit import AuditLog
from vibebridge.config import Settings
from vibebridge.consent import ConsentEngine
from vibebridge.state import BridgeState
from vibebridge.web import build_app


@pytest.fixture()
def app(tmp_path):
    state = BridgeState(path=tmp_path / "state.json",
                        panel_token="panel-secret")
    return build_app(consent=ConsentEngine(),
                     audit=AuditLog(tmp_path / "a.log"), state=state,
                     settings=Settings()), state


@pytest.fixture()
def client(app):
    built, _ = app
    c = TestClient(built)
    c.cookies.set("vb_panel", "panel-secret")
    return c


# ------------------------------------------------------------- the front door

def test_the_panel_address_without_a_token_explains_itself(app):
    """A person who typed the address or lost the tab gets a page, not JSON."""
    built, _ = app
    r = TestClient(built).get("/")
    assert r.status_code == 401
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "меню-бар" in body or "menu bar" in body.lower()


def test_the_help_page_never_leaks_the_token(app):
    built, state = app
    r = TestClient(built).get("/")
    assert state.panel_token not in r.text


def test_a_wrong_token_is_still_refused(app):
    built, _ = app
    assert TestClient(built).get("/?token=nope").status_code == 403


# ------------------------------------------------- attaching an existing robot

def test_an_existing_robot_can_be_attached_by_hand(client, app):
    _, state = app
    r = client.post("/api/robot/attach", json={
        "base_url": "https://robot.example.ts.net",
        "chat_url": "https://robot.example.ts.net/v1",
        "key": "secret-key", "name": "Вася"})
    assert r.status_code == 200 and r.json()["ok"]
    assert state.robot_base_url == "https://robot.example.ts.net"
    assert state.robot_chat_key == "secret-key"
    assert state.robot_name == "Вася"


def test_attaching_mints_a_robot_token_so_standalone_can_authenticate(client,
                                                                      app):
    """standalone gates /mcp on this token. Attaching without minting one
    would leave the endpoint unguarded in the very mode chosen to guard it."""
    _, state = app
    client.post("/api/robot/attach", json={
        "base_url": "https://robot.example.ts.net", "key": "k"})
    assert state.robot_token


def test_the_attach_response_hands_back_what_the_robot_needs(client):
    body = client.post("/api/robot/attach", json={
        "base_url": "https://robot.example.ts.net", "key": "k"}).json()
    assert body["robot_token"]                 # to put into the robot's config
    assert body["bridge_url"]


def test_attaching_without_an_address_is_refused_with_a_reason(client):
    r = client.post("/api/robot/attach", json={"key": "k"})
    assert r.status_code == 400
    assert "адрес" in r.json()["error"].lower()


def test_a_non_http_address_is_refused(client):
    r = client.post("/api/robot/attach",
                    json={"base_url": "ssh://robot", "key": "k"})
    assert r.status_code == 400


def test_attaching_is_journalled_like_pairing(client):
    client.post("/api/robot/attach", json={
        "base_url": "https://robot.example.ts.net", "key": "k",
        "name": "Вася"})
    feed = client.get("/api/journal").json()["entries"]
    assert any(e["tool"] == "pair" and "Вася" in e["line"] for e in feed)


def test_attaching_needs_the_panel_token(app):
    built, _ = app
    r = TestClient(built).post("/api/robot/attach",
                               json={"base_url": "https://x", "key": "k"})
    assert r.status_code == 401


# ------------------------------------------------------------------ the state

def test_onboarding_state_says_what_is_still_missing(client):
    body = client.get("/api/onboarding").json()
    assert body["robot_attached"] is False
    assert body["steps"]                       # an ordered list, not prose


def test_onboarding_reports_done_once_a_robot_is_attached(client):
    client.post("/api/robot/attach", json={
        "base_url": "https://robot.example.ts.net", "key": "k"})
    body = client.get("/api/onboarding").json()
    assert body["robot_attached"] is True
