# Remote Validation Design

**Date:** 2026-03-28

**Status:** Approved for planning

**Goal**

Enable real `validate` execution in production for both Git and ZIP analyses without depending on the Render host Docker daemon and without introducing a dedicated VM.

## Scope

This design covers only remote build validation.

Included:
- `validate` execution for analyses created from Git repositories
- `validate` execution for analyses created from uploaded ZIP archives
- asynchronous dispatch, polling, result capture, and error reporting
- private bundle storage and short-lived validation artifacts
- configuration required to run the web app on Render and execute validation in GitHub Actions

Excluded:
- executable previews
- public preview URLs
- live log streaming
- repository write-back during validation
- replacing the existing GitHub PR integration

## Current State

AutoDocker currently performs validation locally through `BuildValidationService`, which materializes the source, overlays generated artifacts, and runs `docker build` or `docker compose build` on the same machine as Django. This works in local Docker-based development, but not on Render free because the web service does not have a Docker host available.

The current project already has the pieces needed to support an asynchronous remote flow:
- validation jobs already use `ExecutionJob`
- validation is already triggered through `POST /api/analyses/{id}/validate/`
- the UI already polls `GET /api/jobs/{id}/`
- uploaded archives can already live in remote storage

The main gap is the execution backend.

## Recommended Architecture

Use a dedicated private GitHub repository as a validation executor. AutoDocker will upload a reproducible validation bundle to private object storage, dispatch a GitHub Actions workflow in the executor repository, and poll GitHub for completion and artifacts.

### High-Level Flow

1. The user clicks `Validate`.
2. AutoDocker creates an `ExecutionJob(kind=validation)`.
3. The validation runner selects a backend:
- local backend for local development
- GitHub Actions backend for production
4. AutoDocker materializes the analysis source, overlays the current generated artifacts, creates a validation bundle, computes a checksum, and uploads the bundle to private storage.
5. AutoDocker dispatches a GitHub Actions workflow in the executor repository using a system token.
6. The workflow downloads the bundle, verifies its checksum, extracts it, and runs:
- `docker compose -f docker-compose.yml config` and `docker compose -f docker-compose.yml build` when a compose file exists
- `docker build -t autodocker-validate .` otherwise
7. The workflow uploads:
- `result.json`
- `validation.log`
8. AutoDocker polls the workflow run until completion, downloads the result artifacts, and updates the original `ExecutionJob`.
9. The UI keeps polling the job endpoint and displays the final state exactly as it does today.

## Design Principles

- Keep the public API unchanged.
- Keep validation asynchronous.
- Use one execution path for both Git and ZIP analyses.
- Validate exactly what the user sees, not the original source alone.
- Keep executor credentials out of the database.
- Keep validation bundles private and short-lived.

## Components

### 1. Validation Backend Selection

`BuildValidationService` becomes a thin façade that selects one of two execution backends:
- `local`
- `github_actions`

The local backend preserves the current behavior for local development and test scenarios.

The GitHub Actions backend becomes the production path.

### 2. ValidationBundleService

This new service is responsible for building the exact validation input.

Responsibilities:
- materialize the analysis source into a temporary workspace
- overlay all current generated artifacts onto that workspace
- create a reproducible ZIP bundle
- compute `sha256`
- return bundle metadata needed for dispatch and later verification

The bundle must contain only what is needed to validate the generated Docker artifacts and the project source. It must not include unrelated application state from AutoDocker itself.

### 3. RemoteValidationService

This new service is responsible for remote orchestration.

Responsibilities:
- upload the validation bundle to private storage
- dispatch the GitHub Actions workflow
- persist remote metadata into the job
- poll the run state
- download result artifacts when the run completes
- map remote results to the existing `ExecutionJob` lifecycle

### 4. GitHubActionsClient

This is a small wrapper around the GitHub API for the executor repository.

Responsibilities:
- trigger `workflow_dispatch`
- locate the matching workflow run
- read run status and conclusion
- download workflow artifacts
- build a stable GitHub run URL for operator visibility

### 5. Polling/Reconciliation Path

The first version should not block on long-running validation inside the request-response cycle. Polling should happen asynchronously through the existing job execution path.

The same service that dispatches the remote workflow should also handle polling and final reconciliation inside the job runner. This keeps the feature compatible with current `thread`, `inline`, and future `celery` modes.

## Data Model and Contracts

No new database model is required in the first phase.

### `ExecutionJob.metadata`

Use `metadata` to store remote execution state:

```json
{
  "validation_backend": "github_actions",
  "bundle_storage_key": "validation-bundles/2026/03/28/job-uuid/source.zip",
  "bundle_sha256": "hex-checksum",
  "bundle_size_bytes": 123456,
  "executor_repo": "owner/autodocker-validator",
  "workflow_id": "validate.yml",
  "workflow_run_id": 123456789,
  "workflow_run_url": "https://github.com/owner/autodocker-validator/actions/runs/123456789",
  "remote_status": "queued",
  "submitted_at": "2026-03-28T12:00:00Z",
  "last_polled_at": "2026-03-28T12:00:30Z"
}
```

### `ExecutionJob.result_payload`

Use `result_payload` to store the normalized final result:

```json
{
  "success": true,
  "executor": "github_actions",
  "summary": "docker compose build completed successfully",
  "command": [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "build"
  ],
  "logs": "trimmed final logs",
  "duration_seconds": 86,
  "artifact_urls": {
    "workflow_run": "https://github.com/owner/autodocker-validator/actions/runs/123456789"
  }
}
```

### `ExecutionJob.status`

Status mapping remains:
- `QUEUED` before dispatch
- `RUNNING` after dispatch and during polling
- `READY` when validation succeeds
- `FAILED` when dispatch fails, polling fails terminally, or the workflow concludes unsuccessfully

