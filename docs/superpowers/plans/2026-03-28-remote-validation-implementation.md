# Remote Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production `validate` execution from local Docker host execution to a GitHub Actions-backed remote executor while preserving the current local validation path.

**Architecture:** Keep `ExecutionJob` as the single source of truth for validation lifecycle, add a bundle builder plus a remote validation backend, and let `BuildValidationService` choose between `local` and `github_actions` based on environment. The API and UI remain stable; only the execution path changes. A dedicated executor workflow in a private GitHub repository runs Docker validation and returns normalized artifacts and logs.

**Tech Stack:** Django 5, DRF, existing async job runner, GitHub REST API, private object storage, Docker build/compose validation, unittest/pytest-style Django test suite

---

### Task 1: Add failing tests for backend selection and remote validation contracts

**Files:**
- Modify: `core/tests.py`

- [ ] **Step 1: Write failing tests for backend selection and remote metadata**

Add tests near the existing validation coverage in `core/tests.py` that assert:

```python
@override_settings(
    AUTODOCKER_ENABLE_RUNTIME_JOBS=True,
    AUTODOCKER_VALIDATION_BACKEND="github_actions",
)
@patch("core.services.build_validation.RemoteValidationService.validate")
def test_validate_endpoint_uses_remote_backend(self, mock_remote_validate):
    mock_remote_validate.return_value = BuildValidationResult(
        success=True,
        command=["remote", "validate"],
        logs="remote ok",
        image_tag="",
    )
    response = self._post_analysis(
        files={
            "demo/package.json": json.dumps(
                {
                    "name": "demo",
                    "scripts": {"build": "next build", "start": "next start"},
                    "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                }
            )
        }
    )
    analysis_id = response.json()["id"]

    validate_response = self.client.post(
        reverse("core-api:analysis-validate", args=[analysis_id])
    )

    self.assertEqual(validate_response.status_code, 202)
    payload = validate_response.json()
    self.assertEqual(payload["status"], ExecutionJob.Status.READY)
    self.assertEqual(payload["result_payload"]["executor"], "github_actions")
```

```python
def test_remote_validation_result_payload_is_persisted(self):
    analysis = ProjectAnalysis.objects.create(
        owner=self.user,
        project_name="demo",
        source_type=ProjectAnalysis.SourceType.GIT,
        repository_url="https://github.com/acme/demo",
        status=ProjectAnalysis.Status.READY,
    )
    job = ExecutionJob.objects.create(
        owner=self.user,
        analysis=analysis,
        kind=ExecutionJob.Kind.VALIDATION,
        status=ExecutionJob.Status.QUEUED,
    )

    result = BuildValidationResult(
        success=True,
        command=["docker", "build", "."],
        logs="remote logs",
        image_tag="",
        metadata={
            "validation_backend": "github_actions",
            "workflow_run_id": 123,
            "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
        },
        result_payload={
            "executor": "github_actions",
            "summary": "docker build completed successfully",
            "artifact_urls": {
                "workflow_run": "https://github.com/acme/executor/actions/runs/123",
            },
        },
    )

    self.assertEqual(result.result_payload["executor"], "github_actions")
    self.assertEqual(result.metadata["workflow_run_id"], 123)
```

- [ ] **Step 2: Run the focused validation tests and confirm failure**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- at least one failure because `BuildValidationResult` does not yet support remote metadata/result payload
- or because `BuildValidationService` does not yet dispatch `RemoteValidationService`

- [ ] **Step 3: Commit the failing tests**

```bash
git add core/tests.py
git commit -m "test: cover remote validation backend selection"
```

### Task 2: Extend validation result contracts and settings

**Files:**
- Modify: `core/services/build_validation.py`
- Modify: `config/settings.py`
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Modify: `.env.prod.example`
- Test: `core/tests.py`

- [ ] **Step 1: Add tests for validation backend configuration**

Add a settings-level test:

```python
def test_validation_backend_defaults_to_local(self):
    with patch.dict(os.environ, {}, clear=True):
        self.assertEqual(project_settings.env("AUTODOCKER_VALIDATION_BACKEND", "local"), "local")
```

And one for explicit remote selection:

