from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Max, Q

from core.crypto import is_encrypted_secret, open_secret, seal_secret


class WorkspaceQuerySet(models.QuerySet):
    def for_user(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(Q(owner=user) | Q(memberships__user=user)).distinct()


class Workspace(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        TEAM = "team", "Team"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_workspaces",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.CharField(max_length=255, blank=True)
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class WorkspaceMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        related_name="memberships",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="workspace_memberships",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["workspace__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"],
                name="unique_workspace_membership_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.workspace_id}:{self.user_id}:{self.role}"


class WorkspaceInvitationQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=WorkspaceInvitation.Status.PENDING)

    def incoming_for_user(self, user):
        if not user or not user.is_authenticated:
            return self.none()

        filters = Q(invited_user=user)
        email = (getattr(user, "email", "") or "").strip()
        if email:
            filters |= Q(email__iexact=email)
        return (
            self.filter(filters, status=WorkspaceInvitation.Status.PENDING)
            .select_related("workspace", "invited_by", "invited_user")
            .order_by("-created_at")
            .distinct()
        )


class WorkspaceInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        REVOKED = "revoked", "Revoked"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_APP = "in_app", "In app"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        related_name="invitations",
        on_delete=models.CASCADE,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sent_workspace_invitations",
        on_delete=models.CASCADE,
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="received_workspace_invitations",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    email = models.EmailField(blank=True)
    role = models.CharField(
        max_length=16,
        choices=WorkspaceMembership.Role.choices,
        default=WorkspaceMembership.Role.VIEWER,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    delivery_error = models.CharField(max_length=255, blank=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    responded_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceInvitationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.workspace_id}:{self.target_label}:{self.status}"

    @property
    def target_label(self) -> str:
        if self.invited_user_id:
            return self.invited_user.get_username()
        return self.email


class ProjectAnalysisQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related("owner", "workspace").prefetch_related(
            "artifacts",
            "execution_jobs",
            "preview_runs",
        )

    def for_user(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(
            Q(owner=user) | Q(workspace__memberships__user=user)
        ).distinct()


class ProjectAnalysis(models.Model):
    class SourceType(models.TextChoices):
        ZIP = "zip", "ZIP"
        GIT = "git", "Git"

    class GenerationProfile(models.TextChoices):
        PRODUCTION = "production", "Production"
        DEVELOPMENT = "development", "Development"
        CI = "ci", "CI"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        ANALYZING = "analyzing", "Analyzing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="project_analyses",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        related_name="analyses",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    project_name = models.CharField(max_length=255)
    source_type = models.CharField(max_length=12, choices=SourceType.choices)
    generation_profile = models.CharField(
        max_length=16,
        choices=GenerationProfile.choices,
        default=GenerationProfile.PRODUCTION,
    )
    repository_url = models.URLField(blank=True)
    archive = models.FileField(upload_to="uploads/%Y/%m/%d/", blank=True, null=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    job_id = models.CharField(max_length=255, blank=True, db_index=True)
    detected_language = models.CharField(max_length=64, blank=True)
    detected_framework = models.CharField(max_length=64, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    execution_root = models.CharField(max_length=255, blank=True)
    package_manager = models.CharField(max_length=64, blank=True)
    install_command = models.CharField(max_length=255, blank=True)
    build_command = models.CharField(max_length=255, blank=True)
    start_command = models.CharField(max_length=255, blank=True)
    probable_ports = models.JSONField(default=list, blank=True)
    environment_variables = models.JSONField(default=list, blank=True)
    services = models.JSONField(default=list, blank=True)
    found_files = models.JSONField(default=list, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    analysis_payload = models.JSONField(default=dict, blank=True)
    security_report = models.JSONField(default=dict, blank=True)
    healthcheck_report = models.JSONField(default=dict, blank=True)
    cicd_report = models.JSONField(default=dict, blank=True)
    deploy_report = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    source_commit = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectAnalysisQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.project_name} ({self.get_status_display()})"

    @property
    def source_label(self) -> str:
        if self.source_type == self.SourceType.GIT:
            return self.repository_url
        if self.archive:
            return self.archive.name.rsplit("/", maxsplit=1)[-1]
        return self.project_name

    @property
    def is_processing(self) -> bool:
        return self.status in {self.Status.QUEUED, self.Status.ANALYZING}


class GeneratedArtifact(models.Model):
    class Kind(models.TextChoices):
        DOCKERFILE = "dockerfile", "Dockerfile"
        COMPOSE = "compose", "Docker Compose"
        IGNORE = "ignore", "Docker Ignore"
        GUIDE = "guide", "Guide"
        PIPELINE = "pipeline", "Pipeline"
        DEPLOY = "deploy", "Deploy"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(
        ProjectAnalysis,
        related_name="artifacts",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    path = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis", "path"],
                name="unique_generated_artifact_per_analysis",
            )
        ]

    def __str__(self) -> str:
        return self.path


class ArtifactSnapshot(models.Model):
    class Event(models.TextChoices):
        GENERATION = "generation", "Generation"
        EDIT = "edit", "Edit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(
        ProjectAnalysis,
        related_name="artifact_snapshots",
        on_delete=models.CASCADE,
    )
    version = models.PositiveIntegerField()
    event = models.CharField(max_length=16, choices=Event.choices)
    generation_profile = models.CharField(
        max_length=16,
        choices=ProjectAnalysis.GenerationProfile.choices,
    )
    kind = models.CharField(max_length=32, choices=GeneratedArtifact.Kind.choices)
    path = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version", "path"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis", "version", "path"],
                name="unique_artifact_snapshot_per_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.analysis_id}:{self.version}:{self.path}"

    @classmethod
    def next_version_for(cls, analysis: ProjectAnalysis) -> int:
        current = cls.objects.filter(analysis=analysis).aggregate(max_version=Max("version"))
        return (current["max_version"] or 0) + 1


class ExecutionJobQuerySet(models.QuerySet):
    def for_user(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(
            Q(owner=user) | Q(analysis__workspace__memberships__user=user)
        ).distinct()


class ExecutionJob(models.Model):
    class Kind(models.TextChoices):
        VALIDATION = "validation", "Validation"
        GITHUB_PR = "github_pr", "GitHub PR"
        PREVIEW = "preview", "Preview"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="execution_jobs",
        on_delete=models.CASCADE,
    )
    analysis = models.ForeignKey(
        ProjectAnalysis,
        related_name="execution_jobs",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    label = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    logs = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ExecutionJobQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind}:{self.analysis_id}:{self.status}"

    @property
    def is_processing(self) -> bool:
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class ExternalRepoConnectionQuerySet(models.QuerySet):
    def for_user(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(owner=user)


class ExternalRepoConnection(models.Model):
    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="external_repo_connections",
        on_delete=models.CASCADE,
    )
    provider = models.CharField(max_length=24, choices=Provider.choices, default=Provider.GITHUB)
    label = models.CharField(max_length=120)
    account_name = models.CharField(max_length=255, blank=True)
    access_token = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ExternalRepoConnectionQuerySet.as_manager()

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "provider", "label"],
                name="unique_repo_connection_label_per_owner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.label}"

    @property
    def token_storage(self) -> str:
        return "encrypted" if is_encrypted_secret(self.access_token) else "legacy-plain"

    def get_access_token(self) -> str:
        return open_secret(self.access_token)

    def set_access_token(self, value: str) -> None:
        self.access_token = seal_secret(value)

    def save(self, *args, **kwargs):
        if self.access_token and not is_encrypted_secret(self.access_token):
            self.access_token = seal_secret(self.access_token)
        return super().save(*args, **kwargs)


class PreviewRunQuerySet(models.QuerySet):
    def for_user(self, user):
        if not user.is_authenticated:
            return self.none()
        return self.filter(
            Q(owner=user) | Q(analysis__workspace__memberships__user=user)
        ).distinct()


class PreviewRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        STOPPED = "stopped", "Stopped"

    class RuntimeKind(models.TextChoices):
        COMPOSE = "compose", "Compose"
        CONTAINER = "container", "Container"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="preview_runs",
        on_delete=models.CASCADE,
    )
    analysis = models.ForeignKey(
        ProjectAnalysis,
        related_name="preview_runs",
        on_delete=models.CASCADE,
    )
    execution_job = models.OneToOneField(
        ExecutionJob,
        related_name="preview_run",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    runtime_kind = models.CharField(max_length=16, choices=RuntimeKind.choices, blank=True)
    workspace_path = models.CharField(max_length=1024, blank=True)
    workspace_root = models.CharField(max_length=1024, blank=True)
    command = models.CharField(max_length=512, blank=True)
    access_url = models.URLField(blank=True)
    ports = models.JSONField(default=dict, blank=True)
    resource_names = models.JSONField(default=list, blank=True)
    logs = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PreviewRunQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.analysis_id}:{self.status}"

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.QUEUED, self.Status.RUNNING, self.Status.READY}
