from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core.services.contracts import DetectionResult, GenerationResult


@dataclass(slots=True)
class SecurityFinding:
    severity: str
    title: str
    detail: str
    path: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SecurityScanReport:
    score: int
    summary: str
    findings: list[SecurityFinding] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "recommendations": self.recommendations,
        }


class SecurityScannerService:
    SECRET_TOKENS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY")
    EXPOSED_SERVICE_PORTS = {
        "5432:5432": "postgres",
        "3306:3306": "mysql",
        "6379:6379": "redis",
        "27017:27017": "mongodb",
    }
    SEVERITY_WEIGHT = {"critical": 30, "high": 20, "medium": 12, "low": 6}

    def scan(self, detection: DetectionResult, generation: GenerationResult) -> SecurityScanReport:
        findings: list[SecurityFinding] = []
        recommendations: list[str] = []
        artifacts = {artifact.path: artifact for artifact in generation.artifacts}
        dockerfiles = [artifact for artifact in generation.artifacts if artifact.path.endswith("Dockerfile")]
        compose = artifacts.get("docker-compose.yml")

        for artifact in dockerfiles:
            lines = artifact.content.splitlines()
            from_lines = [line for line in lines if line.startswith("FROM ")]
            final_stage_lines = lines[-12:]
            if any(":latest" in line for line in from_lines):
                findings.append(
                    SecurityFinding(
                        severity="medium",
                        title="Base image floating",
                        detail="El Dockerfile usa una imagen sin pinning estable.",
                        path=artifact.path,
                        recommendation="Usá tags explícitos para evitar cambios sorpresivos en build.",
                    )
                )
            if not any(line.startswith("USER ") for line in final_stage_lines):
                findings.append(
                    SecurityFinding(
                        severity="medium",
                        title="Runtime sin usuario dedicado",
                        detail="La etapa final no define un usuario no-root.",
                        path=artifact.path,
                        recommendation="Agregá un usuario de aplicación y usalo en runtime.",
                    )
                )
            if "HEALTHCHECK" not in artifact.content:
                findings.append(
                    SecurityFinding(
                        severity="low",
                        title="Sin healthcheck",
                        detail="El contenedor no define verificación de salud.",
                        path=artifact.path,
                        recommendation="Sumá HEALTHCHECK o healthchecks de compose para mejorar observabilidad.",
                    )
                )

        for env_var in detection.environment_variables:
            if any(token in env_var.upper() for token in self.SECRET_TOKENS):
                findings.append(
                    SecurityFinding(
                        severity="medium",
                        title="Variable sensible detectada",
                        detail=f"Se detectó una variable potencialmente sensible: {env_var}.",
                        recommendation="No hardcodees secretos y documentá su origen por entorno.",
                    )
                )

        if compose:
            for port_mapping, service in self.EXPOSED_SERVICE_PORTS.items():
                if port_mapping in compose.content:
                    findings.append(
                        SecurityFinding(
                            severity="low",
                            title="Servicio auxiliar expuesto",
                            detail=f"El compose publica {service} directamente al host.",
                            path=compose.path,
                            recommendation="Si no lo necesitás en desarrollo, evitá publicar el puerto al host.",
                        )
                    )

        if detection.existing_dockerfile:
            recommendations.append("Compará los Dockerfiles existentes contra los generados antes de exportar.")
        if detection.shared_services:
            recommendations.append(
                "Revisá credenciales y exposición de servicios auxiliares en docker-compose antes de compartir el entorno."
            )
        if any(component.framework in {"Next.js", "Django", "FastAPI"} for component in detection.components):
            recommendations.append("Activá validación de build y smoke checks antes de abrir un PR automático.")

        findings = self._dedupe_findings(findings)
        score = max(0, 100 - sum(self.SEVERITY_WEIGHT.get(item.severity, 0) for item in findings))
        summary = self._build_summary(findings, score)
        recommendations = sorted(dict.fromkeys(recommendations))
        return SecurityScanReport(
            score=score,
            summary=summary,
            findings=findings,
            recommendations=recommendations,
        )

    def _build_summary(self, findings: list[SecurityFinding], score: int) -> str:
        if not findings:
            return f"Scanner limpio · score {score}/100."
        counts: dict[str, int] = {}
        for item in findings:
            counts[item.severity] = counts.get(item.severity, 0) + 1
        parts = [f"{counts[key]} {key}" for key in ("critical", "high", "medium", "low") if counts.get(key)]
        return f"Score {score}/100 · " + ", ".join(parts)

    def _dedupe_findings(self, findings: list[SecurityFinding]) -> list[SecurityFinding]:
        unique: dict[tuple[str, str, str], SecurityFinding] = {}
        for item in findings:
            key = (item.severity, item.title, item.path)
            unique[key] = item
        return list(unique.values())
