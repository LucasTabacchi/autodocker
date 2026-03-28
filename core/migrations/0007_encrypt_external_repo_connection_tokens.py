from __future__ import annotations

from django.db import migrations

from core.crypto import is_encrypted_secret, seal_secret


def encrypt_external_repo_tokens(apps, schema_editor):
    ExternalRepoConnection = apps.get_model("core", "ExternalRepoConnection")
    for connection in ExternalRepoConnection.objects.exclude(access_token="").iterator():
        if is_encrypted_secret(connection.access_token):
            continue
        connection.access_token = seal_secret(connection.access_token)
        connection.save(update_fields=["access_token", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_workspaceinvitation"),
    ]

    operations = [
        migrations.RunPython(encrypt_external_repo_tokens, migrations.RunPython.noop),
    ]
