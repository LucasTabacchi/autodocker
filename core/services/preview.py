from __future__ import annotations

import json
import socket
import time
from datetime import timedelta
from pathlib import Path

import yaml
from django.utils import timezone

from core.models import PreviewRun, ProjectAnalysis
from core.services.ingestion import cleanup_workspace, overlay_generated_artifacts, prepare_source_workspace
from core.services.runtime import (
    CommandExecutionError,
    docker_command,
    docker_compose_command,
    run_command,
)


class PreviewService:
    def start(self, preview_run: PreviewRun) -> PreviewRun:
        analysis = preview_run.analysis
        temp_dir, source_root = prepare_source_workspace(analysis, prefix="autodocker-preview-")
        preview_run.status = PreviewRun.Status.RUNNING
        preview_run.started_at = timezone.now()
        preview_run.finished_at = None
        preview_run.workspace_root = str(temp_dir)
        preview_run.workspace_path = str(source_root)
        preview_run.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "workspace_root",
                "workspace_path",
                "updated_at",
            ]
        )

        try:
            overlay_generated_artifacts(source_root, list(analysis.artifacts.all()))
            if (source_root / "docker-compose.yml").exists():
                self._start_compose_preview(preview_run, analysis, source_root)
            else:
                self._start_single_container_preview(preview_run, source_root)
        except Exception as exc:
            preview_run.status = PreviewRun.Status.FAILED
            preview_run.logs = str(exc)
            preview_run.finished_at = timezone.now()
            preview_run.save(update_fields=["status", "logs", "finished_at", "updated_at"])
            self._cleanup_workspace(preview_run)
        return preview_run

    def stop(self, preview_run: PreviewRun) -> PreviewRun:
        workspace = Path(preview_run.workspace_path) if preview_run.workspace_path else None
        try:
            if preview_run.runtime_kind == PreviewRun.RuntimeKind.COMPOSE and workspace and workspace.exists():
                compose_base = docker_compose_command()
                command = [
                    *compose_base,
                    "-f",
                    self._preview_compose_filename(),
                    "-p",
                    self._compose_project_name(preview_run),
                    "down",
                    "-v",
                ]
                run_command(command, workspace, timeout=300, check=False)
            elif preview_run.runtime_kind == PreviewRun.RuntimeKind.CONTAINER:
                docker_base = docker_command()
                for name in preview_run.resource_names:
                    run_command([*docker_base, "rm", "-f", name], workspace or Path.cwd(), timeout=180, check=False)
        finally:
            preview_run.status = PreviewRun.Status.STOPPED
            preview_run.finished_at = timezone.now()
            preview_run.save(update_fields=["status", "finished_at", "updated_at"])
            self._cleanup_workspace(preview_run)
        return preview_run

    def refresh_logs(self, preview_run: PreviewRun) -> PreviewRun:
        workspace = Path(preview_run.workspace_path) if preview_run.workspace_path else None
        if not workspace or not workspace.exists():
            return preview_run
        try:
            if preview_run.runtime_kind == PreviewRun.RuntimeKind.COMPOSE:
                compose_base = docker_compose_command()
                command = [
                    *compose_base,
                    "-f",
                    self._preview_compose_filename(),
                    "-p",
                    self._compose_project_name(preview_run),
                    "logs",
                    "--no-color",
                    "--tail",
                    "200",
                ]
                result = run_command(command, workspace, timeout=180, check=False)
                preview_run.logs = result.output
            elif preview_run.runtime_kind == PreviewRun.RuntimeKind.CONTAINER and preview_run.resource_names:
                docker_base = docker_command()
                result = run_command(
                    [*docker_base, "logs", "--tail", "200", preview_run.resource_names[0]],
                    workspace,
                    timeout=180,
                    check=False,
                )
                preview_run.logs = result.output
            preview_run.save(update_fields=["logs", "updated_at"])
        except CommandExecutionError:
            pass
        return preview_run

    def _start_compose_preview(
        self,
        preview_run: PreviewRun,
        analysis: ProjectAnalysis,
        source_root: Path,
    ) -> None:
        preview_compose_path, service_urls = self._write_preview_override(source_root, analysis)
        candidate_service_urls = dict(service_urls)
        compose_base = docker_compose_command()
        command = [
            *compose_base,
            "-f",
            preview_compose_path.name,
            "-p",
            self._compose_project_name(preview_run),
            "up",
            "-d",
            "--build",
        ]
        result = run_command(command, source_root, timeout=1800)
        service_urls = self._wait_for_accessible_services(
            source_root,
            preview_run,
            preview_compose_path.name,
            service_urls,
        )
        logs = self._collect_compose_logs(source_root, preview_run, preview_compose_path.name)
        preview_run.status = PreviewRun.Status.READY if service_urls else PreviewRun.Status.FAILED
        preview_run.runtime_kind = PreviewRun.RuntimeKind.COMPOSE
        preview_run.command = " ".join(command)
        preview_notes = self._build_preview_notes(
            source_root,
            preview_run,
            preview_compose_path.name,
            candidate_service_urls,
            service_urls,
        )
        preview_run.logs = "\n\n".join(part for part in [result.output, logs, preview_notes] if part).strip()
        preview_run.ports = service_urls
        preview_run.access_url = self._pick_access_url(service_urls)
        preview_run.resource_names = list(service_urls.keys())
        preview_run.expires_at = timezone.now() + timedelta(hours=2)
        preview_run.finished_at = timezone.now()
        preview_run.save(
            update_fields=[
                "status",
                "runtime_kind",
                "command",
                "logs",
                "ports",
                "access_url",
                "resource_names",
                "expires_at",
                "finished_at",
                "updated_at",
            ]
        )

    def _wait_for_accessible_services(
        self,
        source_root: Path,
        preview_run: PreviewRun,
        compose_filename: str,
        service_urls: dict[str, list[str]],
        timeout_seconds: int = 30,
    ) -> dict[str, list[str]]:
        deadline = time.monotonic() + timeout_seconds
        healthchecked_targets = self._compose_healthchecked_targets(
            source_root,
            compose_filename,
            set(service_urls.keys()),
        )
        last_filtered = self._filter_accessible_service_urls(
            source_root,
            preview_run,
            compose_filename,
            service_urls,
            healthchecked_targets,
        )
        while not last_filtered and time.monotonic() < deadline:
            time.sleep(2)
            last_filtered = self._filter_accessible_service_urls(
                source_root,
                preview_run,
                compose_filename,
                service_urls,
                healthchecked_targets,
            )
        return last_filtered

    def _filter_accessible_service_urls(
        self,
        source_root: Path,
        preview_run: PreviewRun,
        compose_filename: str,
        service_urls: dict[str, list[str]],
        healthchecked_targets: set[str] | None = None,
    ) -> dict[str, list[str]]:
        healthchecked_targets = healthchecked_targets or set()
        service_states = self._compose_service_states(source_root, preview_run, compose_filename)
        filtered: dict[str, list[str]] = {}
        for service_name, urls in service_urls.items():
            service_state = service_states.get(service_name, {})
            state = str(service_state.get("state") or "").lower()
            health = str(service_state.get("health") or "").lower()
            if state != "running":
                continue
            if service_name in healthchecked_targets:
                if health != "healthy":
                    continue
            elif health and health != "healthy":
                continue
            filtered[service_name] = urls
        return filtered

    def _build_preview_notes(
        self,
        source_root: Path,
        preview_run: PreviewRun,
        compose_filename: str,
        candidate_service_urls: dict[str, list[str]],
        service_urls: dict[str, list[str]],
    ) -> str:
        service_states = self._compose_service_states(source_root, preview_run, compose_filename)
        hidden = []
        for service_name in candidate_service_urls:
            state = service_states.get(service_name, {})
            if service_name in service_urls:
                continue
            status = state.get("status") or state.get("state") or "sin estado"
            hidden.append(f"- {service_name}: {status}")
        if not hidden:
            return ""
        return "Servicios ocultos porque no quedaron accesibles:\n" + "\n".join(hidden)

    def _start_single_container_preview(self, preview_run: PreviewRun, source_root: Path) -> None:
        image_tag = f"autodocker-preview-{str(preview_run.id)[:8]}"
        container_name = image_tag
        docker_base = docker_command()
        build_command = [*docker_base, "build", "-t", image_tag, "."]
        build_result = run_command(build_command, source_root, timeout=1800)
        run_command(
            [*docker_base, "rm", "-f", container_name],
            source_root,
            timeout=120,
            check=False,
        )
        run_command(
            [*docker_base, "run", "-d", "-P", "--name", container_name, image_tag],
            source_root,
            timeout=300,
        )
        port_result = run_command([*docker_base, "port", container_name], source_root, timeout=120, check=False)
        ports = self._parse_docker_port_output(port_result.output)
        log_result = run_command(
            [*docker_base, "logs", "--tail", "200", container_name],
            source_root,
            timeout=180,
            check=False,
        )
        preview_run.status = PreviewRun.Status.READY
        preview_run.runtime_kind = PreviewRun.RuntimeKind.CONTAINER
        preview_run.command = " ".join(build_command)
        preview_run.logs = "\n\n".join([build_result.output, log_result.output]).strip()
        preview_run.ports = ports
        preview_run.access_url = self._pick_access_url(ports)
        preview_run.resource_names = [container_name]
        preview_run.expires_at = timezone.now() + timedelta(hours=2)
        preview_run.finished_at = timezone.now()
        preview_run.save(
            update_fields=[
                "status",
                "runtime_kind",
                "command",
                "logs",
                "ports",
                "access_url",
                "resource_names",
                "expires_at",
                "finished_at",
                "updated_at",
            ]
        )

    def _write_preview_override(
        self,
        source_root: Path,
        analysis: ProjectAnalysis,
    ) -> tuple[Path, dict[str, list[str]]]:
        compose_path = source_root / "docker-compose.yml"
        compose_data = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = compose_data.get("services", {})
        component_names = {
            component["name"].replace("_", "-").replace("/", "-")
            for component in analysis.analysis_payload.get("components", [])
        }
        shared_services = set(analysis.services)
        service_urls: dict[str, list[str]] = {}

        for service_name, service_config in services.items():
            ports = service_config.get("ports") or []
            if not ports:
                continue
            remapped_ports = []
            access_urls = []
            for port_definition in ports:
                container_port = self._container_port_from_compose(port_definition)
                if not container_port:
                    continue
                host_port = self._free_port()
                remapped_ports.append(f"{host_port}:{container_port}")
                if service_name not in shared_services:
                    access_urls.append(f"http://127.0.0.1:{host_port}")
            service_config["ports"] = remapped_ports
            if service_name in component_names and access_urls:
                service_urls[service_name] = access_urls

        preview_compose_path = source_root / self._preview_compose_filename()
        preview_compose_path.write_text(yaml.safe_dump(compose_data, sort_keys=False), encoding="utf-8")
        return preview_compose_path, service_urls

    def _compose_service_states(
        self,
        source_root: Path,
        preview_run: PreviewRun,
        compose_filename: str,
    ) -> dict[str, dict[str, str]]:
        compose_base = docker_compose_command()
        result = run_command(
            [
                *compose_base,
                "-f",
                compose_filename,
                "-p",
                self._compose_project_name(preview_run),
                "ps",
                "--format",
                "json",
            ],
            source_root,
            timeout=180,
            check=False,
        )
        states: dict[str, dict[str, str]] = {}
        lines = [line.strip() for line in result.output.splitlines() if line.strip()]
        payloads = []
        if len(lines) == 1 and lines[0].startswith("["):
            try:
                payloads = json.loads(lines[0])
            except json.JSONDecodeError:
                payloads = []
        else:
            for line in lines:
                try:
                    payloads.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for item in payloads:
            service_name = item.get("Service")
            if not service_name:
                continue
            states[service_name] = {
                "state": str(item.get("State") or ""),
                "health": str(item.get("Health") or ""),
                "status": str(item.get("Status") or ""),
            }
        return states

    def _compose_healthchecked_targets(
        self,
        source_root: Path,
        compose_filename: str,
        service_names: set[str],
    ) -> set[str]:
        compose_path = source_root / compose_filename
        compose_data = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = compose_data.get("services", {})
        return {
            service_name
            for service_name in service_names
            if service_name in services and services[service_name].get("healthcheck")
        }

    def _collect_compose_logs(self, source_root: Path, preview_run: PreviewRun, override_name: str) -> str:
        compose_base = docker_compose_command()
        result = run_command(
            [
                *compose_base,
                "-f",
                override_name,
                "-p",
                self._compose_project_name(preview_run),
                "logs",
                "--no-color",
                "--tail",
                "200",
            ],
            source_root,
            timeout=180,
            check=False,
        )
        return result.output

    def _parse_docker_port_output(self, output: str) -> dict[str, list[str]]:
        ports: dict[str, list[str]] = {"app": []}
        for line in output.splitlines():
            if "->" not in line:
                continue
            host_segment = line.split("->", maxsplit=1)[1].strip()
            host_port = host_segment.rsplit(":", maxsplit=1)[-1]
            if host_port.isdigit():
                ports["app"].append(f"http://127.0.0.1:{host_port}")
        return ports

    def _container_port_from_compose(self, port_definition) -> int | None:
        if isinstance(port_definition, int):
            return port_definition
        if isinstance(port_definition, str):
            value = port_definition.split("/")[-2] if "/" in port_definition else port_definition
            last = value.rsplit(":", maxsplit=1)[-1]
            return int(last) if last.isdigit() else None
        if isinstance(port_definition, dict):
            target = port_definition.get("target")
            return int(target) if target else None
        return None

    def _pick_access_url(self, service_urls: dict[str, list[str]]) -> str:
        if "web" in service_urls and service_urls["web"]:
            return service_urls["web"][0]
        for urls in service_urls.values():
            if urls:
                return urls[0]
        return ""

    def _compose_project_name(self, preview_run: PreviewRun) -> str:
        return f"autodocker-preview-{str(preview_run.id).replace('-', '')[:10]}"

    def _preview_compose_filename(self) -> str:
        return "autodocker.preview.compose.yml"

    def _cleanup_workspace(self, preview_run: PreviewRun) -> None:
        if preview_run.workspace_root:
            cleanup_workspace(Path(preview_run.workspace_root))

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
