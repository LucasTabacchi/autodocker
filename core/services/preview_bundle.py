from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from core.models import ProjectAnalysis
from core.services.ingestion import (
    cleanup_workspace,
    overlay_generated_artifacts,
    prepare_source_workspace,
)


class PreviewBundleError(RuntimeError):
    """Error controlado al crear el bundle de preview."""


@dataclass(slots=True)
class PreviewBundle:
    workspace_root: Path
    bundle_path: Path
    sha256: str
    bundle_size_bytes: int


class PreviewBundleService:
    def build(self, analysis: ProjectAnalysis) -> PreviewBundle:
        workspace_root, source_root = prepare_source_workspace(analysis, prefix="autodocker-preview-bundle-")
        try:
            overlay_generated_artifacts(source_root, list(analysis.artifacts.all()))
            bundle_path = workspace_root / "preview-bundle.zip"
            self._write_bundle(source_root, bundle_path)
            bundle_size_bytes = bundle_path.stat().st_size
            self._enforce_size_limit(bundle_size_bytes)
            return PreviewBundle(
                workspace_root=workspace_root,
                bundle_path=bundle_path,
                sha256=self._sha256(bundle_path),
                bundle_size_bytes=bundle_size_bytes,
            )
        except Exception:
            cleanup_workspace(workspace_root)
            raise

    def _write_bundle(self, source_root: Path, bundle_path: Path) -> None:
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(source_root.rglob("*")):
                if not path.is_file():
                    continue
                if ".git" in path.relative_to(source_root).parts:
                    continue
                archive_name = path.relative_to(source_root).as_posix()
                info = zipfile.ZipInfo(archive_name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, path.read_bytes())

    def _sha256(self, bundle_path: Path) -> str:
        digest = hashlib.sha256()
        with bundle_path.open("rb") as bundle_file:
            for chunk in iter(lambda: bundle_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _enforce_size_limit(self, bundle_size_bytes: int) -> None:
        max_bytes = settings.AUTODOCKER_PREVIEW_MAX_BUNDLE_MB * 1024 * 1024
        if bundle_size_bytes > max_bytes:
            raise PreviewBundleError(
                f"El bundle de preview excede el límite de {settings.AUTODOCKER_PREVIEW_MAX_BUNDLE_MB} MB."
            )
