# ADR-0003: Auto-ajuste del logo (editor web, rotación, recorte y fit)

- Estado: aceptada (2026-09-23)
- Contexto: el logo debía encajar en 1024×1024 y `validate_logo`
  rechazaba con 400 cualquier imagen mayor. La web no ofrecía edición:
  se subía el archivo crudo.
- Decisión:
  1. **El servidor (PIL) es la autoridad de calidad.** La web envía la
     imagen original + metadatos del editor; el procesado de píxeles
     vive en `core.prepare_logo`. El pipeline aplica primero
     `exif_transpose` (alineado con lo que el navegador muestra),
     rotación en cuartos de vuelta por transposición (sin pérdida) y
     crop por recorte exacto (sin pérdida); solo si el resultado excede
     `MAX_LOGO_DIMENSIONS` se reescala con Lanczos y premultiplicación
     de alpha (des-premultiplicando solo los píxeles con alpha parcial).
  2. **El modo se conserva**: RGBA mantiene su alpha bit-exacta, RGB/L
     mantienen su modo; una paleta `P` sin reescalado se conserva tal
     cual y solo al reescalar convierte a RGBA (con transparencia) o
     RGB, porque reescalar índices corrompería los colores.
  3. **>1024 ya no rechaza**: cualquier logo mayor se ajusta a la caja
     manteniendo el aspecto y avisa (`X-QR-Warnings` / `warnings` en
     previews), siguiendo la política tolerante de la omisión SVG
     (ADR-0001). El CLI comparte el mismo pipeline.
  4. Campos de transporte (como `logo`, fuera de `QRConfig`):
     `logo_rotate` (`0/90/180/270`, vacío → 0) y `logo_crop` (JSON
     `{"x","y","w","h"}` en píxeles de la imagen ya rotada; inválido →
     400 con mensaje user-visible).
- Alternativa descartada: procesado completo en el cliente (canvas):
  evitaba el cambio de contrato pero el reescalado del canvas pierde
  calidad frente a Lanczos+premultiply y el PNG saldría siempre RGBA
  de 8 bits, sin preservar el modo original.
- Consecuencias:
  - `validate_logo` se elimina (reemplazada por `prepare_logo`, que
    garantiza el límite por construcción).
  - Los previews siguen siendo fieles automáticamente: consumen
    `config.logo` ya procesada.
  - El editor web (modal vanilla JS) dibuja la selección sobre la
    imagen rotada; sus coordenadas viajan tal cual al servidor.
  - El coste CPU del unpremultiply queda acotado: solo corre si el
    alpha reescalado tiene valores parciales (bordes suaves); logos de
    bordes duros lo saltan por histograma.
