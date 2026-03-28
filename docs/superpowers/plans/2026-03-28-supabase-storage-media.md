# Supabase Storage Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move uploaded analysis archives from local `media/` storage to a private, backend-only Supabase Storage bucket so the app can run on Render free without a persistent disk.

**Architecture:** Keep Django `FileField` as the storage abstraction and switch the default media backend to an S3-compatible storage when Supabase Storage credentials are present. Replace local-path assumptions in ingestion with file-stream handling so ZIP uploads work from both local filesystem storage and remote object storage.

**Tech Stack:** Django 5, django-storages, boto3, Supabase Storage S3-compatible API, Render Blueprint

---

### Task 1: Add failing tests for storage config and remote archive ingestion

**Files:**
- Modify: `core/tests.py`

- [ ] Add a settings test that asserts the app enables S3-compatible media storage only when Supabase Storage env vars are configured.
- [ ] Add an ingestion test that proves ZIP extraction uses `archive.open("rb")` and does not require `archive.path`.
- [ ] Run the focused tests and confirm they fail for the expected reasons before implementing.

### Task 2: Implement Supabase Storage-backed media configuration

**Files:**
- Modify: `config/settings.py`
- Modify: `requirements.txt`

- [ ] Add `django-storages` and `boto3` dependencies.
- [ ] Add environment-driven Django storage configuration for a private Supabase S3-compatible bucket while keeping local media storage as the default fallback.
- [ ] Keep staticfiles/WhiteNoise configuration unchanged.

### Task 3: Remove local-path assumptions from ZIP ingestion

**Files:**
- Modify: `core/services/ingestion.py`

- [ ] Change ZIP source materialization to read from `analysis.archive.open("rb")`.
- [ ] Preserve current path traversal protections and extraction behavior.
- [ ] Ensure the code still works for local storage and remote object storage.

### Task 4: Adapt deployment/config docs to Render free + Supabase Storage

**Files:**
- Modify: `render.yaml`
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Modify: `.env.prod.example`
- Modify: `.env.production.example`
- Modify: `README.md`

- [ ] Remove persistent disk usage and move the Render Blueprint to the free web plan.
- [ ] Add the Supabase Storage env vars needed for the private S3-compatible bucket.
- [ ] Document the Render free trade-offs and the new storage setup.

### Task 5: Verify the integrated behavior

**Files:**
- Modify: `core/tests.py` if verification reveals missing coverage

- [ ] Run the focused tests for settings and ingestion.
- [ ] Run the full Django test suite.
- [ ] Summarize any remaining production setup required in Supabase and Render.
