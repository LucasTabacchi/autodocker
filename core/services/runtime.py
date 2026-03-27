from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


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