```python
@override_settings(AUTODOCKER_VALIDATION_BACKEND="github_actions")
def test_validation_backend_can_be_github_actions(self):
    service = BuildValidationService()
    self.assertEqual(service.backend_name, "github_actions")
```

- [ ] **Step 2: Implement the minimal settings and result-shape support**

Update `config/settings.py` to add:

```python
AUTODOCKER_VALIDATION_BACKEND = env(
    "AUTODOCKER_VALIDATION_BACKEND",
    "local",
)
AUTODOCKER_VALIDATION_EXECUTOR_REPO = env("AUTODOCKER_VALIDATION_EXECUTOR_REPO", "")
AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW = env(
    "AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW",
    "validate.yml",
)
AUTODOCKER_VALIDATION_EXECUTOR_TOKEN = env(
    "AUTODOCKER_VALIDATION_EXECUTOR_TOKEN",
    "",
)
AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS = int(
    env("AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS", "86400")
)
AUTODOCKER_VALIDATION_MAX_BUNDLE_MB = int(
    env("AUTODOCKER_VALIDATION_MAX_BUNDLE_MB", "100")
)
```

Update `BuildValidationResult` in `core/services/build_validation.py`:

```python
@dataclass(slots=True)
class BuildValidationResult:
    success: bool
    command: list[str]
    logs: str
    image_tag: str = ""
    metadata: dict | None = None
    result_payload: dict | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["metadata"] = self.metadata or {}
        payload["result_payload"] = self.result_payload or {}
        return payload
```

- [ ] **Step 3: Add env examples for all new variables**

Add these entries to all env templates:

```text
AUTODOCKER_VALIDATION_BACKEND=local
AUTODOCKER_VALIDATION_EXECUTOR_REPO=
AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW=validate.yml
AUTODOCKER_VALIDATION_EXECUTOR_TOKEN=
AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS=86400
AUTODOCKER_VALIDATION_MAX_BUNDLE_MB=100
```

