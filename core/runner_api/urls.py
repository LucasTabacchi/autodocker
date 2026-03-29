from django.urls import path

from core.runner_api.views import (
    PreviewRunnerSessionDetailApiView,
    PreviewRunnerSessionListCreateApiView,
    PreviewRunnerSessionLogsApiView,
    PreviewRunnerSessionStopApiView,
)

urlpatterns = [
    path("previews", PreviewRunnerSessionListCreateApiView.as_view(), name="preview-runner-create"),
    path("previews/<uuid:preview_id>", PreviewRunnerSessionDetailApiView.as_view(), name="preview-runner-detail"),
    path(
        "previews/<uuid:preview_id>/logs",
        PreviewRunnerSessionLogsApiView.as_view(),
        name="preview-runner-logs",
    ),
    path(
        "previews/<uuid:preview_id>/stop",
        PreviewRunnerSessionStopApiView.as_view(),
        name="preview-runner-stop",
    ),
]
