# Preview Runner Oracle Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the preview runner with Caddy-managed subdomains and add real Oracle deployment artifacts and operational docs.

**Architecture:** Add a dedicated publication service that owns Caddy route files and reloads, invoke it from preview lifecycle paths, and ship deploy-ready systemd/Caddy templates for an Oracle VM. Keep the current runner API and preview runtime flow intact.

**Tech Stack:** Django 5, Python 3.13, Docker, Caddy, systemd, unittest

---

### Task 1: Add failing tests for Caddy publication

**Files:**
- Modify: `core/tests/test_services.py`

- [ ] Add tests for route publish/unpublish/reconcile.
- [ ] Run the focused service tests and verify failure.

### Task 2: Implement publication service and wire preview lifecycle

**Files:**
- Create: `core/services/preview_publication.py`
- Modify: `core/services/preview.py`
- Modify: `core/services/preview_runner_sessions.py`
- Modify: `config/settings.py`

- [ ] Implement file-based Caddy route publication with reload support.
- [ ] Wire publish/unpublish/reconcile through preview start/stop/expire flows.
- [ ] Run focused tests and make them pass.

### Task 3: Add Oracle deploy artifacts

**Files:**
- Create: `deploy/oracle/preview-runner/Caddyfile`
- Create: `deploy/oracle/preview-runner/preview-runner.env.example`
- Create: `deploy/oracle/preview-runner/systemd/preview-runner.service`
- Create: `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.service`
- Create: `deploy/oracle/preview-runner/systemd/reconcile-preview-runner-sessions.timer`

- [ ] Add deploy-ready templates for the Oracle VM.

### Task 4: Document provisioning and hardening

**Files:**
- Modify: `README.md`
- Create: `docs/oracle-preview-runner.md`
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Modify: `.env.prod.example`

- [ ] Document provision checklist, firewall, token handling, and systemd/Caddy rollout.

### Task 5: Verify end to end

**Files:**
- Modify: tests/docs only if verification exposes gaps

- [ ] Run focused preview tests.
- [ ] Run the full Django suite.
