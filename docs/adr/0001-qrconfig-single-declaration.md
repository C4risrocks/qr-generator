# ADR-0001: QRConfig es la única declaración del contrato de opciones QR

- Estado: aceptada (2026-09-23)
- Contexto: el contrato de opciones QR estaba escrito cuatro veces (JS
  `buildFormData`, dos firmas `Form()` de ~15 parámetros, `_build_config`
  de 17 parámetros y el CLI). Añadir un campo tocaba 4-5 archivos.
- Decisión: adoptar el diseño "QRConfig como única declaración"
  (opción C del *design-it-twice*). `parse_options(source: Mapping,
  **overrides) -> QRConfig` en `core.py` coerciona según los tipos
  declarados en el propio dataclass; los campos `data` y `logo` son
  transporte y llegan como overrides. La web consume la costura vía la
  dependencia `form_request`; el CLI mapea sus `dest` a los nombres de
  campo. Los nombres de formulario y flags CLI quedan byte-idénticos.
- Alternativas descartadas:
  - *Parser único + `OptionsOutcome`* (A): interfaz nueva y vocabulario
    extra (`OptionsOutcome`) sin ganancia adicional respecto a C.
  - *Registro de descriptores* (B): máxima flexibilidad (schema hacia el
    catálogo), pero +60 líneas de abstracción y riesgo de drift
    byte-a-byte con el JS; flexibilidad que nadie pide aún
    (speculative).
- Consecuencias:
  - Nueva opción = 1 campo del dataclass (+ mapping del caller).
  - Los handlers declaran cero parámetros de opciones (`FormRequest`).
  - La coerción y sus mensajes de error viven en un solo sitio
    (`_coerce_option`); los tests la validan en `tests/test_options.py`.
  - Los errores 422 de pydantic por int/float malos pasan a 400 con
    `InvalidInput` (mensajes user-visible); ninguna prueba externa
    dependía de 422.
  - La rama SVG de `buildFormData` se mantiene por eficiencia (no subir
    bytes de logo para SVG); el servidor ya es tolerante.
