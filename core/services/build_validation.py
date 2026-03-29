from __future__ import annotations

from dataclasses import asdict, dataclass, field

from django.core.files.base import File
from django.core.files.storage import default_storage
from django.conf import settings

from core.models import ExecutionJob, ProjectAnalysis
from core.services.github_actions import GitHubActionsClient
from core.services.ingestion import (
    cleanup_workspace,
    overlay_generated_artifacts,
    prepare_source_workspace,
)
from core.services.validation_bundle import ValidationBundleService
from core.services.runtime import (
    CommandExecutionError,
    docker_command,
    docker_compose_command,
    ensure_docker_runtime_access,
    ensure_runtime_jobs_enabled,
    run_command,
)


@dataclass(slots=True)
class BuildValidationResult:
    success: bool
    command: list[str]
    logs: str
    image_tag: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    result_payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["metadata"] = data.get("metadata") or {}
        data["result_payload"] = data.get("result_payload") or {}
        return data


class BuildValidationService:
    @property
    def backend_name(self) -> str:
        return (getattr(settings, "AUTODOCKER_VALIDATION_BACKEND", "local") or "local").strip()

    def validate(self, job: ExecutionJob) -> BuildValidationResult:
        if self.backend_name == "github_actions":
            return RemoteValidationService().validate(job)
        if not job.analysis:
            raise ValueError("El job de validación no tiene análisis asociado.")
        return self._validate_local(job.analysis)

    def _validate_local(self, analysis: ProjectAnalysis) -> BuildValidationResult:
        ensure_runtime_jobs_enabled("La validación de build")
        ensure_docker_runtime_access("La validación de build")
        temp_dir, source_root = prepare_source_workspace(analysis, prefix="autodocker-validate-")
        try:
            overlay_generated_artifacts(source_root, list(analysis.artifacts.all()))
            compose_path = source_root / "docker-compose.yml"

            if compose_path.exists():
                compose_base = docker_compose_command()
                config_command = [*compose_base, "-f", "docker-compose.yml", "config"]
                config_result = run_command(config_command, source_root, timeout=180)
                build_command = [*compose_base, "-f", "docker-compose.yml", "build"]
                build_result = run_command(build_command, source_root, timeout=1800)
                logs = "\n\n".join(
                    [
                        f"$ {' '.join(config_command)}",
                        config_result.output,
                        f"$ {' '.join(build_command)}",
                        build_result.output,
                    ]
                ).strip()
                return BuildValidationResult(
                    success=True,
                    command=build_command,
                    logs=logs,
                )

            image_tag = f"autodocker-validation-{str(analysis.id)[:8]}"
            build_command = [*docker_command(), "build", "-t", image_tag, "."]
            result = run_command(build_command, source_root, timeout=1800)
            return BuildValidationResult(
                success=True,
                command=build_command,
                logs=f"$ {' '.join(build_command)}\n{result.output}".strip(),
                image_tag=image_tag,
            )
        except CommandExecutionError as exc:
            return BuildValidationResult(
                success=False,
                command=[],
                logs=str(exc),
            )
        finally:
            cleanup_workspace(temp_dir)


class RemoteValidationService:
    def validate(self, job: ExecutionJob) -> BuildValidationResult:
        if not job.analysis:
            raise ValueError("El job de validación no tiene análisis asociado.")

        bundle = ValidationBundleService().build(job.analysis)
        try:
            bundle_key = self._bundle_storage_key(job)
            with bundle.bundle_path.open("rb") as bundle_stream:
                saved_key = default_storage.save(bundle_key, File(bundle_stream, name=bundle.bundle_path.name))
            bundle_url = default_storage.url(saved_key)
            client = GitHubActionsClient()
            dispatch = client.dispatch_validation(
                job_id=str(job.id),
                bundle_url=bundle_url,
                bundle_sha256=bundle.sha256,
                analysis_id=str(job.analysis_id),
            )
            completion = client.wait_for_completion(dispatch["workflow_run_id"])
            return BuildValidationResult(
                success=completion["success"],
                command=completion["command"],
                logs=completion["logs"],
                image_tag="",
                metadata={
                    "validation_backend": "github_actions",
                    "workflow_run_id": dispatch["workflow_run_id"],
                    "workflow_run_url": dispatch["workflow_run_url"],
                    "bundle_sha256": bundle.sha256,
                },
                result_payload={
                    "executor": "github_actions",
                    "summary": completion["summary"],
                    "artifact_urls": {
                        "workflow_run": dispatch["workflow_run_url"],
                    },
                    "duration_seconds": completion["duration_seconds"],
                },
            )
        finally:
            cleanup_workspace(bundle.workspace_root)

    def _bundle_storage_key(self, job: ExecutionJob) -> str:
        return f"validation-bundles/{str(job.id)}/bundle.zip"
