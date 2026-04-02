# AutoDocker

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![Version](https://img.shields.io/badge/version-v0.1.0-blue)
![License](https://img.shields.io/badge/license-pending-lightgrey)

AutoDocker es una web app con API para desarrolladores que analiza un proyecto a partir de un `.zip` o una URL Git, detecta su stack técnico y genera artefactos Docker editables antes de exportarlos o validarlos. El objetivo es reducir el tiempo necesario para dockerizar aplicaciones reales sin obligar al usuario a resolver manualmente cada detalle de runtime, puertos, servicios auxiliares y pipelines base.

**Tipo de proyecto:** web app + API  
**Audiencia:** desarrolladores  
**Tecnologías principales:** Python, Django, Django REST Framework, Celery, Redis, PostgreSQL, Docker, JavaScript, Monaco Editor, Supabase Storage, GitHub Actions

## Motivación

Dockerizar proyectos reales suele implicar mucho trabajo repetitivo:

- detectar framework, runtime y comandos de arranque
- decidir cómo separar build y runtime
- generar `Dockerfile`, `.dockerignore` y `docker-compose.yml`
- contemplar servicios auxiliares como Postgres o Redis
- agregar una validación reproducible antes de exportar

AutoDocker automatiza esa primera capa de trabajo y deja el resultado en un formato editable para que el developer conserve control sobre la configuración final.

## Características

- análisis por archivo `.zip` o repositorio Git
- detección heurística de stacks como Node.js, Python, PHP, Java, Go y Ruby
- soporte para monorepos y componentes múltiples
- generación de `Dockerfile`, `.dockerignore`, `docker-compose.yml`, guía de uso y bootstrap de CI/deploy
- editor embebido para ajustar artefactos antes de descargarlos
- historial por usuario y workspaces compartidos
- validación local o remota de builds
- preview local con Docker o preview remota vía preview runner dedicado
- integración con GitHub para abrir PRs con los artefactos generados

## Demo y capturas

**Demo web actual:** `https://autodocker-web.onrender.com`

Si todavía no tenés assets visuales definitivos, podés usar estos placeholders en el repo:

- `docs/assets/dashboard.png`
- `docs/assets/editor.png`
- `docs/assets/validation.png`

Ejemplo de cómo quedarían:

```md
![Dashboard](docs/assets/dashboard.png)
![Editor de artefactos](docs/assets/editor.png)
![Validación remota](docs/assets/validation.png)
```

## Tecnologías usadas

### Backend

- Python 3.13
- Django 5
- Django REST Framework
- Celery
- Gunicorn
- WhiteNoise

### Infraestructura y storage

- PostgreSQL
- Redis
- Docker y Docker Compose
- Supabase Storage con compatibilidad S3

### Frontend

- Templates server-rendered de Django
- JavaScript vanilla
- Monaco Editor vía CDN

### Integraciones

- GitHub Actions para validación remota
- GitHub API para apertura de pull requests
- preview runner privado para previews remotas efímeras

## Requisitos previos

### Desarrollo mínimo local

- Python 3.13
- `pip`
- virtualenv o `python -m venv`

### Desarrollo con stack completo

- Docker
- Docker Compose

### Producción o validación remota

- PostgreSQL
- Redis
- bucket privado en Supabase Storage
- repo executor privado en GitHub Actions
- host dedicado con Docker si querés previews remotas públicas

### Runner remoto de previews recomendado

Para previews remotas de producción, la topología soportada y recomendada es:

- app principal separada del host que ejecuta previews
- `preview-runner` en una VM dedicada
- Docker + Docker Compose plugin en ese host
- Caddy publicando `https://prv-<id>.<dominio>`
- URLs efímeras con TTL corto y cleanup automático

Configuración inicial recomendada para Oracle Cloud Free Tier:

- shape `VM.Standard.A1.Flex`
- `2 OCPUs`
- `12 GB RAM`
- Ubuntu `24.04 LTS`
- `2 previews` simultáneas máximas
- `1` único servicio HTTP público por preview

## Instalación

### Opción 1: desarrollo local con Python

1. Cloná el repositorio.

```bash
git clone https://github.com/LucasTabacchi/autodocker.git
cd autodocker
```

2. Creá y activá el entorno virtual.

```bash
python -m venv .venv
```

En Windows:

```bash
.venv\Scripts\activate
```

En macOS/Linux:

```bash
source .venv/bin/activate
```

3. Instalá dependencias.

```bash
pip install -r requirements.txt
```

4. Creá tu archivo de entorno local.

```bash
copy .env.example .env
```

5. Aplicá migraciones.

```bash
python manage.py migrate
```

6. Creá un superusuario.

```bash
python manage.py createsuperuser
```

7. Levantá el servidor.

```bash
python manage.py runserver
```

8. Abrí la app en `http://127.0.0.1:8000`.

### Opción 2: desarrollo con Docker Compose

1. Creá el archivo de entorno para Docker.

```bash
copy .env.docker.example .env.docker
```

2. Levantá el stack.

```bash
docker compose up --build
```

3. Creá un superusuario dentro del contenedor web.

```bash
docker compose exec web python manage.py createsuperuser
```

4. Accedé a `http://127.0.0.1:8000`.

## Uso

### Flujo principal desde la UI

1. Iniciá sesión.
2. Subí un `.zip` o pegá una URL Git.
3. Esperá a que termine el análisis.
4. Revisá y editá los artefactos generados.
5. Elegí entre descargar, regenerar, validar o abrir un PR.

### Ejemplo de creación de análisis desde la API

La API requiere sesión autenticada. Un ejemplo desde el navegador o desde un cliente que ya tenga la cookie de sesión:

```js
const formData = new FormData();
formData.append("project_name", "demo-repo");
formData.append("repository_url", "https://github.com/acme/demo");
formData.append("generation_profile", "production");

const response = await fetch("/api/analyses/", {
  method: "POST",
  body: formData,
  credentials: "same-origin",
});

const analysis = await response.json();
console.log(analysis.id, analysis.status);
```

### Ejemplo de validación de un análisis existente

```js
const response = await fetch(`/api/analyses/${analysisId}/validate/`, {
  method: "POST",
  credentials: "same-origin",
});

const job = await response.json();
console.log(job.id, job.status);
```

### Endpoints principales

- `GET /api/analyses/`
- `POST /api/analyses/`
- `GET /api/analyses/{id}/`
- `POST /api/analyses/{id}/regenerate/`
- `POST /api/analyses/{id}/validate/`
- `POST /api/analyses/{id}/github-pr/`
- `GET /api/analyses/{id}/download/`
- `PATCH /api/artifacts/{id}/`
- `GET /api/jobs/{id}/`

## Configuración de previews remotas

### Variables del runner

Estas variables aplican al host donde corre `preview-runner`:

```env
AUTODOCKER_DEPLOYMENT_ROLE=preview_runner
AUTODOCKER_PREVIEW_KEEP_WORKSPACES=false
AUTODOCKER_PREVIEW_TTL_SECONDS=1800
AUTODOCKER_PREVIEW_MAX_TTL_SECONDS=2700
AUTODOCKER_PREVIEW_URL_STRATEGY=runner_managed
AUTODOCKER_PREVIEW_PUBLIC_BASE_DOMAIN=previews.example.com
AUTODOCKER_PREVIEW_RUNNER_REQUEST_TIMEOUT=60
AUTODOCKER_PREVIEW_RUNNER_MAX_ACTIVE_SESSIONS=2
AUTODOCKER_PREVIEW_MAX_BUNDLE_MB=100
AUTODOCKER_PREVIEW_PER_SESSION_CPU=0.75
AUTODOCKER_PREVIEW_PER_SESSION_MEMORY_MB=2560
AUTODOCKER_PREVIEW_HTTP_READY_TIMEOUT_SECONDS=75
AUTODOCKER_PREVIEW_RUNNER_TOKEN=replace-with-runner-shared-token
```

Notas operativas:

- el runner expone un solo servicio HTTP público por preview
- el runner prioriza servicios `web`, `app`, `frontend` y `site`
- con `AUTODOCKER_PREVIEW_CADDY_ENABLED=true` publica y remueve subdominios reales a través de Caddy
- las previews vencidas se pueden reconciliar con:

```bash
python manage.py reconcile_preview_runner_sessions
```

- en producción conviene ejecutar ese comando desde `cron` o `systemd timer`

### Variables de la app principal

Estas variables aplican a la app web que pide previews al runner:

```env
AUTODOCKER_PREVIEW_BACKEND=remote_runner
AUTODOCKER_PREVIEW_RUNNER_BASE_URL=http://preview-runner.internal:9000
AUTODOCKER_PREVIEW_RUNNER_TOKEN=replace-with-runner-shared-token
AUTODOCKER_ENABLE_RUNTIME_JOBS=false
AUTODOCKER_PREVIEW_URL_STRATEGY=runner_managed
AUTODOCKER_PREVIEW_PUBLIC_BASE_DOMAIN=previews.example.com
```

### Seguridad mínima del runner

- no expongas el puerto del runner públicamente
- abrí solo `22`, `80` y `443` en la VM
- mantené el runner escuchando en `127.0.0.1:9000` o detrás de red privada
- usá un bearer token largo y rotado

### Deploy Oracle listo para usar

Hay artefactos de despliegue listos en:

- `deploy/oracle/preview-runner/Caddyfile`
- `deploy/oracle/preview-runner/preview-runner.env.example`
- `deploy/oracle/preview-runner/systemd/preview-runner.service`
- `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.service`
- `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.timer`

Guía operativa paso a paso:

- `docs/oracle-preview-runner.md`

## Estructura del proyecto

```text
autodocker/
├── config/                     # settings, urls, wsgi, celery
├── core/
│   ├── api/                    # endpoints DRF y serializers
│   ├── services/               # detección, generación, validación, preview, GitHub, workspaces
│   ├── static/core/            # JS y assets del dashboard
│   ├── templates/core/         # templates server-rendered
│   ├── forms.py
│   ├── models.py
│   ├── tests.py
│   └── views.py
├── docker/                     # scripts y soporte de runtime
├── docs/                       # documentación operativa y ejemplos
├── scripts/                    # utilidades auxiliares
├── manage.py
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── render.yaml
└── requirements.txt
```

## Variables de entorno

El proyecto trae varias plantillas:

- `.env.example` para desarrollo simple con `manage.py`
- `.env.docker.example` para `docker compose`
- `.env.prod.example` para producción
- `.env.runner.example` para el host dedicado del preview runner

### Variables mínimas para desarrollo local

| Variable | Descripción |
| --- | --- |
| `DJANGO_SECRET_KEY` | clave secreta de Django |
| `DJANGO_DEBUG` | activa modo debug |
| `DJANGO_USE_SQLITE` | permite usar SQLite en desarrollo |
| `DJANGO_ALLOWED_HOSTS` | hosts permitidos |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | orígenes confiables para CSRF |
| `AUTODOCKER_DEPLOYMENT_ROLE` | `app` o `preview_runner` |
| `AUTODOCKER_ASYNC_MODE` | `inline`, `thread` o `celery` |
| `AUTODOCKER_ENABLE_RUNTIME_JOBS` | habilita validación/preview que ejecutan runtime |
| `AUTODOCKER_TOKEN_ENCRYPTION_KEY` | cifra tokens externos almacenados |

### Variables para producción

| Variable | Descripción |
| --- | --- |
| `DATABASE_URL` | conexión a PostgreSQL |
| `CELERY_BROKER_URL` | broker de Celery |
| `CELERY_RESULT_BACKEND` | backend de resultados de Celery |
| `DJANGO_EMAIL_BACKEND` | backend de correo |
| `DJANGO_DEFAULT_FROM_EMAIL` | remitente por defecto |
| `DJANGO_SECURE_SSL_REDIRECT` | fuerza HTTPS |
| `DJANGO_CSRF_COOKIE_SECURE` | cookie CSRF segura |
| `DJANGO_SESSION_COOKIE_SECURE` | cookie de sesión segura |
| `DJANGO_SECURE_HSTS_SECONDS` | HSTS |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | HSTS subdominios |
| `DJANGO_SECURE_HSTS_PRELOAD` | HSTS preload |
| `AUTODOCKER_VALIDATION_BACKEND` | `local` o `github_actions` |
| `AUTODOCKER_PREVIEW_BACKEND` | `local` o `remote_runner` |
| `AUTODOCKER_PREVIEW_RUNNER_BASE_URL` | base URL privada del preview runner |
| `AUTODOCKER_PREVIEW_RUNNER_TOKEN` | bearer token compartido entre app y runner |
| `AUTODOCKER_PREVIEW_TTL_SECONDS` | TTL máximo de previews remotas |

### Variables para storage en Supabase

| Variable | Descripción |
| --- | --- |
| `SUPABASE_STORAGE_BUCKET` | bucket privado para uploads y bundles |
| `SUPABASE_STORAGE_S3_ENDPOINT_URL` | endpoint S3 de Supabase |
| `SUPABASE_STORAGE_S3_REGION` | región S3 |
| `SUPABASE_STORAGE_ACCESS_KEY_ID` | access key S3 |
| `SUPABASE_STORAGE_SECRET_ACCESS_KEY` | secret key S3 |
| `SUPABASE_STORAGE_MEDIA_PATH_PREFIX` | prefijo opcional dentro del bucket |

### Modo preview runner

Para correr el host dedicado del runner con este mismo código:

1. Copiá `.env.runner.example` a `.env.runner`.
2. Configurá `AUTODOCKER_DEPLOYMENT_ROLE=preview_runner`.
3. Asegurá acceso real a Docker en ese host.
4. Levantá Django con ese archivo de entorno; el servicio expone la API privada `POST /previews`, `GET /previews/{id}`, `GET /previews/{id}/logs` y `POST /previews/{id}/stop`.

En ese host el backend de preview debe quedar en `local`; el backend `remote_runner` se usa del lado de la app principal para delegar previews al runner dedicado.

### Variables para validación remota con GitHub Actions

| Variable | Descripción |
| --- | --- |
| `AUTODOCKER_VALIDATION_BACKEND` | `local` o `github_actions` |
| `AUTODOCKER_VALIDATION_EXECUTOR_REPO` | repo privado executor, por ejemplo `owner/autodocker-validator` |
| `AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW` | workflow del executor, por ejemplo `validate.yml` |
| `AUTODOCKER_VALIDATION_EXECUTOR_TOKEN` | token del sistema con permisos sobre el repo executor |
| `AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS` | retención del bundle |
| `AUTODOCKER_VALIDATION_MAX_BUNDLE_MB` | tamaño máximo del bundle |

## Validación remota

En producción, AutoDocker puede ejecutar la validación real fuera del web process usando GitHub Actions:

1. materializa la fuente del análisis
2. superpone los artefactos editados
3. arma un bundle reproducible
4. lo sube a storage privado
5. dispara un workflow en un repo executor privado
6. consume el resultado y los logs desde los artifacts del workflow

El workflow de referencia está en [`docs/github-actions/validate.yml.example`](./docs/github-actions/validate.yml.example).

## Cómo contribuir

1. Hacé fork del repositorio.
2. Creá una rama descriptiva.

```bash
git checkout -b feature/mi-cambio
```

3. Implementá el cambio.
4. Corré la suite de tests.

```bash
python manage.py test
```

5. Si corresponde, actualizá documentación y ejemplos.
6. Abrí un pull request con contexto claro:
   - problema
   - solución
   - riesgos
   - forma de probar

### Recomendaciones para contribuciones

- mantené los cambios de dominio dentro de `core/services/`
- agregá tests cuando cambies comportamiento
- evitá mezclar refactors no relacionados con fixes funcionales
- documentá nuevas variables de entorno en `.env.prod.example` y en este README

## Licencia

Actualmente el repositorio no incluye un archivo `LICENSE`, así que no hay una licencia pública definida todavía. Si vas a distribuir el proyecto o aceptar contribuciones externas de forma sostenida, conviene agregar una licencia explícita y actualizar este README.
