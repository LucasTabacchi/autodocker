from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
        service.reconcile()
        serializer = PreviewRunnerSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        session = PreviewRunnerSession.objects.filter(
            preview_id=validated["preview_id"]
        ).first()
        if session is None:
            try:
                service.ensure_capacity_available(including_new_session=True)
            except Exception as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
            session = PreviewRunnerSession.objects.create(
                preview_id=validated["preview_id"],
                analysis_id=validated["analysis_id"],
                project_name=validated["project_name"],
                bundle_url=validated["bundle_url"],
                bundle_sha256=validated["bundle_sha256"],
                requested_ttl_seconds=validated["requested_ttl_seconds"],
                metadata=validated.get("metadata") or {},
                status=PreviewRunnerSession.Status.STARTING,
                expires_at=timezone.now()
                + timedelta(seconds=self._ttl_seconds(validated["requested_ttl_seconds"])),
            )
            schedule_preview_runner_session(session)
            session.refresh_from_db()

        return Response(
            PreviewRunnerSessionSerializer(session).data,
            status=status.HTTP_202_ACCEPTED,
        )

    def _ttl_seconds(self, requested_ttl_seconds: int) -> int:
        configured_default = max(int(settings.AUTODOCKER_PREVIEW_TTL_SECONDS), 1)
        maximum = max(
            int(getattr(settings, "AUTODOCKER_PREVIEW_MAX_TTL_SECONDS", configured_default)),
            1,
        )
        return min(max(int(requested_ttl_seconds), 1), configured_default, maximum)


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
