# AutoDocker

## 1. Resumen de la idea
AutoDocker es un SaaS para developers que recibe un `.zip` o una URL Git, detecta el stack técnico del proyecto y genera artefactos Docker editables antes de exportarlos. El foco del MVP es acelerar la dockerización sin obligar al usuario a conocer todos los matices de cada stack.

## 2. Arquitectura general de la app
- Monolito Django 5 con DRF para la API y templates server-rendered para la UI.
- Capa de dominio separada en servicios: ingestión, detección, generación, validación y scheduling.
- Persistencia de historial y artefactos generados en base de datos.
- Exportación ZIP desde backend con los archivos ya editados por el usuario.
- Procesamiento asíncrono por worker con Celery o fallback thread en desarrollo local.

## 3. Tecnologías recomendadas
- Backend y frontend inicial: Django 5 + DRF + templates + JS.
- Editor embebido: Monaco cargado por CDN.
- Base de datos: SQLite para tests y local mínimo, PostgreSQL para Docker y producción.
- Jobs asíncronos: Celery + Redis.
- Static files y runtime web: WhiteNoise + Gunicorn.

## 4. Estructura de carpetas del proyecto
```text
autodocker/
├── config/
├── core/
│   ├── api/
│   ├── services/
│   ├── static/core/
│   ├── templates/core/
│   ├── forms.py
│   ├── models.py
│   └── views.py
├── manage.py
└── requirements.txt
```

## 5. Flujo completo del usuario
1. El usuario inicia sesión con auth de Django.
2. Sube un `.zip` o pega una URL Git.
3. La API crea un análisis en estado `queued`.
4. Un worker procesa la fuente, detecta framework, lenguaje, puertos y servicios auxiliares.
5. El generador crea Dockerfile, `.dockerignore`, `docker-compose.yml` y guía.
6. La UI hace polling, muestra el resumen y habilita Monaco cuando los artefactos están listos.
7. El usuario guarda cambios, regenera o descarga un ZIP final.

## 6. Lógica de detección de stack
- Node.js: lectura de `package.json`, `scripts`, lockfiles y dependencias.
- Python: `requirements.txt`, `pyproject.toml`, `manage.py`.
- PHP: `composer.json`, `artisan`.
- Java: `pom.xml`.
- Go: `go.mod`.
- Ruby: `Gemfile`.
- Monorepos: `workspaces`, `apps/*`, `packages/*`, `services/*`, `pnpm-workspace.yaml`, `turbo.json`, `nx.json`.
- Variables de entorno: regex sobre código y `.env`.
- Puertos: regex sobre listeners/CLI más defaults por framework.
- Servicios auxiliares: heurísticas sobre dependencias y nombres de variables.

## 7. Lógica de generación de Dockerfile y docker-compose
- Next.js: multi-stage en Node.
- React/Vite: build stage y runtime sobre nginx.
- Express/Nest: Node runtime con instalación productiva.
- Django/FastAPI/Flask: Python slim con arranque detectado.
- Laravel, Java, Go, Rails: plantillas de producción iniciales.
- Compose se genera cuando hay varios componentes o servicios auxiliares.

## 8. Base de datos necesaria
- `ProjectAnalysis`: historial del análisis, owner, job id, estados de ejecución, resumen detectado, errores, recomendaciones y payload completo.
- `GeneratedArtifact`: archivos generados y editables asociados al análisis.

## 9. Endpoints API necesarios
- `GET /api/analyses/`
- `POST /api/analyses/`
- `GET /api/analyses/{id}/`
- `POST /api/analyses/{id}/regenerate/`
- `GET /api/analyses/{id}/download/`
- `PATCH /api/artifacts/{id}/`

## 10. Diseño de la interfaz
- Estética industrial/minimalista con tipografía técnica.
- Layout en dos columnas: workspace principal + historial.
- Resumen con métricas, recomendaciones, tabs de artefactos y editor Monaco.
- Pensado para developers: foco en lectura rápida, paths y comandos.

## 11. MVP inicial
- Login, historial por usuario y permisos básicos.
- Alta por `.zip` o Git.
- Detección heurística real para stacks principales y monorepos simples.
- Generación de Dockerfile, `.dockerignore`, compose y guía.
- Jobs en background.
- Edición en Monaco y descarga.

## 12. Funcionalidades futuras
- Multi-tenant real y workspaces por organización.
- Plantillas de despliegue para Railway, Render, ECS y Kubernetes.
- Validación avanzada de seguridad Docker.
- Diff inteligente contra Dockerfiles existentes.

## 13. Código inicial base del proyecto
- Modelos en `core/models.py`.
- Servicios de dominio en `core/services/`.
- API en `core/api/`.
- UI inicial en `core/templates/core/dashboard.html` y `core/static/core/`.

