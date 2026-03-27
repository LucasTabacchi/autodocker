from django.urls import path

from core.api.views import (
    AnalysisDetailApiView,
    AnalysisDiffApiView,
    AnalysisDownloadApiView,
    AnalysisGitHubPrApiView,
    AnalysisListCreateApiView,
    AnalysisPreviewApiView,
    AnalysisRegenerateApiView,
    AnalysisValidateApiView,
    ArtifactDetailApiView,
    ExecutionJobDetailApiView,
    PreviewDetailApiView,
    PreviewStopApiView,
    RepoConnectionDetailApiView,
    RepoConnectionListCreateApiView,
    WorkspaceListCreateApiView,
    WorkspaceInvitationAcceptApiView,
    WorkspaceInvitationDeclineApiView,
    WorkspaceInvitationListApiView,
    WorkspaceMemberCreateApiView,
    WorkspaceMemberDetailApiView,
)

app_name = "core-api"

urlpatterns = [
    path("analyses/", AnalysisListCreateApiView.as_view(), name="analysis-list-create"),
    path("analyses/<uuid:analysis_id>/", AnalysisDetailApiView.as_view(), name="analysis-detail"),
    path(
        "analyses/<uuid:analysis_id>/regenerate/",
        AnalysisRegenerateApiView.as_view(),
        name="analysis-regenerate",
    ),
    path(
        "analyses/<uuid:analysis_id>/validate/",
        AnalysisValidateApiView.as_view(),
        name="analysis-validate",
    ),
    path(
        "analyses/<uuid:analysis_id>/diff/",
        AnalysisDiffApiView.as_view(),
        name="analysis-diff",
    ),
    path(
        "analyses/<uuid:analysis_id>/preview/",
        AnalysisPreviewApiView.as_view(),
        name="analysis-preview",
    ),
    path(
        "analyses/<uuid:analysis_id>/github-pr/",
        AnalysisGitHubPrApiView.as_view(),
        name="analysis-github-pr",
    ),
    path(
        "analyses/<uuid:analysis_id>/download/",
        AnalysisDownloadApiView.as_view(),
        name="analysis-download",
    ),
    path("artifacts/<uuid:artifact_id>/", ArtifactDetailApiView.as_view(), name="artifact-detail"),
    path("jobs/<uuid:job_id>/", ExecutionJobDetailApiView.as_view(), name="execution-job-detail"),
    path("previews/<uuid:preview_id>/", PreviewDetailApiView.as_view(), name="preview-detail"),
    path("previews/<uuid:preview_id>/stop/", PreviewStopApiView.as_view(), name="preview-stop"),
    path("connections/", RepoConnectionListCreateApiView.as_view(), name="connection-list-create"),
    path("connections/<uuid:connection_id>/", RepoConnectionDetailApiView.as_view(), name="connection-detail"),
    path("workspaces/", WorkspaceListCreateApiView.as_view(), name="workspace-list-create"),
    path(
        "workspace-invitations/",
        WorkspaceInvitationListApiView.as_view(),
        name="workspace-invitation-list",
    ),
    path(
        "workspace-invitations/<uuid:invitation_id>/accept/",
        WorkspaceInvitationAcceptApiView.as_view(),
        name="workspace-invitation-accept",
    ),
    path(
        "workspace-invitations/<uuid:invitation_id>/decline/",
        WorkspaceInvitationDeclineApiView.as_view(),
        name="workspace-invitation-decline",
    ),
    path(
        "workspaces/<uuid:workspace_id>/members/",
        WorkspaceMemberCreateApiView.as_view(),
        name="workspace-member-create",
    ),
    path(
        "workspaces/<uuid:workspace_id>/members/<uuid:membership_id>/",
        WorkspaceMemberDetailApiView.as_view(),
        name="workspace-member-detail",
    ),
]
