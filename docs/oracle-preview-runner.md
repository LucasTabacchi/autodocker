# Oracle Preview Runner Deployment

## Objetivo

Levantar el `preview-runner` en una VM `VM.Standard.A1.Flex` de Oracle Cloud para publicar previews efímeras con Caddy y limpiar sesiones vencidas automáticamente.

## Setup operativo actual

Estado validado el `2026-04-08`.

- VM pública Oracle: `144.22.177.160`
- Host interno del runner: `127.0.0.1:9000`
- Host público del runner: `https://runner.144.22.177.160.sslip.io`
- Base domain de previews: `144.22.177.160.sslip.io`
- Layout real:
  - `/srv/preview-runner/app`
  - `/srv/preview-runner/env/preview-runner.env`
  - `/var/lib/autodocker/previews`
  - `/etc/caddy/Caddyfile`
  - `/etc/caddy/routes/`

Los secretos reales no se documentan en el repo. Quedaron cargados en `/srv/preview-runner/env/preview-runner.env`.

## Layout recomendado del host

- `/srv/preview-runner/app`
- `/srv/preview-runner/env/preview-runner.env`
- `/var/lib/autodocker/previews`
- `/etc/caddy/Caddyfile`
- `/etc/caddy/routes/`
- `/var/log/preview-runner/`

## Provisionado de la VM

1. Crear VM `VM.Standard.A1.Flex` con `2 OCPUs` y `12 GB RAM`.
2. Asignar Ubuntu `24.04 LTS`.
3. Configurar DNS wildcard:
   - `*.previews.example.com -> IP pública de la VM`
4. Instalar paquetes:
   - `docker`
   - `docker compose plugin`
   - `caddy`
   - `python3.13`, `python3.13-venv`, `git`
5. Crear usuario del servicio:
   - `sudo useradd --system --create-home --shell /bin/bash autodocker`
6. Crear directorios:
   - `/srv/preview-runner`
   - `/srv/preview-runner/env`
   - `/var/lib/autodocker/previews`
   - `/etc/caddy/routes`

## Deploy de la aplicación

1. Clonar el repo en `/srv/preview-runner/app`.
2. Crear virtualenv:

```bash
python3.13 -m venv /srv/preview-runner/app/.venv
```

3. Instalar dependencias:

```bash
/srv/preview-runner/app/.venv/bin/pip install -r /srv/preview-runner/app/requirements.txt
```

4. Copiar `deploy/oracle/preview-runner/preview-runner.env.example` a `/srv/preview-runner/env/preview-runner.env`.
5. Completar secretos reales.
6. Correr migraciones:

```bash
cd /srv/preview-runner/app
source .venv/bin/activate
python manage.py migrate --noinput
```

## Caddy

1. Copiar `deploy/oracle/preview-runner/Caddyfile` a `/etc/caddy/Caddyfile`.
2. Verificar que `/etc/caddy/routes` exista y sea escribible por `autodocker`.
3. Validar configuración:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

4. Reiniciar Caddy:

```bash
sudo systemctl restart caddy
```

En el setup actual el `Caddyfile` publica:

- `runner.144.22.177.160.sslip.io`
- `import /etc/caddy/routes/*.caddy`

Cada preview genera dinámicamente un archivo `prv-*.caddy` en `/etc/caddy/routes`.

## systemd

1. Copiar estos archivos:
  - `deploy/oracle/preview-runner/systemd/preview-runner.service`
   - `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.service`
   - `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.timer`
