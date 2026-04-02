from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model

from core.models import GeneratedArtifact, ProjectAnalysis


@dataclass(frozen=True)
class LocalPreviewSmokeFixture:
    username: str
    password: str
    analysis: ProjectAnalysis


class LocalPreviewSmokeService:
    DEFAULT_USERNAME = "local-preview-smoke"
    DEFAULT_PASSWORD = "test-pass-123"

    def prepare_analysis(
        self,
        *,
        owner,
        repository_url: str,
        project_name: str | None = None,
        use_repo_artifacts: bool = False,
    ) -> ProjectAnalysis:
        analysis = ProjectAnalysis.objects.create(
            owner=owner,
            project_name=(project_name or self._project_name_from_repository_url(repository_url)),
            source_type=ProjectAnalysis.SourceType.GIT,
            repository_url=repository_url,
            status=ProjectAnalysis.Status.READY,
            generation_profile=ProjectAnalysis.GenerationProfile.PRODUCTION,
            analysis_payload={
                "components": [{"name": "web", "path": ".", "framework": "Node.js"}],
            },
            services=[],
        )
        if not use_repo_artifacts:
            self._create_injected_artifacts(analysis)
        return analysis

    def ensure_fixture(
        self,
        *,
        repository_url: str,
        project_name: str | None = None,
        username: str | None = None,
        password: str | None = None,
        use_repo_artifacts: bool = False,
    ) -> LocalPreviewSmokeFixture:
        username = (username or self.DEFAULT_USERNAME).strip()
        password = password or self.DEFAULT_PASSWORD
        user_model = get_user_model()
        user, _created = user_model.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com"},
        )
        user.set_password(password)
        user.save(update_fields=["password"])
        analysis = self.prepare_analysis(
            owner=user,
            repository_url=repository_url,
            project_name=project_name,
            use_repo_artifacts=use_repo_artifacts,
        )
        return LocalPreviewSmokeFixture(
            username=username,
            password=password,
            analysis=analysis,
        )

    def _create_injected_artifacts(self, analysis: ProjectAnalysis) -> None:
        GeneratedArtifact.objects.bulk_create(
            [
                GeneratedArtifact(
                    analysis=analysis,
                    kind=GeneratedArtifact.Kind.DOCKERFILE,
                    path="Dockerfile",
                    description="Smoke-test Dockerfile",
                    content=(
                        "FROM node:22-alpine\n"
                        "WORKDIR /app\n"
                        "COPY . .\n"
                        "ENV PORT=3000\n"
                        "EXPOSE 3000\n"
                        'CMD ["node", "autodocker-smoke-server.js"]\n'
                    ),
                ),
                GeneratedArtifact(
                    analysis=analysis,
                    kind=GeneratedArtifact.Kind.GUIDE,
                    path="autodocker-smoke-server.js",
                    description="Deterministic smoke-test HTTP server",
                    content=(
                        "const http = require('http');\n"
                        "const port = Number(process.env.PORT || 3000);\n"
                        "const server = http.createServer((_req, res) => {\n"
                        "  res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' });\n"
                        "  res.end('preview smoke ok');\n"
                        "});\n"
                        "server.listen(port, '0.0.0.0');\n"
                    ),
                ),
                GeneratedArtifact(
                    analysis=analysis,
                    kind=GeneratedArtifact.Kind.COMPOSE,
                    path="docker-compose.yml",
                    description="Deterministic smoke-test compose file",
                    content=(
                        "services:\n"
                        "  web:\n"
                        "    build: .\n"
                        "    ports:\n"
                        '      - "3000"\n'
                    ),
                ),
            ]
        )

    def _project_name_from_repository_url(self, repository_url: str) -> str:
        normalized = repository_url.rstrip("/")
        tail = normalized.rsplit("/", maxsplit=1)[-1]
        return tail.removesuffix(".git") or "preview-smoke"
