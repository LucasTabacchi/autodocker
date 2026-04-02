# Oracle Preview Runner Deployment

## Objetivo

Levantar el `preview-runner` en una VM `VM.Standard.A1.Flex` de Oracle Cloud para publicar previews efímeras con Caddy y limpiar sesiones vencidas automáticamente.

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
