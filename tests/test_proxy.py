from __future__ import annotations

from starlette.requests import Request

from qrgen.proxy import client_ip, trusted_proxy_networks


def make_request(client_host: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_direct_ip_used_without_trusted_proxies(monkeypatch) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    req = make_request("203.0.113.7", {"X-Forwarded-For": "198.51.100.9"})
    assert client_ip(req) == "203.0.113.7"


def test_trusted_proxy_uses_first_forwarded_ip(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8, 172.18.0.2")
    req = make_request("10.1.2.3", {"X-Forwarded-For": "198.51.100.9, 10.1.2.3"})
    assert client_ip(req) == "10.1.2.3"


def test_spoofed_leftmost_entry_ignored(monkeypatch) -> None:
    """Entries are read right-to-left: the proxy appends what it really saw,
    so an attacker-chosen leftmost entry must not win."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    req = make_request("10.1.2.3", {"X-Forwarded-For": "1.2.3.4, 198.51.100.9"})
    assert client_ip(req) == "198.51.100.9"


def test_open_allowlist_entry_rejected(monkeypatch) -> None:
    """0.0.0.0/0 would let anyone spoof; it must be refused."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "0.0.0.0/0")
    assert trusted_proxy_networks() == []
    req = make_request("203.0.113.7", {"X-Forwarded-For": "198.51.100.9"})
    assert client_ip(req) == "203.0.113.7"


def test_trusted_single_ip_entry(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "172.18.0.2")
    req = make_request("172.18.0.2", {"X-Forwarded-For": "203.0.113.7"})
    assert client_ip(req) == "203.0.113.7"


def test_spoofed_header_from_untrusted_peer_ignored(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    req = make_request("203.0.113.7", {"X-Forwarded-For": "10.9.9.9"})
    assert client_ip(req) == "203.0.113.7"


def test_invalid_forwarded_ip_falls_back_to_direct(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    req = make_request("10.1.2.3", {"X-Forwarded-For": "not-an-ip"})
    assert client_ip(req) == "10.1.2.3"


def test_invalid_allowlist_entry_ignored(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "nonsense, 10.0.0.0/8")
    req = make_request("10.1.2.3", {"X-Forwarded-For": "198.51.100.9"})
    assert client_ip(req) == "198.51.100.9"
