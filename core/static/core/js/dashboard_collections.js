(function () {
    function createCollectionsModule({
        state,
        elements,
        escapeHtml,
        labelStatus,
        deliveryStatusLabel,
    }) {
        function currentWorkspace() {
            return state.workspaces.find((workspace) => workspace.id === state.currentWorkspaceId) || null;
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

            const suffix = state.incomingInvitations.length === 1 ? "" : "es";
            elements.incomingInvitationsSummary.textContent = `Tenés ${state.incomingInvitations.length} invitación${suffix} pendiente${suffix}.`;
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

        return {
            currentWorkspace,
            goToNextHistoryPage,
            goToPreviousHistoryPage,
            onViewportResize,
            prependHistoryItem,
            renderHistory,
            renderHistoryList,
            renderIncomingInvitations,
            renderWorkspaceInvitations,
            renderWorkspaceMembers,
            renderWorkspaces,
            setActiveHistoryItem,
            syncHistoryItem,
            toggleHistoryExpanded,
        };
    }

    window.AutoDockerDashboardCollections = {
        create: createCollectionsModule,
    };
})();
