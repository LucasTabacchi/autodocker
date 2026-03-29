from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


class CommandExecutionError(RuntimeError):
    """Raised when a subprocess command fails."""


@dataclass(slots=True)
class CommandExecutionResult:
    command: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        chunks = [self.stdout.strip(), self.stderr.strip()]
        return "\n".join(chunk for chunk in chunks if chunk)


def run_command(
    command: list[str],
    cwd: Path,
    *,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> CommandExecutionResult:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(f"No se encontró el binario requerido: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandExecutionError(
            f"El comando excedió el timeout de {timeout}s: {' '.join(command)}"
        ) from exc

    result = CommandExecutionResult(
        command=command,
        cwd=str(cwd),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )

    if check and result.returncode != 0:
        raise CommandExecutionError(
            result.output or f"El comando falló con código {result.returncode}: {' '.join(command)}"
        )

    return result


def docker_command() -> list[str]:
    if shutil.which("docker"):
        return ["docker"]
    raise CommandExecutionError("No se encontró el binario requerido: docker")


def runtime_jobs_enabled() -> bool:
    return bool(getattr(settings, "AUTODOCKER_ENABLE_RUNTIME_JOBS", False))


def validation_backend_name() -> str:
    return (getattr(settings, "AUTODOCKER_VALIDATION_BACKEND", "local") or "local").strip().lower()


def validation_runtime_capability() -> dict[str, str | bool]:
    backend = validation_backend_name()
    if backend == "github_actions":
        return {
            "enabled": True,
            "backend": backend,
            "reason": "",
        }

    try:
        ensure_runtime_jobs_enabled("La validación de build")
        ensure_docker_runtime_access("La validación de build")
    except CommandExecutionError as exc:
        return {
            "enabled": False,
            "backend": backend,
            "reason": str(exc),
        }

    return {
        "enabled": True,
        "backend": backend,
        "reason": "",
    }


def preview_runtime_capability() -> dict[str, str | bool]:
    try:
        ensure_runtime_jobs_enabled("La preview ejecutable")
        ensure_docker_runtime_access("La preview ejecutable")
    except CommandExecutionError as exc:
        return {
            "enabled": False,
            "backend": "docker",
            "reason": str(exc),
        }

    return {
        "enabled": True,
        "backend": "docker",
        "reason": "",
    }


def ensure_runtime_jobs_enabled(action_label: str) -> None:
    if runtime_jobs_enabled():
        return
    raise CommandExecutionError(
        (
            f"{action_label} está deshabilitado en este entorno. "
            "Activá AUTODOCKER_ENABLE_RUNTIME_JOBS=true solo donde realmente quieras "
            "permitir jobs que construyen o ejecutan contenedores."
        )
    )


def ensure_docker_runtime_access(action_label: str) -> None:
    docker_command()
    if os.environ.get("DOCKER_HOST"):
        return
    if os.name == "nt":
        return
    if Path("/var/run/docker.sock").exists():
        return
    raise CommandExecutionError(
        (
            f"{action_label} requiere acceso al Docker host. "
            "Montá /var/run/docker.sock o configurá DOCKER_HOST antes de habilitar estos jobs."
        )
    )


def docker_compose_command() -> list[str]:
    if shutil.which("docker"):
        try:
            completed = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandExecutionError("El comando docker compose excedió el timeout de 20s.") from exc
        if completed.returncode == 0:
            return ["docker", "compose"]

    if shutil.which("docker-compose"):
        return ["docker-compose"]

    if shutil.which("docker"):
        raise CommandExecutionError("Docker está disponible, pero no se encontró soporte para docker compose.")
    raise CommandExecutionError("No se encontró el binario requerido: docker")
