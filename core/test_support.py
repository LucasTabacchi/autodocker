from __future__ import annotations

import io
import json
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import GeneratedArtifact, ProjectAnalysis, Workspace, WorkspaceMembership


class AnalysisApiTestSupport:
    user = None
    client = None

    def _build_workspace_analysis_for_viewer(self, *, username="viewer"):
        viewer = get_user_model().objects.create_user(
            username=username,
            password="test-pass-123",
        )
        workspace = Workspace.objects.create(
            owner=self.user,
            name=f"Equipo {username}",
            slug=f"equipo-{username}",
            description="Compartido",
            visibility=Workspace.Visibility.TEAM,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=self.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=viewer,
            role=WorkspaceMembership.Role.VIEWER,
        )
        analysis = ProjectAnalysis.objects.create(
            owner=self.user,
            workspace=workspace,
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
        artifact = GeneratedArtifact.objects.get(analysis=analysis, path="Dockerfile")
        return viewer, workspace, analysis, artifact

    def _post_analysis(self, *, files: dict[str, str], generation_profile: str | None = None):
        archive = SimpleUploadedFile(
            "project.zip",
            self._build_zip(files),
            content_type="application/zip",
        )
        payload = {"project_name": "next-sample", "archive": archive}
        if generation_profile:
            payload["generation_profile"] = generation_profile
        return self.client.post(reverse("core-api:analysis-list-create"), payload)

    def _build_zip(self, files: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path, content in files.items():
                if isinstance(content, (dict, list)):
                    content = json.dumps(content)
                zipped.writestr(path, content)
        return buffer.getvalue()
