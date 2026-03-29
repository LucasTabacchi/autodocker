from __future__ import annotations

import threading

from django.conf import settings
from django.db import close_old_connections

from core.models import PreviewRunnerSession
from core.services.preview_runner_sessions import PreviewRunnerSessionService


def schedule_preview_runner_session(session: PreviewRunnerSession) -> PreviewRunnerSession:
    mode = settings.AUTODOCKER_ASYNC_MODE

    if settings.CELERY_TASK_ALWAYS_EAGER or mode == "inline":
        _execute_preview_runner_session(str(session.pk))
        session.refresh_from_db()
        return session

    thread = threading.Thread(
        target=_execute_preview_runner_session,
        args=(str(session.pk),),
        daemon=True,
    )
    thread.start()
    return session


def _execute_preview_runner_session(session_id: str) -> None:
    close_old_connections()
    session = PreviewRunnerSession.objects.get(pk=session_id)
    PreviewRunnerSessionService().start(session)
