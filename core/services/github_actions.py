from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass
from urllib import error, parse, request

from django.conf import settings


class GitHubActionsError(RuntimeError):
    """Raised when the GitHub Actions executor workflow fails."""


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pragma: no cover - exercised indirectly
        return None


@dataclass(slots=True)
class GitHubActionsClient:
    token: str | None = None
    repo: str | None = None
    workflow: str | None = None
    api_base: str = "https://api.github.com"
    request_timeout: int = 60

    def __post_init__(self) -> None:
        self.token = self.token or settings.AUTODOCKER_VALIDATION_EXECUTOR_TOKEN
        self.repo = self.repo or settings.AUTODOCKER_VALIDATION_EXECUTOR_REPO
        self.workflow = self.workflow or settings.AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW

    def dispatch_validation(
        self,
        *,
        job_id: str,
        bundle_url: str,
        bundle_sha256: str,
        analysis_id: str,
    ) -> dict:
        self._request(
            "POST",
            f"/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "job_id": job_id,
                    "analysis_id": analysis_id,
                    "bundle_url": bundle_url,
                    "bundle_sha256": bundle_sha256,
                },
            },
        )
        run = self.find_workflow_run(job_id)
        return {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_run_url": run["workflow_run_url"],
        }

    def find_workflow_run(
        self,
        job_id: str,
        *,
        timeout_seconds: int = 60,
        poll_interval_seconds: int = 2,
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while True:
            payload = self._request(
                "GET",
                f"/repos/{self.repo}/actions/workflows/{self.workflow}/runs?per_page=20&event=workflow_dispatch",
            )
            runs = payload.get("workflow_runs") or []
            for run in runs:
                haystack = " ".join(
                    str(value)
                    for value in [
                        run.get("display_title", ""),
                        run.get("name", ""),
                        run.get("head_branch", ""),
                    ]
                    if value
                )
                if job_id and job_id not in haystack and job_id not in json.dumps(run):
                    continue
                return {
                    "workflow_run_id": run["id"],
                    "workflow_run_url": run.get("html_url", ""),
                }
            if time.monotonic() >= deadline:
                raise GitHubActionsError(f"No se encontró un workflow run para el job {job_id}.")
            time.sleep(poll_interval_seconds)

    def wait_for_completion(
        self,
        workflow_run_id: int,
        *,
        poll_interval_seconds: int = 5,
        timeout_seconds: int = 3600,
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while True:
            run = self._request(
                "GET",
                f"/repos/{self.repo}/actions/runs/{workflow_run_id}",
            )
            if run.get("status") == "completed":
                artifacts = self.download_result_artifacts(workflow_run_id)
                return {
                    "success": run.get("conclusion") == "success" and bool(artifacts.get("success")),
                    "summary": artifacts.get("summary", ""),
                    "command": artifacts.get("command", []),
                    "logs": artifacts.get("logs", ""),
                    "duration_seconds": artifacts.get("duration_seconds", 0),
                    "artifact_urls": {
                        "workflow_run": run.get("html_url", ""),
                    },
                }
            if time.monotonic() >= deadline:
                raise GitHubActionsError(
                    f"Timeout esperando el workflow run {workflow_run_id}."
                )
            time.sleep(poll_interval_seconds)

    def download_result_artifacts(self, workflow_run_id: int) -> dict:
        payload = self._request(
            "GET",
            f"/repos/{self.repo}/actions/runs/{workflow_run_id}/artifacts",
        )
        artifacts = payload.get("artifacts") or []
        artifact = next(
            (
                item
                for item in artifacts
                if (item.get("name") or "") in {"validation-results"}
                or (item.get("name") or "").startswith("validation-result-")
            ),
            None,
        )
        if artifact is None:
            raise GitHubActionsError(
                f"No se encontró el artifact de validación para el workflow run {workflow_run_id}."
            )

        raw = self._request_raw(artifact["archive_download_url"])
        with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
            names = zipped.namelist()
            result_name = next((name for name in names if name.endswith("result.json")), "")
            log_name = next((name for name in names if name.endswith("validation.log")), "")
            if not result_name:
                raise GitHubActionsError(
                    f"El artifact de validación {artifact.get('name', '')} no contiene result.json."
                )

            result = json.loads(zipped.read(result_name).decode("utf-8"))
            if log_name:
                result["logs"] = zipped.read(log_name).decode("utf-8")
            return result

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = request.Request(
            f"{self.api_base}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=self._headers(body is not None),
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.request_timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise GitHubActionsError(detail or f"GitHub devolvió HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise GitHubActionsError(str(exc)) from exc

    def _request_raw(self, url: str) -> bytes:
        req = request.Request(url, headers=self._headers(False), method="GET")
        opener = request.build_opener(_NoRedirectHandler)
        try:
            with opener.open(req, timeout=self.request_timeout) as response:
                return response.read()
        except error.HTTPError as exc:
            redirect_location = exc.headers.get("Location", "")
            if exc.code in {301, 302, 303, 307, 308} and redirect_location:
                redirect_req = request.Request(redirect_location, method="GET")
                with request.urlopen(redirect_req, timeout=self.request_timeout) as response:
                    return response.read()
            detail = exc.read().decode("utf-8", errors="ignore")
            raise GitHubActionsError(detail or f"GitHub devolvió HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise GitHubActionsError(str(exc)) from exc

    def _headers(self, include_json: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers
