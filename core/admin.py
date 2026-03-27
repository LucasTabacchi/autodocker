from django.contrib import admin

from core.models import GeneratedArtifact, ProjectAnalysis


class GeneratedArtifactInline(admin.TabularInline):
    model = GeneratedArtifact
    extra = 0
    readonly_fields = ("path", "kind", "updated_at")
    fields = ("path", "kind", "description", "updated_at")


@admin.register(ProjectAnalysis)
class ProjectAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "project_name",
        "owner",
        "source_type",
        "status",
        "detected_framework",
        "confidence",
        "created_at",
    )
    list_filter = ("status", "source_type", "detected_framework")
    search_fields = ("project_name", "repository_url", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at", "analysis_payload", "job_id")
    inlines = [GeneratedArtifactInline]


@admin.register(GeneratedArtifact)
class GeneratedArtifactAdmin(admin.ModelAdmin):
    list_display = ("path", "kind", "analysis", "updated_at")
    list_filter = ("kind",)
    search_fields = ("path", "analysis__project_name")

# Register your models here.
