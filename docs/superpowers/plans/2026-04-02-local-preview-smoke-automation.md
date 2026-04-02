# Local Preview Smoke Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic local smoke-test flow that uses a real Git repository plus injected preview artifacts, and cover the fixture preparation path with automated tests.

**Architecture:** Introduce a small Python helper that prepares a `ProjectAnalysis` ready for smoke testing from a Git repo, with deterministic injected artifacts by default and an opt-in raw-repo mode. Expose that helper through a management command and drive the end-to-end HTTP flow from a single PowerShell script that boots the app and runner locally, provisions the analysis, creates a preview, polls for readiness, validates the served response, and stops the preview.

**Tech Stack:** Django management commands, existing preview services, PowerShell, Django TestCase/call_command.

---

### Task 1: Add failing tests for smoke fixture preparation

**Files:**
- Modify: `core/tests/test_services.py`
- Test: `core/tests/test_services.py`

- [ ] **Step 1: Write failing tests for injected-artifact fixture preparation**
- [ ] **Step 2: Run the targeted test selection to verify the new tests fail because the helper does not exist yet**
- [ ] **Step 3: Implement the minimal helper/service to make those tests pass**
- [ ] **Step 4: Re-run the targeted tests and confirm green**

### Task 2: Add failing tests for the management command

**Files:**
- Modify: `core/tests/test_services.py`
- Create: `core/management/commands/prepare_local_preview_smoke.py`

- [ ] **Step 1: Write failing command tests that assert JSON output and repo-artifact mode**
- [ ] **Step 2: Run the targeted tests to verify failure**
- [ ] **Step 3: Implement the command on top of the helper**
- [ ] **Step 4: Re-run the targeted tests and confirm green**

### Task 3: Add the single-entry smoke script and docs

**Files:**
- Create: `scripts/run-local-preview-smoke.ps1`
- Modify: `docs/local-preview-runner.md`

- [ ] **Step 1: Implement the PowerShell smoke script using the management command plus HTTP polling**
- [ ] **Step 2: Update the local runner docs with the new single-command flow**
- [ ] **Step 3: Run the smoke script against the public fixture repo and confirm preview readiness plus stop/cleanup**

### Task 4: Final verification

**Files:**
- Modify: `core/tests/test_services.py`
- Modify: `docs/local-preview-runner.md`
- Create: `scripts/run-local-preview-smoke.ps1`

- [ ] **Step 1: Run the targeted automated tests for the new helper/command**
- [ ] **Step 2: Run the relevant broader Django test modules if needed**
- [ ] **Step 3: Record the real smoke-test result against `https://github.com/LucasTabacchi/autodocker-pr-fixture-monorepo`**
