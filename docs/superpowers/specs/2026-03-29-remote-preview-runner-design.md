# Remote Preview Runner Design

**Date:** 2026-03-29

**Status:** Proposed and approved in design conversation

**Goal**

Enable production previews through a dedicated remote preview runner hosted on a separate preview-only machine, with public but aggressively short-lived URLs, while preserving the current local Docker preview flow for development.

## Scope

This design covers only remote preview execution.

Included:
- remote preview execution for analyses created from Git repositories
- remote preview execution for analyses created from uploaded ZIP archives
- reusable bundle generation based on source + edited artifacts
- lifecycle management for preview creation, readiness, expiration, stop, and cleanup
- public preview URLs with aggressive TTL
- integration contract between AutoDocker and a separate `preview-runner` service
- dual backend support: local preview in development, remote preview in production

Excluded:
- long-lived preview environments
- persistent preview data or attached databases
- multi-runner scheduling and auto-scaling
- live terminal streaming or bidirectional remote shells
- multiple concurrent previews per analysis in the first version
- preview resume after stop or expiration
- replacing the existing local preview path for development

## Current State

AutoDocker currently executes previews locally through `PreviewService`, which materializes the source, overlays generated artifacts, and then runs either Docker Compose or a single Docker container on the same machine as the Django process or worker. This works in development where Docker host access is available, but it is not suitable for current production because:

- the production deployment disables runtime jobs
- production does not expose a Docker host to the Render service
- preview URLs require a runtime that stays alive and can be reached from the public internet

The project already has important building blocks that can be reused:

- `ExecutionJob(kind=preview)` already represents preview lifecycle at the product layer
- `PreviewRun` already persists preview metadata, URLs, logs, ports, and status
- source materialization and artifact overlay already exist
- remote bundle creation already exists for validation and can be adapted conceptually for previews
- the UI already polls preview state and renders preview URLs/logs

The missing piece is a production-grade remote preview execution backend.

## Recommended Architecture

Use a dedicated `preview-runner` service on a host reserved for previews. AutoDocker remains the control plane and source of truth for product state. The runner owns actual preview execution, URL publication, runtime cleanup, and readiness checks.

### High-Level Topology

- `AutoDocker web` remains the product-facing control plane
- `preview-runner` exposes a private API consumed only by AutoDocker
- the preview host runs:
  - Docker
  - the `preview-runner` service
  - a reverse proxy such as Caddy
- public preview traffic resolves to the preview host
- the runner maps each preview subdomain to the correct local container/service

### Why This Architecture

This approach gives a clean separation between:

- product orchestration and user-facing state in AutoDocker
- risky execution of third-party application code inside a preview-specific environment

It is the smallest architecture that is still correct for a product-facing preview feature. It avoids forcing GitHub Actions into a workload it is not designed for, avoids exposing Docker privileges to the production web process, and keeps the door open to multiple runners later without redesigning the control plane.

## Rejected Alternatives

### 1. GitHub Actions plus public tunnel

Rejected for V1 because previews are long-lived, URL-bearing workloads. GitHub Actions runners are ephemeral and awkward for:

- stable preview URLs
- explicit stop behavior
- readiness checks over time
- operator debugging
- cleanup guarantees

This option is acceptable for experiments, not for a product-facing preview system.

### 2. Running previews directly from the production web/worker host

Rejected because it mixes preview execution with core product infrastructure. This is operationally and security-wise the wrong boundary for code that may come from end users.

## URL and Publication Model

Each preview receives:

- a globally unique `preview_id`
- a public ephemeral subdomain
- an explicit `expires_at`

Example URL shape:

```text
https://prv-abc123.previews.example.com
```

The reverse proxy on the preview host terminates TLS and routes each preview subdomain to the correct container or Compose service selected by the runner.

### Publication Rules for V1

- previews are public but aggressively short-lived
- default TTL should be between 30 and 60 minutes
- TTL is always enforced server-side by the runner
- only one active preview per analysis is allowed in the first version
- URLs are never reused after stop or expiration

## Execution Model

### Dual Backend Strategy

AutoDocker should keep two preview backends:

- `local` for development and local operators with Docker host access
- `remote_runner` for production

This mirrors the validation architecture and avoids breaking the current developer workflow.

### Preview Creation Flow

