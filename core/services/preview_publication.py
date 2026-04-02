from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

from core.services.runtime import run_command


def runner_managed_public_domain() -> str:
    strategy = (getattr(settings, "AUTODOCKER_PREVIEW_URL_STRATEGY", "") or "").strip().lower()
    domain = (getattr(settings, "AUTODOCKER_PREVIEW_PUBLIC_BASE_DOMAIN", "") or "").strip(".")
    if strategy != "runner_managed" or not domain:
        return ""
    return domain


def preview_route_id(preview_id) -> str:
    return f"prv-{str(preview_id).replace('-', '')[:12]}"


def preview_public_host(preview_id) -> str:
    domain = runner_managed_public_domain()
    if not domain:
        return ""
    return f"{preview_route_id(preview_id)}.{domain}"


def preview_public_url(preview_id) -> str:
    host = preview_public_host(preview_id)
    return f"https://{host}" if host else ""


class PreviewPublicationService:
    def enabled(self) -> bool:
        return bool(
            getattr(settings, "AUTODOCKER_PREVIEW_CADDY_ENABLED", False)
            and runner_managed_public_domain()
        )

    def publish(self, preview_run, service_urls: dict[str, list[str]]) -> dict[str, list[str]]:
        if not service_urls or not self.enabled():
            return service_urls

        primary_service_name = next(iter(service_urls))
        upstream_url = service_urls[primary_service_name][0]
        parsed = urlparse(upstream_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            raise RuntimeError("La preview no expuso un upstream HTTP válido para Caddy.")

        route_path = self._route_path(preview_run)
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_text(
            self._route_config(preview_run, parsed.hostname, parsed.port),
            encoding="utf-8",
        )
        self._reload()
        return {primary_service_name: [preview_public_url(preview_run.id)]}

    def unpublish(self, preview_run) -> None:
        if not self.enabled():
            return
        route_path = self._route_path(preview_run)
        if route_path.exists():
            route_path.unlink()
            self._reload()

    def reconcile(self, active_preview_ids: list[str]) -> int:
        if not self.enabled():
            return 0
        routes_dir = self._routes_dir()
        if not routes_dir.exists():
            return 0
        active_route_ids = {preview_route_id(preview_id) for preview_id in active_preview_ids}
        removed = 0
        for route_path in routes_dir.glob("prv-*.caddy"):
            if route_path.stem in active_route_ids:
                continue
            route_path.unlink()
            removed += 1
        if removed:
            self._reload()
        return removed

    def _route_config(self, preview_run, upstream_host: str, upstream_port: int) -> str:
        host = preview_public_host(preview_run.id)
        return (
            f"{host} {{\n"
            f"    reverse_proxy {upstream_host}:{upstream_port}\n"
            "}\n"
        )

    def _routes_dir(self) -> Path:
        return Path(getattr(settings, "AUTODOCKER_PREVIEW_CADDY_ROUTES_DIR", "/etc/caddy/routes"))

    def _route_path(self, preview_run) -> Path:
        return self._routes_dir() / f"{preview_route_id(preview_run.id)}.caddy"

    def _reload(self) -> None:
        config_path = getattr(settings, "AUTODOCKER_PREVIEW_CADDY_CONFIG_PATH", "/etc/caddy/Caddyfile")
        configured_command = getattr(
            settings,
            "AUTODOCKER_PREVIEW_CADDY_RELOAD_COMMAND",
            "",
        )
        command = configured_command or f"caddy reload --config {config_path}"
        run_command(shlex.split(command), Path.cwd(), timeout=60)
