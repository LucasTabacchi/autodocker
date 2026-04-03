(function () {
    function escapeHtml(value) {
        return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;");
    }

    function buildErrorMessage(error) {
        if (!error) {
            return "An unexpected error occurred.";
        }

        if (typeof error === "string") {
            const text = error.trim();
            if (text.startsWith("<!DOCTYPE") || text.startsWith("<html")) {
                return "The server returned an unexpected HTML response. Check the backend logs.";
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

        return "An unexpected error occurred.";
    }

    function labelStatus(status) {
        return (
            {
                queued: "Queued",
                analyzing: "Analyzing",
                running: "Running",
                ready: "Ready",
                failed: "Failed",
                canceled: "Canceled",
                stopped: "Stopped",
            }[status] || status || "Unknown"
        );
    }

    function profileLabel(profile) {
        return (
            {
                production: "Production",
                development: "Development",
                ci: "CI",
            }[profile] || profile
        );
    }

    function deliveryStatusLabel(status) {
        return (
            {
                pending: "pending",
                in_app: "available in app",
                sent: "email sent",
                failed: "email failed",
            }[status] || status || "pending"
        );
    }

    window.AutoDockerHelpers = {
        buildErrorMessage,
        deliveryStatusLabel,
        escapeHtml,
        labelStatus,
        profileLabel,
    };
})();
