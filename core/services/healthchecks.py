from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core.services.contracts import ComponentSpec, DetectionResult


@dataclass(slots=True)
class HealthcheckSpec:
    component_path: str
    component_name: str
    port: int
    endpoint: str
    command: list[str]
    supported: bool
    reason: str = ""
    interval: str = "30s"
    timeout: str = "5s"
    retries: int = 5
    start_period: str = "20s"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class HealthcheckReport:
    summary: str
    items: list[HealthcheckSpec] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "items": [item.to_dict() for item in self.items],
            "recommendations": self.recommendations,
        }


class HealthcheckPlannerService:
    def plan(self, detection: DetectionResult) -> HealthcheckReport:
        items = [self._plan_component(component) for component in detection.components]
        supported = sum(1 for item in items if item.supported)
        unsupported = len(items) - supported

        recommendations = []
        if supported:
            recommendations.append("Se agregaron healthchecks automáticos en Dockerfile y compose cuando el runtime lo permite.")
        if unsupported:
            recommendations.append("Algunos componentes necesitan healthchecks manuales por limitaciones del runtime o del binario final.")
        if detection.shared_services:
            recommendations.append("Usá dependencias y orden de arranque conservador cuando el stack incluye servicios auxiliares.")

        summary = f"{supported} healthchecks automáticos · {unsupported} componentes con ajuste manual."
        return HealthcheckReport(
            summary=summary,
            items=items,
            recommendations=recommendations,
        )

    def _plan_component(self, component: ComponentSpec) -> HealthcheckSpec:
        port = component.probable_ports[0] if component.probable_ports else 8000
        endpoint = self._endpoint_for(component)

        if component.language == "Node.js":
            return HealthcheckSpec(
                component_path=component.path,
                component_name=component.name,
                port=port,
                endpoint=endpoint,
                command=[
                    "node",
                    "-e",
                    (
                        "fetch('http://127.0.0.1:%s%s').then("
                        "res => process.exit(res.ok ? 0 : 1)"
                        ").catch(() => process.exit(1))"
                    ) % (port, endpoint),
                ],
                supported=True,
            )

        if component.language == "Python":
            return HealthcheckSpec(
                component_path=component.path,
                component_name=component.name,
                port=port,
                endpoint=endpoint,
                command=[
                    "python",
                    "-c",
                    (
                        "import sys, urllib.request; "
                        "resp = urllib.request.urlopen('http://127.0.0.1:%s%s'); "
                        "sys.exit(0 if getattr(resp, 'status', 200) < 500 else 1)"
                    ) % (port, endpoint),
                ],
                supported=True,
            )

        if component.language == "PHP":
            return HealthcheckSpec(
                component_path=component.path,
                component_name=component.name,
                port=port,
                endpoint=endpoint,
                command=[
                    "php",
                    "-r",
                    "exit(@file_get_contents('http://127.0.0.1:%s%s') === false ? 1 : 0);"
                    % (port, endpoint),
                ],
                supported=True,
            )

        return HealthcheckSpec(
            component_path=component.path,
            component_name=component.name,
            port=port,
            endpoint=endpoint,
            command=[],
            supported=False,
            reason="Runtime sin comando portable de healthcheck automático.",
        )

    def _endpoint_for(self, component: ComponentSpec) -> str:
        return component.healthcheck_path or "/"
