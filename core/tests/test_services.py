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
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from core.models import (
    ArtifactSnapshot,
    ExecutionJob,
    ExternalRepoConnection,
    GeneratedArtifact,
    PreviewRun,
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
from core.services.validation_bundle import ValidationBundleService
from core.services.execution_runner import ExecutionJobRunner
from core.services.preview import PreviewService
from core.services.preview_bundle import PreviewBundleService
from core.services.preview_runner import PreviewRunnerClient, PreviewRunnerError
from core.services.runtime import (
    CommandExecutionError,
    docker_compose_command,
    preview_runtime_capability,
)
from core.test_support import AnalysisApiTestSupport



class StackDetectorTests(SimpleTestCase):
    def test_detects_nextjs_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            result = StackDetector().analyze(root)
            component = result.primary_component()

            self.assertIsNotNone(component)
            self.assertEqual(component.framework, "Next.js")
            self.assertEqual(component.package_manager, "npm")
            self.assertEqual(component.probable_ports, [3000])

    def test_detects_fastapi_env_vars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            (root / "main.py").write_text(
                """
                import os
                from fastapi import FastAPI

                app = FastAPI()
                port = int(os.getenv("PORT", "9000"))
                """,
                encoding="utf-8",
            )
            result = StackDetector().analyze(root)
            component = result.primary_component()

            self.assertEqual(component.framework, "FastAPI")
            self.assertIn("PORT", component.environment_variables)
            self.assertIn(9000, component.probable_ports)


class DatabaseConfigTests(SimpleTestCase):
    def test_database_url_keeps_supported_postgres_options(self):
        database_url = (
            "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
            "?sslmode=require&connect_timeout=10&application_name=autodocker&pool_mode=session"
        )

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": database_url,
                "DJANGO_USE_SQLITE": "false",
            },
            clear=True,
        ), patch.object(project_settings.sys, "argv", ["manage.py"]):
            config = project_settings.database_config()

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["HOST"], "db.example.supabase.co")
        self.assertEqual(config["NAME"], "postgres")
        self.assertEqual(
            config["OPTIONS"],
            {
                "sslmode": "require",
                "connect_timeout": "10",
                "application_name": "autodocker",
            },
        )

    def test_validation_backend_env_defaults_to_local(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(project_settings.env("AUTODOCKER_VALIDATION_BACKEND", "local"), "local")

    def test_preview_backend_env_defaults_to_local(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(project_settings.env("AUTODOCKER_PREVIEW_BACKEND", "local"), "local")


class RenderHostConfigTests(SimpleTestCase):
    def test_render_external_url_adds_hostname_and_csrf_origin(self):
        with patch.dict(
            os.environ,
            {
                "RENDER_EXTERNAL_URL": "https://autodocker-web.onrender.com",
            },
            clear=True,
        ):
            allowed_hosts, csrf_trusted_origins = project_settings.render_host_config()

        self.assertIn("autodocker-web.onrender.com", allowed_hosts)
        self.assertIn("https://autodocker-web.onrender.com", csrf_trusted_origins)

    def test_render_external_hostname_takes_priority_when_present(self):
        with patch.dict(
            os.environ,
            {
                "RENDER_EXTERNAL_HOSTNAME": "autodocker-web.onrender.com",
                "RENDER_EXTERNAL_URL": "https://ignored.onrender.com",
            },
            clear=True,
        ):
            allowed_hosts, csrf_trusted_origins = project_settings.render_host_config()

        self.assertIn("autodocker-web.onrender.com", allowed_hosts)
        self.assertIn("https://autodocker-web.onrender.com", csrf_trusted_origins)


class DeploymentContractTests(SimpleTestCase):
    def test_dockerfile_uses_supported_python_runtime(self):
        dockerfile = (project_settings.BASE_DIR / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.13-slim AS builder", dockerfile)
        self.assertIn("FROM python:3.13-slim AS runner", dockerfile)

    def test_render_yaml_runs_migrate_at_startup_not_in_build(self):
        render_yaml = (project_settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("python3 -m pip install --upgrade pip", render_yaml)
        self.assertIn("python3 manage.py collectstatic --noinput", render_yaml)
        self.assertNotIn("migrate --noinput", render_yaml.split("buildCommand:", maxsplit=1)[1].split("startCommand:", maxsplit=1)[0])
        self.assertIn("${VENV_ROOT}/bin/python manage.py migrate --noinput", render_yaml)
        self.assertIn("${VENV_ROOT}/bin/gunicorn config.wsgi:application", render_yaml)

    def test_deployment_role_is_normalized(self):
        with patch.dict(
            os.environ,
            {
                "AUTODOCKER_DEPLOYMENT_ROLE": " PREVIEW_RUNNER ",
            },
            clear=True,
        ):
            self.assertEqual(project_settings.deployment_role(), "preview_runner")


class BuildValidationServiceTests(SimpleTestCase):
    @override_settings(AUTODOCKER_VALIDATION_BACKEND="github_actions")
    def test_backend_name_uses_overridden_remote_backend(self):
        self.assertEqual(BuildValidationService().backend_name, "github_actions")


class MediaStorageConfigTests(SimpleTestCase):
    def test_uses_supabase_s3_backend_when_storage_env_is_present(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_STORAGE_BUCKET": "autodocker-media",
                "SUPABASE_STORAGE_S3_ENDPOINT_URL": "https://example.supabase.co/storage/v1/s3",
                "SUPABASE_STORAGE_ACCESS_KEY_ID": "storage-access-key",
                "SUPABASE_STORAGE_SECRET_ACCESS_KEY": "storage-secret",
                "SUPABASE_STORAGE_S3_REGION": "us-east-1",
            },
            clear=True,
        ):
            config = project_settings.media_storage_config()

        self.assertEqual(config["BACKEND"], "storages.backends.s3.S3Storage")
        self.assertEqual(config["OPTIONS"]["bucket_name"], "autodocker-media")
        self.assertEqual(
            config["OPTIONS"]["endpoint_url"],
            "https://example.supabase.co/storage/v1/s3",
        )
        self.assertTrue(config["OPTIONS"]["querystring_auth"])


class RemoteArchiveIngestionTests(SimpleTestCase):
    def test_prepare_source_workspace_reads_zip_from_storage_stream(self):
        class RemoteArchiveFile:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.open_calls = 0

            def open(self, mode: str = "rb"):
                self.open_calls += 1
                return io.BytesIO(self.payload)

            @property
            def path(self):
                raise AssertionError("prepare_source_workspace should not require archive.path")

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr("next-sample/package.json", '{"name": "next-sample"}')

        analysis = SimpleNamespace(
            source_type=ProjectAnalysis.SourceType.ZIP,
            archive=RemoteArchiveFile(archive_buffer.getvalue()),
            repository_url="",
        )

        temp_dir = None
        try:
            temp_dir, source_root = prepare_source_workspace(analysis, prefix="autodocker-test-")
            self.assertEqual(analysis.archive.open_calls, 1)
            self.assertTrue((source_root / "package.json").exists())
        finally:
            if temp_dir is not None:
                cleanup_workspace(temp_dir)

    def _build_ready_zip_analysis(
        self,
        *,
        files: dict[str, str],
        artifacts: list[GeneratedArtifactSpec] | None = None,
    ):
        class RemoteArchiveFile:
            def __init__(self, payload: bytes):
                self.payload = payload

            def open(self, mode: str = "rb"):
                return io.BytesIO(self.payload)

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path, content in files.items():
                zipped.writestr(path, content)

        return SimpleNamespace(
            source_type=ProjectAnalysis.SourceType.ZIP,
            archive=RemoteArchiveFile(archive_buffer.getvalue()),
            repository_url="",
            artifacts=SimpleNamespace(all=lambda: list(artifacts or [])),
        )

    def _build_ready_git_analysis(
        self,
        *,
        files: dict[str, str],
        artifacts: list[GeneratedArtifactSpec] | None = None,
    ):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo_root = Path(temp_dir.name)
        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.com"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "AutoDocker Tests"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        for path, content in files.items():
            file_path = repo_root / path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

        return SimpleNamespace(
            source_type=ProjectAnalysis.SourceType.GIT,
            archive=None,
            repository_url=str(repo_root),
            artifacts=SimpleNamespace(all=lambda: list(artifacts or [])),
        )

    def test_validation_bundle_service_builds_bundle_from_zip_analysis(self):
        analysis = self._build_ready_zip_analysis(
            files={
                "package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                "src/index.js": "console.log('zip analysis');",
            }
        )

        bundle = ValidationBundleService().build(analysis)
        try:
            self.assertTrue(bundle.bundle_path.exists())
            self.assertTrue(bundle.bundle_path.is_file())
            self.assertEqual(len(bundle.sha256), 64)
            self.assertGreater(bundle.bundle_size_bytes, 0)

            with zipfile.ZipFile(bundle.bundle_path) as zipped:
                self.assertIn("package.json", zipped.namelist())
                self.assertIn("src/index.js", zipped.namelist())
        finally:
            cleanup_workspace(bundle.workspace_root)

    def test_validation_bundle_service_overlays_generated_artifacts(self):
        analysis = self._build_ready_git_analysis(
            files={
                "package.json": json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                "src/server.js": "console.log('git analysis');",
            },
            artifacts=[
                GeneratedArtifactSpec(
                    kind="dockerfile",
                    path="Dockerfile",
                    content="FROM node:22-alpine\nRUN echo remote bundle",
                    description="Dockerfile",
                ),
                GeneratedArtifactSpec(
                    kind="compose",
                    path="docker-compose.yml",
                    content="services:\n  app:\n    build: .\n",
                    description="Compose",
                ),
            ],
        )

        bundle = ValidationBundleService().build(analysis)
        try:
            with zipfile.ZipFile(bundle.bundle_path) as zipped:
                self.assertEqual(
                    zipped.read("Dockerfile").decode("utf-8").replace("\r\n", "\n"),
                    "FROM node:22-alpine\nRUN echo remote bundle",
                )
                self.assertEqual(
                    zipped.read("docker-compose.yml").decode("utf-8").replace("\r\n", "\n"),
                    "services:\n  app:\n    build: .\n",
                )
        finally:
            cleanup_workspace(bundle.workspace_root)

    def test_preview_bundle_service_overlays_generated_artifacts(self):
        analysis = self._build_ready_git_analysis(
            files={
                "package.json": json.dumps(
                    {
                        "name": "preview-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                "src/server.js": "console.log('preview analysis');",
            },
            artifacts=[
                GeneratedArtifactSpec(
                    kind="dockerfile",
                    path="Dockerfile",
                    content="FROM node:22-alpine\nRUN echo preview bundle",
                    description="Dockerfile",
                ),
                GeneratedArtifactSpec(
                    kind="compose",
                    path="docker-compose.yml",
                    content="services:\n  app:\n    build: .\n",
                    description="Compose",
                ),
            ],
        )

        bundle = PreviewBundleService().build(analysis)
        try:
            with zipfile.ZipFile(bundle.bundle_path) as zipped:
                self.assertEqual(
                    zipped.read("Dockerfile").decode("utf-8").replace("\r\n", "\n"),
                    "FROM node:22-alpine\nRUN echo preview bundle",
                )
                self.assertEqual(
                    zipped.read("docker-compose.yml").decode("utf-8").replace("\r\n", "\n"),
                    "services:\n  app:\n    build: .\n",
                )
        finally:
            cleanup_workspace(bundle.workspace_root)


class AdditionalDetectorCoverageTests(SimpleTestCase):
    def test_detects_express_health_endpoint_and_env_fallback_port(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "api",
                        "scripts": {"start": "node server.js"},
                        "dependencies": {"express": "5.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "server.js").write_text(
                """
                const express = require("express");
                const app = express();
                const port = Number(process.env.PORT || 4000);
                app.get("/health", (_req, res) => res.json({ ok: true }));
                app.listen(port);
                """,
                encoding="utf-8",
            )

            result = StackDetector().analyze(root)
            component = result.primary_component()

            self.assertEqual(component.framework, "Express")
            self.assertIn(4000, component.probable_ports)
            self.assertEqual(component.healthcheck_path, "/health")

    def test_detects_workspace_fullstack_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps({"name": "mono", "workspaces": ["apps/*"]}),
                encoding="utf-8",
            )
            web = root / "apps" / "web"
            api = root / "apps" / "api"
            web.mkdir(parents=True)
            api.mkdir(parents=True)
            (web / "package.json").write_text(
                json.dumps(
                    {
                        "name": "web",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (api / "package.json").write_text(
                json.dumps(
                    {
                        "name": "api",
                        "scripts": {"start": "node server.js"},
                        "dependencies": {"express": "5.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            result = StackDetector().analyze(root)

            self.assertEqual(result.project_type, "fullstack")
            self.assertEqual(len(result.components), 2)
            self.assertEqual({component.role for component in result.components}, {"frontend", "backend"})

    def test_ignores_root_workspace_orchestrator_component(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "mono",
                        "private": True,
                        "workspaces": ["apps/*"],
                        "scripts": {
                            "dev:web": "npm --workspace web run dev",
                            "dev:api": "npm --workspace api run dev",
                            "build:web": "npm --workspace web run build",
                            "start:api": "npm --workspace api run start",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps({"name": "mono", "lockfileVersion": 3, "packages": {"": {}}}),
                encoding="utf-8",
            )
            web = root / "apps" / "web"
            api = root / "apps" / "api"
            web.mkdir(parents=True)
            api.mkdir(parents=True)
            (web / "package.json").write_text(
                json.dumps(
                    {
                        "name": "web",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (api / "package.json").write_text(
                json.dumps(
                    {
                        "name": "api",
                        "scripts": {"start": "node server.js"},
                        "dependencies": {"express": "5.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            result = StackDetector().analyze(root)

            self.assertEqual({component.name for component in result.components}, {"web", "api"})
            self.assertNotIn(".", {component.path for component in result.components})


class ArtifactGeneratorTests(SimpleTestCase):
    def _build_monorepo_fixture(self, root: Path) -> None:
        (root / "package.json").write_text(
            json.dumps(
                {
                    "name": "autodocker-monorepo-demo",
                    "private": True,
                    "workspaces": ["apps/*"],
                }
            ),
            encoding="utf-8",
        )
        (root / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "autodocker-monorepo-demo",
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {
                        "": {"workspaces": ["apps/*"]},
                        "apps/web": {"dependencies": {"next": "16.2.1"}},
                        "apps/api": {"dependencies": {"express": "5.1.0"}},
                    },
                }
            ),
            encoding="utf-8",
        )
        web = root / "apps" / "web"
        api = root / "apps" / "api"
        web.mkdir(parents=True)
        api.mkdir(parents=True)
        (web / "package.json").write_text(
            json.dumps(
                {
                    "name": "web",
                    "private": True,
                    "scripts": {
                        "dev": "next dev",
                        "build": "next build",
                        "start": "next start",
                    },
                    "dependencies": {"next": "16.2.1", "react": "19.2.4", "react-dom": "19.2.4"},
                }
            ),
            encoding="utf-8",
        )
        (api / "package.json").write_text(
            json.dumps(
                {
                    "name": "api",
                    "private": True,
                    "scripts": {
                        "dev": "node --watch server.js",
                        "start": "node server.js",
                    },
                    "dependencies": {"express": "5.1.0"},
                }
            ),
            encoding="utf-8",
        )

    def test_generates_compose_for_postgres_dependency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").write_text(
                "django\npsycopg2-binary\ngunicorn\n",
                encoding="utf-8",
            )
            (root / "manage.py").write_text("print('manage')", encoding="utf-8")

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(detection)
            compose = next(
                artifact for artifact in generation.artifacts if artifact.kind == "compose"
            )

            self.assertIn("postgres:", compose.content)

    def test_guide_uses_detected_port_for_single_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(detection)
            guide = next(
                artifact for artifact in generation.artifacts if artifact.kind == "guide"
            )

            self.assertIn("docker run --rm -p 3000:3000", guide.content)

    def test_development_profile_uses_dev_command_for_nextjs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {
                            "dev": "next dev",
                            "build": "next build",
                            "start": "next start",
                        },
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(
                detection,
                profile=ArtifactGenerator.DEVELOPMENT,
            )
            dockerfile = next(
                artifact for artifact in generation.artifacts if artifact.kind == "dockerfile"
            )

            self.assertIn("ENV NODE_ENV=development", dockerfile.content)
            self.assertIn("npm run dev", dockerfile.content)

    def test_development_profile_adds_bind_mounts_to_compose(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").write_text(
                "django\npsycopg2-binary\ngunicorn\n",
                encoding="utf-8",
            )
            (root / "manage.py").write_text("print('manage')", encoding="utf-8")

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(
                detection,
                profile=ArtifactGenerator.DEVELOPMENT,
            )
            compose = next(
                artifact for artifact in generation.artifacts if artifact.kind == "compose"
            )

            self.assertIn("volumes:", compose.content)
            self.assertIn("AUTODOCKER_PROFILE", compose.content)

    def test_generator_embeds_healthcheck_and_extra_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "next-sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )

            detection = StackDetector().analyze(root)
            healthchecks = HealthcheckPlannerService().plan(detection)
            generation = ArtifactGenerator().generate(
                detection,
                healthchecks={item.component_path: item.to_dict() for item in healthchecks.items},
            )

            dockerfile = next(
                artifact for artifact in generation.artifacts if artifact.kind == "dockerfile"
            )
            self.assertIn("HEALTHCHECK", dockerfile.content)

    def test_compose_healthcheck_uses_cmd_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "requirements.txt").write_text(
                "django\npsycopg2-binary\ngunicorn\n",
                encoding="utf-8",
            )
            (root / "manage.py").write_text("print('manage')", encoding="utf-8")

            detection = StackDetector().analyze(root)
            healthchecks = HealthcheckPlannerService().plan(detection)
            generation = ArtifactGenerator().generate(
                detection,
                healthchecks={item.component_path: item.to_dict() for item in healthchecks.items},
            )

            compose = next(
                artifact for artifact in generation.artifacts if artifact.kind == "compose"
            )
            self.assertIn('        - "CMD"', compose.content)

    def test_monorepo_node_components_use_root_build_context_in_compose(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_monorepo_fixture(root)

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(detection)
            compose = next(
                artifact for artifact in generation.artifacts if artifact.kind == "compose"
            )

            self.assertIn("      context: .", compose.content)
            self.assertIn("      dockerfile: apps/web/Dockerfile", compose.content)
            self.assertIn("      dockerfile: apps/api/Dockerfile", compose.content)

    def test_compose_uses_detected_port_as_default_port_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            api = root / "apps" / "api"
            api.mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"name": "mono", "private": True, "workspaces": ["apps/*"]}),
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps({"name": "mono", "lockfileVersion": 3, "packages": {"": {}}}),
                encoding="utf-8",
            )
            (api / "package.json").write_text(
                json.dumps(
                    {
                        "name": "api",
                        "scripts": {"start": "node server.js"},
                        "dependencies": {"express": "5.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (api / "server.js").write_text(
                """
                const express = require("express");
                const app = express();
                const port = Number(process.env.PORT || 4000);
                const redisUrl = process.env.REDIS_URL || null;
                app.get("/health", (_req, res) => res.json({ ok: true }));
                app.listen(port);
                """,
                encoding="utf-8",
            )

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(detection)
            compose = next(artifact for artifact in generation.artifacts if artifact.kind == "compose")

            self.assertIn('PORT: "4000"', compose.content)

    def test_monorepo_node_dockerfile_scopes_commands_to_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_monorepo_fixture(root)

            detection = StackDetector().analyze(root)
            generation = ArtifactGenerator().generate(detection)
            web_dockerfile = next(
                artifact
                for artifact in generation.artifacts
                if artifact.kind == "dockerfile" and artifact.path == "apps/web/Dockerfile"
            )

            self.assertIn("COPY package.json ./package.json", web_dockerfile.content)
            self.assertIn("COPY apps/api/package.json ./apps/api/package.json", web_dockerfile.content)
            self.assertIn("COPY apps/web/package.json ./apps/web/package.json", web_dockerfile.content)
            self.assertIn("RUN npm run build --workspace apps/web", web_dockerfile.content)
            self.assertIn('CMD ["sh", "-c", "npm run start --workspace apps/web"]', web_dockerfile.content)


class RuntimeCommandTests(SimpleTestCase):
    @patch("core.services.runtime.shutil.which")
    @patch("core.services.runtime.subprocess.run")
    def test_prefers_docker_compose_plugin_when_available(self, mock_run, mock_which):
        mock_which.side_effect = lambda name: {"docker": "docker-path"}.get(name)
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "version"],
            returncode=0,
            stdout="Docker Compose version v2.0.0",
            stderr="",
        )

        self.assertEqual(docker_compose_command(), ["docker", "compose"])

    @patch("core.services.runtime.shutil.which")
    @patch("core.services.runtime.subprocess.run")
    def test_falls_back_to_docker_compose_binary(self, mock_run, mock_which):
        def which(name):
            return {
                "docker": "docker-path",
                "docker-compose": "docker-compose-path",
            }.get(name)

        mock_which.side_effect = which
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "version"],
            returncode=1,
            stdout="",
            stderr="docker: 'compose' is not a docker command.",
        )

        self.assertEqual(docker_compose_command(), ["docker-compose"])

    @patch("core.services.runtime.shutil.which", return_value=None)
    def test_raises_when_no_docker_runtime_is_available(self, _mock_which):
        with self.assertRaisesMessage(CommandExecutionError, "No se encontró el binario requerido: docker"):
            docker_compose_command()


class PreviewRuntimeCapabilityTests(SimpleTestCase):
    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    def test_remote_runner_backend_is_available_without_local_docker_runtime(self):
        capability = preview_runtime_capability()

        self.assertTrue(capability["enabled"])
        self.assertEqual(capability["backend"], "remote_runner")
        self.assertEqual(capability["reason"], "")

    @override_settings(
        AUTODOCKER_ENABLE_RUNTIME_JOBS=False,
        AUTODOCKER_PREVIEW_BACKEND="remote_runner",
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="",
    )
    def test_remote_runner_backend_requires_runner_configuration(self):
        capability = preview_runtime_capability()

        self.assertFalse(capability["enabled"])
        self.assertEqual(capability["backend"], "remote_runner")
        self.assertIn("AUTODOCKER_PREVIEW_RUNNER_BASE_URL", capability["reason"])
        self.assertIn("AUTODOCKER_PREVIEW_RUNNER_TOKEN", capability["reason"])


class PreviewRunnerClientTests(SimpleTestCase):
    class _FakeResponse:
        def __init__(self, payload: dict):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.payload

    @override_settings(
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal/",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
        AUTODOCKER_PREVIEW_RUNNER_REQUEST_TIMEOUT=45,
    )
    @patch("core.services.preview_runner.request.urlopen")
    def test_create_preview_posts_expected_payload(self, mock_urlopen):
        mock_urlopen.return_value = self._FakeResponse(
            {
                "preview_id": "preview-123",
                "status": "starting",
                "runtime_kind": "compose",
                "access_url": "",
                "resource_names": ["prv-demo-web"],
                "expires_at": "2026-03-29T18:35:00Z",
            }
        )

        client = PreviewRunnerClient()
        response = client.create_preview(
            preview_id="preview-123",
            analysis_id="analysis-456",
            project_name="demo-app",
            bundle_url="https://storage.example/bundles/preview.zip",
            bundle_sha256="a" * 64,
            requested_ttl_seconds=1800,
            metadata={"generation_profile": "production"},
        )

        self.assertEqual(response["status"], "starting")
        request_obj = mock_urlopen.call_args.args[0]
        self.assertEqual(request_obj.full_url, "https://preview-runner.internal/previews")
        self.assertEqual(request_obj.get_method(), "POST")
        self.assertEqual(request_obj.headers["Authorization"], "Bearer preview-token")
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(payload["preview_id"], "preview-123")
        self.assertEqual(payload["analysis_id"], "analysis-456")
        self.assertEqual(payload["requested_ttl_seconds"], 1800)
        self.assertEqual(payload["metadata"]["generation_profile"], "production")

    @override_settings(
        AUTODOCKER_PREVIEW_RUNNER_BASE_URL="https://preview-runner.internal",
        AUTODOCKER_PREVIEW_RUNNER_TOKEN="preview-token",
    )
    @patch("core.services.preview_runner.request.urlopen")
    def test_stop_preview_raises_controlled_error_on_http_failure(self, mock_urlopen):
        mock_urlopen.side_effect = error.HTTPError(
            url="https://preview-runner.internal/previews/preview-123/stop",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"runner unavailable"}'),
        )

        with self.assertRaisesMessage(PreviewRunnerError, "runner unavailable"):
            PreviewRunnerClient().stop_preview("preview-123")


class PreviewServiceTests(SimpleTestCase):
    def test_preview_override_remaps_shared_service_ports_without_exposing_them(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docker-compose.yml").write_text(
                "\n".join(
                    [
                        "services:",
                        "  web:",
                        "    ports:",
                        '      - "3000:3000"',
                        "  api:",
                        "    ports:",
                        '      - "3001:3000"',
                        "  postgres:",
                        "    ports:",
                        '      - "5432:5432"',
                        "  redis:",
                        "    ports:",
                        '      - "6379:6379"',
                    ]
                ),
                encoding="utf-8",
            )
            analysis = SimpleNamespace(
                analysis_payload={
                    "components": [
                        {"name": "web"},
                        {"name": "api"},
                    ]
                },
                services=["postgres", "redis"],
            )

            service = PreviewService()
            with patch.object(
                service,
                "_free_port",
                side_effect=[41000, 41001, 41002, 41003],
            ):
                override_path, service_urls = service._write_preview_override(root, analysis)

            override = yaml.safe_load(override_path.read_text(encoding="utf-8"))
            self.assertEqual(
                override["services"]["postgres"]["ports"],
                ["41002:5432"],
            )
            self.assertEqual(
                override["services"]["redis"]["ports"],
                ["41003:6379"],
            )
            self.assertEqual(service_urls["web"], ["http://127.0.0.1:41000"])
            self.assertEqual(service_urls["api"], ["http://127.0.0.1:41001"])
            self.assertNotIn("postgres", service_urls)
            self.assertNotIn("redis", service_urls)

    def test_filter_accessible_service_urls_keeps_only_running_and_healthy_targets(self):
        service = PreviewService()
        preview_run = SimpleNamespace(id="preview-1234")
        service_urls = {
            "web": ["http://127.0.0.1:34229"],
            "api": ["http://127.0.0.1:53185"],
            "app": ["http://127.0.0.1:48259"],
        }

        with patch.object(
            service,
            "_compose_service_states",
            return_value={
                "web": {"state": "running", "health": "healthy", "status": "Up (healthy)"},
                "api": {"state": "running", "health": "unhealthy", "status": "Up (unhealthy)"},
                "app": {"state": "exited", "health": "", "status": "Exited (1)"},
            },
        ):
            filtered = service._filter_accessible_service_urls(
                Path("C:/tmp"),
                preview_run,
                "autodocker.preview.compose.yml",
                service_urls,
                {"web", "api", "app"},
            )

        self.assertEqual(filtered, {"web": ["http://127.0.0.1:34229"]})

    def test_filter_accessible_service_urls_excludes_healthchecked_services_while_starting(self):
        service = PreviewService()
        preview_run = SimpleNamespace(id="preview-1234")
        with patch.object(
            service,
            "_compose_service_states",
            return_value={
                "web": {"state": "running", "health": "starting", "status": "Up (health: starting)"},
            },
        ):
            filtered = service._filter_accessible_service_urls(
                Path("C:/tmp"),
                preview_run,
                "autodocker.preview.compose.yml",
                {"web": ["http://127.0.0.1:34229"]},
                {"web"},
            )

        self.assertEqual(filtered, {})

    def test_build_preview_notes_reports_only_hidden_candidate_services(self):
        service = PreviewService()
        preview_run = SimpleNamespace(id="preview-1234")
        with patch.object(
            service,
            "_compose_service_states",
            return_value={
                "web": {"state": "running", "health": "healthy", "status": "Up (healthy)"},
                "api": {"state": "running", "health": "unhealthy", "status": "Up (unhealthy)"},
                "app": {"state": "exited", "health": "", "status": "Exited (1)"},
                "postgres": {"state": "running", "health": "", "status": "Up"},
            },
        ):
            notes = service._build_preview_notes(
                Path("C:/tmp"),
                preview_run,
                "autodocker.preview.compose.yml",
                {
                    "web": ["http://127.0.0.1:34229"],
                    "api": ["http://127.0.0.1:53185"],
                    "app": ["http://127.0.0.1:48259"],
                },
                {"web": ["http://127.0.0.1:34229"]},
            )

        self.assertIn("- api: Up (unhealthy)", notes)
        self.assertIn("- app: Exited (1)", notes)
        self.assertNotIn("postgres", notes)


