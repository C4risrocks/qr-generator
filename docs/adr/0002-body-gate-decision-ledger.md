# ADR-0002: BodyGate con Decision observable y puerto InflightLedger

- Estado: aceptada (2026-09-23)
- Contexto: los límites del cuerpo (`MAX_BODY_BYTES`,
  `MAX_INFLIGHT_BODY_BYTES`) y el contador en vuelo eran globals de
  módulo en `server.py`; más de diez sitios de test hacían
  `monkeypatch.setattr(server, ...)` y asertaban sobre el contador crudo
  `_inflight_body_bytes`.
- Decisión: adoptar el diseño `BodyGate` + `Decision` + `InflightLedger`
  (opción F del *design-it-twice*). El gate es el módulo ASGI externo
  (`qrgen/body_gate.py`) que acota, hace spool y re-emite el cuerpo; sus
  veredictos son objetos `Decision` observables (413/503 +
  `Retry-After`); la contabilidad en vuelo vive tras el puerto
  `InflightLedger` (producción: `InflightLedger`; tests: fakes que
  registran costes). Los límites llegan como parámetros de constructor
  desde `build_app(max_body_bytes=…, inflight_max_bytes=…)` — los tests
  construyen en vez de parchear. El rate limit se separó en su propio
  middleware interno (`RateLimitMiddleware`): el gate solo llama hacia
  dentro cuando el cuerpo está completo, así las rechazas de tamaño
  nunca consumen cuota.
- Alternativas descartadas:
  - *Constructor-only* (D): diff mínimo, pero los tests seguirían
    mirando estado interno del gate (`gate._inflight`).
  - *Settings completo* (E): toca db/rate_limit/admin/proxy — es la
    costura del candidato de sesión/BD y merece su propio deepening
    (queda como exploración futura).
- Consecuencias:
  - Seam real: dos adaptadores del ledger (proceso + fake) — cumple la
    regla de los dos adaptadores.
  - Los tests asertan comportamiento (Decision, `ledger.reserved`) y no
    estado de módulo; desaparecen 8+ sitios de monkeypatch.
  - `SESSION_SECRET`/`SESSION_HTTPS_ONLY` siguen leyéndose al import:
    queda fuera de este ADR (candidato #3/#4 del review de
    arquitectura).
  - Compat intacta: `add_middleware(BodyGate, …)`, `uvicorn.run(app,
    workers=1)`, Dockerfile y compose sin cambios.
