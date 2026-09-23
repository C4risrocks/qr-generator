"""Body gate: the outermost seam for request bodies.

Streams the request into a spooled temporary file, enforcing a per-request
size limit (413) and a process-wide in-flight cap on spooled bytes (503
with ``Retry-After``). Multipart bodies count double: the form parser may
spool file parts to the tmpfs independently of this spool, so every
multipart byte may occupy it twice.

The gate answers with an observable :class:`Decision`; the in-flight
accounting lives behind the :class:`InflightLedger` port, so tests assert
behaviour through the interface instead of reading module state. Rate
limiting is server policy and lives in its own inner middleware, not here.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import closing
from dataclasses import dataclass

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 6 * 1024 * 1024
# Cap on bytes held in request spools at any moment (avoids filling the
# tmpfs with concurrent 6 MB uploads). The counter is per process and the
# server deliberately runs a single worker, so the cap matches the
# per-container tmpfs budget; scale out with container replicas instead of
# extra workers. Excess requests get 503.
DEFAULT_INFLIGHT_BUDGET = int(
    os.environ.get("MAX_INFLIGHT_BODY_BYTES", str(32 * 1024 * 1024))
)

RETRY_AFTER_BUSY = 5


@dataclass(frozen=True)
class Decision:
    """Observable outcome of a body-size decision."""

    allowed: bool
    status: int | None = None
    detail: str = ""
    retry_after: int | None = None


TOO_LARGE = Decision(allowed=False, status=413, detail="request body too large")
BUSY = Decision(
    allowed=False,
    status=503,
    detail="server busy; try again later",
    retry_after=RETRY_AFTER_BUSY,
)


class InflightLedger:
    """Accounting of spooled bytes reserved right now.

    Pure bookkeeping: the budget decision belongs to the gate, so tests can
    swap a recording fake without touching module state.
    """

    def __init__(self) -> None:
        self.reserved = 0

    def add(self, cost: int) -> None:
        self.reserved += cost

    def release(self, cost: int) -> None:
        self.reserved -= cost


def _header(scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value
    return None


def _declared_length(scope) -> int | None:
    """Content-Length as declared by the client, or None when absent/invalid."""
    raw = _header(scope, b"content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_multipart(scope) -> bool:
    """Multipart bodies count double in the in-flight cap (see module docs)."""
    raw = _header(scope, b"content-type")
    return raw is not None and raw.split(b";", 1)[0].strip().lower() == b"multipart/form-data"


class BodyGate:
    """Stream, cap and replay request bodies before application work.

    Bodies are spooled to memory only up to a small threshold and then to
    the temporary filesystem. This lets the gate reject oversized chunked
    requests before they consume rate-limit quota, without keeping the full
    body in RAM. The completed body is replayed to the inner app after the
    size checks pass.

    The in-flight cap bounds how many spooled bytes may exist at once
    across requests; beyond it the gate answers 503 with ``Retry-After``
    instead of exhausting the tmpfs. The server runs a single worker per
    container so the cap matches the per-container tmpfs budget; scale out
    with container replicas instead of extra workers.
    """

    def __init__(
        self,
        app,
        max_bytes: int | None = None,
        inflight_budget: int | None = None,
        ledger: InflightLedger | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes if max_bytes is not None else MAX_BODY_BYTES
        self.inflight_budget = (
            inflight_budget if inflight_budget is not None else DEFAULT_INFLIGHT_BUDGET
        )
        self.ledger = ledger if ledger is not None else InflightLedger()

    def decision_for_scope(self, scope) -> Decision:
        """Declared-length check; also the place where cost rules live."""
        if scope["type"] != "http":
            return Decision(allowed=True)
        length = _declared_length(scope)
        if length is not None and length > self.max_bytes:
            return TOO_LARGE
        return Decision(allowed=True)

    @staticmethod
    async def _respond(scope, receive, send, verdict: Decision) -> None:
        headers = (
            {"Retry-After": str(verdict.retry_after)}
            if verdict.retry_after is not None
            else None
        )
        response = JSONResponse(
            {"detail": verdict.detail}, status_code=verdict.status, headers=headers
        )
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        verdict = self.decision_for_scope(scope)
        if not verdict.allowed:
            await self._respond(scope, receive, send, verdict)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        multipart = _is_multipart(scope)
        reserved = 0
        try:
            with closing(
                tempfile.SpooledTemporaryFile(max_size=64 * 1024, mode="w+b")
            ) as body:
                received = 0
                while True:
                    message = await receive()
                    if message["type"] != "http.request":
                        return
                    chunk = message.get("body", b"")
                    received += len(chunk)
                    if received > self.max_bytes:
                        await self._respond(scope, receive, send, TOO_LARGE)
                        return
                    cost = len(chunk) * (2 if multipart else 1)
                    if cost and self.ledger.reserved + cost > self.inflight_budget:
                        await self._respond(scope, receive, send, BUSY)
                        return
                    if cost:
                        self.ledger.add(cost)
                        reserved += cost
                    body.write(chunk)
                    if not message.get("more_body"):
                        break

                body.seek(0)

                async def replay():
                    chunk = body.read(64 * 1024)
                    if chunk:
                        return {
                            "type": "http.request",
                            "body": chunk,
                            "more_body": True,
                        }
                    return {"type": "http.request", "body": b"", "more_body": False}

                await self.app(scope, replay, send)
        finally:
            self.ledger.release(reserved)
