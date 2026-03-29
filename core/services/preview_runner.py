from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request

from django.conf import settings


class PreviewRunnerError(RuntimeError):
    """Raised when the remote preview runner request fails."""


@dataclass(slots=True)
class PreviewRunnerClient:
    base_url: str | None = None
    token: str | None = None
    request_timeout: int | None = None

    def __post_init__(self) -> None:
        configured_base_url = self.base_url or settings.AUTODOCKER_PREVIEW_RUNNER_BASE_URL
        configured_token = self.token or settings.AUTODOCKER_PREVIEW_RUNNER_TOKEN

        self.base_url = (configured_base_url or "").rstrip("/")
        self.token = configured_token or ""
        self.request_timeout = int(
            self.request_timeout
            or settings.AUTODOCKER_PREVIEW_RUNNER_REQUEST_TIMEOUT
        )

    def create_preview(
        self,
        *,
        preview_id: str,
        analysis_id: str,
        project_name: str,
        bundle_url: str,
        bundle_sha256: str,
        requested_ttl_seconds: int,
        metadata: dict[str, object] | None = None,
    ) -> dict:
        return self._request(
            "POST",
            "/previews",
            {
                "preview_id": preview_id,
                "analysis_id": analysis_id,
                "project_name": project_name,
                "bundle_url": bundle_url,
                "bundle_sha256": bundle_sha256,
                "requested_ttl_seconds": requested_ttl_seconds,
                "metadata": metadata or {},
            },
        )

    def get_preview(self, preview_id: str) -> dict:
        return self._request("GET", f"/previews/{preview_id}")

    def get_logs(self, preview_id: str) -> dict:
        return self._request("GET", f"/previews/{preview_id}/logs")

    def stop_preview(self, preview_id: str) -> dict:
        return self._request("POST", f"/previews/{preview_id}/stop", {})

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.base_url:
            raise PreviewRunnerError("AUTODOCKER_PREVIEW_RUNNER_BASE_URL no está configurado.")
        if not self.token:
            raise PreviewRunnerError("AUTODOCKER_PREVIEW_RUNNER_TOKEN no está configurado.")

        req = request.Request(
            f"{self.base_url}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=self._headers(include_json=body is not None),
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.request_timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise PreviewRunnerError(detail or f"Preview runner devolvió HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise PreviewRunnerError(str(exc)) from exc

    def _headers(self, *, include_json: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers
