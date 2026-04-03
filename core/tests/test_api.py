from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib import error

import yaml
from config import settings as project_settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test import RequestFactory
from django.urls import reverse
from core.api.serializers import ProjectAnalysisSerializer, WorkspaceSerializer
from core.models import (
    ArtifactSnapshot,
    ExecutionJob,
    ExternalRepoConnection,
    GeneratedArtifact,
    PreviewRun,
    PreviewRunnerSession,
    ProjectAnalysis,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from core.crypto import TOKEN_PREFIX
from core.services.build_validation import BuildValidationResult, BuildValidationService, RemoteValidationService
from core.services.detector import StackDetector
from core.services.generator import ArtifactGenerator
from core.services.github_actions import GitHubActionsClient
from core.services.github_pr import GitHubPullRequestResult
from core.services.healthchecks import HealthcheckPlannerService
from core.services.contracts import GeneratedArtifactSpec
from core.services.ingestion import cleanup_workspace, prepare_source_workspace
from core.services.local_preview_smoke import LocalPreviewSmokeService
from core.services.validation_bundle import ValidationBundleService
from core.services.execution_runner import ExecutionJobRunner
from core.services.preview import PreviewService
from core.services.preview_runner import PreviewRunnerError
from core.services.runtime import CommandExecutionError, docker_compose_command
from core.test_support import AnalysisApiTestSupport



class DashboardAuthTests(TestCase):
    def setUp(self):
        self.password = "test-pass-123"
        self.user = get_user_model().objects.create_user(
            username="lucas",
            password=self.password,
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_page_exposes_signup_link(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("signup"))
        self.assertContains(response, reverse("password_reset"))
        self.assertNotContains(response, "<form")

    def test_password_reset_page_renders(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset your password")
        self.assertContains(response, "Send reset link")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_sends_email_for_existing_account(self):
        self.user.email = "lucas@example.com"
        self.user.save(update_fields=["email"])

        response = self.client.post(
            reverse("password_reset"),
            {"email": "lucas@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Reset your AutoDocker password", mail.outbox[0].subject)
        self.assertIn("/accounts/password-reset/", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_does_not_leak_unknown_email(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "unknown@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_done_page_renders(self):
        response = self.client.get(reverse("password_reset_done"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your inbox")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_confirm_allows_setting_new_password(self):
        self.user.email = "lucas@example.com"
        self.user.save(update_fields=["email"])
        self.client.post(reverse("password_reset"), {"email": "lucas@example.com"})

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        reset_path = next(
            line.strip()
            for line in body.splitlines()
            if "/accounts/password-reset/" in line and "/confirm/" in line
        )
        confirm_path = reset_path.replace("http://testserver", "")

        get_response = self.client.get(confirm_path, follow=True)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Set a new password")
        final_confirm_path = get_response.request["PATH_INFO"]

        post_response = self.client.post(
            final_confirm_path,
            {
                "new_password1": "brand-new-pass-123",
                "new_password2": "brand-new-pass-123",
            },
        )

        self.assertRedirects(post_response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-pass-123"))

    def test_password_reset_complete_page_renders(self):
        response = self.client.get(reverse("password_reset_complete"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password updated")

    def test_login_json_redirects_to_dashboard_and_logs_user_in(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "lucas",
                "password": self.password,
            },
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["redirect_url"], reverse("core:dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_json_returns_error_on_invalid_credentials(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "lucas",
                "password": "wrong-password",
            },
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("Invalid", payload["detail"])

    def test_signup_page_renders(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create account")
        self.assertContains(response, "Start your workspace")

    def test_signup_redirects_to_dashboard_and_logs_user_in(self):
        response = self.client.post(
            reverse("signup"),
            {
                "first_name": "Lucas",
                "last_name": "Garcia",
                "username": "new-user",
                "email": "new-user@example.com",
                "password1": "super-secret-pass-123",
                "password2": "super-secret-pass-123",
                "accept_terms": "on",
            },
        )

        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertTrue(get_user_model().objects.filter(username="new-user").exists())
        created_user = get_user_model().objects.get(username="new-user")
        self.assertEqual(int(self.client.session["_auth_user_id"]), created_user.pk)
        self.assertTrue(
            WorkspaceMembership.objects.filter(
                user=created_user,
                role=WorkspaceMembership.Role.OWNER,
            ).exists()
        )


    def test_signup_rejects_invalid_password_confirmation(self):
        response = self.client.post(
            reverse("signup"),
            {
                "first_name": "Broken",
                "last_name": "User",
                "username": "broken-user",
                "email": "broken-user@example.com",
                "password1": "super-secret-pass-123",
                "password2": "another-pass-123",
                "accept_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="broken-user").exists())
        self.assertContains(response, "Los dos campos de contraseñas no coinciden")

    def test_signup_requires_terms_and_email(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "terms-user",
                "email": "",
                "password1": "super-secret-pass-123",
                "password2": "super-secret-pass-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="terms-user").exists())
        self.assertContains(response, "Este campo es requerido")

    def test_dashboard_loads_split_dashboard_scripts(self):
        user = get_user_model().objects.create_user(
            username="dashboard-user",
            password="super-secret-pass-123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "core/js/dashboard_form.js")
        self.assertContains(response, "core/js/dashboard_collections.js")
        self.assertContains(response, "core/favicon.svg")

    @override_settings(
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="",
    )
    def test_dashboard_hides_preview_controls_when_remote_runner_is_not_configured(self):
        user = get_user_model().objects.create_user(
            username="dashboard-no-preview",
            password="super-secret-pass-123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="preview-button"')
        self.assertNotContains(response, 'id="stop-preview-button"')
        self.assertNotContains(response, 'id="preview-summary"')
        self.assertNotContains(response, "Executable environment")

    @override_settings(
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    def test_dashboard_keeps_preview_controls_when_remote_runner_is_configured(self):
        user = get_user_model().objects.create_user(
            username="dashboard-with-preview",
            password="super-secret-pass-123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="preview-button"')
        self.assertContains(response, 'id="stop-preview-button"')
        self.assertContains(response, 'id="preview-summary"')
        self.assertContains(response, "Executable environment")

    def test_dashboard_bootstraps_initial_collections_in_html(self):
        user = get_user_model().objects.create_user(
            username="dashboard-bootstrap",
            password="super-secret-pass-123",
        )
        workspace = Workspace.objects.create(
            owner=user,
            name="Equipo plataforma",
            slug="equipo-plataforma",
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=user,
            role=WorkspaceMembership.Role.OWNER,
        )
        analysis = ProjectAnalysis.objects.create(
            owner=user,
            workspace=workspace,
            project_name="bootstrap-app",
            source_type=ProjectAnalysis.SourceType.GIT,
            generation_profile=ProjectAnalysis.GenerationProfile.PRODUCTION,
            repository_url="https://github.com/acme/bootstrap-app",
            status=ProjectAnalysis.Status.READY,
        )
        GeneratedArtifact.objects.create(
            analysis=analysis,
            kind=GeneratedArtifact.Kind.DOCKERFILE,
            path="Dockerfile",
            content="HEAVY-DASHBOARD-BOOTSTRAP-MARKER",
        )
        ExternalRepoConnection.objects.create(
            owner=user,
            provider=ExternalRepoConnection.Provider.GITHUB,
            label="GitHub personal",
            access_token="ghp_test_token",
        )
        WorkspaceInvitation.objects.create(
            workspace=workspace,
            invited_by=user,
            email="invitee@example.com",
            role=WorkspaceMembership.Role.VIEWER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dashboard-bootstrap"')
        self.assertContains(response, "bootstrap-app")
        self.assertContains(response, "Equipo plataforma")
        self.assertContains(response, "GitHub personal")
        self.assertContains(response, str(analysis.id))
        self.assertNotContains(response, "HEAVY-DASHBOARD-BOOTSTRAP-MARKER")

    def test_dashboard_initial_render_does_not_query_heavy_analysis_relations(self):
        user = get_user_model().objects.create_user(
            username="dashboard-lightweight",
            password="super-secret-pass-123",
        )
        workspace = Workspace.objects.create(
            owner=user,
            name="Equipo liviano",
            slug="equipo-liviano",
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=user,
            role=WorkspaceMembership.Role.OWNER,
        )
        analysis = ProjectAnalysis.objects.create(
            owner=user,
            workspace=workspace,
            project_name="lightweight-app",
            source_type=ProjectAnalysis.SourceType.GIT,
            generation_profile=ProjectAnalysis.GenerationProfile.PRODUCTION,
            repository_url="https://github.com/acme/lightweight-app",
            status=ProjectAnalysis.Status.READY,
        )
        GeneratedArtifact.objects.create(
            analysis=analysis,
            kind=GeneratedArtifact.Kind.DOCKERFILE,
            path="Dockerfile",
            content="FROM python:3.13-slim",
        )
        ExecutionJob.objects.create(
            owner=user,
            analysis=analysis,
            kind=ExecutionJob.Kind.VALIDATION,
            status=ExecutionJob.Status.READY,
            label="validation",
        )
        PreviewRun.objects.create(
            owner=user,
            analysis=analysis,
            status=PreviewRun.Status.READY,
            runtime_kind=PreviewRun.RuntimeKind.CONTAINER,
            access_url="https://preview.example.com",
        )
        self.client.force_login(user)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        sql = "\n".join(query["sql"].lower() for query in queries.captured_queries)
        self.assertNotIn("core_generatedartifact", sql)
        self.assertNotIn("core_executionjob", sql)
        self.assertNotIn("core_previewrun", sql)


class DashboardSerializationPerformanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="perf-user",
            password="super-secret-pass-123",
            email="perf-user@example.com",
        )
        self.request = RequestFactory().get("/")

    def test_project_analysis_serializer_uses_prefetched_relations_without_extra_queries(self):
        workspace = Workspace.objects.create(
            owner=self.user,
            name="Perf workspace",
            slug="perf-workspace",
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=self.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            workspace=workspace,
            project_name="perf-analysis",
            source_type=ProjectAnalysis.SourceType.GIT,
            generation_profile=ProjectAnalysis.GenerationProfile.PRODUCTION,
            repository_url="https://github.com/acme/perf-analysis",
            status=ProjectAnalysis.Status.READY,
        )
        GeneratedArtifact.objects.create(
            analysis=analysis,
            kind=GeneratedArtifact.Kind.DOCKERFILE,
            path="Dockerfile",
            content="FROM python:3.13-slim",
        )
        ExecutionJob.objects.create(
            owner=self.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.VALIDATION,
            status=ExecutionJob.Status.READY,
            label="validation",
        )
        ExecutionJob.objects.create(
            owner=self.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.GITHUB_PR,
            status=ExecutionJob.Status.QUEUED,
            label="pr",
        )
        PreviewRun.objects.create(
            owner=self.user,
            analysis=analysis,
            status=PreviewRun.Status.READY,
            runtime_kind=PreviewRun.RuntimeKind.CONTAINER,
            access_url="https://preview.example.com",
        )
        prefetched = ProjectAnalysis.objects.with_related().get(pk=analysis.pk)

        with CaptureQueriesContext(connection) as queries:
            payload = ProjectAnalysisSerializer(prefetched, context={"request": self.request}).data

        self.assertEqual(len(queries), 0)
        self.assertEqual(payload["latest_validation_job"]["kind"], ExecutionJob.Kind.VALIDATION)
        self.assertEqual(payload["latest_github_pr_job"]["kind"], ExecutionJob.Kind.GITHUB_PR)
        self.assertEqual(payload["active_preview"]["status"], PreviewRun.Status.READY)

    def test_workspace_serializer_uses_prefetched_relations_without_extra_queries(self):
        workspace = Workspace.objects.create(
            owner=self.user,
            name="Perf team",
            slug="perf-team",
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=self.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        WorkspaceInvitation.objects.create(
            workspace=workspace,
            invited_by=self.user,
            email="invitee@example.com",
            role=WorkspaceMembership.Role.EDITOR,
        )
        prefetched = Workspace.objects.for_user(self.user).prefetch_related(
            "memberships__user",
            "invitations__invited_user",
            "invitations__invited_by",
        ).get(pk=workspace.pk)

        with CaptureQueriesContext(connection) as queries:
            payload = WorkspaceSerializer(prefetched).data

        self.assertEqual(len(queries), 0)
        self.assertEqual(payload["member_count"], 1)
        self.assertEqual(payload["pending_invitations"][0]["email"], "invitee@example.com")


@override_settings(AUTODOCKER_ASYNC_MODE="inline", CELERY_TASK_ALWAYS_EAGER=False)
class AnalysisApiTests(AnalysisApiTestSupport, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="lucas",
            password="test-pass-123",
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_analysis_assigns_owner_and_generates_artifacts(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        analysis = ProjectAnalysis.objects.get()
        self.assertEqual(payload["owner"], "lucas")
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["generation_profile"], ProjectAnalysis.GenerationProfile.PRODUCTION)
        self.assertTrue(payload["artifacts"])
        self.assertEqual(analysis.owner, self.user)
        self.assertIsNotNone(analysis.workspace)
        self.assertEqual(
            ArtifactSnapshot.objects.filter(
                analysis=analysis,
                event=ArtifactSnapshot.Event.GENERATION,
            ).count(),
            analysis.artifacts.count(),
        )

    def test_create_analysis_populates_scanner_healthcheck_cicd_and_deploy(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )

        self.assertEqual(response.status_code, 202)
        analysis = ProjectAnalysis.objects.get()
        artifact_paths = set(analysis.artifacts.values_list("path", flat=True))
        self.assertIn(".github/workflows/autodocker-ci.yml", artifact_paths)
        self.assertIn("render.yaml", artifact_paths)
        self.assertIn("deploy/kubernetes/app.yaml", artifact_paths)
        self.assertTrue(analysis.security_report.get("summary"))
        self.assertTrue(analysis.healthcheck_report.get("summary"))
        self.assertTrue(analysis.cicd_report.get("summary"))
        self.assertTrue(analysis.deploy_report.get("summary"))
        self.assertEqual(analysis.security_report.get("coverage"), "heuristic")
        self.assertEqual(analysis.cicd_report.get("maturity"), "bootstrap")
        self.assertEqual(analysis.deploy_report.get("maturity"), "bootstrap")

    def test_create_analysis_persists_development_profile(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {
                            "dev": "next dev",
                            "build": "next build",
                            "start": "next start",
                        },
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            },
            generation_profile=ProjectAnalysis.GenerationProfile.DEVELOPMENT,
        )

        self.assertEqual(response.status_code, 202)
        analysis = ProjectAnalysis.objects.get()
        dockerfile = analysis.artifacts.get(path="Dockerfile")
        self.assertEqual(analysis.generation_profile, ProjectAnalysis.GenerationProfile.DEVELOPMENT)
        self.assertIn("NODE_ENV=development", dockerfile.content)

    def test_create_analysis_persists_ci_profile(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            },
            generation_profile=ProjectAnalysis.GenerationProfile.CI,
        )

        self.assertEqual(response.status_code, 202)
        analysis = ProjectAnalysis.objects.get()
        self.assertEqual(analysis.generation_profile, ProjectAnalysis.GenerationProfile.CI)

    @patch(
        "core.api.views.ProjectAnalysis.objects.create",
        side_effect=PermissionError("permission denied"),
    )
    def test_create_analysis_returns_json_when_archive_storage_fails(self, _mock_create):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"],
            "The uploaded archive could not be saved. Check the media storage configuration or local volume setup.",
        )

    def test_editing_artifact_creates_snapshot_version(self):
        self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis = ProjectAnalysis.objects.get()
        artifact = analysis.artifacts.get(path="Dockerfile")
        response = self.client.patch(
            reverse("core-api:artifact-detail", args=[artifact.id]),
            data=json.dumps({"content": f"{artifact.content}\n# edited"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        latest_snapshot = ArtifactSnapshot.objects.filter(analysis=analysis).order_by("-version").first()
        self.assertIsNotNone(latest_snapshot)
        self.assertEqual(latest_snapshot.event, ArtifactSnapshot.Event.EDIT)
        self.assertEqual(latest_snapshot.version, 2)
        self.assertIn("# edited", latest_snapshot.content)

    def test_regenerate_updates_profile_and_creates_new_generation_snapshot(self):
        self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {
                            "dev": "next dev",
                            "build": "next build",
                            "start": "next start",
                        },
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis = ProjectAnalysis.objects.get()

        response = self.client.post(
            reverse("core-api:analysis-regenerate", args=[analysis.id]),
            {"generation_profile": ProjectAnalysis.GenerationProfile.DEVELOPMENT},
        )

        self.assertEqual(response.status_code, 202)
        analysis.refresh_from_db()
        self.assertEqual(analysis.generation_profile, ProjectAnalysis.GenerationProfile.DEVELOPMENT)
        latest_generation = ArtifactSnapshot.objects.filter(
            analysis=analysis,
            event=ArtifactSnapshot.Event.GENERATION,
        ).order_by("-version").first()
        self.assertIsNotNone(latest_generation)
        self.assertEqual(latest_generation.version, 2)

    def test_diff_endpoint_marks_missing_generated_files_as_new(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        diff_response = self.client.get(reverse("core-api:analysis-diff", args=[analysis_id]))

        self.assertEqual(diff_response.status_code, 200)
        items = diff_response.json()["items"]
        ignore_item = next(item for item in items if item["path"] == ".dockerignore")
        self.assertEqual(ignore_item["status"], "new")

    @patch(
        "core.services.execution_runner.BuildValidationService.validate",
        return_value=BuildValidationResult(
            success=True,
            command=["docker", "build", "."],
            logs="validation ok",
            image_tag="autodocker-test",
        ),
    )
    def test_validate_endpoint_creates_ready_execution_job(self, _mock_validate):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        validate_response = self.client.post(
            reverse("core-api:analysis-validate", args=[analysis_id])
        )

        self.assertEqual(validate_response.status_code, 202)
        payload = validate_response.json()
        self.assertEqual(payload["kind"], ExecutionJob.Kind.VALIDATION)
        self.assertEqual(payload["status"], ExecutionJob.Status.READY)
        self.assertEqual(payload["result_payload"]["image_tag"], "autodocker-test")

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=True,
        AUTODOCKER_VALIDATION_BACKEND="github_actions",
    )
    def test_validate_endpoint_uses_remote_backend_when_configured(self):
        class FakeRemoteValidationService:
            pass

        remote_validation_result = BuildValidationResult(
            success=True,
            command=["remote", "validate"],
            logs="remote ok",
            image_tag="",
            metadata={
                "validation_backend": "github_actions",
                "workflow_run_id": 123,
                "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
                "bundle_sha256": "b" * 64,
            },
            result_payload={
                "executor": "github_actions",
                "summary": "docker build completed successfully",
                "artifact_urls": {
                    "workflow_run": "https://github.com/acme/executor/actions/runs/123",
                },
                "duration_seconds": 86,
            },
        )
        local_validation_result = SimpleNamespace(
            success=True,
            logs="local ok",
            to_dict=lambda: {
                "success": True,
                "command": ["docker", "build", "."],
                "logs": "local ok",
                "image_tag": "autodocker-test",
                "result_payload": {"executor": "local"},
            },
        )

        with patch("core.services.build_validation.ensure_runtime_jobs_enabled") as mock_ensure_runtime_jobs_enabled, patch(
            "core.services.build_validation.ensure_docker_runtime_access"
        ) as mock_ensure_docker_runtime_access, patch(
            "core.services.build_validation.docker_command",
            return_value=["docker"],
        ) as mock_docker_command, patch(
            "core.services.build_validation.run_command",
            return_value=SimpleNamespace(output="local ok"),
        ) as mock_run_command, patch(
            "core.services.build_validation.BuildValidationResult",
            return_value=local_validation_result,
        ), patch(
            "core.services.build_validation.RemoteValidationService",
            new=FakeRemoteValidationService,
            create=True,
        ), patch.object(
            FakeRemoteValidationService,
            "validate",
            create=True,
            return_value=remote_validation_result,
        ) as mock_remote_validate:
            response = self._post_analysis(
                files={
                    "next-sample/package.json": json.dumps(
                        {
                            "name": "next-sample",
                            "scripts": {"build": "next build", "start": "next start"},
                            "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                        }
                    )
                }
            )
            analysis_id = response.json()["id"]

            validate_response = self.client.post(
                reverse("core-api:analysis-validate", args=[analysis_id])
            )

        self.assertEqual(validate_response.status_code, 202)
        payload = validate_response.json()
        self.assertEqual(payload["status"], ExecutionJob.Status.READY)
        mock_remote_validate.assert_called_once()
        self.assertEqual(payload["result_payload"]["executor"], "github_actions")
        mock_ensure_runtime_jobs_enabled.assert_not_called()
        mock_ensure_docker_runtime_access.assert_not_called()
        mock_docker_command.assert_not_called()
        mock_run_command.assert_not_called()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_VALIDATION_BACKEND="github_actions",
    )
    @patch("core.services.build_validation.RemoteValidationService.validate")
    def test_validate_endpoint_allows_remote_backend_when_runtime_jobs_are_disabled(
        self,
        mock_remote_validate,
    ):
        mock_remote_validate.return_value = BuildValidationResult(
            success=True,
            command=["remote", "validate"],
            logs="remote ok",
            image_tag="",
            metadata={
                "validation_backend": "github_actions",
                "workflow_run_id": 123,
            },
            result_payload={
                "executor": "github_actions",
                "summary": "docker build completed successfully",
            },
        )
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        validate_response = self.client.post(
            reverse("core-api:analysis-validate", args=[analysis_id])
        )

        self.assertEqual(validate_response.status_code, 202)
        payload = validate_response.json()
        self.assertEqual(payload["status"], ExecutionJob.Status.READY)
        self.assertEqual(payload["result_payload"]["executor"], "github_actions")
        mock_remote_validate.assert_called_once()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_VALIDATION_BACKEND="github_actions",
    )
    def test_analysis_detail_exposes_runtime_capabilities(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        detail_response = self.client.get(
            reverse("core-api:analysis-detail", args=[analysis_id])
        )

        self.assertEqual(detail_response.status_code, 200)
        capabilities = detail_response.json()["runtime_capabilities"]
        self.assertEqual(capabilities["validation"]["backend"], "github_actions")
        self.assertTrue(capabilities["validation"]["enabled"])
        self.assertFalse(capabilities["preview"]["enabled"])
        self.assertIn(
            "AUTODOCKER_ENABLE_RUNTIME_JOBS",
            capabilities["preview"]["reason"],
        )

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_VALIDATION_BACKEND="github_actions",
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    def test_analysis_detail_exposes_remote_preview_runtime_capability(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        detail_response = self.client.get(
            reverse("core-api:analysis-detail", args=[analysis_id])
        )

        self.assertEqual(detail_response.status_code, 200)
        capabilities = detail_response.json()["runtime_capabilities"]
        self.assertEqual(capabilities["preview"]["backend"], "remote_runner")
        self.assertTrue(capabilities["preview"]["enabled"])
        self.assertEqual(capabilities["preview"]["reason"], "")

    def test_build_validation_result_can_carry_remote_metadata_and_payload(self):
        result = BuildValidationResult(
            success=True,
            command=["remote", "validate"],
            logs="remote ok",
            image_tag="",
            metadata={
                "validation_backend": "github_actions",
                "workflow_run_id": 123,
                "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
            },
            result_payload={
                "executor": "github_actions",
                "summary": "docker build completed successfully",
                "artifact_urls": {
                    "workflow_run": "https://github.com/acme/executor/actions/runs/123",
                },
            },
        )

        self.assertEqual(result.result_payload["executor"], "github_actions")
        self.assertEqual(result.metadata["workflow_run_id"], 123)

    @patch("core.services.github_actions.GitHubActionsClient.dispatch_validation")
    @patch("core.services.github_actions.GitHubActionsClient.wait_for_completion")
    @patch("core.services.build_validation.ValidationBundleService.build")
    @patch("django.core.files.storage.default_storage.url")
    @patch("django.core.files.storage.default_storage.save")
    def test_remote_validation_service_dispatches_and_normalizes_remote_result(
        self,
        mock_storage_save,
        mock_storage_url,
        mock_build_bundle,
        mock_wait_for_completion,
        mock_dispatch_validation,
    ):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="remote-demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        job = ExecutionJob.objects.create(
            owner=self.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.VALIDATION,
        )

        bundle_root = Path(tempfile.mkdtemp(prefix="autodocker-test-"))
        self.addCleanup(cleanup_workspace, bundle_root)
        (bundle_root / "validation-bundle.zip").write_bytes(b"fake bundle")
        mock_build_bundle.return_value = SimpleNamespace(
            workspace_root=bundle_root,
            bundle_path=bundle_root / "validation-bundle.zip",
            sha256="a" * 64,
            bundle_size_bytes=1234,
        )
        mock_storage_save.return_value = "validation-bundles/job-123/bundle.zip"
        mock_storage_url.return_value = "https://storage.example/validation-bundles/job-123/bundle.zip"

        mock_dispatch_validation.return_value = {
            "workflow_run_id": 123,
            "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
        }
        mock_wait_for_completion.return_value = {
            "success": True,
            "summary": "docker build completed successfully",
            "command": ["docker", "build", "-t", "autodocker-validate", "."],
            "logs": "remote ok",
            "duration_seconds": 86,
            "artifact_urls": {
                "workflow_run": "https://github.com/acme/executor/actions/runs/123",
            },
        }

        result = RemoteValidationService().validate(job)

        self.assertEqual(result.metadata["validation_backend"], "github_actions")
        self.assertEqual(result.metadata["workflow_run_id"], 123)
        self.assertEqual(result.metadata["workflow_run_url"], "https://github.com/acme/executor/actions/runs/123")
        self.assertEqual(result.metadata["bundle_sha256"], "a" * 64)
        self.assertEqual(result.result_payload["executor"], "github_actions")
        self.assertEqual(result.result_payload["artifact_urls"]["workflow_run"], "https://github.com/acme/executor/actions/runs/123")
        mock_build_bundle.assert_called_once()
        mock_storage_save.assert_called_once()
        mock_storage_url.assert_called_once()
        mock_dispatch_validation.assert_called_once()
        mock_wait_for_completion.assert_called_once()

    @patch("core.services.github_actions.GitHubActionsClient._request_raw")
    @patch("core.services.github_actions.GitHubActionsClient._request")
    def test_github_actions_client_reads_result_from_validation_results_artifact(
        self,
        mock_request,
        mock_request_raw,
    ):
        artifact_archive = io.BytesIO()
        with zipfile.ZipFile(artifact_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr(
                "bundle/result.json",
                json.dumps(
                    {
                        "success": True,
                        "summary": "docker build completed successfully",
                        "command": ["docker", "build", "-t", "autodocker-validate", "."],
                        "duration_seconds": 12,
                    }
                ),
            )
            zipped.writestr("bundle/validation.log", "remote logs")
        mock_request.return_value = {
            "artifacts": [
                {
                    "name": "validation-results",
                    "archive_download_url": "https://example.com/artifacts/123.zip",
                }
            ]
        }
        mock_request_raw.return_value = artifact_archive.getvalue()

        result = GitHubActionsClient(
            token="token",
            repo="LucasTabacchi/autodocker-validator",
            workflow="validate.yml",
        ).download_result_artifacts(workflow_run_id=123)

        self.assertTrue(result["success"])
        self.assertEqual(result["summary"], "docker build completed successfully")
        self.assertEqual(result["command"], ["docker", "build", "-t", "autodocker-validate", "."])
        self.assertEqual(result["duration_seconds"], 12)
        self.assertEqual(result["logs"], "remote logs")

    @patch("core.services.github_actions.request.urlopen")
    @patch("core.services.github_actions.request.build_opener")
    def test_github_actions_client_follows_artifact_redirect_without_forwarding_github_auth(
        self,
        mock_build_opener,
        mock_urlopen,
    ):
        redirect_location = "https://pipelines.actions.githubusercontent.com/blob.zip?sig=abc123"
        redirect_error = error.HTTPError(
            url="https://api.github.com/artifacts/123.zip",
            code=302,
            msg="Found",
            hdrs={"Location": redirect_location},
            fp=io.BytesIO(b""),
        )
        mock_opener = Mock()
        mock_opener.open.side_effect = redirect_error
        mock_build_opener.return_value = mock_opener

        final_response = MagicMock()
        final_response.__enter__.return_value.read.return_value = b"zip-bytes"
        mock_urlopen.return_value = final_response

        payload = GitHubActionsClient(
            token="token",
            repo="LucasTabacchi/autodocker-validator",
            workflow="validate.yml",
        )._request_raw("https://api.github.com/artifacts/123.zip")

        self.assertEqual(payload, b"zip-bytes")
        mock_opener.open.assert_called_once()
        mock_urlopen.assert_called_once()
        redirected_request = mock_urlopen.call_args.args[0]
        self.assertEqual(redirected_request.full_url, redirect_location)
        self.assertNotIn("Authorization", redirected_request.headers)

    @patch("core.services.github_actions.time.sleep")
    @patch("core.services.github_actions.GitHubActionsClient._request")
    def test_github_actions_client_retries_until_dispatched_run_is_visible(
        self,
        mock_request,
        _mock_sleep,
    ):
        mock_request.side_effect = [
            {},
            {
                "workflow_runs": [
                    {
                        "id": 456,
                        "html_url": "https://github.com/acme/executor/actions/runs/456",
                        "display_title": "Validate 9567099c-23ff-4adb-93e3-f49c26296f5b",
                    }
                ]
            },
        ]

        run = GitHubActionsClient(
            token="token",
            repo="LucasTabacchi/autodocker-validator",
            workflow="validate.yml",
        ).find_workflow_run("9567099c-23ff-4adb-93e3-f49c26296f5b", timeout_seconds=1)

        self.assertEqual(run["workflow_run_id"], 456)
        self.assertEqual(run["workflow_run_url"], "https://github.com/acme/executor/actions/runs/456")

    @patch("core.services.github_actions.GitHubActionsClient.download_result_artifacts")
    @patch("core.services.github_actions.GitHubActionsClient._request")
    def test_github_actions_client_wait_for_completion_formats_summary_command_and_logs(
        self,
        mock_request,
        mock_download_result_artifacts,
    ):
        mock_request.return_value = {
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/acme/executor/actions/runs/123",
        }
        mock_download_result_artifacts.return_value = {
            "success": True,
            "summary": "docker compose build completed successfully",
            "command": ["docker", "compose", "-f", "docker-compose.yml", "build"],
            "logs": "STEP 1/8\nSTEP 2/8\nDONE",
            "duration_seconds": 14,
        }

        result = GitHubActionsClient(
            token="token",
            repo="LucasTabacchi/autodocker-validator",
            workflow="validate.yml",
        ).wait_for_completion(workflow_run_id=123, timeout_seconds=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["summary"], "docker compose build completed successfully")
        self.assertEqual(
            result["logs"],
            "\n".join(
                [
                    "Summary: docker compose build completed successfully",
                    "Command: docker compose -f docker-compose.yml build",
                    "Duration: 14s",
                    "",
                    "STEP 1/8",
                    "STEP 2/8",
                    "DONE",
                ]
            ),
        )

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=True,
        AUTODOCKER_VALIDATION_BACKEND="github_actions",
    )
    @patch("core.services.build_validation.RemoteValidationService.validate")
    def test_execution_job_runner_persists_remote_validation_metadata_and_payload(
        self,
        mock_remote_validate,
    ):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="runner-remote-demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        job = ExecutionJob.objects.create(
            owner=self.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.VALIDATION,
        )

        mock_remote_validate.return_value = BuildValidationResult(
            success=True,
            command=["remote", "validate"],
            logs="remote ok",
            image_tag="",
            metadata={
                "validation_backend": "github_actions",
                "workflow_run_id": 123,
                "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
                "bundle_sha256": "b" * 64,
            },
            result_payload={
                "executor": "github_actions",
                "summary": "docker build completed successfully",
                "artifact_urls": {
                    "workflow_run": "https://github.com/acme/executor/actions/runs/123",
                },
                "duration_seconds": 86,
            },
        )

        ExecutionJobRunner().run(job)
        job.refresh_from_db()

        self.assertEqual(job.status, ExecutionJob.Status.READY)
        self.assertEqual(job.metadata["workflow_run_id"], 123)
        self.assertEqual(job.result_payload["executor"], "github_actions")
        self.assertEqual(job.result_payload["summary"], "docker build completed successfully")
        mock_remote_validate.assert_called_once()

    @patch(
        "core.services.execution_runner.BuildValidationService.validate",
        return_value=BuildValidationResult(
            success=False,
            command=["docker", "build", "."],
            logs="build failed",
            image_tag="",
        ),
    )
    def test_validate_endpoint_marks_job_failed_on_validation_failure(self, _mock_validate):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        validate_response = self.client.post(
            reverse("core-api:analysis-validate", args=[analysis_id])
        )

        self.assertEqual(validate_response.status_code, 202)
        payload = validate_response.json()
        self.assertEqual(payload["status"], ExecutionJob.Status.FAILED)
        self.assertEqual(payload["logs"], "build failed")

    @override_settings(AUTODOCKER_ENABLE_RUNTIME_JOBS=False)
    def test_validate_endpoint_returns_409_when_runtime_jobs_are_disabled(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        validate_response = self.client.post(
            reverse("core-api:analysis-validate", args=[analysis_id])
        )

        self.assertEqual(validate_response.status_code, 409)
        self.assertIn("AUTODOCKER_ENABLE_RUNTIME_JOBS", validate_response.json()["detail"])
        self.assertFalse(ExecutionJob.objects.filter(kind=ExecutionJob.Kind.VALIDATION).exists())

    @patch("core.services.execution_runner.PreviewService.start")
    def test_preview_endpoint_creates_ready_preview_run(self, mock_start):
        def fake_start(preview_run):
            preview_run.status = PreviewRun.Status.READY
            preview_run.runtime_kind = PreviewRun.RuntimeKind.CONTAINER
            preview_run.access_url = "http://127.0.0.1:40123"
            preview_run.ports = {"app": ["http://127.0.0.1:40123"]}
            preview_run.logs = "preview ok"
            preview_run.resource_names = ["preview-container"]
            preview_run.save(
                update_fields=[
                    "status",
                    "runtime_kind",
                    "access_url",
                    "ports",
                    "logs",
                    "resource_names",
                    "updated_at",
                ]
            )
            return preview_run

        mock_start.side_effect = fake_start
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        preview_response = self.client.post(
            reverse("core-api:analysis-preview", args=[analysis_id])
        )

        self.assertEqual(preview_response.status_code, 202)
        payload = preview_response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.READY)
        self.assertEqual(payload["access_url"], "http://127.0.0.1:40123")
        job = ExecutionJob.objects.get(kind=ExecutionJob.Kind.PREVIEW)
        self.assertEqual(job.status, ExecutionJob.Status.READY)

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview.RemotePreviewService.start")
    def test_preview_endpoint_uses_remote_runner_backend_when_configured(self, mock_start):
        def fake_start(preview_run):
            preview_run.status = PreviewRun.Status.RUNNING
            preview_run.runtime_kind = PreviewRun.RuntimeKind.COMPOSE
            preview_run.access_url = ""
            preview_run.ports = {}
            preview_run.logs = "runner accepted preview"
            preview_run.resource_names = ["prv-demo-web"]
            preview_run.save(
                update_fields=[
                    "status",
                    "runtime_kind",
                    "access_url",
                    "ports",
                    "logs",
                    "resource_names",
                    "updated_at",
                ]
            )
            return preview_run

        mock_start.side_effect = fake_start
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        preview_response = self.client.post(
            reverse("core-api:analysis-preview", args=[analysis_id])
        )

        self.assertEqual(preview_response.status_code, 202)
        payload = preview_response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.RUNNING)
        self.assertEqual(payload["runtime_kind"], PreviewRun.RuntimeKind.COMPOSE)
        self.assertEqual(payload["resource_names"], ["prv-demo-web"])
        mock_start.assert_called_once()
        job = ExecutionJob.objects.get(kind=ExecutionJob.Kind.PREVIEW)
        self.assertEqual(job.status, ExecutionJob.Status.READY)

    @override_settings(AUTODOCKER_ENABLE_RUNTIME_JOBS=False)
    def test_preview_endpoint_returns_409_when_runtime_jobs_are_disabled(self):
        response = self._post_analysis(
            files={
                "next-sample/package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        )
        analysis_id = response.json()["id"]

        preview_response = self.client.post(
            reverse("core-api:analysis-preview", args=[analysis_id])
        )

        self.assertEqual(preview_response.status_code, 409)
        self.assertIn("AUTODOCKER_ENABLE_RUNTIME_JOBS", preview_response.json()["detail"])
        self.assertFalse(ExecutionJob.objects.filter(kind=ExecutionJob.Kind.PREVIEW).exists())

    @patch(
        "core.services.execution_runner.GitHubPullRequestService.create_pull_request",
        return_value=GitHubPullRequestResult(
            success=True,
            branch_name="codex/autodocker-deadbeef",
            pr_url="https://github.com/acme/demo/pull/7",
            pull_number=7,
            logs="pr ok",
        ),
    )
    def test_github_pr_endpoint_saves_connection_and_returns_job(self, _mock_pr):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        GeneratedArtifact.objects.create(
            analysis=analysis,
            kind=GeneratedArtifact.Kind.DOCKERFILE,
            path="Dockerfile",
            description="Dockerfile",
            content="FROM node:22-alpine",
        )

        response = self.client.post(
            reverse("core-api:analysis-github-pr", args=[analysis.id]),
            {
                "access_token": "ghp_test_123",
                "save_connection": "true",
                "connection_label": "main-token",
                "account_name": "lucas",
                "base_branch": "main",
                "title": "Dockerize demo",
                "body": "Generated by tests",
            },
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["kind"], ExecutionJob.Kind.GITHUB_PR)
        self.assertEqual(payload["status"], ExecutionJob.Status.READY)
        self.assertEqual(payload["result_payload"]["pr_url"], "https://github.com/acme/demo/pull/7")
        self.assertEqual(ExternalRepoConnection.objects.count(), 1)
        connection = ExternalRepoConnection.objects.get()
        self.assertTrue(connection.access_token.startswith(TOKEN_PREFIX))
        self.assertEqual(connection.get_access_token(), "ghp_test_123")

    def test_connection_crud_endpoints_work(self):
        create_response = self.client.post(
            reverse("core-api:connection-list-create"),
            {
                "label": "my-gh",
                "access_token": "ghp_local_123",
                "account_name": "lucas",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        connection_id = create_response.json()["id"]
        connection = ExternalRepoConnection.objects.get(pk=connection_id)
        self.assertNotEqual(connection.access_token, "ghp_local_123")
        self.assertTrue(connection.access_token.startswith(TOKEN_PREFIX))
        self.assertEqual(connection.get_access_token(), "ghp_local_123")

        list_response = self.client.get(reverse("core-api:connection-list-create"))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()), 1)
        self.assertEqual(list_response.json()[0]["token_storage"], "encrypted")

        delete_response = self.client.delete(
            reverse("core-api:connection-detail", args=[connection_id])
        )
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(ExternalRepoConnection.objects.count(), 0)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AUTODOCKER_APP_BASE_URL="http://127.0.0.1:8000",
    )
    def test_workspace_endpoints_create_workspace_and_invitation(self):
        create_response = self.client.post(
            reverse("core-api:workspace-list-create"),
            {
                "name": "Equipo Plataforma",
                "description": "Dockerización compartida",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        workspace_id = create_response.json()["id"]

        teammate = get_user_model().objects.create_user(
            username="teammate",
            password="test-pass-123",
            email="teammate@example.com",
        )
        member_response = self.client.post(
            reverse("core-api:workspace-member-create", args=[workspace_id]),
            {
                "identifier": teammate.username,
                "role": WorkspaceMembership.Role.EDITOR,
            },
        )
        self.assertEqual(member_response.status_code, 201)
        invitation_payload = member_response.json()
        self.assertEqual(invitation_payload["status"], WorkspaceInvitation.Status.PENDING)
        self.assertEqual(invitation_payload["delivery_status"], WorkspaceInvitation.DeliveryStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)

        list_response = self.client.get(reverse("core-api:workspace-list-create"))
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        created = next(item for item in payload if item["id"] == workspace_id)
        self.assertEqual(created["member_count"], 1)
        self.assertEqual(len(created["pending_invitations"]), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        AUTODOCKER_APP_BASE_URL="http://127.0.0.1:8000",
    )
    def test_invited_user_can_list_and_accept_workspace_invitation(self):
        workspace = Workspace.objects.create(
            owner=self.user,
            name="Equipo",
            slug="equipo-invitaciones",
            description="Compartido",
            visibility=Workspace.Visibility.TEAM,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=self.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        teammate = get_user_model().objects.create_user(
            username="teammate",
            password="test-pass-123",
            email="teammate@example.com",
        )
        create_response = self.client.post(
            reverse("core-api:workspace-member-create", args=[workspace.id]),
            {
                "identifier": teammate.email,
                "role": WorkspaceMembership.Role.EDITOR,
            },
        )
        invitation_id = create_response.json()["id"]

        self.client.force_login(teammate)
        incoming_response = self.client.get(reverse("core-api:workspace-invitation-list"))
        self.assertEqual(incoming_response.status_code, 200)
        self.assertEqual(len(incoming_response.json()), 1)

        accept_response = self.client.post(
            reverse("core-api:workspace-invitation-accept", args=[invitation_id])
        )
        self.assertEqual(accept_response.status_code, 200)
        invitation = WorkspaceInvitation.objects.get(pk=invitation_id)
        self.assertEqual(invitation.status, WorkspaceInvitation.Status.ACCEPTED)
        self.assertTrue(
            WorkspaceMembership.objects.filter(
                workspace=workspace,
                user=teammate,
                role=WorkspaceMembership.Role.EDITOR,
            ).exists()
        )

    def test_invited_user_can_decline_workspace_invitation(self):
        workspace = Workspace.objects.create(
            owner=self.user,
            name="Equipo Decline",
            slug="equipo-decline",
            description="Compartido",
            visibility=Workspace.Visibility.TEAM,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=self.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        teammate = get_user_model().objects.create_user(
            username="teammate-decline",
            password="test-pass-123",
            email="teammate-decline@example.com",
        )
        invitation = WorkspaceInvitation.objects.create(
            workspace=workspace,
            invited_by=self.user,
            invited_user=teammate,
            email=teammate.email,
            role=WorkspaceMembership.Role.VIEWER,
            delivery_status=WorkspaceInvitation.DeliveryStatus.IN_APP,
        )

        self.client.force_login(teammate)
        decline_response = self.client.post(
            reverse("core-api:workspace-invitation-decline", args=[invitation.id])
        )
        self.assertEqual(decline_response.status_code, 200)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, WorkspaceInvitation.Status.DECLINED)
        self.assertFalse(
            WorkspaceMembership.objects.filter(workspace=workspace, user=teammate).exists()
        )

    def test_workspace_viewer_can_view_analysis_but_cannot_validate(self):
        viewer, workspace, analysis, _artifact = self._build_workspace_analysis_for_viewer()

        self.client.force_login(viewer)
        detail_response = self.client.get(reverse("core-api:analysis-detail", args=[analysis.id]))
        self.assertEqual(detail_response.status_code, 200)

        validate_response = self.client.post(reverse("core-api:analysis-validate", args=[analysis.id]))
        self.assertEqual(validate_response.status_code, 403)

    def test_workspace_viewer_cannot_regenerate_analysis(self):
        viewer, _workspace, analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-regenerate"
        )

        self.client.force_login(viewer)
        response = self.client.post(
            reverse("core-api:analysis-regenerate", args=[analysis.id]),
            {"generation_profile": ProjectAnalysis.GenerationProfile.DEVELOPMENT},
        )

        self.assertEqual(response.status_code, 403)

    def test_workspace_viewer_cannot_edit_artifact(self):
        viewer, _workspace, _analysis, artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-artifact"
        )

        self.client.force_login(viewer)
        response = self.client.patch(
            reverse("core-api:artifact-detail", args=[artifact.id]),
            data=json.dumps({"content": "FROM node:22-alpine\n# viewer edit"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("core.api.views.PreviewService.stop")
    def test_workspace_viewer_cannot_stop_preview(self, mock_stop):
        viewer, _workspace, analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-preview-stop"
        )
        preview = PreviewRun.objects.create(
            owner=self.user,
            analysis=analysis,
            status=PreviewRun.Status.READY,
            runtime_kind=PreviewRun.RuntimeKind.COMPOSE,
            access_url="http://127.0.0.1:40123",
            ports={"web": ["http://127.0.0.1:40123"]},
        )

        self.client.force_login(viewer)
        response = self.client.post(reverse("core-api:preview-stop", args=[preview.id]))

        self.assertEqual(response.status_code, 403)
        mock_stop.assert_not_called()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview.RemotePreviewService.refresh_logs")
    def test_preview_detail_refreshes_remote_runner_logs_when_configured(self, mock_refresh_logs):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        preview = PreviewRun.objects.create(
            owner=self.user,
            analysis=analysis,
            status=PreviewRun.Status.RUNNING,
            runtime_kind=PreviewRun.RuntimeKind.COMPOSE,
            resource_names=["prv-demo-web"],
        )

        def fake_refresh(preview_run):
            preview_run.status = PreviewRun.Status.READY
            preview_run.access_url = "https://prv-demo.previews.example.com"
            preview_run.logs = "remote preview logs"
            preview_run.ports = {"web": ["https://prv-demo.previews.example.com"]}
            preview_run.save(
                update_fields=["status", "access_url", "logs", "ports", "updated_at"]
            )
            return preview_run

        mock_refresh_logs.side_effect = fake_refresh

        response = self.client.get(reverse("core-api:preview-detail", args=[preview.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.READY)
        self.assertEqual(payload["logs"], "remote preview logs")
        self.assertEqual(payload["access_url"], "https://prv-demo.previews.example.com")
        mock_refresh_logs.assert_called_once()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview.RemotePreviewService.refresh_logs", side_effect=PreviewRunnerError("runner 500"))
    def test_preview_detail_keeps_returning_preview_when_remote_runner_refresh_fails(self, mock_refresh_logs):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        preview = PreviewRun.objects.create(
            owner=self.user,
            analysis=analysis,
            status=PreviewRun.Status.RUNNING,
            runtime_kind=PreviewRun.RuntimeKind.COMPOSE,
            resource_names=["prv-demo-web"],
            logs="stale remote logs",
        )

        response = self.client.get(reverse("core-api:preview-detail", args=[preview.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.RUNNING)
        self.assertEqual(payload["logs"], "stale remote logs")
        mock_refresh_logs.assert_called_once()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview.RemotePreviewService.stop")
    def test_preview_stop_uses_remote_runner_backend_when_configured(self, mock_stop):
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            project_name="demo",
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url="https://github.com/acme/demo",
            status=ProjectAnalysis.Status.READY,
        )
        preview = PreviewRun.objects.create(
            owner=self.user,
            analysis=analysis,
            status=PreviewRun.Status.READY,
            runtime_kind=PreviewRun.RuntimeKind.COMPOSE,
            access_url="https://prv-demo.previews.example.com",
            ports={"web": ["https://prv-demo.previews.example.com"]},
            resource_names=["prv-demo-web"],
        )

        def fake_stop(preview_run):
            preview_run.status = PreviewRun.Status.STOPPED
            preview_run.logs = "remote preview stopped"
            preview_run.save(update_fields=["status", "logs", "updated_at"])
            return preview_run

        mock_stop.side_effect = fake_stop

        response = self.client.post(reverse("core-api:preview-stop", args=[preview.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.STOPPED)
        self.assertEqual(payload["logs"], "remote preview stopped")
        mock_stop.assert_called_once()

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview.RemotePreviewService.start")
    def test_preview_endpoint_accepts_smoke_fixture_analysis_for_remote_runner(self, mock_start):
        analysis = LocalPreviewSmokeService().prepare_analysis(
            owner=self.user,
            repository_url="https://github.com/acme/demo-app",
        )

        def fake_start(preview_run):
            preview_run.status = PreviewRun.Status.RUNNING
            preview_run.runtime_kind = PreviewRun.RuntimeKind.CONTAINER
            preview_run.logs = "runner accepted smoke fixture"
            preview_run.resource_names = ["adprv_smoke"]
            preview_run.save(
                update_fields=["status", "runtime_kind", "logs", "resource_names", "updated_at"]
            )
            return preview_run

        mock_start.side_effect = fake_start

        response = self.client.post(reverse("core-api:analysis-preview", args=[analysis.id]))

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], PreviewRun.Status.RUNNING)
        self.assertEqual(payload["logs"], "runner accepted smoke fixture")
        self.assertEqual(payload["resource_names"], ["adprv_smoke"])
        mock_start.assert_called_once()

    def test_workspace_viewer_cannot_start_preview(self):
        viewer, _workspace, analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-preview-start"
        )

        self.client.force_login(viewer)
        response = self.client.post(reverse("core-api:analysis-preview", args=[analysis.id]))

        self.assertEqual(response.status_code, 403)

    def test_workspace_viewer_cannot_create_pull_request(self):
        viewer, _workspace, analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-pr"
        )

        self.client.force_login(viewer)
        response = self.client.post(
            reverse("core-api:analysis-github-pr", args=[analysis.id]),
            {
                "access_token": "ghp_test_123",
                "base_branch": "main",
                "title": "Dockerize demo",
                "body": "Generated by tests",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_workspace_viewer_cannot_invite_members(self):
        viewer, workspace, _analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-member-create"
        )
        teammate = get_user_model().objects.create_user(
            username="viewer-member-target",
            password="test-pass-123",
        )

        self.client.force_login(viewer)
        response = self.client.post(
            reverse("core-api:workspace-member-create", args=[workspace.id]),
            {
                "identifier": teammate.username,
                "role": WorkspaceMembership.Role.EDITOR,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_workspace_viewer_cannot_remove_members(self):
        viewer, workspace, _analysis, _artifact = self._build_workspace_analysis_for_viewer(
            username="viewer-member-delete"
        )
        teammate = get_user_model().objects.create_user(
            username="viewer-removal-target",
            password="test-pass-123",
        )
        membership = WorkspaceMembership.objects.create(
            workspace=workspace,
            user=teammate,
            role=WorkspaceMembership.Role.EDITOR,
        )

        self.client.force_login(viewer)
        response = self.client.delete(
            reverse("core-api:workspace-member-detail", args=[workspace.id, membership.id])
        )

        self.assertEqual(response.status_code, 403)

    def test_legacy_plaintext_connection_token_can_still_be_read(self):
        connection = ExternalRepoConnection.objects.create(
            owner=self.user,
            provider=ExternalRepoConnection.Provider.GITHUB,
            label="legacy-gh",
            account_name="lucas",
            access_token="temporary-token",
        )
        ExternalRepoConnection.objects.filter(pk=connection.pk).update(access_token="legacy-plain-token")
        connection.refresh_from_db()

        self.assertEqual(connection.token_storage, "legacy-plain")
        self.assertEqual(connection.get_access_token(), "legacy-plain-token")


class PreviewRunnerApiTests(TestCase):
    preview_id = "11111111-1111-4111-8111-111111111111"
    analysis_id = "22222222-2222-4222-8222-222222222222"

    @override_settings(
        ROOT_URLCONF="config.runner_urls",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
        AUTODOCKER_ASYNC_MODE="inline",
    )
    @patch("core.runner_api.views.schedule_preview_runner_session")
    def test_runner_create_preview_creates_session(self, mock_schedule):
        response = self.client.post(
            "/previews",
            data=json.dumps(
                {
                    "preview_id": self.preview_id,
                    "analysis_id": self.analysis_id,
                    "project_name": "demo-app",
                    "bundle_url": "https://storage.example/bundles/preview.zip",
                    "bundle_sha256": "a" * 64,
                    "requested_ttl_seconds": 1800,
                    "metadata": {
                        "generation_profile": "production",
                        "components": [{"name": "web"}],
                        "services": ["postgres"],
                    },
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer preview-token",
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["preview_id"], self.preview_id)
        self.assertEqual(payload["status"], PreviewRunnerSession.Status.STARTING)
        session = PreviewRunnerSession.objects.get(preview_id=self.preview_id)
        self.assertEqual(session.project_name, "demo-app")
        self.assertEqual(session.metadata["generation_profile"], "production")
        mock_schedule.assert_called_once()

    @override_settings(
        ROOT_URLCONF="config.runner_urls",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
        AUTODOCKER_PREVIEW_RUNNER_MAX_ACTIVE_SESSIONS=2,
        AUTODOCKER_ASYNC_MODE="inline",
    )
    def test_runner_create_preview_rejects_when_active_capacity_is_exhausted(self):
        PreviewRunnerSession.objects.create(
            preview_id="33333333-3333-4333-8333-333333333333",
            analysis_id=self.analysis_id,
            project_name="demo-a",
            bundle_url="https://storage.example/bundles/a.zip",
            bundle_sha256="a" * 64,
            requested_ttl_seconds=1800,
            status=PreviewRunnerSession.Status.READY,
        )
        PreviewRunnerSession.objects.create(
            preview_id="44444444-4444-4444-8444-444444444444",
            analysis_id=self.analysis_id,
            project_name="demo-b",
            bundle_url="https://storage.example/bundles/b.zip",
            bundle_sha256="b" * 64,
            requested_ttl_seconds=1800,
            status=PreviewRunnerSession.Status.STARTING,
        )

        response = self.client.post(
            "/previews",
            data=json.dumps(
                {
                    "preview_id": self.preview_id,
                    "analysis_id": self.analysis_id,
                    "project_name": "demo-app",
                    "bundle_url": "https://storage.example/bundles/preview.zip",
                    "bundle_sha256": "c" * 64,
                    "requested_ttl_seconds": 1800,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer preview-token",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("límite de previews activas", response.json()["detail"])

    @override_settings(
        ROOT_URLCONF="config.runner_urls",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    def test_runner_endpoints_require_bearer_token(self):
        response = self.client.post(
            "/previews",
            data=json.dumps(
                {
                    "preview_id": self.preview_id,
                    "analysis_id": self.analysis_id,
                    "project_name": "demo-app",
                    "bundle_url": "https://storage.example/bundles/preview.zip",
                    "bundle_sha256": "a" * 64,
                    "requested_ttl_seconds": 1800,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(
        ROOT_URLCONF="config.runner_urls",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.runner_api.views.PreviewRunnerSessionService.refresh_logs")
    def test_runner_logs_endpoint_refreshes_session(self, mock_refresh_logs):
        session = PreviewRunnerSession.objects.create(
            preview_id=self.preview_id,
            analysis_id=self.analysis_id,
            project_name="demo-app",
            bundle_url="https://storage.example/bundles/preview.zip",
            bundle_sha256="a" * 64,
            requested_ttl_seconds=1800,
            status=PreviewRunnerSession.Status.STARTING,
        )

        def fake_refresh(target_session):
            target_session.logs = "runner logs"
            target_session.status = PreviewRunnerSession.Status.READY
            target_session.save(update_fields=["logs", "status", "updated_at"])
            return target_session

        mock_refresh_logs.side_effect = fake_refresh

        response = self.client.get(
            f"/previews/{self.preview_id}/logs",
            HTTP_AUTHORIZATION="Bearer preview-token",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preview_id"], self.preview_id)
        self.assertEqual(payload["logs"], "runner logs")
        mock_refresh_logs.assert_called_once_with(session)

    @override_settings(
        ROOT_URLCONF="config.runner_urls",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.runner_api.views.PreviewRunnerSessionService.stop")
    def test_runner_stop_endpoint_stops_session(self, mock_stop):
        session = PreviewRunnerSession.objects.create(
            preview_id=self.preview_id,
            analysis_id=self.analysis_id,
            project_name="demo-app",
            bundle_url="https://storage.example/bundles/preview.zip",
            bundle_sha256="a" * 64,
            requested_ttl_seconds=1800,
            status=PreviewRunnerSession.Status.READY,
            runtime_kind=PreviewRunnerSession.RuntimeKind.COMPOSE,
            access_url="https://prv-demo.previews.example.com",
            resource_names=["prv-demo-web"],
        )

        def fake_stop(target_session):
            target_session.status = PreviewRunnerSession.Status.STOPPED
            target_session.save(update_fields=["status", "updated_at"])
            return target_session

        mock_stop.side_effect = fake_stop

        response = self.client.post(
            f"/previews/{self.preview_id}/stop",
            HTTP_AUTHORIZATION="Bearer preview-token",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preview_id"], self.preview_id)
        self.assertEqual(payload["status"], PreviewRunnerSession.Status.STOPPED)
        mock_stop.assert_called_once_with(session)
