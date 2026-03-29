from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from config import settings as project_settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

import core.services.build_validation as build_validation_module
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
from core.services.build_validation import BuildValidationResult
from core.services.detector import StackDetector
from core.services.generator import ArtifactGenerator
from core.services.github_pr import GitHubPullRequestResult
from core.services.healthchecks import HealthcheckPlannerService
from core.services.ingestion import cleanup_workspace, prepare_source_workspace
from core.services.preview import PreviewService
from core.services.runtime import CommandExecutionError, docker_compose_command
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
        self.assertNotContains(response, "<form")

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
        self.assertIn("correctos", payload["detail"])

    def test_signup_page_renders(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crear cuenta")
        self.assertContains(response, "Registro gratuito")

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
            "No se pudo guardar el archivo subido. Revisá la configuración del storage de media o del volumen local.",
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
        build_validation_module.RemoteValidationService = SimpleNamespace()
        try:
            local_validation_result = SimpleNamespace(
                success=True,
                logs="local ok",
                to_dict=lambda: {
                    "success": True,
                    "command": ["docker", "build", "."],
                    "logs": "local ok",
                    "image_tag": "autodocker-test",
                    "executor": "local",
                },
            )
            with patch("core.services.build_validation.ensure_runtime_jobs_enabled"), patch(
                "core.services.build_validation.ensure_docker_runtime_access"
            ), patch(
                "core.services.build_validation.docker_command",
                return_value=["docker"],
            ), patch(
                "core.services.build_validation.run_command",
                return_value=SimpleNamespace(output="local ok"),
            ), patch(
                "core.services.build_validation.BuildValidationResult",
                return_value=local_validation_result,
            ), patch(
                "core.services.build_validation.RemoteValidationService.validate",
                create=True,
                return_value=BuildValidationResult(
                    success=True,
                    command=["remote", "validate"],
                    logs="remote ok",
                    image_tag="",
                ),
            ):
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
            self.assertIn("executor", payload["result_payload"])
            self.assertEqual(payload["result_payload"]["executor"], "github_actions")
        finally:
            delattr(build_validation_module, "RemoteValidationService")

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
        serialized = result.to_dict()
        self.assertEqual(serialized["result_payload"]["executor"], "github_actions")
        self.assertEqual(serialized["metadata"]["workflow_run_id"], 123)

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
        self.assertEqual(payload["kind"], ExecutionJob.Kind.PREVIEW)
        self.assertEqual(payload["status"], ExecutionJob.Status.READY)
        self.assertEqual(payload["result_payload"]["status"], PreviewRun.Status.READY)

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
