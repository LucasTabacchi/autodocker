# AutoDocker UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild AutoDocker's auth and dashboard UI into a unified, production-ready SaaS interface with clearer information hierarchy, better navigation, stronger accessibility, and mobile-first responsiveness.

**Architecture:** Consolidate visual tokens and shared UI primitives into the existing global stylesheet, refactor auth templates to use the same design language as the dashboard, and restructure the dashboard into a navigation-led control center with focused sections instead of a single overloaded surface. Preserve the current Django template and vanilla JS architecture while reducing inline styling and improving component consistency.

**Tech Stack:** Django templates, vanilla JavaScript, shared CSS, Monaco Editor integration

---

### Task 1: Audit And Prepare Shared UI Foundations

**Files:**
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\templates\base.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\css\app.css`
- Test: `C:\Users\lucas\Documents\Playground\autodocker\templates\registration\login.html`

- [ ] **Step 1: Define the shared app frame in the plan context**

Use this target structure:

```html
<body class="{% block body_class %}{% endblock %}">
    <div class="app-root">
        {% block content %}{% endblock %}
    </div>
    {% block extra_scripts %}{% endblock %}
</body>
```

- [ ] **Step 2: Add unified font loading and metadata to the base template**

Use this head structure:

```html
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}AutoDocker{% endblock %}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{% static 'core/css/app.css' %}">
```

- [ ] **Step 3: Replace the current root tokens in the stylesheet with a unified design system**

Add tokens like:

```css
:root {
  --bg: #0b1020;
  --bg-elevated: #12192b;
  --bg-soft: #182235;
  --bg-strong: #0f1728;
  --border: #27324a;
  --border-soft: rgba(166, 178, 200, 0.18);
  --text: #f3f7fc;
  --text-muted: #a6b2c8;
  --text-soft: #7f8ca6;
  --primary: #3b82f6;
  --primary-soft: rgba(59, 130, 246, 0.14);
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;
  --radius-sm: 10px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --shadow-lg: 0 24px 60px rgba(2, 6, 23, 0.36);
  --font-heading: "Plus Jakarta Sans", sans-serif;
  --font-body: "Inter", sans-serif;
  --font-mono: "JetBrains Mono", monospace;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --transition-fast: 180ms cubic-bezier(0.22, 1, 0.36, 1);
}
```

- [ ] **Step 4: Add shared primitives for buttons, fields, cards, badges, and focus states**

Include concrete primitives:

```css
.btn,
.btn-secondary,
.btn-ghost {
  min-height: 44px;
  border-radius: 999px;
  padding: 0 18px;
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 600;
  transition: transform var(--transition-fast), background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast), box-shadow var(--transition-fast);
}

.btn:focus-visible,
.btn-secondary:focus-visible,
.btn-ghost:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
button:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.24);
}
```

- [ ] **Step 5: Run a quick template render check**

Run: `.\.venv\Scripts\python.exe manage.py check`

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
git add templates/base.html core/static/core/css/app.css
git commit -m "feat: add shared design-system foundations"
```

### Task 2: Rebuild Authentication Screens On The Shared System

**Files:**
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\templates\registration\login.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\templates\registration\signup.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\css\app.css`
- Test: `C:\Users\lucas\Documents\Playground\autodocker\core\views.py`

- [ ] **Step 1: Remove the inline `<style>` blocks from login and signup**

Replace the page structure with semantic sections like:

```html
<main class="auth-page">
  <section class="auth-showcase">...</section>
  <section class="auth-panel">...</section>
</main>
```

- [ ] **Step 2: Replace emoji-based visuals with inline SVG icons**

Use icon wrappers like:

```html
<span class="icon-chip" aria-hidden="true">
  <svg viewBox="0 0 24 24" class="icon-svg">...</svg>