1. User clicks `Preview`.
2. AutoDocker creates or reuses an `ExecutionJob(kind=preview)` and a `PreviewRun`.
3. AutoDocker materializes the source and overlays the current generated artifacts.
4. AutoDocker creates a reproducible preview bundle.
5. AutoDocker uploads that bundle to private storage and obtains a signed URL.
6. AutoDocker calls `preview-runner` `POST /previews`.
7. The runner downloads and verifies the bundle.
8. The runner extracts the project, starts the preview runtime, allocates routing, and waits for readiness.
9. The runner returns `preview_id`, `status`, `access_url`, `expires_at`, and runtime metadata.
10. AutoDocker stores the synchronized state in `PreviewRun`.
11. AutoDocker continues polling the runner until the preview becomes `ready`, `failed`, `stopped`, or `expired`.

### Runtime Behavior

The runner supports:

- single-container previews when a generated `docker-compose.yml` is absent
- Compose-based previews when a generated `docker-compose.yml` exists

It should follow the same practical logic as the current local preview implementation:

- prefer Compose when present
- expose only user-facing services, not internal auxiliaries such as Redis or Postgres
- perform readiness checks before claiming `ready`

## Contract Between AutoDocker and Preview Runner

The runner exposes a small HTTP API. AutoDocker remains the source of truth for the product, but the runner is the source of truth for remote runtime state.

### `POST /previews`

Purpose:
- create a new preview execution request

Request body:

```json
{
  "preview_id": "uuid",
  "analysis_id": "uuid",
  "project_name": "demo-app",
  "bundle_url": "https://private-storage.example/signed/bundle.zip",
  "bundle_sha256": "hex-checksum",
  "requested_ttl_seconds": 1800,
  "metadata": {
    "generation_profile": "production"
  }
}
```

Response body:

```json
{
  "preview_id": "uuid",
  "status": "starting",
  "runtime_kind": "compose",
  "access_url": "",
  "resource_names": ["prv-demo-web"],
  "expires_at": "2026-03-29T18:35:00Z"
}
```

### `GET /previews/{preview_id}`

Purpose:
- return current preview status

Response body:

```json
{
  "preview_id": "uuid",
  "status": "ready",
  "runtime_kind": "compose",
  "access_url": "https://prv-abc123.previews.example.com",
  "resource_names": ["prv-demo-web"],
  "expires_at": "2026-03-29T18:35:00Z",
  "ports": {
    "web": ["https://prv-abc123.previews.example.com"]
  }
}
```

### `GET /previews/{preview_id}/logs`

Purpose:
- return recent preview logs

Response body:

```json
{
  "preview_id": "uuid",
  "logs": "last 200 lines of preview logs"
}
```

### `POST /previews/{preview_id}/stop`

Purpose:
- stop and clean up a preview early

Response body:

```json
{
  "preview_id": "uuid",
  "status": "stopped"
}
```

## Preview States

The runner should use these states:

- `queued`
- `starting`
- `ready`
- `failed`
- `stopped`
- `expired`

AutoDocker should normalize these into the existing `PreviewRun` model while preserving the exact runner status in metadata.

## Data Model Impact in AutoDocker

No new user-facing core model is required for V1. Extend the current preview flow instead of inventing parallel preview state.

### `PreviewRun`

Continue using:

- `status`
- `runtime_kind`
- `workspace_path`
- `access_url`
- `ports`
- `resource_names`
- `logs`
- `started_at`
- `finished_at`
- `expires_at`

Recommended additions for V1 implementation:

- remote backend identifier
- remote preview id if different from local id
- sync metadata such as `last_polled_at`

### `ExecutionJob(kind=preview)`

Keep using `ExecutionJob` as the operational wrapper and scheduling abstraction for:

- dispatch to runner
- retries
- failure reporting
- user-visible status transitions

## Bundle Strategy

Use the same conceptual model as remote validation:

- source materialization
- overlay of edited generated artifacts
- reproducible ZIP bundle
- signed private storage URL
- SHA-256 verification by the remote executor

Why bundle by URL rather than direct binary upload:

- reuse existing storage and signing patterns
- avoid large request bodies between services
- keep replays and retries simpler
- allow the runner to remain a stateless API plus runtime service

## Reverse Proxy and Routing

Use Caddy for the first version.

Reasons:

- simple automatic TLS
- low configuration overhead
- good fit for subdomain routing on a single host
- easier V1 operations than Traefik for this use case

The runner is responsible for:

- registering preview routes
- removing routes at stop/expiration
- ensuring subdomain uniqueness