2. Ejecutar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now preview-runner.service
sudo systemctl enable --now reconcile-preview-runner-sessions.timer
```

## Firewall y red

Abrir solo:

- `22/tcp`
- `80/tcp`
- `443/tcp`

Cerrar:

- `9000/tcp`
- puertos efímeros de contenedores

El runner queda ligado a `127.0.0.1:9000`. No lo publiques en Internet.

Además del Security List/NSG de Oracle, revisar el firewall local de la VM. En el setup actual fue necesario permitir explícitamente `80/tcp` y `443/tcp` en `iptables`.

## Variables clave del runner

Valores funcionales del runner ya desplegado:

```env
AUTODOCKER_DEPLOYMENT_ROLE=preview_runner
AUTODOCKER_ENABLE_RUNTIME_JOBS=true
AUTODOCKER_PREVIEW_BACKEND=local
AUTODOCKER_PREVIEW_CADDY_ENABLED=true
AUTODOCKER_PREVIEW_URL_STRATEGY=runner_managed
AUTODOCKER_PREVIEW_PUBLIC_BASE_DOMAIN=144.22.177.160.sslip.io
AUTODOCKER_PREVIEW_HTTP_READY_TIMEOUT_SECONDS=75
```

Además, el runner necesita secretos reales para:

- `DJANGO_SECRET_KEY`
- `AUTODOCKER_PREVIEW_RUNNER_TOKEN`
- `AUTODOCKER_TOKEN_ENCRYPTION_KEY`

## Variables clave de la app principal

Para apuntar la app principal al runner remoto, el `.env` de la app debe quedar equivalente a esto:

```env
AUTODOCKER_DEPLOYMENT_ROLE=app
AUTODOCKER_ENABLE_RUNTIME_JOBS=false
AUTODOCKER_PREVIEW_BACKEND=remote_runner
AUTODOCKER_PREVIEW_RUNNER_BASE_URL=https://runner.144.22.177.160.sslip.io
AUTODOCKER_PREVIEW_PUBLIC_BASE_DOMAIN=144.22.177.160.sslip.io
AUTODOCKER_PREVIEW_URL_STRATEGY=runner_managed
AUTODOCKER_PREVIEW_RUNNER_REQUEST_TIMEOUT=60
AUTODOCKER_PREVIEW_RUNNER_MAX_ACTIVE_SESSIONS=2
AUTODOCKER_PREVIEW_HTTP_READY_TIMEOUT_SECONDS=75
```

Si la app principal se expone temporalmente con un túnel, también hay que alinear:

- `AUTODOCKER_APP_BASE_URL`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`

## Publicación HTTPS de previews

El runner no debe marcar una preview como `READY` apenas recarga Caddy. La publicación correcta espera a que la URL pública del subdominio responda realmente antes de devolverla.

Esto evita el falso positivo donde:

- la preview queda `READY`
- el subdominio existe
- pero el handshake TLS todavía no terminó o Caddy sigue emitiendo el certificado

El comportamiento actual ya espera a que `https://prv-<id>.<base-domain>` responda antes de confirmar la publicación.

## Checklist de hardening

- usar token largo y rotado para `AUTODOCKER_PREVIEW_RUNNER_TOKEN`
- no reutilizar `DJANGO_SECRET_KEY`
- separar `AUTODOCKER_TOKEN_ENCRYPTION_KEY`
- ejecutar el servicio con usuario `autodocker`
- dar permisos mínimos de escritura solo a:
  - `/etc/caddy/routes`
  - `/var/lib/autodocker/previews`
- revisar periódicamente:
  - `journalctl -u preview-runner.service`
  - `journalctl -u caddy`
  - `systemctl list-timers reconcile-preview-runner-sessions.timer`

## Smoke test

1. Confirmar healthcheck:

```bash
curl http://127.0.0.1:9000/health/
```

2. Crear una preview desde la app principal.
3. Verificar que aparezca un archivo `prv-*.caddy` en `/etc/caddy/routes`.
4. Abrir la URL pública `https://prv-<id>.previews.example.com`.
5. Detener la preview y confirmar que el archivo de ruta se elimine.

## Verificación real aplicada

En la VM actual se verificó:

- `https://runner.144.22.177.160.sslip.io/health/` -> `200 OK`
- creación de previews remotas desde la app principal
- publicación HTTPS real de subdominios `prv-*`
- `stop` remoto con cleanup de rutas públicas
