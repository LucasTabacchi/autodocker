from __future__ import annotations

from django.core.management.base import BaseCommand

from core.services.preview_runner_sessions import PreviewRunnerSessionService


class Command(BaseCommand):
    help = "Expire preview runner sessions whose TTL already elapsed."

    def handle(self, *args, **options):
        reconciled = PreviewRunnerSessionService().reconcile()
        suffix = "" if reconciled == 1 else "s"
        self.stdout.write(f"Reconciled {reconciled} preview runner session{suffix}.")