</span>
```

- [ ] **Step 3: Refactor login into a focused, single-goal panel**

Use this structure:

```html
<div class="auth-panel__card">
  <div class="auth-panel__header">
    <h1>Sign in to AutoDocker</h1>
    <p>Review runs, validation results, previews, and delivery activity from one workspace.</p>
  </div>
  <div id="login-flow" class="auth-form-stack">...</div>
</div>
```

- [ ] **Step 4: Refactor signup into the same shared layout**

Use the same card system, with the signup form grouped by:

```html
<div class="auth-form-grid auth-form-grid--two">
  <div class="field">first name</div>
  <div class="field">last name</div>
</div>
```

- [ ] **Step 5: Add auth-specific CSS classes into the shared stylesheet**

Add styles for:

```css
.auth-page {}
.auth-showcase {}
.auth-panel {}
.auth-panel__card {}
.auth-form-stack {}
.auth-form-grid {}
.icon-chip {}
.auth-helper {}
```

- [ ] **Step 6: Keep existing login/signup behavior but align feedback styling**

Preserve the current JavaScript logic, but make sure error blocks use a shared class:

```html
<div class="form-message form-message--error" id="errorMsg"></div>
```

- [ ] **Step 7: Run auth flow render verification**

Run: `.\.venv\Scripts\python.exe manage.py check`

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 8: Commit**

```bash
git add templates/registration/login.html templates/registration/signup.html core/static/core/css/app.css
git commit -m "feat: unify authentication screens with shared UI system"
```

### Task 3: Restructure Dashboard Information Architecture

**Files:**
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\templates\core\dashboard.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\css\app.css`
- Test: `C:\Users\lucas\Documents\Playground\autodocker\core\views.py`

- [ ] **Step 1: Replace the current stacked dashboard shell with nav + content layout**

Use this target skeleton:

```html
<main class="dashboard-shell">
  <aside class="dashboard-sidebar">...</aside>
  <section class="dashboard-main">
    <header class="dashboard-topbar">...</header>
    <div class="dashboard-content">...</div>
  </section>
</main>
```

- [ ] **Step 2: Move “new analysis” into a primary workbench card**

Structure:

```html
<section class="workbench-card">
  <div class="section-head">...</div>
  <form id="analysis-form" class="analysis-workbench">...</form>
</section>
```

- [ ] **Step 3: Convert the result area into a run detail view with tabs**

Add tab buttons:

```html
<nav class="run-tabs" aria-label="Run detail sections">
  <button type="button" class="run-tab is-active" data-run-tab="summary">Summary</button>
  <button type="button" class="run-tab" data-run-tab="artifacts">Artifacts</button>
  <button type="button" class="run-tab" data-run-tab="delivery">Delivery</button>
  <button type="button" class="run-tab" data-run-tab="workspace">Workspace</button>
</nav>
```

- [ ] **Step 4: Group related content under focused panels**

Map current content like this:

```text
Summary -> KPI cards, recommendations, security, healthchecks
Artifacts -> editor tabs, regenerate, diff
Delivery -> validation, preview, CI/CD, GitHub PR
Workspace -> collaborators, invitations, recent runs
```

- [ ] **Step 5: Replace the hero with a concise overview header**

Use:

```html
<section class="overview-hero">
  <div>
    <p class="section-kicker">Developer control center</p>
    <h1>Generate, validate, and ship Docker artifacts without leaving the workspace.</h1>
    <p class="section-copy">Analyze a repository, inspect output, validate the runtime, and deliver changes through previews or pull requests.</p>
  </div>
  <div class="overview-hero__actions">...</div>
</section>
```

- [ ] **Step 6: Add dashboard layout CSS for the new navigation model**

Add classes for:

```css
.dashboard-shell {}
.dashboard-sidebar {}
.dashboard-main {}
.dashboard-topbar {}
.dashboard-content {}
.run-tabs {}
.run-panel {}
.overview-hero {}
.workbench-card {}
```

- [ ] **Step 7: Preserve all existing IDs needed by JavaScript**

Keep IDs such as:

