from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class ComponentSpec:
    name: str
    path: str = "."
    language: str = ""
    framework: str = ""
    runtime: str = ""
    role: str = "app"
    package_manager: str | None = None
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    probable_ports: list[int] = field(default_factory=list)
    healthcheck_path: str = "/"
    environment_variables: list[str] = field(default_factory=list)
    found_files: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    dependency_names: list[str] = field(default_factory=list)
    needs_multistage: bool = False
    base_image_hint: str = "slim"
    confidence: float = 0.0
    existing_dockerfile: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ComponentSpec":
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class DetectionResult:
    project_name: str
    project_type: str = "single-service"
    confidence: float = 0.0
    components: list[ComponentSpec] = field(default_factory=list)
    shared_services: list[str] = field(default_factory=list)
    environment_variables: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    found_files: list[str] = field(default_factory=list)
    existing_dockerfile: bool = False
    package_managers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def primary_component(self) -> ComponentSpec | None:
        if not self.components:
            return None
        backends = [component for component in self.components if component.role == "backend"]
        return backends[0] if backends else self.components[0]

    @classmethod
    def from_dict(cls, data: dict) -> "DetectionResult":
        components = [ComponentSpec.from_dict(item) for item in data.get("components", [])]
        payload = {**data, "components": components}
        return cls(**payload)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["components"] = [component.to_dict() for component in self.components]
        return data


@dataclass(slots=True)
class GeneratedArtifactSpec:
    kind: str
    path: str
    content: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class GenerationResult:
    artifacts: list[GeneratedArtifactSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "warnings": self.warnings,
        }
