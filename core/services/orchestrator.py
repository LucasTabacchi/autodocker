from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from django.db import transaction

from core.models import ArtifactSnapshot, GeneratedArtifact, ProjectAnalysis
from core.services.cicd import CicdArtifactService
from core.services.contracts import DetectionResult
from core.services.deploy_targets import DeployTargetArtifactService
from core.services.detector import StackDetector
from core.services.generator import ArtifactGenerator
from core.services.healthchecks import HealthcheckPlannerService
from core.services.ingestion import detect_git_commit, materialize_analysis_source
from core.services.security_scan import SecurityScannerService
from core.services.validator import ArtifactValidator


class AnalysisOrchestrator:
    def __init__(self) -> None:
        self.detector = StackDetector()
        self.generator = ArtifactGenerator()
        self.validator = ArtifactValidator()
        self.healthchecks = HealthcheckPlannerService()
        self.security = SecurityScannerService()
        self.cicd = CicdArtifactService()
        self.deploy_targets = DeployTargetArtifactService()

    def run(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        analysis.status = ProjectAnalysis.Status.ANALYZING
        analysis.last_error = ""
        analysis.started_at = timezone.now()
        analysis.finished_at = None
        analysis.save(
            update_fields=["status", "last_error", "started_at", "finished_at", "updated_at"]
        )

        try:
            with materialize_analysis_source(analysis) as project_root:
                detection = self.detector.analyze(project_root)
                source_commit = detect_git_commit(project_root)
            healthcheck_report = self.healthchecks.plan(detection)
            cicd_artifacts, cicd_report = self.cicd.generate(detection, analysis.generation_profile)
            deploy_artifacts, deploy_report = self.deploy_targets.generate(
                detection,
                analysis.generation_profile,
            )
            generation = self.generator.generate(
                detection,
                profile=analysis.generation_profile,
                healthchecks={
                    item.component_path: item.to_dict()
                    for item in healthcheck_report.items
                },
                extra_artifacts=[*cicd_artifacts, *deploy_artifacts],
            )
            security_report = self.security.scan(detection, generation)
            warnings = self.validator.validate(detection, generation)
            detection.recommendations = sorted(
                dict.fromkeys(
                    [
                        *detection.recommendations,
                        *warnings,
                        *security_report.recommendations,
                        *healthcheck_report.recommendations,
                    ]
                )
            )
            self._persist(
                analysis,
                detection,
                generation,
                source_commit=source_commit,
                security_report=security_report.to_dict(),
                healthcheck_report=healthcheck_report.to_dict(),
                cicd_report=cicd_report.to_dict(),
                deploy_report=deploy_report.to_dict(),
            )
            return analysis
        except Exception as exc:  # pragma: no cover
            analysis.status = ProjectAnalysis.Status.FAILED
            analysis.last_error = str(exc)
            analysis.finished_at = timezone.now()
            analysis.save(
                update_fields=["status", "last_error", "finished_at", "updated_at"]
            )
            return analysis

    def regenerate(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        analysis.status = ProjectAnalysis.Status.ANALYZING
        analysis.last_error = ""
        analysis.started_at = timezone.now()
        analysis.finished_at = None
        analysis.save(
            update_fields=["status", "last_error", "started_at", "finished_at", "updated_at"]
        )
        detection = DetectionResult.from_dict(analysis.analysis_payload)
        healthcheck_report = self.healthchecks.plan(detection)
        cicd_artifacts, cicd_report = self.cicd.generate(detection, analysis.generation_profile)
        deploy_artifacts, deploy_report = self.deploy_targets.generate(
            detection,
            analysis.generation_profile,
        )
        generation = self.generator.generate(
            detection,
            profile=analysis.generation_profile,
            healthchecks={
                item.component_path: item.to_dict()
                for item in healthcheck_report.items
            },
            extra_artifacts=[*cicd_artifacts, *deploy_artifacts],
        )
        security_report = self.security.scan(detection, generation)
        warnings = self.validator.validate(detection, generation)
        detection.recommendations = sorted(
            dict.fromkeys(
                [
                    *detection.recommendations,
                    *warnings,
                    *security_report.recommendations,
                    *healthcheck_report.recommendations,
                ]
            )
        )
        self._persist(
            analysis,
            detection,
            generation,
            source_commit=analysis.source_commit,
            security_report=security_report.to_dict(),
            healthcheck_report=healthcheck_report.to_dict(),
            cicd_report=cicd_report.to_dict(),
            deploy_report=deploy_report.to_dict(),
        )
        return analysis

    @transaction.atomic
    def _persist(
        self,
        analysis: ProjectAnalysis,
        detection,
        generation,
        *,
        source_commit: str = "",
        security_report: dict | None = None,
        healthcheck_report: dict | None = None,
        cicd_report: dict | None = None,
        deploy_report: dict | None = None,
    ) -> None:
        primary_component = detection.primary_component()
        analysis.project_name = analysis.project_name or detection.project_name
        analysis.status = ProjectAnalysis.Status.READY
        analysis.detected_language = primary_component.language if primary_component else ""
        analysis.detected_framework = primary_component.framework if primary_component else ""
        analysis.confidence = Decimal(str(detection.confidence or 0))
        analysis.execution_root = primary_component.path if primary_component else "."
        analysis.package_manager = primary_component.package_manager or "" if primary_component else ""
        analysis.install_command = primary_component.install_command or "" if primary_component else ""
        analysis.build_command = primary_component.build_command or "" if primary_component else ""
        analysis.start_command = primary_component.start_command or "" if primary_component else ""
        analysis.probable_ports = primary_component.probable_ports if primary_component else []
        analysis.environment_variables = detection.environment_variables
        analysis.services = detection.shared_services
        analysis.found_files = detection.found_files
        analysis.recommendations = detection.recommendations
        analysis.analysis_payload = detection.to_dict()
        analysis.security_report = security_report or {}
        analysis.healthcheck_report = healthcheck_report or {}
        analysis.cicd_report = cicd_report or {}
        analysis.deploy_report = deploy_report or {}
        analysis.last_error = ""
        analysis.source_commit = source_commit
        analysis.finished_at = timezone.now()
        analysis.save()

        analysis.artifacts.all().delete()
        artifacts = [
            GeneratedArtifact(
                analysis=analysis,
                kind=artifact.kind,
                path=artifact.path,
                description=artifact.description,
                content=artifact.content,
            )
            for artifact in generation.artifacts
        ]
        GeneratedArtifact.objects.bulk_create(artifacts)
        snapshot_version = ArtifactSnapshot.next_version_for(analysis)
        ArtifactSnapshot.objects.bulk_create(
            [
                ArtifactSnapshot(
                    analysis=analysis,
                    version=snapshot_version,
                    event=ArtifactSnapshot.Event.GENERATION,
                    generation_profile=analysis.generation_profile,
                    kind=artifact.kind,
                    path=artifact.path,
                    content=artifact.content,
                )
                for artifact in generation.artifacts
            ]
        )
