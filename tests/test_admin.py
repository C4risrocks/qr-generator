from __future__ import annotations

from fastapi.testclient import TestClient

from qrgen.server import app

client = TestClient(app)


def test_login_page_served() -> None:
    res = client.get("/admin/login")
    assert res.status_code == 200
    assert "Panel de administración" in res.text


def test_admin_redirects_when_unauthenticated() -> None:
    res = client.get("/admin", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/admin/login"


def test_api_requires_auth() -> None:
    for path in ("/admin/api/summary", "/admin/api/usage", "/admin/api/clients", "/admin/api/inputs", "/admin/api/config"):
        res = client.get(path)
        assert res.status_code == 401


def test_login_wrong_password() -> None:
    res = client.post("/admin/login", data={"username": "admin", "password": "wrong"})
    assert res.status_code == 401


def test_login_success_and_summary() -> None:
    res = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/admin"

    summary = client.get("/admin/api/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert "requests_today" in payload
    assert payload["limits"]["generate"] > 0
    assert payload["limits"]["previews"] > 0

    config = client.get("/admin/api/config")
    assert config.status_code == 200
    assert config.json()["admin_configured"] is True


def test_login_rate_limited(monkeypatch) -> None:
    from qrgen import admin

    admin._login_attempts.clear()
    monkeypatch.setattr(admin, "LOGIN_LIMIT_PER_MINUTE", 2)
    for _ in range(2):
        res = client.post("/admin/login", data={"username": "admin", "password": "wrong"})
        assert res.status_code == 401
    res = client.post("/admin/login", data={"username": "admin", "password": "wrong"})
    assert res.status_code == 429


def test_logout_clears_session() -> None:
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    res = client.post("/admin/logout", follow_redirects=False)
    assert res.status_code == 303
    assert client.get("/admin/api/summary").status_code == 401
