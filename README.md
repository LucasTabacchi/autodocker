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
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

### Entornos incluidos
- `.env`: desarrollo local con SQLite y jobs por thread.
- `.env.docker`: desarrollo dockerizado con PostgreSQL + Redis + Celery.
- `.env.production.example`: base para producción.

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