Use `github_actions` only in `.env.prod.example`.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.DatabaseConfigTests core.tests.AnalysisApiTests
```

Expected:
- backend/result-shape tests pass
- remote dispatch tests still fail because bundle and GitHub client do not exist yet

- [ ] **Step 5: Commit the contract and settings changes**

```bash
git add config/settings.py .env.example .env.docker.example .env.prod.example core/services/build_validation.py core/tests.py
git commit -m "feat: add remote validation settings and result contracts"
```

### Task 3: Implement validation bundle creation for Git and ZIP analyses

**Files:**
- Create: `core/services/validation_bundle.py`
- Modify: `core/services/build_validation.py`
- Modify: `core/tests.py`
- Test: `core/tests.py`

- [ ] **Step 1: Add failing bundle tests**

Add tests like:

```python
def _create_ready_zip_analysis(self):
    archive = SimpleUploadedFile(
        "project.zip",
        self._build_zip(
            {
                "sample/package.json": json.dumps(
                    {
                        "name": "sample",
                        "scripts": {"build": "next build", "start": "next start"},
                        "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                    }
                )
            }
        ),
        content_type="application/zip",
    )
    return ProjectAnalysis.objects.create(
        owner=self.user,
        project_name="sample",
        source_type=ProjectAnalysis.SourceType.ZIP,
        archive=archive,
        status=ProjectAnalysis.Status.READY,
    )

def _create_ready_git_analysis(self):
    return ProjectAnalysis.objects.create(
        owner=self.user,
        project_name="demo",
        source_type=ProjectAnalysis.SourceType.GIT,
        repository_url="https://github.com/acme/demo",
        status=ProjectAnalysis.Status.READY,
        analysis_payload={
            "project_name": "demo",
            "components": [{"name": "app", "path": ".", "framework": "Next.js"}],
        },
    )

def test_validation_bundle_service_builds_bundle_from_zip_analysis(self):
    analysis = self._create_ready_zip_analysis()
    service = ValidationBundleService()

    bundle = service.build(analysis)

    self.assertTrue(bundle.bundle_path.exists())
    self.assertTrue(bundle.bundle_size_bytes > 0)
    self.assertTrue(bundle.sha256)
```

```python
def test_validation_bundle_service_overlays_generated_artifacts(self):
    analysis = self._create_ready_git_analysis()
    GeneratedArtifact.objects.update_or_create(
        analysis=analysis,
        path="Dockerfile",
        defaults={
            "kind": GeneratedArtifact.Kind.DOCKERFILE,
            "description": "Dockerfile",
            "content": "FROM node:22-alpine\nRUN echo remote\n",
        },
    )

    bundle = ValidationBundleService().build(analysis)

    with zipfile.ZipFile(bundle.bundle_path, "r") as zipped:
        self.assertIn("Dockerfile", zipped.namelist())
        self.assertIn("RUN echo remote", zipped.read("Dockerfile").decode("utf-8"))
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.RemoteArchiveIngestionTests core.tests.AnalysisApiTests
```

Expected:
- failures because `ValidationBundleService` does not exist yet

- [ ] **Step 3: Implement `ValidationBundleService`**

Create `core/services/validation_bundle.py` with code shaped like:

```python
from __future__ import annotations

import hashlib
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from core.models import ProjectAnalysis
from core.services.ingestion import cleanup_workspace, overlay_generated_artifacts, prepare_source_workspace


@dataclass(slots=True)
class ValidationBundle:
    workspace_root: Path
    bundle_path: Path
    sha256: str
    bundle_size_bytes: int


class ValidationBundleService:
    def build(self, analysis: ProjectAnalysis) -> ValidationBundle:
        temp_root, source_root = prepare_source_workspace(analysis, prefix="autodocker-validate-remote-")
        overlay_generated_artifacts(source_root, list(analysis.artifacts.all()))

        bundle_path = temp_root / "bundle.zip"
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in source_root.rglob("*"):
                if path.is_file():
                    zipped.write(path, path.relative_to(source_root).as_posix())

        digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        size_bytes = bundle_path.stat().st_size
        max_bytes = settings.AUTODOCKER_VALIDATION_MAX_BUNDLE_MB * 1024 * 1024
        if size_bytes > max_bytes:
            cleanup_workspace(temp_root)
            raise RuntimeError("Validation bundle exceeds configured size limit.")

        return ValidationBundle(
            workspace_root=temp_root,
            bundle_path=bundle_path,
            sha256=digest,
            bundle_size_bytes=size_bytes,
        )
```

- [ ] **Step 4: Run focused tests and make them pass**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- bundle-related tests pass

- [ ] **Step 5: Commit the bundle service**

```bash
git add core/services/validation_bundle.py core/services/build_validation.py core/tests.py
git commit -m "feat: build reproducible validation bundles"
```

### Task 4: Add GitHub Actions executor client and remote validation service

**Files:**
- Create: `core/services/github_actions.py`
- Modify: `core/services/build_validation.py`
- Modify: `core/tests.py`
- Test: `core/tests.py`

- [ ] **Step 1: Add failing tests for dispatch and result normalization**

Add tests like:

```python
@patch("core.services.github_actions.GitHubActionsClient.dispatch_validation")
@patch("core.services.github_actions.GitHubActionsClient.wait_for_completion")
def test_remote_validation_service_persists_workflow_metadata(self, mock_wait, mock_dispatch):
    mock_dispatch.return_value = {
        "workflow_run_id": 123,
        "workflow_run_url": "https://github.com/acme/executor/actions/runs/123",
    }
    mock_wait.return_value = {
        "success": True,
        "summary": "docker build completed successfully",
        "command": ["docker", "build", "."],
        "logs": "remote logs",
        "duration_seconds": 12,
    }

    analysis = ProjectAnalysis.objects.create(
        owner=self.user,
        project_name="demo",
        source_type=ProjectAnalysis.SourceType.GIT,
        repository_url="https://github.com/acme/demo",
        status=ProjectAnalysis.Status.READY,
    )
    job = ExecutionJob.objects.create(
        owner=self.user,
        analysis=analysis,
        kind=ExecutionJob.Kind.VALIDATION,
        status=ExecutionJob.Status.QUEUED,
    )

    result = RemoteValidationService().validate(job)

    self.assertTrue(result.success)
    self.assertEqual(result.metadata["workflow_run_id"], 123)
    self.assertEqual(result.result_payload["executor"], "github_actions")
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- failures because `GitHubActionsClient` and `RemoteValidationService` do not exist

- [ ] **Step 3: Implement `GitHubActionsClient`**

Create `core/services/github_actions.py` with a minimal API client:

```python
from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass
from urllib import parse, request

from django.conf import settings


class GitHubActionsError(RuntimeError):
    pass


class GitHubActionsClient:
    api_base = "https://api.github.com"

    def __init__(self) -> None:
        if not settings.AUTODOCKER_VALIDATION_EXECUTOR_REPO:
            raise GitHubActionsError("Missing AUTODOCKER_VALIDATION_EXECUTOR_REPO.")
        if not settings.AUTODOCKER_VALIDATION_EXECUTOR_TOKEN:
            raise GitHubActionsError("Missing AUTODOCKER_VALIDATION_EXECUTOR_TOKEN.")
        self.repo = settings.AUTODOCKER_VALIDATION_EXECUTOR_REPO
        self.workflow = settings.AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW
        self.token = settings.AUTODOCKER_VALIDATION_EXECUTOR_TOKEN

    def dispatch_validation(self, *, job_id: str, bundle_url: str, bundle_sha256: str, analysis_id: str) -> dict:
        self._request(
            f"/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches",
            data={
                "ref": "main",
                "inputs": {
                    "job_id": job_id,
                    "analysis_id": analysis_id,
                    "bundle_url": bundle_url,
                    "bundle_sha256": bundle_sha256,
                },
            },
            method="POST",
        )
        run = self.find_workflow_run(job_id=job_id)
        return {
            "workflow_run_id": run["id"],
            "workflow_run_url": run["html_url"],
        }

    def find_workflow_run(self, *, job_id: str) -> dict:
        response = self._request(
            f"/repos/{self.repo}/actions/workflows/{self.workflow}/runs?event=workflow_dispatch&per_page=20"
        )
        for run in response.get("workflow_runs", []):
            title = (run.get("display_title") or "") + " " + (run.get("name") or "")
            if job_id in title or job_id in json.dumps(run):
                return run
        raise GitHubActionsError(f"No workflow run found for validation job {job_id}.")

    def wait_for_completion(self, *, workflow_run_id: int, timeout_seconds: int = 900) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self._request(f"/repos/{self.repo}/actions/runs/{workflow_run_id}")
            status = run.get("status")
            if status == "completed":
                artifacts = self.download_result_artifacts(workflow_run_id=workflow_run_id)
                return {
                    **artifacts,
                    "success": run.get("conclusion") == "success" and bool(artifacts.get("success")),
                }
            time.sleep(5)
        raise GitHubActionsError(f"Workflow run {workflow_run_id} exceeded polling timeout.")

    def download_result_artifacts(self, *, workflow_run_id: int) -> dict:
        response = self._request(f"/repos/{self.repo}/actions/runs/{workflow_run_id}/artifacts")
        artifact = next(
            item for item in response.get("artifacts", [])
            if item.get("name", "").startswith("validation-result-")
        )
        archive_response = self._request_raw(
            f"/repos/{self.repo}/actions/artifacts/{artifact['id']}/zip"
        )
        with zipfile.ZipFile(io.BytesIO(archive_response)) as zipped:
            result = json.loads(zipped.read("result.json").decode("utf-8"))
            logs = zipped.read("validation.log").decode("utf-8")
        return {**result, "logs": logs}

    def _request(self, path: str, data: dict | None = None, method: str = "GET") -> dict:
        raw = self._request_raw(path, data=data, method=method)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _request_raw(self, path: str, data: dict | None = None, method: str = "GET") -> bytes:
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = request.Request(
            f"{self.api_base}{path}",
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with request.urlopen(req, timeout=60) as response:
            return response.read()
```

Use `urllib.request` to match the existing `github_pr.py` style instead of adding a new HTTP client dependency.

- [ ] **Step 4: Implement `RemoteValidationService` inside `core/services/build_validation.py`**

Add code shaped like:

```python
class RemoteValidationService:
    def __init__(self) -> None:
        self.bundle_service = ValidationBundleService()
        self.github = GitHubActionsClient()

    def validate(self, job: ExecutionJob) -> BuildValidationResult:
        bundle = self.bundle_service.build(job.analysis)
        try:
            bundle_url = self._upload_bundle(bundle)
            dispatch = self.github.dispatch_validation(
                job_id=str(job.id),
                bundle_url=bundle_url,
                bundle_sha256=bundle.sha256,
                analysis_id=str(job.analysis_id),
            )
            remote_result = self.github.wait_for_completion(
                workflow_run_id=dispatch["workflow_run_id"],
            )
            return BuildValidationResult(
                success=bool(remote_result["success"]),
                command=remote_result.get("command", []),
                logs=remote_result.get("logs", ""),
                metadata={
                    "validation_backend": "github_actions",
                    "workflow_run_id": dispatch["workflow_run_id"],
                    "workflow_run_url": dispatch["workflow_run_url"],
                    "bundle_sha256": bundle.sha256,
                },
                result_payload={
                    "executor": "github_actions",
                    "summary": remote_result.get("summary", ""),
                    "artifact_urls": {
                        "workflow_run": dispatch["workflow_run_url"],
                    },
                    "duration_seconds": remote_result.get("duration_seconds"),
                },
            )
        finally:
            cleanup_workspace(bundle.workspace_root)

    def _upload_bundle(self, bundle: ValidationBundle) -> str:
        storage_key = f"validation-bundles/{timezone.now():%Y/%m/%d}/{bundle.bundle_path.name}"
        with bundle.bundle_path.open("rb") as stream:
            saved_key = default_storage.save(storage_key, File(stream))
        return default_storage.url(saved_key)
```

- [ ] **Step 5: Run focused tests and make them pass**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- remote validation service tests pass
- existing local validation tests may still fail until backend selection is wired in

- [ ] **Step 6: Commit the remote validation service**

```bash
git add core/services/github_actions.py core/services/build_validation.py core/tests.py
git commit -m "feat: add github actions validation backend"
```

### Task 5: Wire backend selection through the validation runner and job persistence

**Files:**
- Modify: `core/services/build_validation.py`
- Modify: `core/services/execution_runner.py`
- Modify: `core/tests.py`
- Test: `core/tests.py`

- [ ] **Step 1: Add failing tests for end-to-end job persistence**

Add a test like:

```python
@override_settings(
    AUTODOCKER_ENABLE_RUNTIME_JOBS=True,
    AUTODOCKER_VALIDATION_BACKEND="github_actions",
)
@patch("core.services.build_validation.RemoteValidationService.validate")
def test_execution_runner_merges_remote_metadata_into_job(self, mock_remote_validate):
    analysis = ProjectAnalysis.objects.create(
        owner=self.user,
        project_name="demo",
        source_type=ProjectAnalysis.SourceType.GIT,
        repository_url="https://github.com/acme/demo",
        status=ProjectAnalysis.Status.READY,
    )
    mock_remote_validate.return_value = BuildValidationResult(
        success=True,
        command=["docker", "build", "."],
        logs="remote ok",
        metadata={
            "validation_backend": "github_actions",
            "workflow_run_id": 123,
        },
        result_payload={
            "executor": "github_actions",
            "summary": "remote ok",
        },
    )
    job = ExecutionJob.objects.create(
        owner=self.user,
        analysis=analysis,
        kind=ExecutionJob.Kind.VALIDATION,
    )

    ExecutionJobRunner().run(job)
    job.refresh_from_db()

    self.assertEqual(job.metadata["workflow_run_id"], 123)
    self.assertEqual(job.result_payload["executor"], "github_actions")
    self.assertEqual(job.status, ExecutionJob.Status.READY)
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- job metadata/result payload assertions fail because `ExecutionJobRunner` currently overwrites payloads too narrowly

- [ ] **Step 3: Implement backend selection and metadata merging**

In `BuildValidationService`, add:

```python
class BuildValidationService:
    def __init__(self) -> None:
        self.backend_name = settings.AUTODOCKER_VALIDATION_BACKEND
        self.remote = RemoteValidationService()

    def validate(self, job: ExecutionJob) -> BuildValidationResult:
        if self.backend_name == "github_actions":
            return self.remote.validate(job)
        return self._validate_local(job.analysis)
```

In `ExecutionJobRunner.run`, update the validation branch to merge payloads:

```python
result = self.validation_service.validate(job)
job.result_payload = {
    "success": result.success,
    "command": result.command,
    "logs": result.logs,
    "image_tag": result.image_tag,
    **(result.result_payload or {}),
}
job.metadata = {
    **job.metadata,
    **(result.metadata or {}),
}
job.logs = result.logs
job.status = (
    ExecutionJob.Status.READY
    if result.success
    else ExecutionJob.Status.FAILED
)
```

- [ ] **Step 4: Run focused tests and make them pass**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests core.tests.StackDetectorTests
```

Expected:
- all validation path tests pass

- [ ] **Step 5: Commit the wiring changes**

```bash
git add core/services/build_validation.py core/services/execution_runner.py core/tests.py
git commit -m "feat: wire remote validation through execution jobs"
```

### Task 6: Add executor workflow template and deployment documentation

**Files:**
- Create: `docs/github-actions/validate.yml.example`
- Modify: `README.md`
- Modify: `render.yaml`
- Modify: `.env.prod.example`
- Test: manual verification notes in `README.md`

- [ ] **Step 1: Add a workflow template file for the private executor repo**

Create `docs/github-actions/validate.yml.example` with the core workflow:

```yaml
name: Remote Validate

on:
  workflow_dispatch:
    inputs:
      job_id:
        required: true
        type: string
      analysis_id:
        required: true
        type: string
      bundle_url:
        required: true
        type: string
      bundle_sha256:
        required: true
        type: string

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: Download bundle
        run: curl -L "${{ inputs.bundle_url }}" -o bundle.zip
      - name: Verify checksum
        run: echo "${{ inputs.bundle_sha256 }}  bundle.zip" | sha256sum -c -
      - name: Extract bundle
        run: unzip bundle.zip -d workspace
      - name: Validate compose or docker build
        working-directory: workspace
        run: |
          if [ -f docker-compose.yml ]; then
            docker compose -f docker-compose.yml config
            docker compose -f docker-compose.yml build
          else
            docker build -t autodocker-validate .
          fi
      - name: Write result
        run: |
          cat > result.json <<'JSON'
          {"success": true, "summary": "validation completed", "command": ["docker", "build", "."], "duration_seconds": 0}
          JSON
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: validation-result-${{ inputs.job_id }}
          path: |
            result.json
            workspace/validation.log
```

- [ ] **Step 2: Document production configuration**

Update `README.md` to add:
- new env vars
- remote validation backend explanation
- executor repo setup
- Render-specific production setup

Update `render.yaml` to include:

```yaml
      - key: AUTODOCKER_VALIDATION_BACKEND
        value: "github_actions"
      - key: AUTODOCKER_VALIDATION_EXECUTOR_REPO
        sync: false
      - key: AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW
        value: "validate.yml"
      - key: AUTODOCKER_VALIDATION_EXECUTOR_TOKEN
        sync: false
```

- [ ] **Step 3: Run a doc/config sanity pass**

Run:

```powershell
git diff -- README.md render.yaml .env.prod.example docs/github-actions/validate.yml.example
```

Expected:
- docs and env references are internally consistent
- Render prod points to `github_actions`

- [ ] **Step 4: Commit docs and deploy config**

```bash
git add README.md render.yaml .env.prod.example docs/github-actions/validate.yml.example
git commit -m "docs: add remote validation executor setup"
```

### Task 7: Verify the full implementation

**Files:**
- Modify: `core/tests.py` if verification exposes a missing case
- Modify: `README.md` if verification exposes missing setup notes

- [ ] **Step 1: Run targeted tests for remote validation**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test core.tests.AnalysisApiTests
```

Expected:
- all validation-related tests pass for both local and remote backends

- [ ] **Step 2: Run the full Django suite**

Run:

```powershell
& '.\.venv\Scripts\python.exe' manage.py test
```

Expected:
- full suite passes

- [ ] **Step 3: Manual configuration verification**

Check:
- `AUTODOCKER_VALIDATION_BACKEND=github_actions` in production env
- executor repo exists and workflow file matches the documented template
- executor token can dispatch and read workflow artifacts
- storage bucket for bundles is private

- [ ] **Step 4: Commit any final verification-driven fixes**

```bash
git add core/tests.py README.md render.yaml .env.example .env.docker.example .env.prod.example config/settings.py core/services/build_validation.py core/services/execution_runner.py core/services/github_actions.py core/services/validation_bundle.py docs/github-actions/validate.yml.example
git commit -m "test: verify remote validation integration"
```