## API and UI Impact

No public API changes are required for the first phase.

Endpoints remain:
- `POST /api/analyses/{id}/validate/`
- `GET /api/jobs/{id}/`

UI behavior remains:
- create a validation job
- poll the job endpoint
- render status and logs

User-visible differences:
- validation will take longer in production because it waits on GitHub Actions
- logs may appear in larger increments instead of streaming continuously
- failures can now include GitHub workflow errors and executor artifact download errors

## Storage Design

Validation bundles must be uploaded to private object storage.

Requirements:
- private bucket only
- deterministic storage key prefix for validation jobs
- configurable TTL for cleanup
- size limit before upload
- checksum persisted in job metadata

Suggested storage key shape:

```text
validation-bundles/YYYY/MM/DD/<execution-job-id>/bundle.zip
```

Recommended retention:
- bundles: 24 hours
- workflow artifacts: 24 to 72 hours
- normalized logs/results in database: kept with the job record

## GitHub Executor Repository

The executor repository is a private repository dedicated to validation runs.

Responsibilities:
- host the validation workflow
- run Docker builds in GitHub-hosted runners
- download validation bundles
- verify checksum
- execute validation commands
- upload structured results and logs

### Workflow Inputs

The workflow needs:
- `job_id`
- `bundle_url` or storage reference
- `bundle_sha256`
- `analysis_id`

### Workflow Outputs

The workflow uploads:
- `result.json`
- `validation.log`

`result.json` must contain:

```json
{
  "success": true,
  "summary": "docker build completed successfully",
  "command": ["docker", "build", "-t", "autodocker-validate", "."],
  "duration_seconds": 52
}
```

## Security Model

### User Credentials

User GitHub tokens currently stored for PR creation remain user-scoped and must not be reused for remote validation execution.

### System Executor Credentials

Remote validation uses a separate system credential defined only in environment variables.

Required permissions:
- dispatch workflows in the executor repository
- read workflow runs
- read workflow artifacts

Not required:
- write access to arbitrary user repositories
- repo administration
- organization-wide scopes beyond the executor repository

### Bundle Security

Validation bundles must:
- be stored privately
- be downloaded only by the executor workflow
- be validated with checksum before extraction
- be deleted or allowed to expire automatically

The workflow must not trust bundle contents blindly. Extraction should reject path traversal and should run with normal GitHub-hosted runner isolation.

## Configuration

Add the following environment variables:

```text
AUTODOCKER_VALIDATION_BACKEND=local|github_actions
AUTODOCKER_VALIDATION_EXECUTOR_REPO=owner/autodocker-validator
AUTODOCKER_VALIDATION_EXECUTOR_WORKFLOW=validate.yml
AUTODOCKER_VALIDATION_EXECUTOR_TOKEN=github-token-with-executor-workflow-access
AUTODOCKER_VALIDATION_BUNDLE_TTL_SECONDS=86400
AUTODOCKER_VALIDATION_MAX_BUNDLE_MB=100
```

### Environment Strategy

Local development:
- `AUTODOCKER_VALIDATION_BACKEND=local`

Render production:
- `AUTODOCKER_VALIDATION_BACKEND=github_actions`

This keeps local development fast while moving production validation off-host.

## Failure Handling

### Dispatch Failures

Examples:
- invalid GitHub token
- workflow not found
- rate limiting
- storage upload failure

Behavior:
- mark job `FAILED`
- persist a short actionable error in `logs`
- include the backend name in `result_payload`

### Remote Workflow Failures

Examples:
- bundle download failure
- checksum mismatch
- invalid ZIP extraction
- Docker build failure
- compose config failure

Behavior:
- mark job `FAILED`
- persist the final log snapshot
- persist summary from `result.json` when available

### Polling Failures

Examples:
- temporary GitHub API outage
- transient artifact read failure

Behavior:
- retry within the job runner polling window
- fail terminally only after the configured timeout or retry budget is exhausted

## Testing Strategy

### Unit Tests

Add tests for:
- backend selection based on env
- bundle creation from Git and ZIP analyses
- checksum generation
- job metadata persistence
- workflow dispatch request formatting
- remote result normalization
- failure mapping from remote states to `ExecutionJob`

### Integration Tests

Mock GitHub API calls and verify:
- validation requests still return `202`
- jobs transition from `QUEUED` to `RUNNING` to terminal states
- result artifacts are read and persisted correctly
- local backend remains available for local runs and tests

### Manual Verification

Manual verification should confirm:
- local validation still works in Docker-based local development
- production validation on Render creates a GitHub workflow run
- successful runs show final logs in AutoDocker
- failed runs surface a useful summary in the UI

## Rollout Plan

Phase 1:
- add backend selection
- implement remote bundle creation and workflow dispatch
- support terminal success and failure states

Phase 2:
- improve polling diagnostics and log trimming
- add bundle cleanup automation

Future phase:
- extend the same executor architecture to previews if product demand justifies it

## Risks and Trade-Offs

- GitHub Actions introduces queue latency, so validation is not instantaneous.
- Production validation becomes dependent on GitHub availability and API quotas.
- ZIP support is viable only because the bundle is uploaded to private storage first.
- Logs will be less interactive than a local Docker session.

These trade-offs are acceptable because they remove the Docker host dependency from Render and unify validation for both source types.

## Final Recommendation

Implement remote validation with:
- private GitHub executor repository
- GitHub Actions workflow dispatch
- private validation bundle storage
- existing `ExecutionJob` reuse
- local backend preserved for development

This is the smallest architecture change that makes production validation real for both Git and ZIP analyses without introducing VM operations.
