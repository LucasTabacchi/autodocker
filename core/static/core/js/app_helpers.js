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

    window.AutoDockerHelpers = {
        buildErrorMessage,
        deliveryStatusLabel,
        escapeHtml,
        labelStatus,
        profileLabel,
    };
})();