```text
analysis-form
analysis-result
summary-grid
recommendations
artifact-tabs
artifact-editors
validation-summary
diff-results
preview-summary
history-list
workspace-members
workspace-invitations
incoming-invitations
```

- [ ] **Step 8: Run dashboard render verification**

Run: `.\.venv\Scripts\python.exe manage.py check`

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 9: Commit**

```bash
git add core/templates/core/dashboard.html core/static/core/css/app.css
git commit -m "feat: restructure dashboard into a focused control center"
```

### Task 4: Update Dashboard Interactions For The New Layout

**Files:**
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\js\app.js`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\js\dashboard_form.js`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\css\app.css`
- Test: `C:\Users\lucas\Documents\Playground\autodocker\core\templates\core\dashboard.html`

- [ ] **Step 1: Add run-tab toggling support**

Implement tab behavior:

```js
function setActiveRunTab(tabId) {
    document.querySelectorAll("[data-run-tab]").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.runTab === tabId);
    });
    document.querySelectorAll("[data-run-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.runPanel !== tabId;
    });
}
```

- [ ] **Step 2: Default to the summary tab and switch to relevant tabs after actions**

Examples:

```js
setActiveRunTab("summary");
setActiveRunTab("artifacts");
setActiveRunTab("delivery");
```

- [ ] **Step 3: Improve status messaging for disabled or pending actions**

Use inline helper copy targets like:

```js
elements.resultSubtitle.textContent = "Run a new analysis or select a previous run to unlock artifacts, validation, and delivery actions.";
```

- [ ] **Step 4: Replace symbol-based labels in UI controls**

Remove text like:

```text
↑ Archivo ZIP
⎇ Git URL
Salir →
Analizar proyecto →
```

Replace with plain labels:

```text
Upload ZIP
Connect Git repository
Sign out
Analyze project
```

- [ ] **Step 5: Keep form helpers compatible with the existing API**

Do not change endpoints. Keep current request flow:

```js
requestJson("/api/analyses/", { method: "POST", body: payload });
requestJson(`/api/analyses/${state.analysis.id}/validate/`, { method: "POST" });
```

- [ ] **Step 6: Run static behavior sanity check**

Run: `.\.venv\Scripts\python.exe manage.py check`

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 7: Commit**

```bash
git add core/static/core/js/app.js core/static/core/js/dashboard_form.js core/static/core/css/app.css core/templates/core/dashboard.html
git commit -m "feat: align dashboard interactions with the new IA"
```

### Task 5: Final Responsive And QA Pass

**Files:**
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\static\core\css\app.css`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\templates\registration\login.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\templates\registration\signup.html`
- Modify: `C:\Users\lucas\Documents\Playground\autodocker\core\templates\core\dashboard.html`

- [ ] **Step 1: Add explicit responsive breakpoints**

Implement media queries for:

```css
@media (max-width: 1279px) {}
@media (max-width: 1023px) {}
@media (max-width: 767px) {}
@media (max-width: 479px) {}
```

- [ ] **Step 2: Collapse sidebar and action density on small screens**

Use rules like:

```css
.dashboard-shell {
  grid-template-columns: 1fr;
}

.dashboard-sidebar {
  overflow-x: auto;
}

.result-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
```

- [ ] **Step 3: Ensure all inputs, buttons, and tab controls meet 44px target**

Verify and enforce:

```css
button,
input,
select,
textarea,
.run-tab {
  min-height: 44px;
}
```

- [ ] **Step 4: Verify reduced-motion fallback remains intact**

Keep:

```css
@media (prefers-reduced-motion: reduce) {
  * {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 5: Run final verification**

Run:

```bash
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test core.tests.test_api core.tests.test_services
```

Expected:

```text
System check identified no issues (0 silenced).
...
OK
```

- [ ] **Step 6: Commit**

```bash
git add core/static/core/css/app.css templates/registration/login.html templates/registration/signup.html core/templates/core/dashboard.html
git commit -m "feat: finalize responsive production-ready ui overhaul"
```