## 14. Ejemplo de implementación real de generación de Dockerfile
La implementación está en `core/services/generator.py`. Ahí se construyen variantes específicas para Next.js, React/Vite, Python, Laravel, Java, Go y Rails usando plantillas reales, no pseudocódigo.

## 15. Recomendaciones de seguridad y performance
- Validar zips contra path traversal.
- No ejecutar código del repositorio analizado.
- Limitar tamaño y profundidad del escaneo.
- Persistir solo metadata necesaria.
- Mantener análisis pesados en workers.
- En producción usar PostgreSQL, Redis, storage externo y rate limiting.
- Activar `DJANGO_SECURE_SSL_REDIRECT`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SECURE` y revisar HSTS en entorno real.

## Levantar la app
### Local con Python
```bash
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

### Local con Docker
```bash
cp .env.docker.example .env.docker
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

### Entornos incluidos
- `.env`: archivo local real para desarrollo simple con `manage.py`. Django lo carga automáticamente.
- `.env.example`: plantilla base para crear o reconstruir tu `.env` local.
- `.env.docker.example`: plantilla para crear `.env.docker` en desarrollo con `docker compose`.
- `.env.prod.example`: plantilla para crear `.env.prod` en producción o con `docker-compose.prod.yml`.
- `AUTODOCKER_ENABLE_RUNTIME_JOBS`: habilita preview/validación que construyen o ejecutan contenedores.
- `AUTODOCKER_TOKEN_ENCRYPTION_KEY`: clave separada para proteger tokens externos almacenados.
- `SUPABASE_STORAGE_*`: habilitan storage remoto privado para los ZIPs subidos cuando querés evitar `media/` local.
- `AUTODOCKER_VALIDATION_BACKEND`: elige `local` en desarrollo o `github_actions` para producción.
- `AUTODOCKER_VALIDATION_EXECUTOR_REPO`, `AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW`, `AUTODOCKER_VALIDATION_EXECUTOR_TOKEN`: configuran el executor privado de GitHub Actions.
- `AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS` y `AUTODOCKER_VALIDATION_MAX_BUNDLE_MB`: controlan retención y tamaño máximo del bundle de validación.

### Convención recomendada
- `manage.py` local: usá `.env`
- `docker compose`: copiá `.env.docker.example` a `.env.docker`
- producción / `docker-compose.prod.yml`: copiá `.env.prod.example` a `.env.prod`
- no uses `.env.prod` ni `.env.docker` como templates versionados; son archivos locales con secretos

| Si hacés esto | Archivo a usar |
| --- | --- |
| Correr Django local con `manage.py runserver` | `.env` |
| Reconstruir el entorno local base | `.env.example` |
| Levantar el stack con `docker compose` | `.env.docker` |
| Crear el `.env.docker` inicial | `.env.docker.example` |
| Configurar producción / Supabase / `docker-compose.prod.yml` | `.env.prod` |
| Crear el `.env.prod` inicial | `.env.prod.example` |

### Validación remota en producción
- En producción, AutoDocker valida por defecto con `AUTODOCKER_VALIDATION_BACKEND=github_actions`.
- El backend remoto crea un bundle reproducible, lo sube a storage privado y dispara un workflow en un repo executor privado.
- El executor no reutiliza el token de PRs; usa `AUTODOCKER_VALIDATION_EXECUTOR_TOKEN` solamente para dispatch, lectura de runs y descarga de artifacts.
- La implementación esperada del workflow está documentada en [`docs/github-actions/validate.yml.example`](./docs/github-actions/validate.yml.example).
- El workflow debe leer `job_id`, `analysis_id`, `bundle_url` y `bundle_sha256`, verificar el ZIP, ejecutar `docker compose build` o `docker build`, y publicar `result.json` más `validation.log`.

## Roadmap por fases
### Fase 1
- MVP funcional local.
- Docker propio.
- Auth y UI editable.

### Fase 2
- Jobs asíncronos.
- Mejoras de heurísticas.
- Integraciones Git.

### Fase 3
- Billing, workspaces, auditoría y despliegues integrados.
- Reglas por organización.
- Soporte enterprise.

### Setup de Render para validación remota
- Mantener `AUTODOCKER_ASYNC_MODE=thread` o migrar a Celery según el resto del runtime, pero el backend de validación debe apuntar a `github_actions`.
- En `render.yaml`, dejar `AUTODOCKER_VALIDATION_BACKEND=github_actions` y marcar `AUTODOCKER_VALIDATION_EXECUTOR_REPO` / `AUTODOCKER_VALIDATION_EXECUTOR_TOKEN` como `sync: false`.
- Crear el repo executor privado desde `docs/github-actions/validate.yml.example` y publicar un workflow llamado `validate.yml`.
- Cargar en Render los secretos del executor repo, el token del executor y la configuración de storage privada para bundles.
- `.env.prod.example` debe reflejar la misma configuración para bootstrap manual o despliegues alternativos.

## MVP desarrollable en pocos días
- Día 1: scaffold, modelos, carga de fuentes.
- Día 2: detector heurístico.
- Día 3: generador y exportación.
- Día 4: UI editable.
- Día 5: tests, hardening y deploy inicial.

## Versión premium/escalable del producto
- Procesamiento en background con colas y almacenamiento de snapshots.
- SSO, RBAC, auditoría y políticas de seguridad.
- Integraciones con GitHub/GitLab/Bitbucket.
- Sugerencias de despliegue multi-cloud y políticas de cumplimiento.

## Deploy en Render
### Blueprint incluido
- El repo ahora incluye [render.yaml](./render.yaml) para desplegar AutoDocker en Render con un único web service `free`, usando tu PostgreSQL externo y Supabase Storage para reemplazar `media/`.
- El deploy está configurado en `AUTODOCKER_ASYNC_MODE=thread` y `AUTODOCKER_ENABLE_RUNTIME_JOBS=false`.
- Esa decisión sigue siendo intencional: con la arquitectura actual, separar `web` y `worker` seguiría exigiendo storage compartido y coordinación adicional para jobs.
- Los ZIPs de análisis ya no necesitan disk persistente en Render si configurás el bucket privado de Supabase Storage.
- `PYTHON_VERSION` queda fijada en `3.13.2` para no depender del default actual de Render.

### Qué queda habilitado y qué no
- Funciona: auth, dashboard, análisis por ZIP/Git, generación de artefactos, edición, workspaces, invitaciones y descarga.
- Queda deshabilitado en Render: preview ejecutable y validación Docker host-based.
- Si más adelante querés `worker` real en Render, el paso correcto es mover los uploads a storage compartido externo antes de separar procesos.

### Cómo desplegar
1. Commit y push de `render.yaml` a `main`.
2. En Render, elegir `New +` -> `Blueprint`.
3. Seleccionar el repo `LucasTabacchi/autodocker`.
4. En Supabase, crear un bucket privado para media, por ejemplo `autodocker-media`.
5. En Supabase, generar credenciales S3 para Storage y copiar:
   - endpoint S3
   - region
   - access key id
   - secret access key
6. En el formulario de variables de Render, pegar:
   - `DATABASE_URL`
   - `SUPABASE_STORAGE_BUCKET`
   - `SUPABASE_STORAGE_S3_ENDPOINT_URL`
   - `SUPABASE_STORAGE_S3_REGION`
   - `SUPABASE_STORAGE_ACCESS_KEY_ID`
   - `SUPABASE_STORAGE_SECRET_ACCESS_KEY`
7. Confirmar el Blueprint y esperar el primer deploy.
8. En plan `free`, las migraciones corren durante `buildCommand` porque Render no soporta `preDeployCommand` en ese tier.

### Notas de costo y límites
- El plan `free` evita el requisito de tarjeta en el deploy del web service.
- Supabase Storage free tiene límites; sirve bien para demo y testing, pero no para cargas grandes o muchos ZIPs.
- Si querés pasar luego a `web + worker`, primero tenés que sacar los uploads de disco local y moverlos a storage compartido externo.

## Ruta recomendada para runtime real
Si querés habilitar validación real y más adelante previews públicos sin salir del esquema `Render web + Supabase`, la evolución recomendada es separar el runtime pesado en un worker externo.

### Arquitectura objetivo
- Render: web/UI/API pública.
- Supabase: PostgreSQL + Storage privado para los ZIPs.
- Redis externo: broker/result backend para Celery.
- Worker externo con Docker: ejecuta validaciones reales y, después, previews.
- Tunnel/proxy público: solo necesario para la fase de previews.

### Fase 1: validación real
- Mover el web desde `AUTODOCKER_ASYNC_MODE=thread` a `AUTODOCKER_ASYNC_MODE=celery`.
- Conectar web y worker al mismo `CELERY_BROKER_URL` y `CELERY_RESULT_BACKEND`.
- Correr un `celery worker` dedicado fuera de Render, en una máquina con Docker disponible.
- Mantener los previews deshabilitados mientras se habilita solo la validación real.
- Resultado esperado: el botón de validate deja de depender del host local o del web process.

### Fase 2: previews públicos
- Mantener el worker externo con Docker como executor de previews.
- Agregar un proxy local delante de los contenedores preview.
- Publicarlo con un tunnel/proxy público, por ejemplo Cloudflare Tunnel.
- Adaptar `PreviewService` para devolver URLs públicas reales en lugar de `127.0.0.1:<puerto>`.
- Agregar cleanup de contenedores, rutas y expiración de previews.

### Recomendación operativa
- No mezclar validación real y preview público en el mismo cambio.
- Resolver primero Redis + worker + validación.
- Después agregar el split de flags para validación y preview.
- Recién al final sumar proxy/tunnel y URLs públicas para previews.
