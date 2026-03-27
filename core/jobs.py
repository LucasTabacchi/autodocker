from __future__ import annotations

import threading

from django.conf import settings
from django.db import close_old_connections

from core.models import ExecutionJob, PreviewRun, ProjectAnalysis
from core.services.execution_runner import ExecutionJobRunner
from core.services.orchestrator import AnalysisOrchestrator


def schedule_analysis(analysis: ProjectAnalysis) -> ProjectAnalysis:
    return _schedule("run", analysis)


def schedule_regeneration(analysis: ProjectAnalysis) -> ProjectAnalysis:
    return _schedule("regenerate", analysis)


def schedule_execution_job(job: ExecutionJob) -> ExecutionJob:
    mode = settings.AUTODOCKER_ASYNC_MODE

    if settings.CELERY_TASK_ALWAYS_EAGER or mode == "inline":
        _execute_execution_job(str(job.pk))
        job.refresh_from_db()
        return job

    if mode == "celery":
        from core.tasks import run_execution_job_task

        task = run_execution_job_task.delay(str(job.pk))
        job.metadata = {**job.metadata, "celery_job_id": task.id}
        job.save(update_fields=["metadata", "updated_at"])
        return job

    thread = threading.Thread(
        target=_execute_execution_job,
        args=(str(job.pk),),
        daemon=True,
    )
    thread.start()
    job.metadata = {**job.metadata, "thread_job_id": f"thread:{job.pk}"}
    job.save(update_fields=["metadata", "updated_at"])
    return job


def schedule_preview(analysis: ProjectAnalysis, owner) -> PreviewRun:
    job = ExecutionJob.objects.create(
        owner=owner,
        analysis=analysis,
        kind=ExecutionJob.Kind.PREVIEW,
        label=f"Preview for {analysis.project_name}",
    )
    preview_run = PreviewRun.objects.create(
        owner=owner,
        analysis=analysis,
        execution_job=job,
        status=PreviewRun.Status.QUEUED,
    )
    schedule_execution_job(job)
    preview_run.refresh_from_db()
    return preview_run


def _schedule(action: str, analysis: ProjectAnalysis) -> ProjectAnalysis:
    mode = settings.AUTODOCKER_ASYNC_MODE

    if settings.CELERY_TASK_ALWAYS_EAGER or mode == "inline":
        _execute(action, str(analysis.pk))
        analysis.refresh_from_db()
        return analysis

    if mode == "celery":
        from core.tasks import regenerate_analysis_task, run_analysis_task

        task = (
            regenerate_analysis_task.delay(str(analysis.pk))
            if action == "regenerate"
            else run_analysis_task.delay(str(analysis.pk))
        )
        analysis.job_id = task.id
        analysis.save(update_fields=["job_id", "updated_at"])
        return analysis

    thread = threading.Thread(
        target=_execute,
        args=(action, str(analysis.pk)),
        daemon=True,
    )
    thread.start()
    analysis.job_id = f"thread:{analysis.pk}"
    analysis.save(update_fields=["job_id", "updated_at"])
    return analysis


def _execute(action: str, analysis_id: str) -> None:
    close_old_connections()
    analysis = ProjectAnalysis.objects.get(pk=analysis_id)
    orchestrator = AnalysisOrchestrator()
    if action == "regenerate":
        orchestrator.regenerate(analysis)
    else:
        orchestrator.run(analysis)


def _execute_execution_job(job_id: str) -> None:
    close_old_connections()
    job = ExecutionJob.objects.get(pk=job_id)
    runner = ExecutionJobRunner()
    runner.run(job)
