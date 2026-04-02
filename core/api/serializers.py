from __future__ import annotations

from rest_framework import serializers

from core.models import (
    ExecutionJob,
    ExternalRepoConnection,
    GeneratedArtifact,
    PreviewRun,
    ProjectAnalysis,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from core.services.runtime import preview_runtime_capability, validation_runtime_capability


class GeneratedArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneratedArtifact
        fields = ("id", "kind", "path", "description", "content", "updated_at")


class ExecutionJobSerializer(serializers.ModelSerializer):
    is_processing = serializers.BooleanField(read_only=True)

    class Meta:
        model = ExecutionJob
        fields = (
            "id",
            "kind",
            "status",
            "label",
            "metadata",
            "result_payload",
            "logs",
            "is_processing",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )


class PreviewRunSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = PreviewRun
        fields = (
            "id",
            "status",
            "runtime_kind",
            "workspace_path",
            "command",
            "access_url",
            "ports",
            "resource_names",
            "logs",
            "started_at",
            "finished_at",
            "expires_at",
            "created_at",
            "updated_at",
            "is_active",
        )


class ExternalRepoConnectionSerializer(serializers.ModelSerializer):
    has_token = serializers.SerializerMethodField()
    token_storage = serializers.CharField(read_only=True)

    class Meta:
        model = ExternalRepoConnection
        fields = (
            "id",
            "provider",
            "label",
            "account_name",
            "has_token",
            "token_storage",
            "created_at",
            "updated_at",
        )

    def get_has_token(self, obj: ExternalRepoConnection) -> bool:
        return bool(obj.access_token)


class DashboardConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalRepoConnection
        fields = ("id", "provider", "label", "account_name")


class WorkspaceMembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = WorkspaceMembership
        fields = ("id", "username", "role", "created_at", "updated_at")


class DashboardWorkspaceMembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = WorkspaceMembership
        fields = ("id", "username", "role")


class WorkspaceInvitationSerializer(serializers.ModelSerializer):
    invited_by_username = serializers.CharField(source="invited_by.username", read_only=True)
    invited_username = serializers.SerializerMethodField()
    target_label = serializers.CharField(read_only=True)
    workspace = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceInvitation
        fields = (
            "id",
            "workspace",
            "invited_by_username",
            "invited_username",
            "email",
            "target_label",
            "role",
            "status",
            "delivery_status",
            "delivery_error",
            "accepted_at",
            "responded_at",
            "created_at",
            "updated_at",
        )

    def get_invited_username(self, obj: WorkspaceInvitation) -> str:
        if obj.invited_user_id:
            return obj.invited_user.username
        return ""

    def get_workspace(self, obj: WorkspaceInvitation):
        return {
            "id": str(obj.workspace_id),
            "name": obj.workspace.name,
            "slug": obj.workspace.slug,
        }


class DashboardWorkspaceInvitationSerializer(serializers.ModelSerializer):
    invited_by_username = serializers.CharField(source="invited_by.username", read_only=True)
    invited_username = serializers.SerializerMethodField()
    target_label = serializers.CharField(read_only=True)
    workspace = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceInvitation
        fields = (
            "id",
            "workspace",
            "invited_by_username",
            "invited_username",
            "email",
            "target_label",
            "role",
            "status",
            "delivery_status",
        )

    def get_invited_username(self, obj: WorkspaceInvitation) -> str:
        if obj.invited_user_id:
            return obj.invited_user.username
        return ""

    def get_workspace(self, obj: WorkspaceInvitation):
        return {
            "id": str(obj.workspace_id),
            "name": obj.workspace.name,
            "slug": obj.workspace.slug,
        }


class WorkspaceSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    memberships = WorkspaceMembershipSerializer(many=True, read_only=True)
    pending_invitations = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "visibility",
            "member_count",
            "memberships",
            "pending_invitations",
            "created_at",
            "updated_at",
        )

    def get_member_count(self, obj: Workspace) -> int:
        memberships = self._prefetched_relation(obj, "memberships")
        if memberships is not None:
            return len(memberships)
        return obj.memberships.count()

    def get_pending_invitations(self, obj: Workspace):
        invitations = self._prefetched_relation(obj, "invitations")
        if invitations is not None:
            invitations = [
                invitation
                for invitation in invitations
                if invitation.status == WorkspaceInvitation.Status.PENDING
            ]
        else:
            invitations = (
                obj.invitations.filter(status=WorkspaceInvitation.Status.PENDING)
                .select_related("workspace", "invited_by", "invited_user")
                .order_by("-created_at")
            )
        return WorkspaceInvitationSerializer(invitations, many=True).data

    def _prefetched_relation(self, obj, relation_name: str):
        return getattr(obj, "_prefetched_objects_cache", {}).get(relation_name)


class DashboardWorkspaceSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    memberships = DashboardWorkspaceMembershipSerializer(many=True, read_only=True)
    pending_invitations = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = (
            "id",
            "name",
            "member_count",
            "memberships",
            "pending_invitations",
        )

    def get_member_count(self, obj: Workspace) -> int:
        memberships = getattr(obj, "_prefetched_objects_cache", {}).get("memberships")
        if memberships is not None:
            return len(memberships)
        return obj.memberships.count()

    def get_pending_invitations(self, obj: Workspace):
        invitations = getattr(obj, "_prefetched_objects_cache", {}).get("invitations")
        if invitations is not None:
            invitations = [
                invitation
                for invitation in invitations
                if invitation.status == WorkspaceInvitation.Status.PENDING
            ]
        else:
            invitations = (
                obj.invitations.filter(status=WorkspaceInvitation.Status.PENDING)
                .select_related("workspace", "invited_by", "invited_user")
                .order_by("-created_at")
            )
        return DashboardWorkspaceInvitationSerializer(invitations, many=True).data


class ProjectAnalysisSerializer(serializers.ModelSerializer):
    artifacts = GeneratedArtifactSerializer(many=True, read_only=True)
    source_label = serializers.CharField(read_only=True)
    download_url = serializers.SerializerMethodField()
    is_processing = serializers.BooleanField(read_only=True)
    owner = serializers.SerializerMethodField()
    latest_validation_job = serializers.SerializerMethodField()
    latest_github_pr_job = serializers.SerializerMethodField()
    active_preview = serializers.SerializerMethodField()
    workspace = serializers.SerializerMethodField()
    runtime_capabilities = serializers.SerializerMethodField()

    class Meta:
        model = ProjectAnalysis
        fields = (
            "id",
            "owner",
            "workspace",
            "project_name",
            "source_type",
            "generation_profile",
            "source_label",
            "repository_url",
            "status",
            "job_id",
            "is_processing",
            "detected_language",
            "detected_framework",
            "confidence",
            "execution_root",
            "package_manager",
            "install_command",
            "build_command",
            "start_command",
            "probable_ports",
            "environment_variables",
            "services",
            "found_files",
            "recommendations",
            "analysis_payload",
            "security_report",
            "healthcheck_report",
            "cicd_report",
            "deploy_report",
            "last_error",
            "source_commit",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "download_url",
            "artifacts",
            "latest_validation_job",
            "latest_github_pr_job",
            "active_preview",
            "runtime_capabilities",
        )

    def get_download_url(self, obj: ProjectAnalysis) -> str:
        request = self.context.get("request")
        path = f"/api/analyses/{obj.id}/download/"
        return request.build_absolute_uri(path) if request else path

    def get_owner(self, obj: ProjectAnalysis) -> str:
        if obj.owner_id:
            return obj.owner.get_username()
        return ""

    def get_workspace(self, obj: ProjectAnalysis):
        if not obj.workspace_id:
            return None
        return {
            "id": str(obj.workspace_id),
            "name": obj.workspace.name,
            "slug": obj.workspace.slug,
        }

    def get_latest_validation_job(self, obj: ProjectAnalysis):
        job = self._latest_prefetched_execution_job(obj, ExecutionJob.Kind.VALIDATION)
        if job is None:
            job = obj.execution_jobs.filter(kind=ExecutionJob.Kind.VALIDATION).order_by("-created_at").first()
        return ExecutionJobSerializer(job).data if job else None

    def get_latest_github_pr_job(self, obj: ProjectAnalysis):
        job = self._latest_prefetched_execution_job(obj, ExecutionJob.Kind.GITHUB_PR)
        if job is None:
            job = obj.execution_jobs.filter(kind=ExecutionJob.Kind.GITHUB_PR).order_by("-created_at").first()
        return ExecutionJobSerializer(job).data if job else None

    def get_active_preview(self, obj: ProjectAnalysis):
        preview = self._active_prefetched_preview(obj)
        if preview is None:
            preview = obj.preview_runs.filter(
                status__in=(
                    PreviewRun.Status.QUEUED,
                    PreviewRun.Status.RUNNING,
                    PreviewRun.Status.READY,
                )
            ).order_by("-created_at").first()
        return PreviewRunSerializer(preview).data if preview else None

    def get_runtime_capabilities(self, obj: ProjectAnalysis):
        return {
            "validation": validation_runtime_capability(),
            "preview": preview_runtime_capability(),
        }

    def _latest_prefetched_execution_job(self, obj: ProjectAnalysis, kind: str):
        execution_jobs = getattr(obj, "_prefetched_objects_cache", {}).get("execution_jobs")
        if execution_jobs is None:
            return None
        for job in execution_jobs:
            if job.kind == kind:
                return job
        return None

    def _active_prefetched_preview(self, obj: ProjectAnalysis):
        previews = getattr(obj, "_prefetched_objects_cache", {}).get("preview_runs")
        if previews is None:
            return None
        active_statuses = {
            PreviewRun.Status.QUEUED,
            PreviewRun.Status.RUNNING,
            PreviewRun.Status.READY,
        }
        for preview in previews:
            if preview.status in active_statuses:
                return preview
        return None


class DashboardHistoryAnalysisSerializer(serializers.ModelSerializer):
    workspace = serializers.SerializerMethodField()

    class Meta:
        model = ProjectAnalysis
        fields = (
            "id",
            "workspace",
            "project_name",
            "status",
            "detected_framework",
        )

    def get_workspace(self, obj: ProjectAnalysis):
        if not obj.workspace_id:
            return None
        return {
            "id": str(obj.workspace_id),
            "name": obj.workspace.name,
            "slug": obj.workspace.slug,
        }
