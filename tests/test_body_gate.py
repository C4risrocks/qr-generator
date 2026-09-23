from __future__ import annotations

import asyncio

import pytest

from qrgen.body_gate import BodyGate, InflightLedger


class RecordingLedger(InflightLedger):
    def __init__(self) -> None:
        super().__init__()
        self.costs: list[int] = []

    def add(self, cost: int) -> None:
        self.costs.append(cost)
        super().add(cost)


class Inner:
    """Trivial inner ASGI app: records that it was called and what it read."""

    def __init__(self) -> None:
        self.called = False
        self.bodies: list[bytes] = []

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            self.bodies.append(message.get("body", b""))
            if not message.get("more_body"):
                return


def scope_for(headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/generate",
        "headers": headers,
    }


def receive_of(chunks: list[bytes]):
    messages = [
        {"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks
    ]
    messages.append({"type": "http.request", "body": b"", "more_body": False})
    iterator = iter(messages)

    async def receive():
        return next(iterator)

    return receive


def silence():
    async def send(message) -> None:
        return None

    return send


def test_declared_length_over_limit_rejected() -> None:
    gate = BodyGate(None, max_bytes=100, inflight_budget=10**6)
    verdict = gate.decision_for_scope(scope_for([(b"content-length", b"200")]))
    assert not verdict.allowed
    assert verdict.status == 413
    assert "too large" in verdict.detail


def test_declared_length_within_limit_allowed() -> None:
    gate = BodyGate(None, max_bytes=100, inflight_budget=10**6)
    assert gate.decision_for_scope(scope_for([(b"content-length", b"99")])).allowed


def test_missing_content_length_allowed() -> None:
    gate = BodyGate(None, max_bytes=100, inflight_budget=10**6)
    assert gate.decision_for_scope(scope_for([])).allowed


@pytest.mark.parametrize(
    "content_type",
    (b"multipart/form-data; boundary=B", b"Multipart/Form-Data; boundary=B"),
)
def test_multipart_bytes_cost_double(content_type: bytes) -> None:
    """The double spool accounting must not depend on Content-Type casing."""
    ledger = RecordingLedger()
    gate = BodyGate(Inner(), max_bytes=1_000, inflight_budget=10**6, ledger=ledger)
    asyncio.run(
        gate(scope_for([(b"content-type", content_type)]), receive_of([b"x", b"y"]), silence())
    )
    assert ledger.costs == [2, 2]


def test_plain_body_costs_single() -> None:
    ledger = RecordingLedger()
    gate = BodyGate(Inner(), max_bytes=1_000, inflight_budget=10**6, ledger=ledger)
    asyncio.run(
        gate(
            scope_for([(b"content-type", b"application/x-www-form-urlencoded")]),
            receive_of([b"data=hello"]),
            silence(),
        )
    )
    assert ledger.costs == [10]


def test_inflight_budget_answers_busy_and_releases() -> None:
    """A request whose cost exceeds the budget gets 503 + Retry-After, the
    inner app never runs, and nothing stays reserved."""
    ledger = InflightLedger()
    inner = Inner()
    gate = BodyGate(inner, max_bytes=1_000, inflight_budget=2, ledger=ledger)
    messages: list[dict] = []

    async def send(message) -> None:
        messages.append(message)

    asyncio.run(
        gate(
            scope_for([(b"content-type", b"application/x-www-form-urlencoded")]),
            receive_of([b"x" * 4]),
            send,
        )
    )
    assert messages[0]["status"] == 503
    assert (b"retry-after", b"5") in messages[0]["headers"]
    assert not inner.called
    assert ledger.reserved == 0


def test_reserved_bytes_released_after_passing_through() -> None:
    ledger = InflightLedger()
    gate = BodyGate(Inner(), max_bytes=1_000, inflight_budget=10**6, ledger=ledger)
    asyncio.run(
        gate(
            scope_for([(b"content-type", b"application/x-www-form-urlencoded")]),
            receive_of([b"data=hello"]),
            silence(),
        )
    )
    assert ledger.reserved == 0


def test_client_disconnect_releases_reservation() -> None:
    ledger = InflightLedger()
    gate = BodyGate(Inner(), max_bytes=1_000, inflight_budget=10**6, ledger=ledger)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(gate(scope_for([]), receive, silence()))
    assert ledger.reserved == 0


def test_non_http_scope_passes_through() -> None:
    inner = Inner()
    gate = BodyGate(inner, max_bytes=1, inflight_budget=0, ledger=InflightLedger())
    asyncio.run(gate({"type": "lifespan"}, receive_of([]), silence()))
    assert inner.called
