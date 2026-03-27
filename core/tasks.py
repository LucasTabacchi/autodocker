from __future__ import annotations

from celery import shared_task

from core.jobs import _execute, _execute_execution_job


@shared_task(bind=True)
def run_analysis_task(self, analysis_id: str) -> None:
    _execute("run", analysis_id)


@shared_task(bind=True)
def regenerate_analysis_task(self, analysis_id: str) -> None:
    _execute("regenerate", analysis_id)


@shared_task(bind=True)
def run_execution_job_task(self, execution_job_id: str) -> None:
    _execute_execution_job(execution_job_id)
