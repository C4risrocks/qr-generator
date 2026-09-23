# CONTEXT.md — glosario de dominio de QR Studio

Términos con significado fijo en el código, los tests y el README. Los
agentes y personas deben usarlos tal cual; cuando un término se afine,
actualízalo aquí (ver skill domain-modeling).

## Términos

- **client**: identidad del solicitante para cuotas y monitoreo: IP
  (resuelta vía *trusted proxy*) + User-Agent. Persiste en la tabla
  `clients`; no se borra por retención.
- **input**: lo que el usuario pide codificar — texto/enlace o archivo
  UTF-8 (≤ 2048 caracteres), excluyentes. Solo `/api/generate` lo persiste;
  los *previews* no persisten nada.
- **payload**: el `InputPayload` resuelto (contenido + tipo + filename
  opcional) entregado por la resolución de *input*.
- **style**: forma de los módulos (`square`, `gapped`, `rounded`,
  `circles`, `dots`, `vertical-bars`, `horizontal-bars`). Algunos no
  existen en SVG y caen a cuadrados con aviso.
- **palette / preset**: combinaciones predefinidas de colores
  (*palette*) y de configuración completa (*preset*) que consume el
  catálogo (`/api/catalog`) y la web.
- **gradient**: relleno de módulos (`none`, `linear-h`, `linear-v`,
  `radial`); no disponible en SVG (cae a color sólido con aviso).
- **logo**: imagen incrustada al centro (≤ 5 MB, ≤ 1024 px, ratio ≤ 0.3).
  Fuerza corrección de error `H`. PNG-only.
- **frame / title / subtitle**: marco con color, título y subtítulo
  alrededor del QR. Trio PNG-only.
- **preview**: miniatura PNG por estilo (galería) o vista fiel de la
  configuración seleccionada. Debe reflejar exactamente lo que la
  descarga puede producir (mismo pipeline de coerción).
- **warnings**: avisos de coerción suave (SVG, contraste bajo, EC subida
  a H). Nunca son errores; en la API viajan por `X-QR-Warnings` y en el
  JSON de previews por `warnings`.
- **window**: ventana de rate limit por client+endpoint+day (UTC).
  Retención 48 h; las previas a la migración 0002 se fechan a la
  medianoche UTC de su día.
- **quota**: límite diario por endpoint (`generate` 250, `previews` 500
  por defecto). Al exceder: 429 con `Retry-After`.
- **fail-open**: si la base de datos falla, la petición se permite y se
  registra el error una vez (`warn_once`). Nunca bloquear por falla de
  infraestructura.
- **retention**: limpieza automática cada 15 min: ventanas 48 h,
  inputs `INPUT_RETENTION_DAYS` (30 por defecto). Los clients persisten.
- **spool**: archivo temporal del cuerpo de la petición (≤ 64 KB en
  memoria, luego tmpfs `/tmp`). Los cuerpos multipart cuentan **doble**
  en el presupuesto en vuelo (el parser de formularios puede volcar
  partes aparte).
- **in-flight budget**: presupuesto global de bytes spooled a la vez
  (`MAX_INFLIGHT_BODY_BYTES`, por proceso). Al saturar: 503 con
  `Retry-After`. Lo posee el `BodyGate` vía su `InflightLedger`.
- **BodyGate**: módulo externo de la cadena ASGI que acota y re-emite el
  cuerpo; sus decisiones son objetos `Decision` observables (413/503).
- **single-worker invariant**: un worker Uvicorn por contenedor; escalar
  con réplicas, nunca `--workers > 1` (el presupuesto en vuelo es por
  proceso y el tmpfs es por contenedor).
- **trusted proxy**: proxy de `FORWARDED_ALLOW_IPS` cuyo
  `X-Forwarded-For` se respeta; lectura derecha→izquierda saltando hops
  confiables; nunca rangos abiertos (`0.0.0.0/0` rechazado).
- **options seam**: la declaración de `QRConfig` es la única lista de
  opciones; `parse_options(mapping, **overrides)` la convierte en config
  desde cualquier payload (form web, flags CLI). Ver ADR-0001.
