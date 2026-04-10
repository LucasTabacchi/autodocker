from __future__ import annotations

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from urllib.parse import urljoin, urlparse

from core.models import PreviewRun
from core.services.ingestion import cleanup_workspace
from core.services.preview_bundle import PreviewBundleService
from core.services.preview_runner import PreviewRunnerClient, PreviewRunnerError


class RemotePreviewService:
    RUNNER_TO_MODEL_STATUS = {
        "queued": PreviewRun.Status.QUEUED,
        "starting": PreviewRun.Status.RUNNING,
        "ready": PreviewRun.Status.READY,
        "failed": PreviewRun.Status.FAILED,
        "stopped": PreviewRun.Status.STOPPED,
        "expired": PreviewRun.Status.STOPPED,
    }

    def start(self, preview_run: PreviewRun) -> PreviewRun:
        preview_run.status = PreviewRun.Status.RUNNING
        preview_run.started_at = timezone.now()
        preview_run.finished_at = None
        preview_run.command = "remote_runner:create_preview"
        preview_run.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "command",
                "updated_at",
            ]
        )

        bundle = PreviewBundleService().build(preview_run.analysis)
        try:
            bundle_key = self._bundle_storage_key(preview_run)
            with bundle.bundle_path.open("rb") as bundle_stream:
                saved_key = default_storage.save(bundle_key, File(bundle_stream, name=bundle.bundle_path.name))
            bundle_url = self._absolute_bundle_url(default_storage.url(saved_key))
            payload = PreviewRunnerClient().create_preview(
                preview_id=str(preview_run.id),
                analysis_id=str(preview_run.analysis_id),
                project_name=preview_run.analysis.project_name,
                bundle_url=bundle_url,
                bundle_sha256=bundle.sha256,
                requested_ttl_seconds=settings.AUTODOCKER_PREVIEW_TTL_SECONDS,
                metadata={
                    "generation_profile": preview_run.analysis.generation_profile,
                    "components": preview_run.analysis.analysis_payload.get("components", []),
                    "services": preview_run.analysis.services,
                },
            )
            self._apply_runner_payload(preview_run, payload)
            return preview_run
        except Exception as exc:
            preview_run.status = PreviewRun.Status.FAILED
            preview_run.logs = str(exc)
            preview_run.finished_at = timezone.now()
            preview_run.save(update_fields=["status", "logs", "finished_at", "updated_at"])
            return preview_run
        finally:
            cleanup_workspace(bundle.workspace_root)

    def refresh_logs(self, preview_run: PreviewRun) -> PreviewRun:
        client = PreviewRunnerClient()
        logs_payload = client.get_logs(str(preview_run.id))
        payload = client.get_preview(str(preview_run.id))
        self._apply_runner_payload(preview_run, payload, logs=logs_payload.get("logs", preview_run.logs))
        return preview_run

    def stop(self, preview_run: PreviewRun) -> PreviewRun:
        try:
            payload = PreviewRunnerClient().stop_preview(str(preview_run.id))
            self._apply_runner_payload(preview_run, payload, logs=preview_run.logs)
        except PreviewRunnerError as exc:
            detail = str(exc)
            if "No PreviewRunnerSession matches the given query." not in detail:
                raise
            preview_run.status = PreviewRun.Status.STOPPED
            preview_run.finished_at = timezone.now()
            preview_run.logs = "\n".join(
                part
                for part in [preview_run.logs.strip(), "La runner session ya no existía; se marcó la preview como detenida localmente."]
                if part
            )
            preview_run.save(update_fields=["status", "finished_at", "logs", "updated_at"])
            return preview_run
        if preview_run.status != PreviewRun.Status.FAILED:
            preview_run.status = PreviewRun.Status.STOPPED
            if not preview_run.finished_at:
                preview_run.finished_at = timezone.now()
            preview_run.save(update_fields=["status", "finished_at", "updated_at"])
        return preview_run

    def _apply_runner_payload(
        self,
        preview_run: PreviewRun,
        payload: dict[str, object],
        *,
        logs: str | None = None,
    ) -> None:
        runner_status = str(payload.get("status") or "").strip().lower()
        if runner_status:
            preview_run.status = self.RUNNER_TO_MODEL_STATUS.get(runner_status, PreviewRun.Status.FAILED)

        runtime_kind = str(payload.get("runtime_kind") or "").strip().lower()
        if runtime_kind in {PreviewRun.RuntimeKind.COMPOSE, PreviewRun.RuntimeKind.CONTAINER}:
            preview_run.runtime_kind = runtime_kind

        if "access_url" in payload:
            preview_run.access_url = str(payload.get("access_url") or "")
        if "ports" in payload and isinstance(payload.get("ports"), dict):
            preview_run.ports = payload.get("ports") or {}
        if "resource_names" in payload and isinstance(payload.get("resource_names"), list):
            preview_run.resource_names = payload.get("resource_names") or []
        if logs is not None:
            preview_run.logs = logs

        expires_at = payload.get("expires_at")
        if isinstance(expires_at, str) and expires_at.strip():
            parsed_expires_at = parse_datetime(expires_at)
            if parsed_expires_at is not None:
                preview_run.expires_at = parsed_expires_at

        if preview_run.status in {PreviewRun.Status.READY, PreviewRun.Status.RUNNING, PreviewRun.Status.QUEUED}:
            preview_run.finished_at = None
        elif not preview_run.finished_at:
            preview_run.finished_at = timezone.now()

        preview_run.save(
            update_fields=[
                "status",
                "runtime_kind",
                "access_url",
                "ports",
                "resource_names",
                "logs",
                "expires_at",
                "finished_at",
                "updated_at",
            ]
        )

    def _bundle_storage_key(self, preview_run: PreviewRun) -> str:
        return f"preview-bundles/{str(preview_run.id)}/bundle.zip"

    def _absolute_bundle_url(self, bundle_url: str) -> str:
        parsed = urlparse(bundle_url)
        if parsed.scheme and parsed.netloc:
            return bundle_url
        return urljoin(f"{settings.AUTODOCKER_APP_BASE_URL.rstrip('/')}/", bundle_url.lstrip("/"))
