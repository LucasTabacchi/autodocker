from __future__ import annotations

from core.services.contracts import DetectionResult, GenerationResult


class ArtifactValidator:
    REQUIRED_DOCKERFILE_TOKENS = ("FROM", "WORKDIR", "COPY")

    def validate(self, detection: DetectionResult, generation: GenerationResult) -> list[str]:
        warnings: list[str] = []
        dockerfiles = [
            artifact for artifact in generation.artifacts if artifact.kind == "dockerfile"
        ]
        for artifact in dockerfiles:
            content = artifact.content
            for token in self.REQUIRED_DOCKERFILE_TOKENS:
                if token not in content:
                    warnings.append(f"{artifact.path}: falta la instrucción {token}.")
            if "CMD" not in content and "ENTRYPOINT" not in content:
                warnings.append(f"{artifact.path}: falta definir CMD o ENTRYPOINT.")

        needs_compose = len(detection.components) > 1 or bool(detection.shared_services)
        has_compose = any(artifact.kind == "compose" for artifact in generation.artifacts)
        if needs_compose and not has_compose:
            warnings.append("El análisis detectó múltiples servicios pero no se generó docker-compose.")
        return warnings
