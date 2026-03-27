from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from core.services.contracts import ComponentSpec, DetectionResult


class StackDetector:
    EXCLUDED_DIRS = {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".venv",
        "venv",
        "__pycache__",
        "coverage",
        "vendor",
        "tmp",
        "logs",
    }
    MARKER_FILES = {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "composer.json",
        "pom.xml",
        "go.mod",
        "Gemfile",
        "manage.py",
    }
    MONOREPO_MARKERS = {"pnpm-workspace.yaml", "turbo.json", "nx.json"}
    DEFAULT_PORTS = {
        "Next.js": 3000,
        "React": 80,
        "Vite": 80,
        "Express": 3000,
        "NestJS": 3000,
        "Django": 8000,
        "FastAPI": 8000,
        "Flask": 5000,
        "Laravel": 8000,
        "Spring Boot": 8080,
        "Go": 8080,
        "Ruby on Rails": 3000,
        "Node.js": 3000,
        "Python": 8000,
    }
    ENV_PATTERNS = [
        re.compile(pattern)
        for pattern in [
            r"process\.env\.([A-Z0-9_]+)",
            r"os\.getenv\([\"']([A-Z0-9_]+)[\"']",
            r"os\.environ(?:\.get)?\([\"']([A-Z0-9_]+)[\"']",
            r"import\.meta\.env\.([A-Z0-9_]+)",
            r"env\([\"']([A-Z0-9_]+)[\"']",
            r"System\.getenv\([\"']([A-Z0-9_]+)[\"']",
            r"ENV\[['\"]([A-Z0-9_]+)['\"]\]",
        ]
    ]
    PORT_PATTERNS = [
        re.compile(pattern)
        for pattern in [
            r"listen\((\d{2,5})",
            r"--port(?:=|\s+)(\d{2,5})",
            r"port\s*[:=]\s*(\d{2,5})",
            r"PORT\s*(?:\|\||\?\?)\s*(\d{2,5})",
            r"0\.0\.0\.0:(\d{2,5})",
            r"EXPOSE\s+(\d{2,5})",
            r"getenv\([\"']PORT[\"']\s*,\s*[\"']?(\d{2,5})",
        ]
    ]
    TEXT_EXTENSIONS = {
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".py",
        ".php",
        ".java",
        ".go",
        ".rb",
        ".json",
        ".toml",
        ".yml",
        ".yaml",
        ".env",
        ".txt",
        ".ini",
        ".cfg",
        ".properties",
    }

    def analyze(self, project_root: Path) -> DetectionResult:
        all_files = self._collect_files(project_root)
        components = self._detect_components(project_root)
        if not components:
            components = [self._inspect_component(project_root, project_root, "app")]

        components = [component for component in components if component and component.language]
        project_type = self._classify_project_type(project_root, components)
        shared_services = sorted(
            {service for component in components for service in component.services}
        )
        env_vars = sorted(
            {env for component in components for env in component.environment_variables}
        )
        package_managers = sorted(
            {manager for component in components if (manager := component.package_manager)}
        )
        recommendations = self._build_recommendations(project_type, components, shared_services)
        confidence = round(
            (sum(component.confidence for component in components) / max(len(components), 1)), 2
        )

        return DetectionResult(
            project_name=project_root.name,
            project_type=project_type,
            confidence=confidence,
            components=components,
            shared_services=shared_services,
            environment_variables=env_vars,
            recommendations=recommendations,
            found_files=sorted(str(path.relative_to(project_root)) for path in all_files)[:200],
            existing_dockerfile=any(path.name.startswith("Dockerfile") for path in all_files),
            package_managers=package_managers,
            notes=self._build_notes(project_root, components),
        )

    def _detect_components(self, project_root: Path) -> list[ComponentSpec]:
        candidates: list[tuple[Path, str]] = []
        if self._has_markers(project_root):
            candidates.append((project_root, self._guess_role(project_root.name)))
        for workspace in self._workspace_candidates(project_root):
            candidates.append((workspace, self._guess_role(workspace.name)))

        for child in project_root.iterdir():
            if not child.is_dir() or child.name in self.EXCLUDED_DIRS:
                continue
            if self._has_markers(child):
                candidates.append((child, self._guess_role(child.name)))
            if child.name in {"apps", "packages", "services"}:
                for nested in child.iterdir():
                    if nested.is_dir() and self._has_markers(nested):
                        candidates.append((nested, self._guess_role(nested.name)))

        unique: dict[str, ComponentSpec] = {}
        for candidate, role in candidates:
            component = self._inspect_component(project_root, candidate, role)
            if component.language:
                unique[component.path] = component

        components = list(unique.values())
        return self._prune_root_orchestrator_component(project_root, components)

    def _inspect_component(
        self,
        project_root: Path,
        candidate_root: Path,
        role_hint: str,
    ) -> ComponentSpec:
        relative_path = "."
        if candidate_root != project_root:
            relative_path = str(candidate_root.relative_to(project_root)).replace("\\", "/")
        direct_files = {path.name: path for path in candidate_root.iterdir() if path.is_file()}

        (
            language,
            framework,
            runtime,
            package_manager,
            install_command,
            build_command,
            start_command,
            dependencies,
            scripts,
            confidence,
        ) = self._detect_runtime(candidate_root, direct_files)

        env_vars = self._detect_environment_variables(candidate_root)
        services = self._detect_services(dependencies, env_vars)
        probable_ports = self._detect_ports(candidate_root, framework or language, env_vars)
        healthcheck_path = self._detect_healthcheck_path(candidate_root)
        needs_multistage = framework in {"Next.js", "React", "Vite", "NestJS", "Spring Boot", "Go"}
        base_image_hint = self._choose_base_image(framework or language, dependencies)

        component = ComponentSpec(
            name=self._component_name(relative_path),
            path=relative_path,
            language=language,
            framework=framework or language,
            runtime=runtime,
            role=role_hint,
            package_manager=package_manager,
            install_command=install_command,
            build_command=build_command,
            start_command=start_command,
            probable_ports=probable_ports,
            healthcheck_path=healthcheck_path,
            environment_variables=env_vars,
            found_files=sorted(direct_files.keys()),
            services=services,
            dependency_names=sorted(dependencies),
            needs_multistage=needs_multistage,
            base_image_hint=base_image_hint,
            confidence=confidence,
            existing_dockerfile=any(name.startswith("Dockerfile") for name in direct_files),
        )
        component.role = self._resolve_role(component, candidate_root, scripts)
        return component

    def _detect_runtime(self, candidate_root: Path, files: dict[str, Path]):
        dependencies: set[str] = set()
        scripts: dict = {}
        language = ""
        framework = ""
        runtime = ""
        package_manager = None
        install_command = None
        build_command = None
        start_command = None
        confidence = 0.4

        if "package.json" in files:
            package = self._read_json(files["package.json"])
            dependencies |= set(package.get("dependencies", {}))
            dependencies |= set(package.get("devDependencies", {}))
            scripts = package.get("scripts", {})
            language = "Node.js"
            framework = self._detect_node_framework(dependencies, candidate_root, scripts)
            runtime = "node"
            package_manager = self._detect_package_manager(candidate_root)
            install_command = {
                "pnpm": "pnpm install --frozen-lockfile",
                "yarn": "yarn install --frozen-lockfile",
            }.get(package_manager, "npm ci")
            build_command = self._pick_script_command(package_manager, scripts, "build")
            start_command = self._node_start_command(package_manager, scripts, framework, candidate_root)
            confidence = 0.9 if framework != "Node.js" else 0.76
            return (
                language,
                framework,
                runtime,
                package_manager,
                install_command,
                build_command,
                start_command,
                dependencies,
                scripts,
                confidence,
            )

        if "requirements.txt" in files or "pyproject.toml" in files or "manage.py" in files:
            dependencies |= self._python_dependencies(files)
            language = "Python"
            framework = self._detect_python_framework(dependencies, files)
            runtime = "python"
            install_command = (
                "pip install -r requirements.txt"
                if "requirements.txt" in files
                else "pip install ."
            )
            start_command = self._python_start_command(framework, dependencies, candidate_root)
            confidence = 0.9 if framework != "Python" else 0.72
            return (
                language,
                framework,
                runtime,
                package_manager,
                install_command,
                build_command,
                start_command,
                dependencies,
                scripts,
                confidence,
            )

        if "composer.json" in files or (candidate_root / "artisan").exists():
            package = self._read_json(files.get("composer.json")) if "composer.json" in files else {}
            dependencies |= set(package.get("require", {}))
            language = "PHP"
            framework = (
                "Laravel"
                if "laravel/framework" in dependencies or (candidate_root / "artisan").exists()
                else "PHP"
            )
            runtime = "php"
            install_command = "composer install --no-dev --optimize-autoloader"
            start_command = (
                "php artisan serve --host=0.0.0.0 --port=8000"
                if framework == "Laravel"
                else "php -S 0.0.0.0:8000 -t public"
            )
            confidence = 0.88 if framework == "Laravel" else 0.7
            return (
                language,
                framework,
                runtime,
                package_manager,
                install_command,
                build_command,
                start_command,
                dependencies,
                scripts,
                confidence,
            )

        if "pom.xml" in files:
            pom_text = self._read_text(files["pom.xml"])
            language = "Java"
            framework = "Spring Boot" if "spring-boot" in pom_text else "Java"
            runtime = "java"
            install_command = (
                "./mvnw dependency:go-offline"
                if (candidate_root / "mvnw").exists()
                else "mvn dependency:go-offline"
            )
            build_command = (
                "./mvnw -DskipTests package"
                if (candidate_root / "mvnw").exists()
                else "mvn -DskipTests package"
            )
            start_command = "java -jar /app/app.jar"
            confidence = 0.9 if framework == "Spring Boot" else 0.7
            return (
                language,
                framework,
                runtime,
                package_manager,
                install_command,
                build_command,
                start_command,
                dependencies,
                scripts,
                confidence,
            )

        if "go.mod" in files:
            go_mod = self._read_text(files["go.mod"])
            dependencies |= set(re.findall(r"^\s*([a-zA-Z0-9\.\-_/]+)\s+v", go_mod, re.MULTILINE))
            return (
                "Go",
                "Go",
                "go",
                package_manager,
                "go mod download",
                "go build -o /tmp/app ./...",
                "/app/app",
                dependencies,
                scripts,
                0.8,
            )

        if "Gemfile" in files:
            gemfile = self._read_text(files["Gemfile"])
            dependencies |= set(re.findall(r"gem [\"']([^\"']+)[\"']", gemfile))
            framework = "Ruby on Rails" if "rails" in dependencies else "Ruby"
            return (
                "Ruby",
                framework,
                "ruby",
                package_manager,
                "bundle install",
                build_command,
                "bundle exec rails server -b 0.0.0.0 -p 3000",
                dependencies,
                scripts,
                0.88 if framework == "Ruby on Rails" else 0.68,
            )

        return (
            language,
            framework,
            runtime,
            package_manager,
            install_command,
            build_command,
            start_command,
            dependencies,
            scripts,
            confidence,
        )

    def _detect_node_framework(self, dependencies: set[str], root: Path, scripts: dict) -> str:
        if "next" in dependencies:
            return "Next.js"
        if "@nestjs/core" in dependencies:
            return "NestJS"
        if "express" in dependencies:
            return "Express"
        if "vite" in dependencies or any((root / name).exists() for name in ("vite.config.ts", "vite.config.js")):
            return "Vite"
        if "react" in dependencies:
            return "React"
        script_text = " ".join(str(value) for value in scripts.values()).lower()
        if "nest start" in script_text:
            return "NestJS"
        if "next dev" in script_text or "next build" in script_text:
            return "Next.js"
        if "vite" in script_text:
            return "Vite"
        if "node server" in script_text or "nodemon" in script_text:
            return "Express"
        return "Node.js"

    def _detect_python_framework(self, dependencies: set[str], files: dict[str, Path]) -> str:
        dependency_text = " ".join(sorted(dependencies)).lower()
        if "django" in dependency_text or "manage.py" in files:
            return "Django"
        if "fastapi" in dependency_text:
            return "FastAPI"
        if "flask" in dependency_text:
            return "Flask"
        return "Python"

    def _python_dependencies(self, files: dict[str, Path]) -> set[str]:
        dependencies: set[str] = set()
        if "requirements.txt" in files:
            for line in self._read_text(files["requirements.txt"]).splitlines():
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                package = re.split(r"[<>=~!\[]", value, maxsplit=1)[0].strip()
                if package:
                    dependencies.add(package.lower())
        if "pyproject.toml" in files:
            payload = tomllib.loads(self._read_text(files["pyproject.toml"]))
            project_dependencies = payload.get("project", {}).get("dependencies", [])
            for item in project_dependencies:
                dependencies.add(re.split(r"[<>=~!\[]", item, maxsplit=1)[0].strip().lower())
            poetry_dependencies = payload.get("tool", {}).get("poetry", {}).get("dependencies", {})
            dependencies |= {str(key).lower() for key in poetry_dependencies if key != "python"}
        return dependencies

    def _python_start_command(self, framework: str, dependencies: set[str], root: Path) -> str:
        if framework == "Django":
            settings_module = self._guess_django_project_module(root)
            if "gunicorn" in dependencies:
                return f"gunicorn {settings_module}.wsgi:application --bind 0.0.0.0:8000"
            return "python manage.py runserver 0.0.0.0:8000"
        if framework == "FastAPI":
            entrypoint = self._guess_asgi_module(root, default="main:app")
            if "gunicorn" in dependencies:
                return f"gunicorn {entrypoint} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"
            return f"uvicorn {entrypoint} --host 0.0.0.0 --port 8000"
        if framework == "Flask":
            if "gunicorn" in dependencies:
                return "gunicorn app:app --bind 0.0.0.0:5000"
            return "flask run --host=0.0.0.0 --port=5000"
        return "python main.py"

    def _guess_django_project_module(self, root: Path) -> str:
        settings_files = list(root.glob("*/settings.py"))
        if settings_files:
            return settings_files[0].parent.name
        return "config"

    def _guess_asgi_module(self, root: Path, default: str) -> str:
        for candidate in ("main.py", "app.py", "src/main.py", "src/app.py"):
            path = root / candidate
            if path.exists():
                module = candidate.replace("/", ".").replace(".py", "")
                return f"{module}:app"
        return default

    def _detect_package_manager(self, root: Path) -> str:
        if (root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (root / "yarn.lock").exists():
            return "yarn"
        return "npm"

    def _node_start_command(
        self,
        package_manager: str,
        scripts: dict,
        framework: str,
        root: Path,
    ) -> str:
        if "start" in scripts:
            return self._pick_script_command(package_manager, scripts, "start")
        if framework == "NestJS":
            return "node dist/main.js"
        if framework == "Next.js":
            return f"{package_manager} run start" if package_manager != "npm" else "npm run start"
        if framework == "Express":
            for fallback in ("server.js", "app.js", "index.js", "dist/main.js"):
                if (root / fallback).exists():
                    return f"node {fallback}"
            return "node server.js"
        return "node index.js"

    def _pick_script_command(self, package_manager: str | None, scripts: dict, script_name: str) -> str | None:
        if script_name not in scripts:
            return None
        if package_manager == "pnpm":
            return f"pnpm {script_name}"
        if package_manager == "yarn":
            return f"yarn {script_name}"
        return f"npm run {script_name}"

    def _detect_environment_variables(self, root: Path) -> list[str]:
        env_vars: set[str] = set()
        for path in self._collect_files(root):
            if path.suffix.lower() not in self.TEXT_EXTENSIONS and not path.name.startswith(".env"):
                continue
            text = self._read_text(path)
            if path.name.startswith(".env"):
                env_vars |= {
                    match.group(1)
                    for match in re.finditer(r"^([A-Z0-9_]+)\s*=", text, re.MULTILINE)
                }
            for pattern in self.ENV_PATTERNS:
                for match in pattern.finditer(text):
                    value = next(group for group in match.groups() if group)
                    env_vars.add(value)
        return sorted(env_vars)

    def _detect_ports(self, root: Path, framework: str, env_vars: list[str]) -> list[int]:
        ports: set[int] = set()
        for path in self._collect_files(root):
            if path.suffix.lower() not in self.TEXT_EXTENSIONS:
                continue
            text = self._read_text(path)
            for pattern in self.PORT_PATTERNS:
                ports |= {int(match.group(1)) for match in pattern.finditer(text)}

        if not ports:
            default_port = self.DEFAULT_PORTS.get(framework)
            if default_port:
                ports.add(default_port)
        if "PORT" in env_vars and not ports:
            ports.add(3000)
        return sorted(port for port in ports if 0 < port < 65536)

    def _detect_services(self, dependencies: set[str], env_vars: list[str]) -> list[str]:
        dependency_text = " ".join(sorted(dependencies)).lower()
        env_text = " ".join(env_vars)
        services = set()
        if any(token in dependency_text for token in ("pg", "psycopg", "postgres", "psycopg2")) or "POSTGRES" in env_text:
            services.add("postgres")
        if any(token in dependency_text for token in ("mysql", "mysql2", "pymysql")) or "MYSQL" in env_text:
            services.add("mysql")
        if any(token in dependency_text for token in ("redis", "hiredis")) or "REDIS" in env_text:
            services.add("redis")
        if any(token in dependency_text for token in ("mongoose", "mongodb", "pymongo")) or "MONGO" in env_text:
            services.add("mongodb")
        return sorted(services)

    def _detect_healthcheck_path(self, root: Path) -> str:
        endpoint_candidates = ("/health", "/healthz", "/ready", "/status")
        for path in self._collect_files(root):
            if path.suffix.lower() not in self.TEXT_EXTENSIONS:
                continue
            text = self._read_text(path)
            for endpoint in endpoint_candidates:
                if endpoint in text:
                    return endpoint
        return "/"

    def _choose_base_image(self, framework: str, dependencies: set[str]) -> str:
        dependency_text = " ".join(sorted(dependencies)).lower()
        if framework in {"Next.js", "Django", "FastAPI", "Flask", "Spring Boot", "Ruby on Rails"}:
            return "slim"
        if "sharp" in dependency_text or "prisma" in dependency_text:
            return "slim"
        return "alpine"

    def _classify_project_type(self, project_root: Path, components: list[ComponentSpec]) -> str:
        if len(components) <= 1:
            return "single-service"
        roles = {component.role for component in components}
        if {"frontend", "backend"}.issubset(roles):
            return "fullstack"
        if any((project_root / marker).exists() for marker in self.MONOREPO_MARKERS):
            return "monorepo"
        if (project_root / "package.json").exists():
            package = self._read_json(project_root / "package.json")
            if package.get("workspaces"):
                return "monorepo"
        return "monorepo"

    def _build_recommendations(
        self,
        project_type: str,
        components: list[ComponentSpec],
        shared_services: list[str],
    ) -> list[str]:
        recommendations = []
        if project_type in {"fullstack", "monorepo"}:
            recommendations.append(
                "Generar Dockerfiles por componente y orquestarlos desde docker-compose."
            )
        if shared_services:
            recommendations.append(
                f"Incluir servicios auxiliares en compose: {', '.join(shared_services)}."
            )
        for component in components:
            if component.framework == "Django" and "gunicorn" not in component.dependency_names:
                recommendations.append(
                    "Para Django en producción conviene agregar gunicorn al proyecto analizado."
                )
            if component.framework == "FastAPI" and "uvicorn" not in component.dependency_names:
                recommendations.append(
                    "Para FastAPI conviene declarar uvicorn explícitamente en dependencias."
                )
            if component.framework in {"React", "Vite"}:
                recommendations.append(
                    f"{component.name}: conviene publicar el build estático detrás de nginx."
                )
        return sorted(dict.fromkeys(recommendations))

    def _build_notes(self, project_root: Path, components: list[ComponentSpec]) -> list[str]:
        notes = []
        if (project_root / "Dockerfile").exists():
            notes.append("El proyecto ya contiene al menos un Dockerfile existente.")
        if any(component.existing_dockerfile for component in components):
            notes.append("Se detectaron Dockerfiles dentro de componentes específicos.")
        return notes

    def _component_name(self, relative_path: str) -> str:
        if relative_path == ".":
            return "app"
        return relative_path.split("/")[-1] or "app"

    def _guess_role(self, folder_name: str) -> str:
        lower_name = folder_name.lower()
        if lower_name in {"frontend", "web", "client", "ui"}:
            return "frontend"
        if lower_name in {"backend", "api", "server"}:
            return "backend"
        return "app"

    def _resolve_role(self, component: ComponentSpec, root: Path, scripts: dict) -> str:
        if component.role in {"frontend", "backend"}:
            return component.role

        if component.framework in {"Next.js", "React", "Vite"}:
            return "frontend"
        if component.framework in {
            "Express",
            "NestJS",
            "Django",
            "FastAPI",
            "Flask",
            "Laravel",
            "Spring Boot",
            "Go",
            "Ruby on Rails",
            "PHP",
            "Java",
        }:
            return "backend"

        script_text = " ".join(str(value) for value in scripts.values()).lower()
        if any(token in script_text for token in ("vite", "next", "react-scripts")):
            return "frontend"
        if any(token in script_text for token in ("gunicorn", "uvicorn", "node", "nest")):
            return "backend"

        return self._guess_role(root.name)

    def _workspace_candidates(self, project_root: Path) -> list[Path]:
        candidates: list[Path] = []
        package_json = project_root / "package.json"
        if package_json.exists():
            package = self._read_json(package_json)
            workspaces = package.get("workspaces", [])
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])
            for pattern in workspaces:
                candidates.extend(self._resolve_workspace_pattern(project_root, pattern))

        for directory in ("apps", "packages", "services"):
            base = project_root / directory
            if not base.exists():
                continue
            for child in base.iterdir():
                if child.is_dir() and self._has_markers(child):
                    candidates.append(child)

        unique: dict[str, Path] = {}
        for candidate in candidates:
            unique[str(candidate.resolve())] = candidate
        return list(unique.values())

    def _prune_root_orchestrator_component(
        self,
        project_root: Path,
        components: list[ComponentSpec],
    ) -> list[ComponentSpec]:
        if len(components) <= 1:
            return components

        root_component = next((component for component in components if component.path == "."), None)
        if not root_component:
            return components

        if self._is_workspace_orchestrator_root(project_root, root_component):
            return [component for component in components if component.path != "."]

        if root_component.framework in {"Node.js", "Python"} and len(root_component.found_files) <= 3:
            return [component for component in components if component.path != "."]

        return components

    def _is_workspace_orchestrator_root(
        self,
        project_root: Path,
        component: ComponentSpec,
    ) -> bool:
        if component.language != "Node.js":
            return False

        package = self._read_json(project_root / "package.json")
        has_workspaces = bool(package.get("workspaces")) or any(
            (project_root / marker).exists() for marker in self.MONOREPO_MARKERS
        )
        if not has_workspaces:
            return False

        scripts = package.get("scripts", {})
        dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
        has_workspace_dirs = any((project_root / directory).exists() for directory in ("apps", "packages", "services"))
        if not has_workspace_dirs:
            return False

        orchestrator_tokens = (
            "workspace",
            "workspaces",
            "turbo",
            "nx",
            "lerna",
            "pnpm -r",
            "pnpm --filter",
        )
        script_values = [str(value).lower() for value in scripts.values()]
        orchestrator_scripts_only = bool(script_values) and all(
            any(token in value for token in orchestrator_tokens) for value in script_values
        )
        direct_runtime_files = any(
            (project_root / candidate).exists()
            for candidate in ("server.js", "app.js", "index.js", "main.js", "src/main.js", "src/index.js")
        )
        root_framework_signal = self._detect_node_framework(dependencies, project_root, scripts)
        has_runtime_dependency = root_framework_signal != "Node.js"
        has_generic_runtime_script = any(
            name in scripts for name in ("start", "dev", "serve")
        ) and not orchestrator_scripts_only

        return (
            not has_runtime_dependency
            and not direct_runtime_files
            and not has_generic_runtime_script
            and (not dependencies or orchestrator_scripts_only)
        )

    def _resolve_workspace_pattern(self, project_root: Path, pattern: str) -> list[Path]:
        matches = []
        normalized = pattern.strip().replace("\\", "/")
        for match in project_root.glob(normalized):
            if match.is_dir() and self._has_markers(match):
                matches.append(match)
        return matches

    def _has_markers(self, path: Path) -> bool:
        return any((path / marker).exists() for marker in self.MARKER_FILES)

    def _collect_files(self, root: Path) -> list[Path]:
        paths: list[Path] = []
        root = root.resolve()
        for current_root, dirnames, filenames in os.walk(root):
            current_path = Path(current_root)
            relative_parts = current_path.relative_to(root).parts
            dirnames[:] = [name for name in dirnames if name not in self.EXCLUDED_DIRS]
            if len(relative_parts) > 5:
                dirnames[:] = []
                continue
            for filename in filenames:
                path = current_path / filename
                try:
                    if path.stat().st_size > 1_000_000:
                        continue
                except OSError:
                    continue
                paths.append(path)
        return paths

    def _read_text(self, path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="latin-1", errors="ignore")

    def _read_json(self, path: Path | None) -> dict:
        if path is None or not path.exists():
            return {}
        try:
            return json.loads(self._read_text(path))
        except json.JSONDecodeError:
            return {}
