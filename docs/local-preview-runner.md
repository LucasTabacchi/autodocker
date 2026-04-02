# Local Preview Runner

## Objetivo

Levantar la app principal y el `preview-runner` en la misma máquina para validar el flujo remoto de previews sin depender de una VM externa.

## Archivos de entorno

1. Copiá:

```powershell
Copy-Item .env.local-app.example .env.local-app
Copy-Item .env.local-runner.example .env.local-runner
```

2. Usá el mismo token en ambos archivos:

- `AUTODOCKER_PREVIEW_RUNNER_TOKEN`

## Requisitos

- `.venv` creada
- Docker Desktop corriendo
- puerto `8000` libre
- puerto `9000` libre

## Arranque

### Terminal 1: app principal

```powershell
.\scripts\run-local-app.ps1
```

### Terminal 2: preview runner

```powershell
.\scripts\run-local-preview-runner.ps1
```

## Smoke test

1. Entrá a `http://127.0.0.1:8000`
2. Creá o elegí un análisis listo
3. Dispará una preview
4. Confirmá:
   - la app principal llama al runner en `http://127.0.0.1:9000`
   - el runner descarga el bundle usando `http://127.0.0.1:8000/media/...`
   - la preview pasa a `READY`

## Smoke test automático

Podés correr el flujo completo con un solo comando. El script:

- levanta la app principal
- levanta el preview-runner
- crea un usuario smoke
- crea un análisis listo desde un repo Git
- inyecta artefactos mínimos por defecto para que el flujo sea determinista
- dispara la preview
- valida la respuesta HTTP
- ejecuta `stop` y confirma cleanup

Ejemplo con el fixture público:

```powershell
.\scripts\run-local-preview-smoke.ps1 `
  -RepositoryUrl https://github.com/LucasTabacchi/autodocker-pr-fixture-monorepo
```

Modo opcional para probar el repo tal cual viene, sin inyectar artefactos:

```powershell
.\scripts\run-local-preview-smoke.ps1 `
  -RepositoryUrl https://github.com/LucasTabacchi/autodocker-pr-fixture-monorepo `
  -UseRepoArtifacts
```

## Notas

- en local, `AUTODOCKER_PREVIEW_CADDY_ENABLED=false`
- la URL publicada del servicio será la URL local detectada por Docker, no un subdominio público
- si querés simular Caddy después, activalo explícitamente y apuntá `AUTODOCKER_PREVIEW_CADDY_ROUTES_DIR` a un directorio temporal
