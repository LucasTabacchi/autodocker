from __future__ import annotations

import io
import logging
import zipfile

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import (
    ExecutionJobSerializer,
    ExternalRepoConnectionSerializer,
    GeneratedArtifactSerializer,
    PreviewRunSerializer,
    ProjectAnalysisSerializer,
    WorkspaceInvitationSerializer,
    WorkspaceMembershipSerializer,
    WorkspaceSerializer,
)
from core.forms import AnalysisSubmissionForm
from core.jobs import schedule_analysis, schedule_execution_job, schedule_preview, schedule_regeneration
from core.models import (
    ArtifactSnapshot,
    ExecutionJob,
    ExternalRepoConnection,
    GeneratedArtifact,
    PreviewRun,
    ProjectAnalysis,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from core.services.diffing import ArtifactDiffService
from core.services.preview import PreviewService
from core.services.runtime import preview_runtime_capability, validation_runtime_capability
from core.services.workspaces import (
    accept_workspace_invitation,
    add_workspace_member,
    decline_workspace_invitation,
    default_workspace_for_user,
    ensure_personal_workspace,
    incoming_workspace_invitations_for_user,
    invite_workspace_member,
    user_can_manage_workspace,
)

logger = logging.getLogger(__name__)


class AuthenticatedApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get_analysis_queryset(self):
        return ProjectAnalysis.objects.with_related().for_user(self.request.user)

    def get_execution_job_queryset(self):
        return ExecutionJob.objects.for_user(self.request.user)

    def get_connection_queryset(self):
        return ExternalRepoConnection.objects.for_user(self.request.user)

    def get_preview_queryset(self):
        return PreviewRun.objects.for_user(self.request.user)

    def get_workspace_queryset(self):
        ensure_personal_workspace(self.request.user)
        return Workspace.objects.for_user(self.request.user).prefetch_related(
            "memberships__user",
            "invitations__invited_user",
            "invitations__invited_by",
        )

    def get_requested_workspace(self):
        workspace_id = self.request.query_params.get("workspace_id") or self.request.data.get("workspace_id")
        if workspace_id:
            return get_object_or_404(self.get_workspace_queryset(), pk=workspace_id)
        return default_workspace_for_user(self.request.user)

    def user_can_mutate_analysis(self, analysis: ProjectAnalysis) -> bool:
        if analysis.owner_id == self.request.user.id:
            return True
        if analysis.workspace_id:
            return user_can_manage_workspace(self.request.user, analysis.workspace)
        return False


class AnalysisListCreateApiView(AuthenticatedApiView):
    def get(self, request):
        analyses = self.get_analysis_queryset()
        workspace = self.get_requested_workspace()
        if workspace:
            analyses = analyses.filter(workspace=workspace)
        analyses = analyses[:20]
        serializer = ProjectAnalysisSerializer(
            analyses,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)

    def post(self, request):
        form = AnalysisSubmissionForm(request.data, request.FILES)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        cleaned = form.cleaned_data
        project_name = cleaned.get("project_name") or _guess_project_name(cleaned)
        workspace = self.get_requested_workspace()
        source_type = (
            ProjectAnalysis.SourceType.ZIP
            if cleaned.get("archive")
            else ProjectAnalysis.SourceType.GIT
        )
        try:
            analysis = ProjectAnalysis.objects.create(
                owner=request.user,
                workspace=workspace,
                project_name=project_name,
                source_type=source_type,
                generation_profile=cleaned.get("generation_profile") or ProjectAnalysis.GenerationProfile.PRODUCTION,
                repository_url=cleaned.get("repository_url") or "",
                archive=cleaned.get("archive"),
                status=ProjectAnalysis.Status.QUEUED,
            )
        except OSError:
            logger.exception("Failed to persist uploaded archive for analysis creation")
            return Response(
                {
                    "detail": (
                        "The uploaded archive could not be saved. "
                        "Check the media storage configuration or local volume setup."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        analysis = schedule_analysis(analysis)
        serializer = ProjectAnalysisSerializer(analysis, context={"request": request})
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


class AnalysisDetailApiView(AuthenticatedApiView):
    def get(self, request, analysis_id):
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        serializer = ProjectAnalysisSerializer(analysis, context={"request": request})
        return Response(serializer.data)


class ArtifactDetailApiView(AuthenticatedApiView):
    def patch(self, request, artifact_id):
        artifact = get_object_or_404(
            GeneratedArtifact.objects.select_related("analysis", "analysis__owner", "analysis__workspace"),
            pk=artifact_id,
        )
        if not self.user_can_mutate_analysis(artifact.analysis):
            return Response({"detail": "You do not have permission to edit this artifact."}, status=status.HTTP_403_FORBIDDEN)
        content = request.data.get("content")
        if not isinstance(content, str):
            return Response(
                {"content": ["Editable content is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artifact.content = content
        artifact.save(update_fields=["content", "updated_at"])
        ArtifactSnapshot.objects.create(
            analysis=artifact.analysis,
            version=ArtifactSnapshot.next_version_for(artifact.analysis),
            event=ArtifactSnapshot.Event.EDIT,
            generation_profile=artifact.analysis.generation_profile,
            kind=artifact.kind,
            path=artifact.path,
            content=artifact.content,
        )
        return Response(GeneratedArtifactSerializer(artifact).data)


class AnalysisRegenerateApiView(AuthenticatedApiView):
    def post(self, request, analysis_id):
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        if not self.user_can_mutate_analysis(analysis):
            return Response({"detail": "You do not have permission to regenerate this analysis."}, status=status.HTTP_403_FORBIDDEN)
        generation_profile = request.data.get("generation_profile")
        if generation_profile in {
            ProjectAnalysis.GenerationProfile.DEVELOPMENT,
            ProjectAnalysis.GenerationProfile.PRODUCTION,
            ProjectAnalysis.GenerationProfile.CI,
        }:
            analysis.generation_profile = generation_profile
        analysis.status = ProjectAnalysis.Status.QUEUED
        analysis.last_error = ""
        analysis.save(update_fields=["generation_profile", "status", "last_error", "updated_at"])
        analysis = schedule_regeneration(analysis)
        serializer = ProjectAnalysisSerializer(analysis, context={"request": request})
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


class AnalysisDownloadApiView(AuthenticatedApiView):
    def get(self, request, analysis_id):
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
            for artifact in analysis.artifacts.all():
                zipped.writestr(artifact.path, artifact.content)
        buffer.seek(0)
        filename = f"{analysis.project_name.lower().replace(' ', '-')}-docker-config.zip"
        return FileResponse(buffer, as_attachment=True, filename=filename)


class AnalysisDiffApiView(AuthenticatedApiView):
    def get(self, request, analysis_id):
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        service = ArtifactDiffService()
        items = [entry.to_dict() for entry in service.build_diff(analysis)]
        return Response({"items": items})


class AnalysisValidateApiView(AuthenticatedApiView):
    def post(self, request, analysis_id):
        capability = validation_runtime_capability()
        if not capability["enabled"]:
            return Response(
                {"detail": capability["reason"]},
                status=status.HTTP_409_CONFLICT,
            )
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        if not self.user_can_mutate_analysis(analysis):
            return Response({"detail": "You do not have permission to validate this analysis."}, status=status.HTTP_403_FORBIDDEN)
        job = ExecutionJob.objects.create(
            owner=request.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.VALIDATION,
            label=f"Validate {analysis.project_name}",
            metadata={"generation_profile": analysis.generation_profile},
        )
        job = schedule_execution_job(job)
        return Response(ExecutionJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class AnalysisGitHubPrApiView(AuthenticatedApiView):
    def post(self, request, analysis_id):
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        if not self.user_can_mutate_analysis(analysis):
            return Response({"detail": "You do not have permission to open pull requests from this analysis."}, status=status.HTTP_403_FORBIDDEN)
        connection_id = request.data.get("connection_id")
        access_token = (request.data.get("access_token") or "").strip()
        save_connection = str(request.data.get("save_connection", "")).lower() in {"1", "true", "yes", "on"}
        label = (request.data.get("connection_label") or "GitHub personal token").strip()
        account_name = (request.data.get("account_name") or "").strip()

        if not connection_id and not access_token:
            return Response(
                {"detail": "A saved connection or GitHub token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if save_connection and access_token and not connection_id:
            connection = ExternalRepoConnection.objects.create(
                owner=request.user,
                provider=ExternalRepoConnection.Provider.GITHUB,
                label=label,
                account_name=account_name,
                access_token=access_token,
            )
            connection_id = str(connection.id)

        job = ExecutionJob.objects.create(
            owner=request.user,
            analysis=analysis,
            kind=ExecutionJob.Kind.GITHUB_PR,
            label=f"GitHub PR for {analysis.project_name}",
            metadata={
                "connection_id": connection_id or "",
                "access_token": access_token if not connection_id else "",
                "base_branch": request.data.get("base_branch", "main"),
                "title": request.data.get("title") or f"Dockerize {analysis.project_name}",
                "body": request.data.get("body")
                or "Auto-generated Docker configuration from AutoDocker.",
            },
        )
        job = schedule_execution_job(job)
        return Response(ExecutionJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class AnalysisPreviewApiView(AuthenticatedApiView):
    def post(self, request, analysis_id):
        capability = preview_runtime_capability()
        if not capability["enabled"]:
            return Response(
                {"detail": capability["reason"]},
                status=status.HTTP_409_CONFLICT,
            )
        analysis = get_object_or_404(self.get_analysis_queryset(), pk=analysis_id)
        if not self.user_can_mutate_analysis(analysis):
            return Response({"detail": "You do not have permission to run previews for this analysis."}, status=status.HTTP_403_FORBIDDEN)
        active_preview = analysis.preview_runs.filter(
            status__in=(
                PreviewRun.Status.QUEUED,
                PreviewRun.Status.RUNNING,
                PreviewRun.Status.READY,
            )
        ).order_by("-created_at").first()
        if active_preview:
            return Response(PreviewRunSerializer(active_preview).data)
        preview_run = schedule_preview(analysis, request.user)
        return Response(PreviewRunSerializer(preview_run).data, status=status.HTTP_202_ACCEPTED)


class PreviewDetailApiView(AuthenticatedApiView):
    def get(self, request, preview_id):
        preview = get_object_or_404(self.get_preview_queryset(), pk=preview_id)
        PreviewService().refresh_logs(preview)
        preview.refresh_from_db()
        return Response(PreviewRunSerializer(preview).data)


class PreviewStopApiView(AuthenticatedApiView):
    def post(self, request, preview_id):
        preview = get_object_or_404(self.get_preview_queryset(), pk=preview_id)
        if not self.user_can_mutate_analysis(preview.analysis):
            return Response(
                {"detail": "You do not have permission to stop this preview."},
                status=status.HTTP_403_FORBIDDEN,
            )
        PreviewService().stop(preview)
        return Response(PreviewRunSerializer(preview).data)


class ExecutionJobDetailApiView(AuthenticatedApiView):
    def get(self, request, job_id):
        job = get_object_or_404(self.get_execution_job_queryset(), pk=job_id)
        return Response(ExecutionJobSerializer(job).data)


class RepoConnectionListCreateApiView(AuthenticatedApiView):
    def get(self, request):
        serializer = ExternalRepoConnectionSerializer(
            self.get_connection_queryset(),
            many=True,
        )
        return Response(serializer.data)

    def post(self, request):
        access_token = (request.data.get("access_token") or "").strip()
        label = (request.data.get("label") or "").strip()
        if not access_token or not label:
            return Response(
                {"detail": "`label` and `access_token` are required to save a connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        connection = ExternalRepoConnection.objects.create(
            owner=request.user,
            provider=ExternalRepoConnection.Provider.GITHUB,
            label=label,
            account_name=(request.data.get("account_name") or "").strip(),
            access_token=access_token,
        )
        return Response(ExternalRepoConnectionSerializer(connection).data, status=status.HTTP_201_CREATED)


class RepoConnectionDetailApiView(AuthenticatedApiView):
    def delete(self, request, connection_id):
        connection = get_object_or_404(self.get_connection_queryset(), pk=connection_id)
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceListCreateApiView(AuthenticatedApiView):
    def get(self, request):
        serializer = WorkspaceSerializer(self.get_workspace_queryset(), many=True)
        return Response(serializer.data)

    def post(self, request):
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "A workspace name is required."}, status=status.HTTP_400_BAD_REQUEST)

        description = (request.data.get("description") or "").strip()
        visibility = request.data.get("visibility") or Workspace.Visibility.TEAM
        valid_visibilities = {choice for choice, _label in Workspace.Visibility.choices}
        workspace = Workspace.objects.create(
            owner=request.user,
            name=name,
            slug=self._unique_slug(name),
            description=description,
            visibility=visibility if visibility in valid_visibilities else Workspace.Visibility.TEAM,
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=request.user,
            role=WorkspaceMembership.Role.OWNER,
        )
        return Response(WorkspaceSerializer(workspace).data, status=status.HTTP_201_CREATED)

    def _unique_slug(self, value: str) -> str:
        from django.utils.text import slugify

        root = slugify(value) or "workspace"
        candidate = root
        suffix = 2
        while Workspace.objects.filter(slug=candidate).exists():
            candidate = f"{root}-{suffix}"
            suffix += 1
        return candidate


class WorkspaceMemberCreateApiView(AuthenticatedApiView):
    def post(self, request, workspace_id):
        workspace = get_object_or_404(self.get_workspace_queryset(), pk=workspace_id)
        if not user_can_manage_workspace(request.user, workspace):
            return Response({"detail": "You do not have permission to manage this workspace."}, status=status.HTTP_403_FORBIDDEN)

        identifier = (
            request.data.get("identifier")
            or request.data.get("username")
            or request.data.get("email")
            or ""
        ).strip()
        role = request.data.get("role") or WorkspaceMembership.Role.VIEWER
        valid_roles = {choice for choice, _label in WorkspaceMembership.Role.choices}
        if not identifier:
            return Response(
                {"detail": "A username or email is required to invite someone to the workspace."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role not in valid_roles:
            return Response({"detail": "Invalid role for this workspace."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = invite_workspace_member(
                workspace=workspace,
                identifier=identifier,
                role=role,
                invited_by=request.user,
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(WorkspaceInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class WorkspaceMemberDetailApiView(AuthenticatedApiView):
    def delete(self, request, workspace_id, membership_id):
        workspace = get_object_or_404(self.get_workspace_queryset(), pk=workspace_id)
        if not user_can_manage_workspace(request.user, workspace):
            return Response({"detail": "You do not have permission to manage this workspace."}, status=status.HTTP_403_FORBIDDEN)

        membership = get_object_or_404(workspace.memberships.select_related("user"), pk=membership_id)
        if membership.role == WorkspaceMembership.Role.OWNER and membership.user_id == workspace.owner_id:
            return Response({"detail": "The primary workspace owner cannot be removed."}, status=status.HTTP_400_BAD_REQUEST)
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceInvitationListApiView(AuthenticatedApiView):
    def get(self, request):
        invitations = incoming_workspace_invitations_for_user(request.user)
        return Response(WorkspaceInvitationSerializer(invitations, many=True).data)


class WorkspaceInvitationAcceptApiView(AuthenticatedApiView):
    def post(self, request, invitation_id):
        invitation = get_object_or_404(
            WorkspaceInvitation.objects.select_related("workspace", "invited_user", "invited_by"),
            pk=invitation_id,
        )
        try:
            membership = accept_workspace_invitation(invitation=invitation, user=request.user)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkspaceMembershipSerializer(membership)
        return Response(serializer.data)


class WorkspaceInvitationDeclineApiView(AuthenticatedApiView):
    def post(self, request, invitation_id):
        invitation = get_object_or_404(
            WorkspaceInvitation.objects.select_related("workspace", "invited_user", "invited_by"),
            pk=invitation_id,
        )
        try:
            invitation = decline_workspace_invitation(invitation=invitation, user=request.user)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WorkspaceInvitationSerializer(invitation).data)


def _guess_project_name(cleaned: dict) -> str:
    if cleaned.get("archive"):
        return cleaned["archive"].name.rsplit(".", maxsplit=1)[0]
    repository_url = cleaned.get("repository_url", "").rstrip("/")
    if repository_url:
        return repository_url.rsplit("/", maxsplit=1)[-1]
    return "autodocker-project"
