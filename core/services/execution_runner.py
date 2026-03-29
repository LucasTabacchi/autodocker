from __future__ import annotations

from django.utils import timezone

from core.models import ExecutionJob, ExternalRepoConnection
from core.services.build_validation import BuildValidationService
from core.services.github_pr import GitHubPullRequestService
from core.services.preview import PreviewService


class ExecutionJobRunner:
    def __init__(self) -> None:
        self.validation_service = BuildValidationService()
        self.github_service = GitHubPullRequestService()
        self.preview_service = PreviewService()

    def run(self, job: ExecutionJob) -> ExecutionJob:
        job.status = ExecutionJob.Status.RUNNING
        job.started_at = timezone.now()
        job.finished_at = None
        job.logs = ""
        job.save(update_fields=["status", "started_at", "finished_at", "logs", "updated_at"])

        try:
            if job.kind == ExecutionJob.Kind.VALIDATION:
                result = self.validation_service.validate(job)
                result_dict = result.to_dict()
                job.metadata = {
                    **(job.metadata or {}),
                    **result_dict.pop("metadata", {}),
                }
                job.result_payload = {
                    **(job.result_payload or {}),
                    **result_dict.pop("result_payload", {}),
                    **result_dict,
                }
                job.logs = result.logs
                job.status = (
                    ExecutionJob.Status.READY
                    if result.success
                    else ExecutionJob.Status.FAILED
                )
            elif job.kind == ExecutionJob.Kind.GITHUB_PR:
                result = self.github_service.create_pull_request(
                    analysis=job.analysis,
                    connection=self._resolve_connection(job),
                    access_token=self._resolve_access_token(job),
                    base_branch=job.metadata.get("base_branch", "main"),
                    title=job.metadata.get("title") or f"Dockerize {job.analysis.project_name}",
                    body=job.metadata.get("body")
                    or "Auto-generated Docker configuration from AutoDocker.",
                )
                job.result_payload = result.to_dict()
                job.logs = result.logs
                job.status = ExecutionJob.Status.READY
            elif job.kind == ExecutionJob.Kind.PREVIEW:
                preview_run = job.preview_run
                self.preview_service.start(preview_run)
                preview_run.refresh_from_db()
                job.result_payload = {
                    "preview_id": str(preview_run.id),
                    "status": preview_run.status,
                    "access_url": preview_run.access_url,
                    "ports": preview_run.ports,
                    "runtime_kind": preview_run.runtime_kind,
                }
                job.logs = preview_run.logs
                job.status = (
                    ExecutionJob.Status.READY
                    if preview_run.status in {
                        preview_run.Status.QUEUED,
                        preview_run.Status.RUNNING,
                        preview_run.Status.READY,
                    }
                    else ExecutionJob.Status.FAILED
                )
            else:  # pragma: no cover
                raise RuntimeError(f"Tipo de job no soportado: {job.kind}")
        except Exception as exc:  # pragma: no cover
            job.status = ExecutionJob.Status.FAILED
            job.logs = f"{job.logs}\n\n{exc}".strip()
            job.result_payload = {**job.result_payload, "error": str(exc)}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "logs", "metadata", "result_payload", "finished_at", "updated_at"])
        return job

    def _resolve_connection(self, job: ExecutionJob):
        connection_id = job.metadata.get("connection_id")
        if not connection_id:
            return None
        return ExternalRepoConnection.objects.for_user(job.owner).get(pk=connection_id)

    def _resolve_access_token(self, job: ExecutionJob) -> str:
        access_token = job.metadata.get("access_token", "").strip()
        if access_token:
            return access_token
        connection = self._resolve_connection(job)
        if connection:
            return connection.get_access_token()
        raise RuntimeError("Se requiere un token de GitHub o una conexión guardada para abrir el PR.")
