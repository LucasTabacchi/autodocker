from __future__ import annotations

import json
from pathlib import PurePosixPath

from core.services.contracts import (
    ComponentSpec,
    DetectionResult,
    GeneratedArtifactSpec,
    GenerationResult,
)


class ArtifactGenerator:
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    CI = "ci"

    def generate(
        self,
        detection: DetectionResult,
        profile: str = PRODUCTION,
        healthchecks: dict[str, dict] | None = None,
        extra_artifacts: list[GeneratedArtifactSpec] | None = None,
    ) -> GenerationResult:
        artifacts: list[GeneratedArtifactSpec] = []
        healthchecks = healthchecks or {}

        for component in detection.components:
            artifacts.append(
                GeneratedArtifactSpec(
                    kind="dockerfile",
                    path=self._dockerfile_path(component),
                    description=f"Dockerfile optimizado para {component.framework}.",
                    content=self._build_dockerfile(
                        detection,
                        component,
                        profile,
                        healthchecks.get(component.path, {}),
                    ),
                )
            )

        artifacts.append(
            GeneratedArtifactSpec(
                kind="ignore",
                path=".dockerignore",
                description="Exclusiones comunes para builds reproducibles.",
                content=self._build_dockerignore(),
            )
        )

        if len(detection.components) > 1 or detection.shared_services:
            artifacts.append(
                GeneratedArtifactSpec(
                    kind="compose",
                    path="docker-compose.yml",
                    description="Orquestación local para app y servicios auxiliares.",
                    content=self._build_compose(detection, profile, healthchecks),
                )
            )

        artifacts.append(
            GeneratedArtifactSpec(
                kind="guide",
                path="DOCKER_USAGE.md",
                description="Guía rápida para build, validación y ejecución.",
                content=self._build_guide(detection, profile),
            )
        )
        artifacts.extend(extra_artifacts or [])
        return GenerationResult(artifacts=artifacts)

    def _dockerfile_path(self, component: ComponentSpec) -> str:
        if component.path == ".":
            return "Dockerfile"
        return f"{component.path}/Dockerfile"

    def _build_dockerfile(
        self,
        detection: DetectionResult,
        component: ComponentSpec,
        profile: str,
        healthcheck: dict | None = None,
    ) -> str:
        if component.language == "Node.js":
            return self._node_dockerfile(detection, component, profile, healthcheck)
        if component.language == "Python":
            return self._python_dockerfile(component, profile, healthcheck)
        if component.language == "PHP":
            return self._php_dockerfile(component, healthcheck)
        if component.language == "Java":
            return self._java_dockerfile(component)
        if component.language == "Go":
            return self._go_dockerfile(component)
        if component.language == "Ruby":
            return self._ruby_dockerfile(component)
        return self._fallback_dockerfile(component)

    def _node_dockerfile(
        self,
        detection: DetectionResult,
        component: ComponentSpec,
        profile: str,
        healthcheck: dict | None = None,
    ) -> str:
        image = "node:22-slim" if component.base_image_hint == "slim" else "node:22-alpine"
        if self._uses_root_workspace_context(detection, component):
            return self._node_workspace_dockerfile(detection, component, profile, healthcheck, image)

        package_copy = {
            "pnpm": "COPY package.json pnpm-lock.yaml* ./",
            "yarn": "COPY package.json yarn.lock* ./",
        }.get(component.package_manager, "COPY package.json package-lock.json* npm-shrinkwrap.json* ./")
        install_command = component.install_command or "npm ci"
        build_command = component.build_command or "npm run build"
        start_command = component.start_command or "npm run start"
        port = component.probable_ports[0] if component.probable_ports else 3000

        if profile == self.DEVELOPMENT:
            dev_command = self._development_command_for_component(component)
            return self._join(
                [
                    f"FROM {image}",
                    "WORKDIR /app",
                    package_copy,
                    self._corepack_line(component.package_manager),
                    f"RUN {install_command}",
                    "COPY . .",
                    "ENV NODE_ENV=development",
                    f"EXPOSE {port}",
                    self._dockerfile_healthcheck_line(healthcheck),
                    f'CMD {json.dumps(["sh", "-c", dev_command])}',
                ]
            )

        if component.framework in {"React", "Vite"}:
            output_dir = "dist" if component.framework == "Vite" else "build"
            return self._join(
                [
                    f"FROM {image} AS builder",
                    "WORKDIR /app",
                    package_copy,
                    self._corepack_line(component.package_manager),
                    f"RUN {install_command}",
                    "COPY . .",
                    f"RUN {build_command}",
                    "",
                    "FROM nginx:1.27-alpine AS runner",
                    "WORKDIR /usr/share/nginx/html",
                    f"COPY --from=builder /app/{output_dir}/ ./",
                    "EXPOSE 80",
                    'CMD ["nginx", "-g", "daemon off;"]',
                ]
            )

        if component.framework == "Next.js":
            return self._join(
                [
                    f"FROM {image} AS deps",
                    "WORKDIR /app",
                    package_copy,
                    self._corepack_line(component.package_manager),
                    f"RUN {install_command}",
                    "",
                    "FROM deps AS builder",
                    "WORKDIR /app",
                    "COPY . .",
                    f"RUN {build_command}",
                    "",
                    f"FROM {image} AS runner",
                    "WORKDIR /app",
                    "ENV NODE_ENV=production",
                    "COPY --from=deps /app/node_modules ./node_modules",
                    "COPY --from=builder /app/.next ./.next",
                    "COPY --from=builder /app/public ./public",
                    "COPY --from=builder /app/package.json ./package.json",
                    f"EXPOSE {port}",
                    self._dockerfile_healthcheck_line(healthcheck),
                    f"CMD {json.dumps(['sh', '-c', start_command])}",
                ]
            )

        if component.needs_multistage:
            runtime_start = "node dist/main.js" if component.framework == "NestJS" else start_command
            return self._join(
                [
                    f"FROM {image} AS build",
                    "WORKDIR /app",
                    package_copy,
                    self._corepack_line(component.package_manager),
                    f"RUN {install_command}",
                    "COPY . .",
                    f"RUN {build_command}",
                    "",
                    f"FROM {image} AS runner",
                    "WORKDIR /app",
                    "ENV NODE_ENV=production",
                    package_copy,
                    self._corepack_line(component.package_manager),
                    f"RUN {self._node_runtime_install(component)}",
                    "COPY --from=build /app/dist ./dist",
                    "COPY --from=build /app/package.json ./package.json",
                    f"EXPOSE {port}",
                    self._dockerfile_healthcheck_line(healthcheck),
                    f"CMD {json.dumps(['sh', '-c', runtime_start])}",
                ]
            )

        return self._join(
            [
                f"FROM {image}",
                "WORKDIR /app",
                package_copy,
                self._corepack_line(component.package_manager),
                f"RUN {self._node_runtime_install(component)}",
                "COPY . .",
                "ENV NODE_ENV=production",
                f"EXPOSE {port}",
                self._dockerfile_healthcheck_line(healthcheck),
                f"CMD {json.dumps(['sh', '-c', start_command])}",
            ]
        )

    def _node_workspace_dockerfile(
        self,
        detection: DetectionResult,
        component: ComponentSpec,
        profile: str,
        healthcheck: dict | None,
        image: str,
    ) -> str:
        install_command = component.install_command or "npm ci"
        build_command = self._workspace_scoped_node_command(component, component.build_command)
        if profile == self.DEVELOPMENT:
            start_command = self._workspace_scoped_node_command(
                component,
                self._development_command_for_component(component),
            )
        else:
            start_command = self._workspace_scoped_node_command(
                component,
                component.start_command or "npm run start",
            )
        port = component.probable_ports[0] if component.probable_ports else 3000

        lines = [
            f"FROM {image}",
            "WORKDIR /app",
            *self._workspace_manifest_copy_lines(detection),
            self._corepack_line(component.package_manager),
            f"RUN {install_command}",
            "COPY . .",
        ]
        if profile != self.DEVELOPMENT and build_command:
            lines.append(f"RUN {build_command}")
        lines.extend(
            [
                f"ENV NODE_ENV={'development' if profile == self.DEVELOPMENT else 'production'}",
                f"EXPOSE {port}",
                self._dockerfile_healthcheck_line(healthcheck),
                f"CMD {json.dumps(['sh', '-c', start_command])}",
            ]
        )
        return self._join(lines)

    def _python_dockerfile(self, component: ComponentSpec, profile: str, healthcheck: dict | None = None) -> str:
        port = component.probable_ports[0] if component.probable_ports else 8000
        install_lines = ["RUN pip install --no-cache-dir --upgrade pip"]
        if component.install_command == "pip install -r requirements.txt":
            install_lines.extend(
                [
                    "COPY requirements.txt ./",
                    "RUN pip install --no-cache-dir -r requirements.txt",
                ]
            )
        else:
            install_lines.extend(
                [
                    "COPY pyproject.toml ./",
                    "RUN pip install --no-cache-dir .",
                ]
            )
        start_command = (
            self._development_command_for_component(component)
            if profile == self.DEVELOPMENT
            else component.start_command or "python main.py"
        )
        return self._join(
            [
                "FROM python:3.12-slim",
                "ENV PYTHONDONTWRITEBYTECODE=1",
                "ENV PYTHONUNBUFFERED=1",
                f"ENV AUTODOCKER_PROFILE={profile}",
                "WORKDIR /app",
                *install_lines,
                "COPY . .",
                f"EXPOSE {port}",
                self._dockerfile_healthcheck_line(healthcheck),
                f"CMD {json.dumps(['sh', '-c', start_command])}",
            ]
        )

    def _php_dockerfile(self, component: ComponentSpec, healthcheck: dict | None = None) -> str:
        port = component.probable_ports[0] if component.probable_ports else 8000
        start_command = component.start_command or "php -S 0.0.0.0:8000 -t public"
        return self._join(
            [
                "FROM composer:2 AS vendor",
                "WORKDIR /app",
                "COPY composer.json composer.lock* ./",
                f"RUN {component.install_command or 'composer install --no-dev --optimize-autoloader'}",
                "",
                "FROM php:8.3-cli-alpine",
                "WORKDIR /app",
                "COPY --from=vendor /app/vendor ./vendor",
                "COPY . .",
                f"EXPOSE {port}",
                self._dockerfile_healthcheck_line(healthcheck),
                f"CMD {json.dumps(['sh', '-c', start_command])}",
            ]
        )

    def _java_dockerfile(self, component: ComponentSpec) -> str:
        port = component.probable_ports[0] if component.probable_ports else 8080
        build_command = component.build_command or "mvn -DskipTests package"
        return self._join(
            [
                "FROM maven:3.9-eclipse-temurin-21 AS builder",
                "WORKDIR /app",
                "COPY pom.xml ./",
                "RUN mvn -q dependency:go-offline",
                "COPY . .",
                f"RUN {build_command}",
                "",
                "FROM eclipse-temurin:21-jre-jammy",
                "WORKDIR /app",
                "COPY --from=builder /app/target/*.jar /app/app.jar",
                f"EXPOSE {port}",
                'CMD ["java", "-jar", "/app/app.jar"]',
            ]
        )

    def _go_dockerfile(self, component: ComponentSpec) -> str:
        port = component.probable_ports[0] if component.probable_ports else 8080
        return self._join(
            [
                "FROM golang:1.24-alpine AS builder",
                "WORKDIR /src",
                "COPY go.mod go.sum* ./",
                "RUN go mod download",
                "COPY . .",
                "RUN CGO_ENABLED=0 GOOS=linux go build -o /tmp/app ./...",
                "",
                "FROM alpine:3.21",
                "WORKDIR /app",
                "COPY --from=builder /tmp/app /app/app",
                f"EXPOSE {port}",
                'CMD ["./app"]',
            ]
        )

    def _ruby_dockerfile(self, component: ComponentSpec) -> str:
        port = component.probable_ports[0] if component.probable_ports else 3000
        return self._join(
            [
                "FROM ruby:3.3-slim",
                "WORKDIR /app",
                "COPY Gemfile Gemfile.lock* ./",
                "RUN bundle install",
                "COPY . .",
                f"EXPOSE {port}",
                'CMD ["bundle", "exec", "rails", "server", "-b", "0.0.0.0", "-p", "3000"]',
            ]
        )

    def _fallback_dockerfile(self, component: ComponentSpec) -> str:
        port = component.probable_ports[0] if component.probable_ports else 8080
        return self._join(
            [
                "FROM debian:bookworm-slim",
                "WORKDIR /app",
                "COPY . .",
                f"EXPOSE {port}",
                'CMD ["sh", "-c", "echo Ajustar comando de arranque para este stack && sleep infinity"]',
            ]
        )

    def _build_dockerignore(self) -> str:
        return "\n".join(
            [
                ".git",
                ".github",
                ".idea",
                ".vscode",
                ".venv",
                "venv",
                "__pycache__",
                "*.pyc",
                "node_modules",
                "dist",
                "build",
                ".next",
                ".pytest_cache",
                ".mypy_cache",
                "*.log",
                "coverage",
                ".DS_Store",
            ]
        )

    def _build_compose(self, detection: DetectionResult, profile: str, healthchecks: dict[str, dict] | None = None) -> str:
        healthchecks = healthchecks or {}
        lines = ["services:"]
        for component in detection.components:
            service_name = component.name.replace("_", "-").replace("/", "-")
            build_context, dockerfile_path = self._compose_build_spec(detection, component)
            env_vars = list(component.environment_variables[:8])
            if profile in {self.DEVELOPMENT, self.CI} and "AUTODOCKER_PROFILE" not in env_vars:
                env_vars.append("AUTODOCKER_PROFILE")

            lines.extend(
                [
                    f"  {service_name}:",
                    "    build:",
                    f"      context: ./{build_context}" if build_context != "." else "      context: .",
                    f"      dockerfile: {dockerfile_path}",
                ]
            )
            if profile == self.DEVELOPMENT:
                volume_root = build_context
                lines.extend(
                    [
                        "    volumes:",
                        f"      - ./{volume_root}:/app" if volume_root != "." else "      - ./:/app",
                    ]
                )
                if self._uses_root_workspace_context(detection, component):
                    lines.append("      - /app/node_modules")
            if component.probable_ports:
                container_port = 80 if component.framework in {"React", "Vite"} else component.probable_ports[0]
                host_port = container_port
                lines.extend(
                    [
                        "    ports:",
                        f'      - "{host_port}:{container_port}"',
                    ]
                )
            if component.services:
                lines.append("    depends_on:")
                for dependency in component.services:
                    lines.append(f"      - {dependency}")
            if env_vars:
                lines.append("    environment:")
                for env_var in env_vars:
                    if env_var == "AUTODOCKER_PROFILE":
                        default_value = profile
                        lines.append(f'      {env_var}: "${{{env_var}:-{default_value}}}"')
                    elif env_var == "PORT":
                        default_value = str(
                            component.probable_ports[0] if component.probable_ports else self._guide_port(component)
                        )
                        lines.append(f'      {env_var}: "{default_value}"')
                    else:
                        default_value = "change-me"
                        lines.append(f'      {env_var}: "${{{env_var}:-{default_value}}}"')
            lines.extend(self._compose_healthcheck_lines(healthchecks.get(component.path)))

        for service in detection.shared_services:
            if service == "postgres":
                lines.extend(
                    [
                        "  postgres:",
                        "    image: postgres:16-alpine",
                        "    environment:",
                        "      POSTGRES_DB: app",
                        "      POSTGRES_USER: app",
                        "      POSTGRES_PASSWORD: app",
                        "    ports:",
                        '      - "5432:5432"',
                        "    volumes:",
                        "      - postgres_data:/var/lib/postgresql/data",
                    ]
                )
            elif service == "mysql":
                lines.extend(
                    [
                        "  mysql:",
                        "    image: mysql:8.4",
                        "    environment:",
                        "      MYSQL_DATABASE: app",
                        "      MYSQL_USER: app",
                        "      MYSQL_PASSWORD: app",
                        "      MYSQL_ROOT_PASSWORD: root",
                        "    ports:",
                        '      - "3306:3306"',
                        "    volumes:",
                        "      - mysql_data:/var/lib/mysql",
                    ]
                )
            elif service == "redis":
                lines.extend(
                    [
                        "  redis:",
                        "    image: redis:7-alpine",
                        "    ports:",
                        '      - "6379:6379"',
                    ]
                )
            elif service == "mongodb":
                lines.extend(
                    [
                        "  mongodb:",
                        "    image: mongo:7",
                        "    ports:",
                        '      - "27017:27017"',
                        "    volumes:",
                        "      - mongo_data:/data/db",
                    ]
                )

        if detection.shared_services:
            lines.append("volumes:")
            if "postgres" in detection.shared_services:
                lines.append("  postgres_data:")
            if "mysql" in detection.shared_services:
                lines.append("  mysql_data:")
            if "mongodb" in detection.shared_services:
                lines.append("  mongo_data:")

        return "\n".join(lines)

    def _build_guide(self, detection: DetectionResult, profile: str) -> str:
        if len(detection.components) > 1 or detection.shared_services:
            run_block = "docker compose up --build"
        else:
            image_name = detection.project_name.lower().replace(" ", "-")
            component = detection.primary_component()
            container_port = self._guide_port(component) if component else 8000
            run_block = "\n".join(
                [
                    f"docker build -t {image_name} .",
                    f"docker run --rm -p {container_port}:{container_port} {image_name}",
                ]
            )
        return "\n".join(
            [
                "# AutoDocker Usage",
                "",
                f"Perfil de generación: `{profile}`",
                "",
                "## 1. Revisar archivos generados",
                "- Verificá puertos, variables y comandos de arranque.",
                "- Ajustá dependencias nativas o binarios del proyecto original si aplica.",
                "",
                "## 2. Build y run",
                "```bash",
                run_block,
                "```",
                "",
                "## 3. Validaciones sugeridas",
                "- Ejecutar smoke test del contenedor.",
                "- Confirmar que los secretos reales no queden hardcodeados.",
                "- Revisar si conviene bind mount para desarrollo o solo imagen final para producción.",
                "- Ajustar pipeline CI/CD y targets de deploy según tu plataforma final.",
            ]
        )

    def _corepack_line(self, package_manager: str | None) -> str:
        if package_manager in {"pnpm", "yarn"}:
            return "RUN corepack enable"
        return ""

    def _uses_root_workspace_context(self, detection: DetectionResult, component: ComponentSpec) -> bool:
        if component.language != "Node.js" or component.path == ".":
            return False
        normalized_files = [self._normalize_posix_path(path) for path in detection.found_files]
        root_files = set(normalized_files)
        has_root_package = "package.json" in root_files
        has_root_lockfile = any(
            filename in root_files
            for filename in ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
        )
        has_workspace_manifests = any(
            PurePosixPath(path).name == "package.json" and "/" in path
            for path in normalized_files
        )
        return (
            detection.project_type in {"monorepo", "fullstack"}
            and has_root_package
            and has_root_lockfile
            and has_workspace_manifests
        )

    def _workspace_manifest_copy_lines(self, detection: DetectionResult) -> list[str]:
        files: list[str] = []
        normalized_files = [self._normalize_posix_path(path) for path in detection.found_files]
        root_manifests = [
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "yarn.lock",
            "turbo.json",
            "nx.json",
        ]
        root_file_set = set(normalized_files)
        for filename in root_manifests:
            if filename in root_file_set:
                files.append(filename)

        workspace_manifests = sorted(
            path
            for path in normalized_files
            if "/" in path and PurePosixPath(path).name == "package.json"
        )
        files.extend(workspace_manifests)
        return [f"COPY {path} ./{path}" for path in files]

    def _workspace_scoped_node_command(self, component: ComponentSpec, command: str | None) -> str | None:
        if not command:
            return command

        workspace_path = PurePosixPath(component.path).as_posix()
        package_manager = component.package_manager or "npm"
        normalized = command.strip()

        if package_manager == "npm" and normalized.startswith("npm run "):
            return f"{normalized} --workspace {workspace_path}"
        if package_manager == "pnpm" and normalized.startswith("pnpm "):
            return normalized.replace("pnpm ", f"pnpm --filter ./{workspace_path} ", 1)
        if package_manager == "yarn" and normalized.startswith("yarn "):
            yarn_command = normalized.removeprefix("yarn ").strip()
            return f"yarn workspace {component.name} {yarn_command}"
        return normalized

    def _compose_build_spec(self, detection: DetectionResult, component: ComponentSpec) -> tuple[str, str]:
        if self._uses_root_workspace_context(detection, component):
            return ".", self._dockerfile_path(component)
        context = "." if component.path == "." else component.path
        return context, "Dockerfile"

    def _normalize_posix_path(self, path: str) -> str:
        return str(PurePosixPath(path.replace("\\", "/")))

    def _node_runtime_install(self, component: ComponentSpec) -> str:
        if component.package_manager == "pnpm":
            return "pnpm install --prod --frozen-lockfile"
        if component.package_manager == "yarn":
            return "yarn install --production --frozen-lockfile"
        return "npm ci --omit=dev"

    def _development_command_for_component(self, component: ComponentSpec) -> str:
        if component.language == "Node.js":
            package_manager = component.package_manager or "npm"
            if component.framework == "Next.js":
                return self._script_command(package_manager, "dev")
            if component.framework == "Vite":
                return self._script_command(package_manager, "dev")
            if component.framework == "React":
                return self._script_command(package_manager, "start")
            if component.framework == "NestJS":
                return self._script_command(package_manager, "start:dev")
            return component.start_command or self._script_command(package_manager, "dev")

        if component.language == "Python":
            if component.framework == "Django":
                return "python manage.py runserver 0.0.0.0:8000"
            if component.framework == "FastAPI":
                return "uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
            if component.framework == "Flask":
                return "flask run --host=0.0.0.0 --port=5000"
        return component.start_command or "sh"

    def _script_command(self, package_manager: str, script_name: str) -> str:
        if package_manager == "pnpm":
            return f"pnpm {script_name}"
        if package_manager == "yarn":
            return f"yarn {script_name}"
        return f"npm run {script_name}"

    def _guide_port(self, component: ComponentSpec) -> int:
        if component.framework in {"React", "Vite"}:
            return 80
        if component.probable_ports:
            return component.probable_ports[0]
        if component.language == "Python":
            return 8000
        if component.language == "Java":
            return 8080
        if component.language == "Go":
            return 8080
        return 3000 if component.language in {"Node.js", "Ruby"} else 8000

    def _dockerfile_healthcheck_line(self, healthcheck: dict | None) -> str:
        if not healthcheck or not healthcheck.get("supported") or not healthcheck.get("command"):
            return ""
        command = json.dumps(healthcheck["command"])
        return (
            "HEALTHCHECK --interval={interval} --timeout={timeout} "
            "--start-period={start_period} --retries={retries} CMD {command}"
        ).format(
            interval=healthcheck.get("interval", "30s"),
            timeout=healthcheck.get("timeout", "5s"),
            start_period=healthcheck.get("start_period", "20s"),
            retries=healthcheck.get("retries", 5),
            command=command,
        )

    def _compose_healthcheck_lines(self, healthcheck: dict | None) -> list[str]:
        if not healthcheck or not healthcheck.get("supported") or not healthcheck.get("command"):
            return []
        command = healthcheck["command"]
        lines = [
            "    healthcheck:",
            "      test:",
            '        - "CMD"',
            f"        - {json.dumps(command[0])}",
        ]
        for item in command[1:]:
            lines.append(f"        - {json.dumps(item)}")
        lines.extend(
            [
                f'      interval: "{healthcheck.get("interval", "30s")}"',
                f'      timeout: "{healthcheck.get("timeout", "5s")}"',
                f"      retries: {healthcheck.get('retries', 5)}",
                f'      start_period: "{healthcheck.get("start_period", "20s")}"',
            ]
        )
        return lines

    def _join(self, lines: list[str]) -> str:
        return "\n".join(line for line in lines if line is not None)
