# QR Studio

Generador de códigos QR personalizables desde un enlace, texto o archivo de
texto, con interfaz de línea de comandos, web local, base de datos de
monitoreo (rate limit diario + inputs) y panel de administración.

## Requisitos

- [uv](https://docs.astral.sh/uv/)
- Docker (opcional, para Compose)

## Uso (CLI)

```bash
uv sync

# Básico
uv run qrgen "https://ejemplo.com"

# Desde un archivo de texto UTF-8 (guardará nombre + contenido en la BD)
uv run qrgen --file archivo.txt -o qr.png

# Estilo, colores y logo
uv run qrgen "https://ejemplo.com" \
  --style circles \
  --fg "#1d4ed8" --bg "#f5f8ff" \
  --logo logo.png \
  -o qr.png

# Gradiente + transparencia
uv run qrgen "https://ejemplo.com" \
  --style dots --gradient radial \
  --fg "#ea580c" --gradient-to "#db2777" \
  --transparent-background -o qr.png

# Marco y texto (PNG)
uv run qrgen "https://ejemplo.com" \
  --frame "#18181b" --title "Mi enlace" --subtitle "Sitio web" \
  -o qr.png

# Salida SVG
uv run qrgen "https://ejemplo.com" --format svg -o qr.svg

# Catálogo de estilos y combinaciones
uv run qrgen styles

# Generar el hash de la contraseña del admin
uv run qrgen hash-password

# Generar todas las variables .env del panel admin
uv run qrgen admin-setup
# o bien: uv run qrgen admin-setup "mi-clave" --username admin --session-secret "secreto"
uv run qrgen admin-setup "mi-clave" --session-secret "$(openssl rand -base64 32)"

# Aplicar migraciones de base de datos
uv run qrgen migrate

# Web UI
uv run qrgen serve
# o bien
uv run qrgen-serve
```

### Opciones

| Opción | Descripción |
| --- | --- |
| `data` | Enlace o texto a codificar |
| `--file` | Archivo de texto UTF-8 (excluyente con `data`; máx. 2048 caracteres) |
| `-o, --output` | Ruta de salida (por defecto `qr.png`) |
| `-f, --format` | `png` o `svg` (por defecto se infiere de la extensión) |
| `--style` | `square`, `gapped`, `rounded`, `circles`, `dots`, `vertical-bars`, `horizontal-bars` |
| `--fg, --foreground` | Color de los módulos (hex o nombre) |
| `--bg, --background` | Color de fondo (hex o nombre) |
| `--gradient` | `none`, `linear-h`, `linear-v` o `radial` |
| `--gradient-to` | Segundo color del gradiente |
| `--transparent-background` | Fondo transparente (PNG) |
| `-e, --error-correction` | `L`, `M`, `Q` o `H` (por defecto `M`) |
| `--box-size` | Píxeles por módulo (por defecto `10`) |
| `--border` | Margen de seguridad en módulos (por defecto `4`) |
| `--logo` | Imagen a incrustar en el centro (fuerza corrección `H`) |
| `--logo-ratio` | Tamaño del logo como proporción del QR (por defecto `0.2`) |
| `--frame` | Color del marco alrededor del QR (PNG) |
| `--title` / `--subtitle` | Título y subtítulo (PNG) |

## Base de datos y monitoreo

La aplicación usa PostgreSQL para dos propósitos:

- **Rate limit diario**: por cliente (IP + User-Agent), ventana de 24 h (día
  UTC). Límites `RATE_LIMIT_GENERATE_PER_DAY=250` y
  `RATE_LIMIT_PREVIEWS_PER_DAY=500`. Al exceder responde `429` con
  `Retry-After`. Si la base de datos falla, la petición se permite
  (fail-open) y se registra el error.
- **Inputs**: texto o archivo usado para generar un QR (solo en
  `/api/generate`; los previews no persisten nada). El contenido se guarda
  únicamente después de generar correctamente el QR y nunca aparece en logs.

Tablas: `clients`, `rate_limit_windows`, `qr_inputs`. Retención:
ventanas de rate limit 48 h, inputs `INPUT_RETENTION_DAYS=30` (limpieza
automática cada 15 min). Los clientes persisten.

No se almacena información del navegador con fines de analítica más allá de
lo descrito (IP, User-Agent y Accept-Language para el rate limit).

## Panel de administración

Acceso en `/admin` (ruta no enlazada desde la web pública).

- Configuración de la cuenta con variables de entorno:
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD_HASH` (generado con `uv run qrgen hash-password`)
  - `SESSION_SECRET` (firma de la sesión)
- Para obtener las tres variables listas para pegar en `.env`, usa
  `uv run qrgen admin-setup [password] [--username admin] [--session-secret]`.
  Sin argumentos pide la contraseña y genera un `SESSION_SECRET` aleatorio.
  Nota: si pegas el hash en el `.env` de Docker Compose, escapa los `$` como
  `$$` (`ADMIN_PASSWORD_HASH=pbkdf2_sha256$$200000$$...`) para evitar que
  Compose los interpole; en Dokploy se pegan sin escapar.
- Login con rate limit de intentos (10/min por IP).
- Vistas de solo lectura: resumen, peticiones por día (últimos 14 días),
  clientes con % de uso del límite, inputs recientes (contenido truncado) y
  configuración vigente. Los límites se pueden calibrar conforme se
  recopilen datos de uso real.

## Uso (Web)

```bash
uv run qrgen serve
```

Abre http://127.0.0.1:8000 (solo local). La web incluye:

- Galería visual de todos los estilos con miniaturas en vivo y selección.
- Colores, paletas rápidas, gradientes y fondo transparente.
- Logo central con control de tamaño.
- Marco y título/subtítulo.
- Entrada por texto/enlace o por archivo de texto `.txt` (UTF-8, máx. 2048).
- Combinaciones predefinidas que aplican configuración completa.
- Avisos de compatibilidad (SVG, contraste, logo) antes de exportar.
- Modo claro/oscuro.

Las vistas previas se actualizan automáticamente (debounce de 250 ms), la
vista grande refleja la configuración exacta (estilo, marco y texto) y la
descarga usa la configuración actual con tamaño completo. Al exportar en SVG,
el logo, la transparencia, el marco y el texto se desactivan; los gradientes y
los estilos no disponibles caen a color sólido o cuadrados con aviso.

## Despliegue con Docker

### Dockerfile (producción)

- Build multi-stage con `uv sync --locked --no-dev --no-editable`.
- Imagen runtime slim sin `uv`, tests ni herramientas de desarrollo.
- Imágenes base y versión de `uv` fijadas por digest.
- Usuario no root, `HEALTHCHECK` contra `/healthz`.
- Arranque: `scripts/start.sh` espera la BD, aplica migraciones
  (`qrgen migrate`) y sirve con `qrgen-serve`.

```bash
docker build -t qrgen .
```

### Docker Compose (local, con PostgreSQL)

```bash
docker compose up --build
```

Incluye el servicio `db` (Postgres con volumen y healthcheck), migraciones
automáticas, `read_only`, `tmpfs` para `/tmp`, `cap_drop: [ALL]` y
`no-new-privileges`. El healthcheck de la aplicación usa `/readyz`.

### Variables de entorno

| Variable | Descripción | Valor por defecto |
| --- | --- | --- |
| `DATABASE_URL` | URL de PostgreSQL (asyncpg) | requerida |
| `HOST` / `PORT` | Escucha del servidor | `0.0.0.0` / `8000` |
| `RATE_LIMIT_GENERATE_PER_DAY` | Límite diario de `/api/generate` | `250` |
| `RATE_LIMIT_PREVIEWS_PER_DAY` | Límite diario de `/api/previews` | `500` |
| `RATE_LIMIT_WINDOW_RETENTION_HOURS` | Retención de ventanas | `48` |
| `INPUT_RETENTION_DAYS` | Retención de inputs | `30` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | Cuenta del panel | — |
| `SESSION_SECRET` | Firma de sesiones | generado si falta |
| `SESSION_HTTPS_ONLY` | Cookie solo por HTTPS | `false` |
| `GEOIP_DB_PATH` | Base GeoLite2 opcional para país | — |

### Protección de los endpoints

- Rate limit diario por cliente (IP+UA) en `/api/generate` y `/api/previews`
  (responde `429` con `Retry-After`).
- Límite de cuerpo de petición: 6 MB (responde `413`).
- Texto/archivo limitado a 2048 caracteres; archivos binarios o no UTF-8
  rechazados; logo máx. 5 MB y 1024 × 1024 px.

## Despliegue con Dokploy

Modo recomendado: **Application** desde Git con build `Dockerfile`.

1. Crea el servicio **Application** en Dokploy.
2. Conecta el repositorio GitHub y la rama `main`.
3. Build method: `Dockerfile`. Puerto de la aplicación: `8000`.
4. Crea un servicio **PostgreSQL** gestionado y usa su `DATABASE_URL`.
5. Asigna el dominio (HTTPS automático con Traefik).
6. Healthcheck: `/healthz` (liveness) y `/readyz` (readiness con BD).
7. Aplica el endurecimiento de `compose.yaml` en los ajustes avanzados:
   filesystem de solo lectura, `tmpfs` en `/tmp`, `cap_drop: [ALL]` y
   `no-new-privileges`.
8. Configura las variables de entorno y secretos:

```text
DATABASE_URL
SESSION_SECRET
ADMIN_USERNAME
ADMIN_PASSWORD_HASH
RATE_LIMIT_GENERATE_PER_DAY
RATE_LIMIT_PREVIEWS_PER_DAY
INPUT_RETENTION_DAYS
SESSION_HTTPS_ONLY=true
```

9. Guarda en GitHub los secretos para el despliegue automático:

```text
DOKPLOY_URL
DOKPLOY_API_KEY
DOKPLOY_APPLICATION_ID
PRODUCTION_URL
```

10. Desactiva el auto-deploy de Dokploy: el workflow de GitHub es quien
    dispara el despliegue solo tras CI exitoso en `main`.

## CI/CD

- **CI** (`.github/workflows/ci.yml`): en PRs y `main` ejecuta `ruff`, `pytest`
  (contra PostgreSQL real), migraciones, build Docker con caché BuildKit,
  smoke test del contenedor con BD (healthz, readyz, catálogo, generación,
  previews, login admin) y escaneo con Trivy (HIGH/CRITICAL). Concurrencia
  por rama con cancelación de ejecuciones antiguas.
- **Deploy** (`.github/workflows/deploy-dokploy.yml`): se activa únicamente tras
  un CI exitoso causado por un push real a `main` del repositorio correcto
  (o manualmente), llama a la API de Dokploy y espera a que `/healthz`
  responda. Concurrencia de despliegue sin cancelar (cola).
- **Dependabot**: actualizaciones semanales de actions, imágenes Docker y
  dependencias Python.

Alternativa: publicar la imagen en GHCR desde CI (build único, digest
inmutable, rollback por digest) y hacer que Dokploy la descargue. Recomendada
como evolución cuando se necesiten releases versionadas.

## Notas

- En SVG los estilos `rounded`, `vertical-bars` y `horizontal-bars` caen a
  cuadrados simples, y los gradientes a color sólido (con aviso).
- El logo, el fondo transparente, el marco y el texto solo se admiten en PNG.
- El contenido de los inputs se guarda en `qr_inputs` con hash SHA-256 y se
  elimina según `INPUT_RETENTION_DAYS`.

## Desarrollo

```bash
uv run pytest            # SQLite local; DATABASE_URL=... para PostgreSQL
uv run ruff check .
```
