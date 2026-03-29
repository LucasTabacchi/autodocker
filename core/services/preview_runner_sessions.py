from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib import error, request

from django.conf import settings
from django.utils import timezone

from core.models import PreviewRunnerSession
from core.services.ingestion import cleanup_workspace
from core.services.preview import PreviewService


class PreviewRunnerSessionError(RuntimeError):
    """Raised when a runner preview session cannot be created or updated."""


class PreviewRunnerSessionService:
    def start(self, session: PreviewRunnerSession) -> PreviewRunnerSession:
        workspace_root = Path(tempfile.mkdtemp(prefix="autodocker-runner-"))
        source_root = workspace_root / "source"
        source_root.mkdir(parents=True, exist_ok=True)

        session.status = PreviewRunnerSession.Status.STARTING
        session.started_at = timezone.now()
        session.finished_at = None
        session.workspace_root = str(workspace_root)
        session.workspace_path = str(source_root)
        session.expires_at = timezone.now() + timedelta(seconds=self._ttl_seconds(session))
        session.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "workspace_root",
                "workspace_path",
                "expires_at",
                "updated_at",
            ]
        )

        try:
            bundle_bytes = self._download_bundle(session.bundle_url)
            self._verify_sha256(bundle_bytes, session.bundle_sha256)
            self._extract_bundle(bundle_bytes, source_root)
            analysis_like = SimpleNamespace(
                analysis_payload={
                    "components": list(session.metadata.get("components", [])),
                },
                services=list(session.metadata.get("services", [])),
            )
            PreviewService().start_from_workspace(session, analysis_like, source_root)
            session.expires_at = timezone.now() + timedelta(seconds=self._ttl_seconds(session))
            session.save(update_fields=["expires_at", "updated_at"])
            return session
        except Exception as exc:
            session.status = PreviewRunnerSession.Status.FAILED
            session.logs = str(exc)
            session.finished_at = timezone.now()
            session.save(update_fields=["status", "logs", "finished_at", "updated_at"])
            self._cleanup_workspace(session)
            return session

    def refresh_logs(self, session: PreviewRunnerSession) -> PreviewRunnerSession:
        PreviewService().refresh_logs(session)
        return session

    def stop(self, session: PreviewRunnerSession) -> PreviewRunnerSession:
        PreviewService().stop(session)
        return session

    def expire(self, session: PreviewRunnerSession) -> PreviewRunnerSession:
        self.stop(session)
        session.status = PreviewRunnerSession.Status.EXPIRED
        session.finished_at = timezone.now()
        session.save(update_fields=["status", "finished_at", "updated_at"])
        return session

    def _download_bundle(self, bundle_url: str) -> bytes:
        req = request.Request(bundle_url, method="GET")
        try:
            with request.urlopen(req, timeout=settings.AUTODOCKER_PREVIEW_RUNNER_REQUEST_TIMEOUT) as response:
                return response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PreviewRunnerSessionError(detail or f"Bundle download devolvió HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise PreviewRunnerSessionError(str(exc)) from exc

    def _verify_sha256(self, bundle_bytes: bytes, expected_sha256: str) -> None:
        actual = hashlib.sha256(bundle_bytes).hexdigest()
        if actual != expected_sha256:
            raise PreviewRunnerSessionError("El bundle descargado no coincide con el SHA256 esperado.")

    def _extract_bundle(self, bundle_bytes: bytes, source_root: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as bundle:
            root_resolved = source_root.resolve()
            for member in bundle.infolist():
                destination = (source_root / member.filename).resolve()
                if destination != root_resolved and root_resolved not in destination.parents:
                    raise PreviewRunnerSessionError("El bundle contiene rutas inválidas.")
            bundle.extractall(source_root)

    def _ttl_seconds(self, session: PreviewRunnerSession) -> int:
        requested = max(int(session.requested_ttl_seconds or 0), 1)
        return min(requested, settings.AUTODOCKER_PREVIEW_TTL_SECONDS)

    def _cleanup_workspace(self, session: PreviewRunnerSession) -> None:
        if session.workspace_root:
            cleanup_workspace(Path(session.workspace_root))
