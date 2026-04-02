from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandParser

from core.services.local_preview_smoke import LocalPreviewSmokeService


class Command(BaseCommand):
    help = "Prepara un usuario y un análisis listos para ejecutar el smoke test local de previews."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--repository-url", required=True)
        parser.add_argument("--project-name", default="")
        parser.add_argument("--username", default=LocalPreviewSmokeService.DEFAULT_USERNAME)
        parser.add_argument("--password", default=LocalPreviewSmokeService.DEFAULT_PASSWORD)
        parser.add_argument("--use-repo-artifacts", action="store_true")

    def handle(self, *args, **options):
        fixture = LocalPreviewSmokeService().ensure_fixture(
            repository_url=options["repository_url"],
            project_name=options["project_name"] or None,
            username=options["username"],
            password=options["password"],
            use_repo_artifacts=options["use_repo_artifacts"],
        )
        self.stdout.write(
            json.dumps(
                {
                    "username": fixture.username,
                    "password": fixture.password,
                    "analysis_id": str(fixture.analysis.id),
                    "project_name": fixture.analysis.project_name,
                    "use_repo_artifacts": options["use_repo_artifacts"],
                }
            )
        )
