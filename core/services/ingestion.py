from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from core.models import ProjectAnalysis
from core.services.contracts import GeneratedArtifactSpec


class SourceMaterializationError(RuntimeError):
    """Error controlado al descargar o extraer una fuente."""


@contextmanager
def materialize_analysis_source(analysis: ProjectAnalysis):
    temp_dir, source_root = prepare_source_workspace(analysis)
    try:
        yield source_root
    finally:
        cleanup_workspace(temp_dir)


def prepare_source_workspace(
    analysis: ProjectAnalysis,
    prefix: str = "autodocker-",
) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    source_root = _prepare_source_root(analysis, temp_dir)
    return temp_dir, source_root


def cleanup_workspace(temp_dir: Path) -> None:
    shutil.rmtree(temp_dir, ignore_errors=True)


def overlay_generated_artifacts(
    source_root: Path,
    artifacts: list[GeneratedArtifactSpec] | list,
) -> None:
    for artifact in artifacts:
        target = source_root / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")


def detect_git_commit(source_root: Path) -> str:
    git_dir = source_root / ".git"
    if not git_dir.exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _prepare_source_root(analysis: ProjectAnalysis, temp_dir: Path) -> Path:
    if analysis.source_type == ProjectAnalysis.SourceType.ZIP:
        if not analysis.archive:
            raise SourceMaterializationError("No se encontró el archivo ZIP cargado.")
        extract_root = temp_dir / "source"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(analysis.archive.path) as zipped:
            _safe_extract(zipped, extract_root)
        return _normalize_root(extract_root)

    if not analysis.repository_url:
        raise SourceMaterializationError("No se recibió una URL Git válida.")

    destination = temp_dir / "repo"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", analysis.repository_url, str(destination)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SourceMaterializationError(
            "Git no está disponible en el entorno de ejecución."
        ) from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "No se pudo clonar el repositorio."
        raise SourceMaterializationError(message) from exc
    return destination


def _safe_extract(zipped: zipfile.ZipFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in zipped.infolist():
        target = (destination / member.filename).resolve()
        if not str(target).startswith(str(destination_resolved)):
            raise SourceMaterializationError("El ZIP contiene rutas inválidas.")
    zipped.extractall(destination)


def _normalize_root(extract_root: Path) -> Path:
    children = [path for path in extract_root.iterdir() if path.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_root
