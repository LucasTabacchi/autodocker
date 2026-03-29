from __future__ import annotations

from rest_framework import serializers

from core.models import PreviewRunnerSession


class PreviewRunnerSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreviewRunnerSession
        fields = (
            "preview_id",
            "analysis_id",
            "project_name",
            "status",
            "runtime_kind",
            "access_url",
            "ports",
            "resource_names",
            "logs",
            "expires_at",
            "created_at",
            "updated_at",
        )


class PreviewRunnerSessionCreateSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()
    analysis_id = serializers.UUIDField()
    project_name = serializers.CharField(max_length=255)
    bundle_url = serializers.URLField()
    bundle_sha256 = serializers.CharField(min_length=64, max_length=64)
    requested_ttl_seconds = serializers.IntegerField(min_value=1)
    metadata = serializers.JSONField(required=False)