## Security Model

### Runner API Security

The runner must not be public.

Protect it with:

- private network access where possible
- an authentication token or HMAC signature from AutoDocker
- request timestamp validation if signatures are used

### Bundle Integrity

The runner must:

- verify the SHA-256 checksum before execution
- reject mismatched or oversized bundles
- refuse expired or invalid signed URLs

### Runtime Isolation

For V1:

- each preview gets isolated Docker resources
- each preview gets a dedicated Docker network
- resource names include preview-specific prefixes
- CPU and memory limits are applied per preview
- no persistent user volumes are mounted
- no source code bind mounts are used

This is not maximum isolation, but it is sufficient for a product-acotado V1 on a dedicated preview host.

## Lifecycle and Cleanup

Cleanup must not depend on the user clicking stop.

Three cleanup paths are required:

1. explicit stop from AutoDocker
2. automatic TTL expiration
3. periodic reconciliation job in the runner

The runner reconciliation loop should:

- list active previews
- stop anything past `expires_at`
- remove orphaned containers or networks
- reconcile proxy routes

## Product Constraints for V1

To keep scope realistic:

- one active preview per analysis
- aggressive TTL only
- no persistence of user data inside previews
- no multi-runner scheduling
- no preview resume
- no advanced quota system beyond simple hard limits
- no live stream terminal transport; polling-based logs are enough

## AutoDocker Changes Required

At the AutoDocker level, the implementation should introduce a remote preview backend similar in spirit to remote validation, but not reusing GitHub Actions.

Expected changes:

- preview backend selection in runtime/configuration
- a runner client service for preview API requests
- preview bundle creation or extension of existing bundle logic
- `PreviewService` split between local and remote execution paths
- status synchronization from runner to `PreviewRun`
- capability exposure to the UI so production can show preview availability correctly

## Preview Runner Responsibilities

The separate runner service should own:

- bundle download and checksum verification
- runtime materialization on disk
- Docker/Compose execution
- readiness checks
- reverse proxy registration
- TTL expiration
- log collection
- cleanup and reconciliation

AutoDocker should not own these production runtime details.

## Error Handling

### Creation Errors

Examples:

- signed URL invalid
- checksum mismatch
- Docker build failure
- reverse proxy route allocation failure

Result:

- runner returns `failed`
- AutoDocker stores logs and final error reason in `PreviewRun`

### Readiness Errors

Examples:

- container starts but never becomes reachable
- app exits immediately
- only internal service starts successfully

Result:

- runner transitions to `failed`
- cleanup runs
- AutoDocker exposes failure logs and final status

### Expiration

When TTL is reached:

- runner stops the runtime
- removes routing
- marks preview as `expired`
- AutoDocker reflects the final state on next synchronization

## Testing Strategy

### AutoDocker Tests

- unit tests for preview backend selection
- tests for runner client request/response normalization
- tests for synchronization into `PreviewRun`
- API tests for preview creation and stop behavior under remote mode

### Runner Tests

- create preview happy path
- checksum mismatch rejection
- readiness transition to `ready`
- explicit stop
- TTL expiration
- reconciliation of orphaned resources

### End-to-End Smoke Test

One minimal E2E path is enough for V1:

1. create preview
2. poll until `ready`
3. verify public URL responds
4. stop preview
5. verify URL is no longer served

## Operational Notes

The preview host should be treated as a dedicated execution plane. Even for V1, keep it separate from the main AutoDocker web deployment.

Recommended baseline:

- single dedicated host
- Caddy on the same machine
- Docker installed locally
- runner API reachable only from AutoDocker
- preview domain delegated to the host

This keeps the architecture simple while preserving the right boundaries for code execution.

## Future Evolution

If preview demand grows, this design can evolve into:

- multiple preview runners
- a queue and scheduler
- stronger tenant isolation
- per-user quotas
- richer observability
- persistent preview metadata outside the runner

None of those are required for the first version because the current design already establishes the correct control-plane versus execution-plane boundary.

## Recommendation Summary

For the first production-ready preview feature:

- keep local preview for development
- add a remote `preview-runner` backend for production
- host it on a preview-only dedicated machine
- publish public preview URLs through Caddy
- enforce aggressive TTL and hard cleanup
- keep AutoDocker as control plane and source of truth

This is the smallest architecture that is both practical and defensible for a user-facing remote preview feature.
