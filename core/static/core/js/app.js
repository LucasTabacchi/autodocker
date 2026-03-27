(() => {
    const MONACO_LOADER_URL =
        "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs/loader.min.js";
    const form = document.getElementById("analysis-form");

    if (!form) {
        return;
    }

    const byId = (id) => document.getElementById(id);
    const elements = {
        status: byId("analysis-status"),
        submitButton: byId("analysis-submit-button"),
        submitLabel: byId("analysis-submit-label"),
        archiveInput: byId("id_archive"),
        archiveFilename: byId("archive-filename"),
        dropzoneText: byId("dropzone-text"),
        panel: byId("analysis-result"),
        title: byId("result-title"),
        subtitle: byId("result-subtitle"),
        summaryGrid: byId("summary-grid"),
        recommendations: byId("recommendations"),
        tabs: byId("artifact-tabs"),
        editors: byId("artifact-editors"),
        history: byId("history-list"),
        historyMoreButton: byId("history-more-button"),
        historyPagination: byId("history-pagination"),
        historyPageInfo: byId("history-page-info"),
        historyPrevButton: byId("history-prev-button"),
        historyNextButton: byId("history-next-button"),
        regenerate: byId("regenerate-button"),
        validate: byId("validate-button"),
        diff: byId("diff-button"),
        preview: byId("preview-button"),
        stopPreview: byId("stop-preview-button"),
        download: byId("download-button"),
        resultProfile: byId("result-generation-profile"),
        validationSummary: byId("validation-summary"),
        validationLogs: byId("validation-logs"),
        diffResults: byId("diff-results"),
        profileSummary: byId("profile-summary"),
        profileDetails: byId("profile-details"),
        previewSummary: byId("preview-summary"),
        previewLinks: byId("preview-links"),
        previewLogs: byId("preview-logs"),
        githubForm: byId("github-pr-form"),
        githubSelect: byId("github-connection-select"),
        githubToken: byId("github-access-token"),
        githubSave: byId("github-save-connection"),
        githubLabel: byId("github-connection-label"),
        githubAccount: byId("github-account-name"),
        githubBase: byId("github-base-branch"),
        githubTitle: byId("github-pr-title"),
        githubBody: byId("github-pr-body"),
        githubButton: byId("github-pr-button"),
        githubSummary: byId("github-summary"),
        githubLogs: byId("github-logs"),
        workspaceSelect: byId("workspace-select"),
        workspaceSummary: byId("workspace-summary"),
        workspaceForm: byId("workspace-form"),
        workspaceName: byId("workspace-name"),
        workspaceDescription: byId("workspace-description"),
        workspaceMemberForm: byId("workspace-member-form"),
        workspaceMemberUsername: byId("workspace-member-username"),
        workspaceMemberRole: byId("workspace-member-role"),
        workspaceMembers: byId("workspace-members"),
        workspaceInvitations: byId("workspace-invitations"),
        incomingInvitationsSummary: byId("incoming-invitations-summary"),
        incomingInvitations: byId("incoming-invitations"),
        securitySummary: byId("security-summary"),
        securityFindings: byId("security-findings"),
        healthcheckSummary: byId("healthcheck-summary"),
        healthcheckDetails: byId("healthcheck-details"),
        cicdSummary: byId("cicd-summary"),
        cicdArtifacts: byId("cicd-artifacts"),
        deploySummary: byId("deploy-summary"),
        deployTargets: byId("deploy-targets"),
    };

    const state = {
        analysis: null,
        analysisSignature: "",
        artifactSignature: "",
        diffSignature: "",
        activeArtifactId: null,
        historyAnalyses: [],
        historyExpanded: false,
        compactHistory: window.matchMedia("(max-width: 1100px)").matches,
        historyPage: 1,
        workspaces: [],
        incomingInvitations: [],
        currentWorkspaceId: elements.workspaceSelect?.value || "",
        monacoPromise: null,
        monacoLoaderPromise: null,
        editors: new Map(),
        polls: {
            analysis: null,
            validation: null,
            github: null,
            preview: null,
        },
        busy: {
            analysis: false,
            validation: false,
            github: false,
            preview: false,
        },
    };

    init().catch((error) => {
        setStatusBadge("Error inicial", "error");
        window.alert(buildErrorMessage(error));
    });

    async function init() {
        bindEvents();
        renderProfile(null);
        renderSecurityReport(null);
        renderHealthchecks(null);
        renderCicd(null);
        renderDeploy(null);
        await loadWorkspaces();
        await Promise.all([loadConnections(), loadIncomingInvitations(), refreshHistory()]);
    }

    function bindEvents() {
        form.addEventListener("submit", createAnalysis);
        elements.archiveInput.addEventListener("change", updateSelectedFilename);
        elements.history.addEventListener("click", onHistoryClick);
        elements.historyMoreButton?.addEventListener("click", toggleHistoryExpanded);
        elements.historyPrevButton?.addEventListener("click", goToPreviousHistoryPage);
        elements.historyNextButton?.addEventListener("click", goToNextHistoryPage);
        elements.tabs.addEventListener("click", onArtifactTabClick);
        elements.editors.addEventListener("click", onEditorActionClick);
        elements.regenerate.addEventListener("click", regenerateAnalysis);
        elements.validate.addEventListener("click", validateBuild);
        elements.diff.addEventListener("click", loadDiff);
        elements.preview.addEventListener("click", startPreview);
        elements.stopPreview.addEventListener("click", stopPreview);
        elements.githubForm.addEventListener("submit", createPullRequest);
        elements.workspaceSelect?.addEventListener("change", onWorkspaceChange);
        elements.workspaceForm?.addEventListener("submit", createWorkspace);
        elements.workspaceMemberForm?.addEventListener("submit", addWorkspaceMember);
        elements.incomingInvitations?.addEventListener("click", onIncomingInvitationAction);
        window.addEventListener("resize", onViewportResize);
    }

    function getCsrfToken() {
        const field = form.querySelector("input[name=csrfmiddlewaretoken]");
        return field ? field.value : "";
    }

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
                "X-CSRFToken": getCsrfToken(),
                ...(options.headers || {}),
            },
            ...options,
        });
        const contentType = response.headers.get("content-type") || "";
        const payload = contentType.includes("application/json")
            ? await response.json()
            : await response.text();
        if (!response.ok) {
            throw payload;
        }
        return payload;
    }

    function setStatusBadge(text, tone = "subtle") {
        elements.status.textContent = text;
        elements.status.className = `badge ${tone}`;
    }

    function setSubmitLoading(isLoading) {
        elements.submitButton.classList.toggle("is-loading", isLoading);
        elements.submitButton.disabled = isLoading;
        elements.submitLabel.textContent = isLoading ? "Analizando proyecto..." : "Analizar proyecto";
    }

    function setArchiveLabel(text) {
        if (elements.archiveFilename) {
            elements.archiveFilename.textContent = text;
        }
        if (elements.dropzoneText) {
            elements.dropzoneText.textContent = text;
        }
    }

    function updateSelectedFilename() {
        const file = elements.archiveInput.files && elements.archiveInput.files[0];
        setArchiveLabel(file ? file.name : "Arrastrá tu .zip acá");
    }

    function resetSubmissionForm() {
        form.reset();
        updateSelectedFilename();
    }

    async function createAnalysis(event) {
        event.preventDefault();
        setSubmitLoading(true);
        setStatusBadge("Encolando análisis…", "running");

        try {
            const payload = new FormData(form);
            if (state.currentWorkspaceId) {
                payload.append("workspace_id", state.currentWorkspaceId);
            }
            const analysis = await requestJson("/api/analyses/", {
                method: "POST",
                body: payload,
            });
            renderAnalysis(analysis, {reveal: true});
            prependHistoryItem(analysis);
            resetSubmissionForm();
        } catch (error) {
            setStatusBadge("Error en análisis", "error");
            window.alert(buildErrorMessage(error));
        } finally {
            setSubmitLoading(false);
        }
    }

    function onHistoryClick(event) {
        const target = event.target.closest("[data-analysis-id]");
        if (!target) {
            return;
        }
        setActiveHistoryItem(target.dataset.analysisId);
        fetchAnalysis(target.dataset.analysisId);
    }

    async function fetchAnalysis(analysisId) {
        setStatusBadge("Cargando análisis…", "subtle");
        try {
            const analysis = await requestJson(`/api/analyses/${analysisId}/`);
            renderAnalysis(analysis, {reveal: false});
        } catch (error) {
            setStatusBadge("No se pudo cargar", "error");
            window.alert(buildErrorMessage(error));
        }
    }

    function onArtifactTabClick(event) {
        const target = event.target.closest("[data-artifact-tab]");
        if (!target) {
            return;
        }
        activateArtifact(target.dataset.artifactTab);
    }

    async function onEditorActionClick(event) {
        const saveButton = event.target.closest("[data-save-artifact]");
        if (!saveButton) {
            return;
        }

        const artifactId = saveButton.dataset.saveArtifact;
        const content = getEditorValue(artifactId);
        saveButton.disabled = true;

        try {
            await requestJson(`/api/artifacts/${artifactId}/`, {
                method: "PATCH",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({content}),
            });
            saveButton.textContent = "Guardado";
            setTimeout(() => {
                saveButton.textContent = "Guardar cambios";
                saveButton.disabled = false;
            }, 900);
        } catch (error) {
            saveButton.disabled = false;
            window.alert(buildErrorMessage(error));
        }
    }

    async function regenerateAnalysis() {
        if (!state.analysis) {
            return;
        }

        setStatusBadge("Regenerando artefactos…", "running");
        try {
            const analysis = await requestJson(`/api/analyses/${state.analysis.id}/regenerate/`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    generation_profile: elements.resultProfile.value,
                }),
            });
            renderAnalysis(analysis, {reveal: true});
            elements.diffResults.innerHTML =
                '<p class="empty-copy">Regeneración en curso. Volvé a pedir el diff cuando termine.</p>';
        } catch (error) {
            setStatusBadge("Regeneración fallida", "error");
            window.alert(buildErrorMessage(error));
        }
    }

    async function validateBuild() {
        if (!state.analysis) {
            return;
        }

        elements.validationSummary.textContent = "Validación encolada…";
        try {
            const job = await requestJson(`/api/analyses/${state.analysis.id}/validate/`, {
                method: "POST",
            });
            renderJob("validation", job);
            pollJob("validation", job.id);
        } catch (error) {
            elements.validationSummary.textContent = buildErrorMessage(error);
        }
    }

    async function loadDiff() {
        if (!state.analysis) {
            return;
        }

        elements.diffResults.innerHTML =
            '<p class="empty-copy">Comparando artefactos generados contra el repo original…</p>';
        try {
            const payload = await requestJson(`/api/analyses/${state.analysis.id}/diff/`);
            renderDiff(payload.items || []);
        } catch (error) {
            elements.diffResults.innerHTML = `<p class="empty-copy">${escapeHtml(buildErrorMessage(error))}</p>`;
        }
    }

    async function startPreview() {
        if (!state.analysis) {
            return;
        }

        elements.previewSummary.textContent = "Preparando preview…";
        try {
            const preview = await requestJson(`/api/analyses/${state.analysis.id}/preview/`, {
                method: "POST",
            });
            renderPreview(preview);
            if (["queued", "running"].includes(preview.status)) {
                pollPreview(preview.id);
            }
        } catch (error) {
            elements.previewSummary.textContent = buildErrorMessage(error);
        }
    }

    async function stopPreview() {
        if (!state.analysis || !state.analysis.active_preview) {
            return;
        }

        try {
            const preview = await requestJson(
                `/api/previews/${state.analysis.active_preview.id}/stop/`,
                {method: "POST"},
            );
            renderPreview(preview);
            await refreshCurrentAnalysis();
        } catch (error) {
            elements.previewSummary.textContent = buildErrorMessage(error);
        }
    }

    async function createPullRequest(event) {
        event.preventDefault();
        if (!state.analysis) {
            return;
        }

        elements.githubSummary.textContent = "Creando job de PR…";
        const selectedConnectionId = elements.githubSelect.value;
        const payload = {
            connection_id: selectedConnectionId,
            access_token: selectedConnectionId ? "" : elements.githubToken.value,
            save_connection: elements.githubSave.checked,
            connection_label: elements.githubLabel.value,
            account_name: elements.githubAccount.value,
            base_branch: elements.githubBase.value || "main",
            title: elements.githubTitle.value || `Dockerize ${state.analysis.project_name}`,
            body:
                elements.githubBody.value ||
                "Auto-generated Docker configuration from AutoDocker.",
        };

        try {
            const job = await requestJson(`/api/analyses/${state.analysis.id}/github-pr/`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload),
            });
            renderJob("github", job);
            pollJob("github", job.id);
            if (payload.save_connection && payload.access_token) {
                loadConnections();
            }
        } catch (error) {
            elements.githubSummary.textContent = buildErrorMessage(error);
        }
    }

    async function loadConnections() {
        try {
            const connections = await requestJson("/api/connections/");
            const options = [
                '<option value="">Usar token manual</option>',
                ...connections.map(
                    (connection) =>
                        `<option value="${connection.id}">${escapeHtml(connection.label)} · ${escapeHtml(connection.account_name || connection.provider)}</option>`,
                ),
            ];
            elements.githubSelect.innerHTML = options.join("");
        } catch {
            elements.githubSelect.innerHTML = '<option value="">No se pudieron cargar conexiones</option>';
        }
    }

    async function loadWorkspaces() {
        const workspaces = await requestJson("/api/workspaces/");
        renderWorkspaces(workspaces);
    }

    async function loadIncomingInvitations() {
        const invitations = await requestJson("/api/workspace-invitations/");
        renderIncomingInvitations(invitations);
    }

    async function refreshHistory() {
        const query = state.currentWorkspaceId
            ? `?workspace_id=${encodeURIComponent(state.currentWorkspaceId)}`
            : "";
        const analyses = await requestJson(`/api/analyses/${query}`);
        renderHistory(analyses);
        if (state.analysis && state.analysis.workspace?.id !== state.currentWorkspaceId) {
            clearCurrentAnalysis();
        }
    }

    function renderHistory(analyses) {
        state.historyAnalyses = analyses || [];
        state.historyPage = 1;
        if (!state.historyAnalyses.length) {
            elements.history.innerHTML = '<p class="empty-copy">Todavía no hay ejecuciones guardadas.</p>';
            if (elements.historyMoreButton) {
                elements.historyMoreButton.hidden = true;
                elements.historyMoreButton.classList.remove("is-visible");
            }
            if (elements.historyPagination) {
                elements.historyPagination.hidden = true;
                elements.historyPagination.style.display = "none";
            }
            return;
        }
        renderHistoryList();
    }

    function renderHistoryList() {
        const analyses = state.historyAnalyses || [];
        if (!analyses.length) {
            elements.history.innerHTML = '<p class="empty-copy">Todavía no hay ejecuciones guardadas.</p>';
            if (elements.historyMoreButton) {
                elements.historyMoreButton.hidden = true;
                elements.historyMoreButton.classList.remove("is-visible");
            }
            if (elements.historyPagination) {
                elements.historyPagination.hidden = true;
                elements.historyPagination.style.display = "none";
            }
            return;
        }

        const compact = state.compactHistory;
        const collapsedLimit = 6;
        const pageSize = 8;
        const activeIndex = state.analysis
            ? analyses.findIndex((analysis) => analysis.id === state.analysis.id)
            : -1;

        if (compact && !state.historyExpanded && activeIndex >= collapsedLimit) {
            state.historyExpanded = true;
        }

        let visibleAnalyses = analyses;
        if (compact) {
            visibleAnalyses = !state.historyExpanded ? analyses.slice(0, collapsedLimit) : analyses;
        } else {
            const totalPages = Math.max(1, Math.ceil(analyses.length / pageSize));
            state.historyPage = Math.min(Math.max(1, state.historyPage), totalPages);
            if (activeIndex >= 0) {
                const activePage = Math.floor(activeIndex / pageSize) + 1;
                if (activePage !== state.historyPage) {
                    state.historyPage = activePage;
                }
            }
            const start = (state.historyPage - 1) * pageSize;
            visibleAnalyses = analyses.slice(start, start + pageSize);
        }

        elements.history.innerHTML = "";
        visibleAnalyses.forEach((analysis) => {
            elements.history.append(buildHistoryItem(analysis));
        });

        if (elements.historyMoreButton) {
            const shouldShowToggle = compact && analyses.length > collapsedLimit;
            elements.historyMoreButton.hidden = !shouldShowToggle;
            elements.historyMoreButton.classList.toggle("is-visible", shouldShowToggle);
            elements.historyMoreButton.style.display = shouldShowToggle ? "inline-flex" : "none";
            elements.historyMoreButton.textContent = state.historyExpanded ? "Ver menos" : "Ver más";
        }

        if (elements.historyPagination) {
            if (compact) {
                elements.historyPagination.hidden = true;
                elements.historyPagination.style.display = "none";
            } else {
                const totalPages = Math.max(1, Math.ceil(analyses.length / pageSize));
                const shouldShowPagination = totalPages > 1;
                elements.historyPagination.hidden = !shouldShowPagination;
                elements.historyPagination.style.display = shouldShowPagination ? "flex" : "none";
                if (elements.historyPageInfo) {
                    elements.historyPageInfo.textContent = `Página ${state.historyPage} de ${totalPages}`;
                }
                if (elements.historyPrevButton) {
                    elements.historyPrevButton.disabled = state.historyPage <= 1;
                }
                if (elements.historyNextButton) {
                    elements.historyNextButton.disabled = state.historyPage >= totalPages;
                }
            }
        }

        if (state.analysis) {
            setActiveHistoryItem(state.analysis.id);
        }
    }

    function renderWorkspaces(workspaces) {
        state.workspaces = workspaces || [];
        const preferredId = state.currentWorkspaceId || elements.workspaceSelect?.value || "";
        const hasPreferred = state.workspaces.some((workspace) => workspace.id === preferredId);
        state.currentWorkspaceId = hasPreferred
            ? preferredId
            : (state.workspaces[0] && state.workspaces[0].id) || "";

        if (elements.workspaceSelect) {
            elements.workspaceSelect.innerHTML = state.workspaces
                .map(
                    (workspace) => `
                        <option value="${workspace.id}" ${workspace.id === state.currentWorkspaceId ? "selected" : ""}>
                            ${escapeHtml(workspace.name)}
                        </option>
                    `,
                )
                .join("");
        }

        const activeWorkspace = currentWorkspace();
        elements.workspaceSummary.textContent = activeWorkspace
            ? `Workspace activo: ${activeWorkspace.name} · ${activeWorkspace.member_count || 0} miembros`
            : "Todavía no hay un workspace activo.";
        renderWorkspaceMembers(activeWorkspace);
        renderWorkspaceInvitations(activeWorkspace);
    }

    function renderWorkspaceMembers(workspace) {
        if (!workspace) {
            elements.workspaceMembers.innerHTML =
                '<p class="empty-copy">Creá un workspace para invitar a tu equipo.</p>';
            return;
        }

        if (!(workspace.memberships || []).length) {
            elements.workspaceMembers.innerHTML =
                '<p class="empty-copy">Todavía no hay miembros extra en este workspace.</p>';
            return;
        }

        elements.workspaceMembers.innerHTML = workspace.memberships
            .map(
                (membership) => `
                    <article class="history-item history-item--workspace">
                        <span class="history-item__title">${escapeHtml(membership.username)}</span>
                        <span class="history-item__meta">${escapeHtml(membership.role)}</span>
                    </article>
                `,
            )
            .join("");
    }

    function renderWorkspaceInvitations(workspace) {
        if (!workspace) {
            elements.workspaceInvitations.innerHTML =
                '<p class="empty-copy">Creá un workspace para empezar a invitar personas.</p>';
            return;
        }

        const invitations = workspace.pending_invitations || [];
        if (!invitations.length) {
            elements.workspaceInvitations.innerHTML =
                '<p class="empty-copy">Todavía no hay invitaciones pendientes en este workspace.</p>';
            return;
        }

        elements.workspaceInvitations.innerHTML = invitations
            .map(
                (invitation) => `
                    <article class="history-item history-item--workspace">
                        <span class="history-item__title">${escapeHtml(invitation.target_label || invitation.email || "Invitación")}</span>
                        <span class="history-item__meta">${escapeHtml(invitation.role)} · ${escapeHtml(deliveryStatusLabel(invitation.delivery_status))}</span>
                    </article>
                `,
            )
            .join("");
    }

    function renderIncomingInvitations(invitations) {
        state.incomingInvitations = invitations || [];
        if (!state.incomingInvitations.length) {
            elements.incomingInvitationsSummary.textContent = "No tenés invitaciones pendientes.";
            elements.incomingInvitations.innerHTML =
                '<p class="empty-copy">Cuando otro usuario te invite a un workspace, lo vas a poder aceptar o rechazar desde acá.</p>';
            return;
        }

        elements.incomingInvitationsSummary.textContent = `Tenés ${state.incomingInvitations.length} invitación${state.incomingInvitations.length === 1 ? "" : "es"} pendiente${state.incomingInvitations.length === 1 ? "" : "s"}.`;
        elements.incomingInvitations.innerHTML = state.incomingInvitations
            .map(
                (invitation) => `
                    <article class="history-item history-item--invitation">
                        <span class="history-item__title">${escapeHtml(invitation.workspace?.name || "Workspace")}</span>
                        <span class="history-item__meta">Invita ${escapeHtml(invitation.invited_by_username)} · ${escapeHtml(invitation.role)}</span>
                        <div class="history-item__actions">
                            <button class="secondary-button" type="button" data-accept-invitation="${invitation.id}">Aceptar</button>
                            <button class="secondary-button" type="button" data-decline-invitation="${invitation.id}">Rechazar</button>
                        </div>
                    </article>
                `,
            )
            .join("");
    }

    async function onWorkspaceChange(event) {
        state.currentWorkspaceId = event.target.value;
        const activeWorkspace = currentWorkspace();
        renderWorkspaceMembers(activeWorkspace);
        renderWorkspaceInvitations(activeWorkspace);
        elements.workspaceSummary.textContent = activeWorkspace
            ? `Workspace activo: ${activeWorkspace.name} · ${activeWorkspace.member_count || 0} miembros`
            : "Todavía no hay un workspace activo.";
        try {
            await refreshHistory();
        } catch (error) {
            window.alert(buildErrorMessage(error));
        }
    }

    async function createWorkspace(event) {
        event.preventDefault();
        const name = elements.workspaceName.value.trim();
        const description = elements.workspaceDescription.value.trim();

        if (!name) {
            window.alert("Se requiere un nombre para crear el workspace.");
            return;
        }

        try {
            const workspace = await requestJson("/api/workspaces/", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({name, description}),
            });
            state.currentWorkspaceId = workspace.id;
            elements.workspaceForm.reset();
            await loadWorkspaces();
            await loadIncomingInvitations();
            await refreshHistory();
        } catch (error) {
            window.alert(buildErrorMessage(error));
        }
    }

    async function addWorkspaceMember(event) {
        event.preventDefault();
        const workspace = currentWorkspace();
        if (!workspace) {
            window.alert("Seleccioná un workspace antes de agregar miembros.");
            return;
        }

        const identifier = elements.workspaceMemberUsername.value.trim();
        const role = elements.workspaceMemberRole.value;
        if (!identifier) {
            window.alert("Indicá el username o email de la persona que querés invitar.");
            return;
        }

        try {
            await requestJson(`/api/workspaces/${workspace.id}/members/`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({identifier, role}),
            });
            elements.workspaceMemberForm.reset();
            elements.workspaceMemberRole.value = "viewer";
            await loadWorkspaces();
            await loadIncomingInvitations();
        } catch (error) {
            window.alert(buildErrorMessage(error));
        }
    }

    async function onIncomingInvitationAction(event) {
        const acceptButton = event.target.closest("[data-accept-invitation]");
        const declineButton = event.target.closest("[data-decline-invitation]");
        const invitationId =
            acceptButton?.dataset.acceptInvitation || declineButton?.dataset.declineInvitation;
        if (!invitationId) {
            return;
        }

        const action = acceptButton ? "accept" : "decline";
        try {
            await requestJson(`/api/workspace-invitations/${invitationId}/${action}/`, {
                method: "POST",
            });
            await Promise.all([loadWorkspaces(), loadIncomingInvitations(), refreshHistory()]);
        } catch (error) {
            window.alert(buildErrorMessage(error));
        }
    }

    function currentWorkspace() {
        return state.workspaces.find((workspace) => workspace.id === state.currentWorkspaceId) || null;
    }

    function renderAnalysis(analysis, {reveal = false} = {}) {
        const signature = JSON.stringify({
            id: analysis.id,
            status: analysis.status,
            framework: analysis.detected_framework,
            confidence: analysis.confidence,
            root: analysis.execution_root,
            profile: analysis.generation_profile,
            ports: analysis.probable_ports,
            services: analysis.services,
            recommendations: analysis.recommendations,
            artifacts: (analysis.artifacts || []).map((artifact) => ({
                id: artifact.id,
                path: artifact.path,
                updated_at: artifact.updated_at,
            })),
            validation: analysis.latest_validation_job && analysis.latest_validation_job.id,
            github: analysis.latest_github_pr_job && analysis.latest_github_pr_job.id,
            preview: analysis.active_preview && analysis.active_preview.id,
            workspace: analysis.workspace && analysis.workspace.id,
            security: analysis.security_report,
            healthchecks: analysis.healthcheck_report,
            cicd: analysis.cicd_report,
            deploy: analysis.deploy_report,
            updated_at: analysis.updated_at,
        });
        const isSameAnalysis = state.analysis && state.analysis.id === analysis.id;

        state.analysis = analysis;
        if (analysis.workspace?.id) {
            state.currentWorkspaceId = analysis.workspace.id;
        }
        setActiveHistoryItem(analysis.id);
        elements.resultProfile.value = analysis.generation_profile || "production";

        if (isSameAnalysis && signature === state.analysisSignature) {
            syncStatusFromAnalysis(analysis);
            renderJob("validation", analysis.latest_validation_job);
            renderJob("github", analysis.latest_github_pr_job);
            renderProfile(analysis);
            renderPreview(analysis.active_preview);
            renderSecurityReport(analysis.security_report);
            renderHealthchecks(analysis.healthcheck_report);
            renderCicd(analysis.cicd_report);
            renderDeploy(analysis.deploy_report);
            return;
        }

        state.analysisSignature = signature;
        elements.panel.classList.remove("is-empty");
        elements.title.textContent = `${analysis.project_name} · ${analysis.detected_framework || "Stack sin clasificar"}`;
        elements.subtitle.textContent = buildSubtitle(analysis);

        const isReady = analysis.status === "ready";
        const components = analysis.analysis_payload?.components || [];

        elements.regenerate.disabled = !state.analysis;
        elements.validate.disabled = !isReady;
        elements.diff.disabled = !isReady;
        elements.preview.disabled = !isReady;
        elements.githubButton.disabled = !isReady;
        elements.stopPreview.disabled = !(analysis.active_preview && analysis.active_preview.is_active);
        elements.download.href = isReady ? analysis.download_url : "#";
        elements.download.classList.toggle("is-disabled", !isReady);

        renderSummaryCards(analysis, components);
        renderRecommendations(analysis.recommendations || []);

        if (isReady) {
            const artifactSignature = JSON.stringify(
                (analysis.artifacts || []).map((artifact) => ({
                    id: artifact.id,
                    path: artifact.path,
                    updated_at: artifact.updated_at,
                })),
            );
            if (artifactSignature !== state.artifactSignature) {
                state.artifactSignature = artifactSignature;
                renderArtifacts(analysis.artifacts || []);
            }
        } else {
            state.artifactSignature = "";
            state.activeArtifactId = null;
            disposeEditors();
            elements.tabs.innerHTML = "";
            elements.editors.innerHTML = `<p class="empty-copy">${escapeHtml(buildSubtitle(analysis))}</p>`;
        }

        renderJob("validation", analysis.latest_validation_job);
        renderJob("github", analysis.latest_github_pr_job);
        renderProfile(analysis);
        renderPreview(analysis.active_preview);
        renderSecurityReport(analysis.security_report);
        renderHealthchecks(analysis.healthcheck_report);
        renderCicd(analysis.cicd_report);
        renderDeploy(analysis.deploy_report);
        syncStatusFromAnalysis(analysis);
        syncHistoryItem(analysis);
        seedPullRequestForm(analysis);

        if (reveal || isReady) {
            elements.panel.classList.remove("is-revealed");
            window.requestAnimationFrame(() => {
                elements.panel.classList.add("is-revealed");
            });
        }
    }

    function clearCurrentAnalysis() {
        state.analysis = null;
        state.analysisSignature = "";
        state.artifactSignature = "";
        state.diffSignature = "";
        state.activeArtifactId = null;
        disposeEditors();
        elements.panel.classList.add("is-empty");
        elements.title.textContent = "Todavía no hay una generación activa";
        elements.subtitle.textContent = "Subí un proyecto o cargá un análisis del historial.";
        elements.summaryGrid.innerHTML = "";
        elements.recommendations.innerHTML = "";
        elements.tabs.innerHTML = "";
        elements.editors.innerHTML =
            '<p class="empty-copy">Subí un proyecto o cargá un análisis del historial para editar los artefactos generados.</p>';
        elements.regenerate.disabled = true;
        elements.validate.disabled = true;
        elements.diff.disabled = true;
        elements.preview.disabled = true;
        elements.githubButton.disabled = true;
        elements.stopPreview.disabled = true;
        elements.download.href = "#";
        elements.download.classList.add("is-disabled");
        renderJob("validation", null);
        renderJob("github", null);
        renderProfile(null);
        renderPreview(null);
        renderDiff([]);
        renderSecurityReport(null);
        renderHealthchecks(null);
        renderCicd(null);
        renderDeploy(null);
        setStatusBadge("Listo", "subtle");
    }

    function renderSummaryCards(analysis, components) {
        const items = [
            {label: "Stack detectado", value: analysis.detected_framework || "Pendiente"},
            {label: "Confianza", value: analysis.confidence || "0.00"},
            {label: "Root de ejecución", value: analysis.execution_root || "."},
            {label: "Puertos", value: (analysis.probable_ports || []).join(", ") || "Sin puertos"},
            {label: "Servicios", value: (analysis.services || []).join(", ") || "Sin auxiliares"},
            {label: "Componentes", value: String(components.length || 0)},
        ];

        elements.summaryGrid.innerHTML = items
            .map(
                (item) => `
                    <article class="summary-card">
                        <span>${escapeHtml(item.label)}</span>
                        <strong>${escapeHtml(String(item.value))}</strong>
                    </article>
                `,
            )
            .join("");
    }

    function renderRecommendations(recommendations) {
        const fallback = [
            "Generar Dockerfiles por componente y orquestarlos desde docker-compose.",
            "Incluir servicios auxiliares en compose: postgres, redis.",
        ];
        const messages = Array.from(new Set([...(recommendations || []), ...fallback])).slice(0, 4);
        elements.recommendations.innerHTML = messages
            .map((message) => `<div class="recommendation">${escapeHtml(message)}</div>`)
            .join("");
    }

    function renderSecurityReport(report) {
        if (!report || (!report.summary && !(report.findings || []).length)) {
            elements.securitySummary.textContent = "Todavía no hay resultados de seguridad.";
            elements.securityFindings.innerHTML =
                '<p class="empty-copy">El scanner corre automáticamente al finalizar cada análisis.</p>';
            return;
        }

        elements.securitySummary.textContent = report.summary || "Scanner ejecutado.";
        const findings = report.findings || [];
        if (!findings.length) {
            elements.securityFindings.innerHTML =
                '<p class="empty-copy">Sin findings relevantes en esta generación.</p>';
            return;
        }

        elements.securityFindings.innerHTML = findings
            .map(
                (finding) => `
                    <article class="diff-entry">
                        <div class="diff-entry__header">
                            <span class="diff-entry__path">${escapeHtml(finding.title)}</span>
                            <span class="badge subtle">${escapeHtml(String(finding.severity || "").toUpperCase())}</span>
                        </div>
                        <p class="empty-copy">${escapeHtml(finding.detail || "")}</p>
                        ${
                            finding.recommendation
                                ? `<p class="empty-copy"><strong>Acción:</strong> ${escapeHtml(finding.recommendation)}</p>`
                                : ""
                        }
                        ${finding.path ? `<p class="empty-copy">${escapeHtml(finding.path)}</p>` : ""}
                    </article>
                `,
            )
            .join("");
    }

    function renderHealthchecks(report) {
        if (!report || (!report.summary && !(report.items || []).length)) {
            elements.healthcheckSummary.textContent = "Todavía no hay healthchecks calculados.";
            elements.healthcheckDetails.textContent =
                "Los healthchecks automáticos se mostrarán cuando el análisis detecte comandos portables para el runtime.";
            return;
        }

        elements.healthcheckSummary.textContent = report.summary || "Healthchecks calculados.";
        const lines = (report.items || []).map((item) => {
            const prefix = item.supported ? "AUTO" : "MANUAL";
            const command = item.command?.length ? item.command.join(" ") : item.reason || "Sin comando.";
            return `${prefix} · ${item.component_name} (${item.port})\n${command}`;
        });
        elements.healthcheckDetails.textContent = lines.join("\n\n") || report.summary;
    }

    function renderCicd(report) {
        if (!report || !(report.generated_paths || []).length) {
            elements.cicdSummary.textContent = "Todavía no hay pipeline generado.";
            elements.cicdArtifacts.innerHTML = "";
            return;
        }

        elements.cicdSummary.textContent =
            `${report.summary || "Pipeline generado."} · ${report.provider || "provider pendiente"}`;
        elements.cicdArtifacts.innerHTML = (report.generated_paths || [])
            .map(
                (path) => `
                    <span class="secondary-button secondary-button--ghost">
                        ${escapeHtml(path)}
                    </span>
                `,
            )
            .join("");
    }

    function renderDeploy(report) {
        if (!report || !(report.generated_paths || []).length) {
            elements.deploySummary.textContent = "Todavía no hay targets de deploy generados.";
            elements.deployTargets.innerHTML = "";
            return;
        }

        elements.deploySummary.textContent =
            `${report.summary || "Targets generados."} · ${(report.targets || []).join(", ")}`;
        elements.deployTargets.innerHTML = (report.generated_paths || [])
            .map(
                (path) => `
                    <span class="secondary-button secondary-button--ghost">
                        ${escapeHtml(path)}
                    </span>
                `,
            )
            .join("");
    }

    function renderArtifacts(artifacts) {
        disposeEditors();

        if (!artifacts.length) {
            elements.tabs.innerHTML = "";
            elements.editors.innerHTML = '<p class="empty-copy">No se generaron artefactos.</p>';
            return;
        }

        const activeArtifactId = artifacts.some((artifact) => artifact.id === state.activeArtifactId)
            ? state.activeArtifactId
            : artifacts[0].id;
        state.activeArtifactId = activeArtifactId;

        elements.tabs.innerHTML = artifacts
            .map(
                (artifact) => `
                    <button
                        type="button"
                        class="artifact-tab ${artifact.id === activeArtifactId ? "is-active" : ""}"
                        data-artifact-tab="${artifact.id}"
                    >
                        ${escapeHtml(artifact.path)}
                    </button>
                `,
            )
            .join("");

        elements.editors.innerHTML = artifacts
            .map(
                (artifact) => `
                    <article
                        class="editor-card"
                        data-artifact-panel="${artifact.id}"
                        ${artifact.id === activeArtifactId ? "" : "hidden"}
                    >
                        <header class="editor-card__top">
                            <div>
                                <strong class="editor-card__path">${escapeHtml(artifact.path)}</strong>
                                <p class="empty-copy">${escapeHtml(artifact.description || "")}</p>
                            </div>
                            <button
                                type="button"
                                class="secondary-button"
                                data-save-artifact="${artifact.id}"
                            >
                                Guardar cambios
                            </button>
                        </header>
                        <div class="editor-card__surface" data-editor-surface="${artifact.id}"></div>
                        <textarea class="editor-card__raw" data-artifact-content="${artifact.id}">${escapeHtml(artifact.content)}</textarea>
                    </article>
                `,
            )
            .join("");

        activateArtifact(activeArtifactId);
    }

    function renderJob(kind, job) {
        if (kind === "validation") {
            if (!job) {
                elements.validationSummary.textContent = "No ejecutada.";
                elements.validationLogs.textContent = "Todavía no hay logs de validación.";
                return;
            }

            elements.validationSummary.textContent = `${labelStatus(job.status)} · ${job.label || "Validación"}`;
            elements.validationLogs.textContent =
                job.logs || formatJson(job.result_payload) || "Todavía no hay logs de validación.";

            if (job.is_processing) {
                pollJob("validation", job.id);
            } else {
                stopPoll("validation");
            }
            return;
        }

        if (!job) {
            elements.githubSummary.textContent = "No se creó ningún PR todavía.";
            elements.githubLogs.textContent = "Todavía no hay logs de GitHub.";
            return;
        }

        const urlSuffix = job.result_payload?.pr_url ? ` · ${job.result_payload.pr_url}` : "";
        const skippedSuffix = job.result_payload?.skipped ? " · sin cambios" : "";
        elements.githubSummary.textContent = `${labelStatus(job.status)} · ${job.label || "PR"}${skippedSuffix}${urlSuffix}`;
        elements.githubLogs.textContent =
            job.logs || formatJson(job.result_payload) || "Todavía no hay logs de GitHub.";

        if (job.is_processing) {
            pollJob("github", job.id);
        } else {
            stopPoll("github");
        }
    }

    function renderProfile(analysis) {
        if (!analysis) {
            elements.profileSummary.textContent = "Todavía no hay un perfil activo cargado.";
            elements.profileDetails.textContent =
                "Seleccioná un análisis para ver cómo cambia la generación entre desarrollo y producción.";
            return;
        }

        const profile = analysis.generation_profile || "production";
        const components = analysis.analysis_payload?.components || [];
        const lines = [];

        if (profile === "development") {
            lines.push("Perfil orientado a iteración local.");
            lines.push("- bind mounts en compose cuando aplica");
            lines.push("- comandos de hot reload o dev server");
            lines.push("- AUTODOCKER_PROFILE=development");
        } else if (profile === "ci") {
            lines.push("Perfil orientado a pipelines y validación automatizada.");
            lines.push("- artefactos reproducibles para build y test");
            lines.push("- sin bind mounts de desarrollo");
            lines.push("- AUTODOCKER_PROFILE=ci");
        } else {
            lines.push("Perfil orientado a producción.");
            lines.push("- imágenes finales optimizadas");
            lines.push("- multi-stage cuando aplica");
            lines.push("- AUTODOCKER_PROFILE=production");
        }

        if (components.length > 1) {
            lines.push(`- componentes detectados: ${components.length}`);
        }

        elements.profileSummary.textContent = `Perfil activo: ${profileLabel(profile)} · ${analysis.detected_framework || "stack no detectado"}`;
        elements.profileDetails.textContent = lines.join("\n");
    }

    function renderPreview(preview) {
        if (!preview) {
            elements.previewSummary.textContent = "No hay preview activa.";
            elements.previewLinks.innerHTML = "";
            elements.previewLogs.textContent = "Todavía no hay logs de preview.";
            elements.stopPreview.disabled = true;
            return;
        }

        elements.stopPreview.disabled = !preview.is_active;
        elements.previewSummary.textContent = `${labelStatus(preview.status)} · ${preview.runtime_kind || "runtime pendiente"}`;
        elements.previewLinks.innerHTML = renderPreviewLinks(preview.ports || {}, preview.access_url || "");
        elements.previewLogs.textContent = preview.logs || "Todavía no hay logs de preview.";

        if (["queued", "running"].includes(preview.status)) {
            pollPreview(preview.id);
        } else {
            stopPoll("preview");
        }
    }

    function renderDiff(items) {
        const signature = JSON.stringify(items.map((item) => [item.path, item.status]));
        if (signature === state.diffSignature) {
            return;
        }
        state.diffSignature = signature;

        if (!items.length) {
            elements.diffResults.innerHTML =
                '<p class="empty-copy">No se encontraron diferencias relevantes.</p>';
            return;
        }

        elements.diffResults.innerHTML = items
            .map(
                (item) => `
                    <details class="diff-entry">
                        <summary>
                            <span class="diff-entry__path">${escapeHtml(item.path)}</span>
                            <span class="badge subtle">${escapeHtml(item.status)}</span>
                        </summary>
                        <pre class="log-view">${escapeHtml(item.diff || "No hay diff textual.")}</pre>
                    </details>
                `,
            )
            .join("");
    }

    function renderPreviewLinks(ports, fallbackUrl) {
        const entries = Object.entries(ports || {});
        if (!entries.length && !fallbackUrl) {
            return '<p class="empty-copy">No se expusieron puertos todavía.</p>';
        }

        const links = [];
        entries.forEach(([serviceName, urls]) => {
            (urls || []).forEach((url) => {
                links.push(`
                    <a class="secondary-button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
                        ${escapeHtml(serviceName)} · ${escapeHtml(url)}
                    </a>
                `);
            });
        });

        if (!links.length && fallbackUrl) {
            links.push(`
                <a class="secondary-button" href="${escapeHtml(fallbackUrl)}" target="_blank" rel="noopener noreferrer">
                    ${escapeHtml(fallbackUrl)}
                </a>
            `);
        }

        return links.join("");
    }

    function prependHistoryItem(analysis) {
        if (analysis.workspace?.id && analysis.workspace.id !== state.currentWorkspaceId) {
            return;
        }
        mergeHistoryAnalysis(analysis, {prepend: true});
        state.historyPage = 1;
        renderHistoryList();
    }

    function syncHistoryItem(analysis) {
        mergeHistoryAnalysis(analysis);
        renderHistoryList();
    }

    function buildHistoryItem(analysis) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "history-item";
        button.dataset.analysisId = analysis.id;
        button.innerHTML = `
            <span class="history-item__dot ${historyDotClass(analysis.status)}"></span>
            <span class="history-item__body">
                <span class="history-item__title">${escapeHtml(analysis.project_name)}</span>
                <span class="history-item__meta">${escapeHtml(analysis.detected_framework || "Sin clasificar")}</span>
            </span>
            <span class="history-item__badge ${historyBadgeClass(analysis.status)}">${escapeHtml(labelStatus(analysis.status))}</span>
        `;
        return button;
    }

    function setActiveHistoryItem(analysisId) {
        elements.history.querySelectorAll("[data-analysis-id]").forEach((node) => {
            node.classList.toggle("is-active", node.dataset.analysisId === analysisId);
        });
    }

    function activateArtifact(artifactId) {
        state.activeArtifactId = artifactId;
        elements.tabs.querySelectorAll(".artifact-tab").forEach((tab) => {
            tab.classList.toggle("is-active", tab.dataset.artifactTab === artifactId);
        });
        elements.editors.querySelectorAll("[data-artifact-panel]").forEach((panel) => {
            panel.hidden = panel.dataset.artifactPanel !== artifactId;
        });
        ensureEditor(artifactId);
    }

    function stopPoll(name) {
        if (state.polls[name]) {
            clearTimeout(state.polls[name]);
            state.polls[name] = null;
        }
        state.busy[name] = false;
    }

    function pollAnalysis(analysisId) {
        stopPoll("analysis");
        const tick = async () => {
            if (state.busy.analysis) {
                return;
            }
            state.busy.analysis = true;
            try {
                const analysis = await requestJson(`/api/analyses/${analysisId}/`);
                renderAnalysis(analysis, {reveal: analysis.status === "ready"});
                if (analysis.is_processing) {
                    state.polls.analysis = setTimeout(tick, 1500);
                }
            } catch {
                setStatusBadge("Polling interrumpido", "error");
            } finally {
                state.busy.analysis = false;
            }
        };
        state.polls.analysis = setTimeout(tick, 1500);
    }

    function pollJob(kind, jobId) {
        stopPoll(kind);
        const tick = async () => {
            if (state.busy[kind]) {
                return;
            }
            state.busy[kind] = true;
            try {
                const job = await requestJson(`/api/jobs/${jobId}/`);
                renderJob(kind, job);
                if (job.is_processing) {
                    state.polls[kind] = setTimeout(tick, 1500);
                } else {
                    await refreshCurrentAnalysis();
                }
            } catch (error) {
                if (kind === "validation") {
                    elements.validationSummary.textContent = buildErrorMessage(error);
                } else {
                    elements.githubSummary.textContent = buildErrorMessage(error);
                }
            } finally {
                state.busy[kind] = false;
            }
        };
        state.polls[kind] = setTimeout(tick, 1500);
    }

    function pollPreview(previewId) {
        stopPoll("preview");
        const tick = async () => {
            if (state.busy.preview) {
                return;
            }
            state.busy.preview = true;
            try {
                const preview = await requestJson(`/api/previews/${previewId}/`);
                renderPreview(preview);
                if (["queued", "running"].includes(preview.status)) {
                    state.polls.preview = setTimeout(tick, 2000);
                } else {
                    await refreshCurrentAnalysis();
                }
            } catch (error) {
                elements.previewSummary.textContent = buildErrorMessage(error);
            } finally {
                state.busy.preview = false;
            }
        };
        state.polls.preview = setTimeout(tick, 2000);
    }

    async function refreshCurrentAnalysis() {
        if (!state.analysis) {
            return;
        }
        const analysis = await requestJson(`/api/analyses/${state.analysis.id}/`);
        renderAnalysis(analysis, {reveal: false});
    }

    function disposeEditors() {
        state.editors.forEach((editor) => editor.dispose());
        state.editors.clear();
    }

    function getEditorValue(artifactId) {
        const editor = state.editors.get(artifactId);
        if (editor) {
            return editor.getValue();
        }

        const raw = elements.editors.querySelector(`[data-artifact-content="${artifactId}"]`);
        return raw ? raw.value : "";
    }

    function ensureEditor(artifactId) {
        const existingEditor = state.editors.get(artifactId);
        if (existingEditor) {
            existingEditor.layout();
            return Promise.resolve(existingEditor);
        }

        const surface = elements.editors.querySelector(`[data-editor-surface="${artifactId}"]`);
        const raw = elements.editors.querySelector(`[data-artifact-content="${artifactId}"]`);
        const panel = elements.editors.querySelector(`[data-artifact-panel="${artifactId}"]`);
        const path = panel?.querySelector(".editor-card__path")?.textContent || "";

        if (!surface || !raw) {
            return Promise.resolve(null);
        }

        return loadMonaco()
            .then(() => {
                defineMonacoTheme();
                const editor = window.monaco.editor.create(surface, {
                    value: raw.value,
                    language: detectLanguage(path),
                    automaticLayout: true,
                    minimap: {enabled: false},
                    fontFamily: "IBM Plex Mono",
                    fontSize: 13,
                    theme: "autodocker-dark",
                    scrollBeyondLastLine: false,
                });
                state.editors.set(artifactId, editor);
                return editor;
            })
            .catch(() => {
                raw.style.display = "block";
                return null;
            });
    }

    function loadMonaco() {
        if (window.monaco?.editor) {
            return Promise.resolve();
        }

        if (state.monacoPromise) {
            return state.monacoPromise;
        }

        state.monacoPromise = loadMonacoLoader().then(
            () =>
                new Promise((resolve, reject) => {
                    window.require.config({
                        paths: {
                            vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs",
                        },
                    });
                    window.require(["vs/editor/editor.main"], resolve, reject);
                }),
        );

        return state.monacoPromise;
    }

    function loadMonacoLoader() {
        if (window.require?.config) {
            return Promise.resolve();
        }

        if (state.monacoLoaderPromise) {
            return state.monacoLoaderPromise;
        }

        state.monacoLoaderPromise = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = MONACO_LOADER_URL;
            script.async = true;
            script.crossOrigin = "anonymous";
            script.onload = resolve;
            script.onerror = () => reject(new Error("No se pudo cargar Monaco."));
            document.body.appendChild(script);
        });

        return state.monacoLoaderPromise;
    }

    function defineMonacoTheme() {
        if (!window.monaco || window.monaco.editor.__autodockerThemeDefined) {
            return;
        }

        window.monaco.editor.defineTheme("autodocker-dark", {
            base: "vs-dark",
            inherit: true,
            rules: [
                {token: "keyword", foreground: "59D2FF", fontStyle: "bold"},
                {token: "string", foreground: "00E5B0"},
                {token: "variable", foreground: "F5F7F8"},
                {token: "type", foreground: "9EDFFF"},
                {token: "number", foreground: "D0A347"},
            ],
            colors: {
                "editor.background": "#0d1117",
                "editorLineNumber.foreground": "#5a6670",
                "editorLineNumber.activeForeground": "#d8e0e5",
                "editorCursor.foreground": "#00e5b0",
                "editor.selectionBackground": "#17362f",
                "editor.lineHighlightBackground": "#14181d",
                "editorGutter.background": "#0d1117",
            },
        });
        window.monaco.editor.__autodockerThemeDefined = true;
    }

    function detectLanguage(path) {
        if (path.endsWith("Dockerfile") || path.endsWith(".dockerignore")) {
            return "dockerfile";
        }
        if (path.endsWith(".yml") || path.endsWith(".yaml")) {
            return "yaml";
        }
        if (path.endsWith(".md")) {
            return "markdown";
        }
        if (path.endsWith(".json")) {
            return "json";
        }
        return "plaintext";
    }

    function syncStatusFromAnalysis(analysis) {
        if (analysis.status === "failed") {
            setStatusBadge("Análisis fallido", "error");
            stopPoll("analysis");
            return;
        }

        if (analysis.is_processing) {
            setStatusBadge(
                analysis.status === "queued" ? "Trabajo en cola" : "Analizando en background",
                "running",
            );
            pollAnalysis(analysis.id);
            return;
        }

        setStatusBadge("Listo", "ok");
        stopPoll("analysis");
    }

    function buildSubtitle(analysis) {
        if (analysis.status === "queued") {
            return "El análisis fue encolado. La UI va a refrescar automáticamente cuando el worker complete la ejecución.";
        }
        if (analysis.status === "analyzing") {
            return "El worker está procesando el proyecto. Podés revisar el historial mientras tanto.";
        }
        if (analysis.status === "failed") {
            return analysis.last_error || "La ejecución falló sin un mensaje específico.";
        }
        return "Subí un proyecto o cargá un análisis del historial.";
    }

    function seedPullRequestForm(analysis) {
        if (!elements.githubTitle.value) {
            elements.githubTitle.value = `Dockerize ${analysis.project_name}`;
        }
        if (!elements.githubBody.value) {
            elements.githubBody.value = "Auto-generated Docker configuration from AutoDocker.";
        }
    }

    function labelStatus(status) {
        return (
            {
                queued: "En cola",
                analyzing: "Analizando",
                running: "En ejecución",
                ready: "Ready",
                failed: "Falló",
                canceled: "Cancelado",
                stopped: "Detenido",
            }[status] || status || "Sin estado"
        );
    }

    function profileLabel(profile) {
        return (
            {
                production: "Producción",
                development: "Desarrollo",
                ci: "CI",
            }[profile] || profile
        );
    }

    function deliveryStatusLabel(status) {
        return (
            {
                pending: "pendiente",
                in_app: "visible en la app",
                sent: "email enviado",
                failed: "email fallido",
            }[status] || status || "pendiente"
        );
    }

    function formatJson(value) {
        try {
            return value ? JSON.stringify(value, null, 2) : "";
        } catch {
            return String(value);
        }
    }

    function escapeHtml(value) {
        return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;");
    }

    function buildErrorMessage(error) {
        if (!error) {
            return "Ocurrió un error inesperado.";
        }

        if (typeof error === "string") {
            const text = error.trim();
            if (text.startsWith("<!DOCTYPE") || text.startsWith("<html")) {
                return "El servidor devolvió una respuesta HTML inesperada. Revisá los logs del backend.";
            }
            return text;
        }

        if (typeof error === "object") {
            if (typeof error.detail === "string") {
                return error.detail;
            }
            const lines = Object.entries(error).flatMap(([key, value]) => {
                if (Array.isArray(value)) {
                    return value.map((item) => `${key}: ${item}`);
                }
                if (typeof value === "string") {
                    return [`${key}: ${value}`];
                }
                return [];
            });
            if (lines.length) {
                return lines.join("\n");
            }
        }

        return "Ocurrió un error inesperado.";
    }

    function toggleHistoryExpanded() {
        state.historyExpanded = !state.historyExpanded;
        renderHistoryList();
    }

    function goToPreviousHistoryPage() {
        if (state.historyPage <= 1) {
            return;
        }
        state.historyPage -= 1;
        renderHistoryList();
    }

    function goToNextHistoryPage() {
        const pageSize = 8;
        const totalPages = Math.max(1, Math.ceil((state.historyAnalyses || []).length / pageSize));
        if (state.historyPage >= totalPages) {
            return;
        }
        state.historyPage += 1;
        renderHistoryList();
    }

    function onViewportResize() {
        const compact = window.matchMedia("(max-width: 1100px)").matches;
        if (compact === state.compactHistory) {
            return;
        }
        state.compactHistory = compact;
        if (compact) {
            state.historyExpanded = false;
        } else {
            state.historyPage = 1;
        }
        renderHistoryList();
    }

    function mergeHistoryAnalysis(analysis, {prepend = false} = {}) {
        const analyses = [...(state.historyAnalyses || [])];
        const existingIndex = analyses.findIndex((item) => item.id === analysis.id);
        if (existingIndex >= 0) {
            analyses.splice(existingIndex, 1);
        }
        if (prepend) {
            analyses.unshift(analysis);
        } else if (existingIndex >= 0) {
            analyses.splice(existingIndex, 0, analysis);
        } else {
            analyses.unshift(analysis);
        }
        state.historyAnalyses = analyses;
    }

    function historyDotClass(status) {
        return (
            {
                ready: "history-item__dot--ok",
                failed: "history-item__dot--error",
                analyzing: "history-item__dot--running",
                queued: "history-item__dot--running",
            }[status] || ""
        );
    }

    function historyBadgeClass(status) {
        return (
            {
                ready: "history-item__badge--ok",
                failed: "history-item__badge--error",
                analyzing: "history-item__badge--running",
                queued: "history-item__badge--running",
            }[status] || "history-item__badge--running"
        );
    }
})();
