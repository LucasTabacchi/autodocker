from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import PreviewRunnerSession
from core.preview_runner_jobs import schedule_preview_runner_session
from core.runner_api.serializers import (
    PreviewRunnerSessionCreateSerializer,
    PreviewRunnerSessionSerializer,
)
from core.services.preview_runner_sessions import PreviewRunnerSessionService


class PreviewRunnerAuthenticatedApiView(APIView):
    authentication_classes = []
    permission_classes = []

    def dispatch(self, request, *args, **kwargs):
        expected_token = (getattr(settings, "AUTODOCKER_PREVIEW_RUNNER_TOKEN", "") or "").strip()
        if not expected_token:
            return JsonResponse(
                {"detail": "AUTODOCKER_PREVIEW_RUNNER_TOKEN no está configurado en el runner."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if request.headers.get("Authorization", "") != f"Bearer {expected_token}":
            return JsonResponse(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return super().dispatch(request, *args, **kwargs)


class PreviewRunnerSessionListCreateApiView(PreviewRunnerAuthenticatedApiView):
    def post(self, request):
        service = PreviewRunnerSessionService()
        serializer = PreviewRunnerSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            session, created = service.get_or_create_reserved_session(validated_data=validated)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        if created:
            schedule_preview_runner_session(session)
            session.refresh_from_db()

        return Response(
            PreviewRunnerSessionSerializer(session).data,
            status=status.HTTP_202_ACCEPTED,
        )


class PreviewRunnerSessionDetailApiView(PreviewRunnerAuthenticatedApiView):
    def get(self, request, preview_id):
        session = get_object_or_404(PreviewRunnerSession, preview_id=preview_id)
        return Response(PreviewRunnerSessionSerializer(session).data)


class PreviewRunnerSessionLogsApiView(PreviewRunnerAuthenticatedApiView):
    def get(self, request, preview_id):
        session = get_object_or_404(PreviewRunnerSession, preview_id=preview_id)
        service = PreviewRunnerSessionService()
        service.reconcile()
        service.refresh_logs(session)
        session.refresh_from_db()
        return Response(
            {
                "preview_id": str(session.preview_id),
                "logs": session.logs,
            }
        )


class PreviewRunnerSessionStopApiView(PreviewRunnerAuthenticatedApiView):
    def post(self, request, preview_id):
        session = get_object_or_404(PreviewRunnerSession, preview_id=preview_id)
        service = PreviewRunnerSessionService()
        service.reconcile()
        service.stop(session)
        session.refresh_from_db()
        return Response(
            {
                "preview_id": str(session.preview_id),
                "status": session.status,
            }
        )
