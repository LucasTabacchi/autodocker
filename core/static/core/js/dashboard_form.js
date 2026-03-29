(function () {
    function setSource(type) {
        const zipFields = document.getElementById("source-zip-fields");
        const gitFields = document.getElementById("source-git-fields");
        const btnZip = document.getElementById("source-toggle-zip");
        const btnGit = document.getElementById("source-toggle-git");
        const repositoryInput = document.getElementById("id_repository_url");

        if (!zipFields || !gitFields || !btnZip || !btnGit || !repositoryInput) {
            return;
        }

        if (type === "zip") {
            zipFields.style.display = "";
            gitFields.style.display = "none";
            btnZip.classList.add("is-active");
            btnGit.classList.remove("is-active");
            repositoryInput.value = "";
            return;
        }

        zipFields.style.display = "none";
        gitFields.style.display = "";
        btnGit.classList.add("is-active");
        btnZip.classList.remove("is-active");
    }

    function setProfile(value, button) {
        document
            .querySelectorAll(".profile-segment__btn")
            .forEach((item) => item.classList.remove("is-active"));
        button?.classList.add("is-active");
        const profileInput = document.getElementById("id_generation_profile");
        if (profileInput) {
            profileInput.value = value;
        }
    }

    function setStatusBadge(elements, text, tone = "subtle") {
        elements.status.textContent = text;
        elements.status.className = `badge ${tone}`;
    }

    function setSubmitLoading(elements, isLoading) {
        elements.submitButton.classList.toggle("is-loading", isLoading);
        elements.submitButton.disabled = isLoading;
        elements.submitLabel.textContent = isLoading ? "Analizando proyecto..." : "Analizar proyecto";
    }

    function setArchiveLabel(elements, text) {
        if (elements.archiveFilename) {
            elements.archiveFilename.textContent = text;
        }
        if (elements.dropzoneText) {
            elements.dropzoneText.textContent = text;
        }
    }

    function updateSelectedFilename(elements) {
        const file = elements.archiveInput.files && elements.archiveInput.files[0];
        setArchiveLabel(elements, file ? file.name : "Arrastrá tu .zip acá");
    }

    function resetSubmissionForm(form, elements) {
        form.reset();
        updateSelectedFilename(elements);
    }

    function wireDropzoneControls(elements) {
        const archiveInput = elements.archiveInput;
        const dropzone = document.getElementById("dropzone");

        if (archiveInput) {
            archiveInput.addEventListener("change", function onArchiveChange() {
                updateSelectedFilename(elements);
            });
        }

        if (dropzone) {
            ["dragenter", "dragover"].forEach((eventName) => {
                dropzone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropzone.classList.add("is-over");
                });
            });
            ["dragleave", "drop"].forEach((eventName) => {
                dropzone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropzone.classList.remove("is-over");
                });
            });
        }
    }

    window.setSource = setSource;
    window.setProfile = setProfile;
    window.AutoDockerDashboardForm = {
        resetSubmissionForm,
        setArchiveLabel,
        setStatusBadge,
        setSubmitLoading,
        updateSelectedFilename,
        wireDropzoneControls,
    };
})();
