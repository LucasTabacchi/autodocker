from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib import error, parse, request

from core.models import ExternalRepoConnection, ProjectAnalysis
from core.services.runtime import CommandExecutionError, run_command


class GitHubPullRequestError(RuntimeError):
    """Raised when a GitHub PR workflow fails."""


@dataclass(slots=True)
class GitHubPullRequestResult:
    success: bool
    branch_name: str
    pr_url: str
    pull_number: int | None
    logs: str
    skipped: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class GitHubPullRequestService:
    def create_pull_request(
        self,
        analysis: ProjectAnalysis,
        *,
        connection: ExternalRepoConnection | None,
        access_token: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> GitHubPullRequestResult:
        if analysis.source_type != ProjectAnalysis.SourceType.GIT:
            raise GitHubPullRequestError("La creación automática de PR solo está disponible para repos Git.")
        if not analysis.repository_url:
            raise GitHubPullRequestError("El análisis no tiene una URL de repositorio asociada.")

        slug = self._github_slug(analysis.repository_url)
        branch_name = f"codex/autodocker-{str(analysis.id)[:8]}"
        repo_dir = self._clone_repository(slug, access_token, base_branch)
        logs: list[str] = []

        try:
            self._configure_git_identity(repo_dir, connection)
            logs.append(self._run_and_log(["git", "checkout", "-b", branch_name], repo_dir, access_token))
            for artifact in analysis.artifacts.all():
                target = repo_dir / artifact.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(artifact.content, encoding="utf-8")
            logs.append(self._run_and_log(["git", "add", "."], repo_dir, access_token))
            status_result = run_command(["git", "status", "--porcelain"], repo_dir, timeout=60)
            if not status_result.stdout.strip():
                return GitHubPullRequestResult(
                    success=True,
                    branch_name=branch_name,
                    pr_url="",
                    pull_number=None,
                    logs="\n\n".join([*logs, status_result.output]).strip(),
                    skipped=True,
                    reason="No hay cambios para commitear contra la rama base.",
                )

            commit_message = f"chore: dockerize {analysis.project_name}"
            logs.append(self._run_and_log(["git", "commit", "-m", commit_message], repo_dir, access_token))
            push_url = f"https://x-access-token:{parse.quote(access_token, safe='')}@github.com/{slug}.git"
            logs.append(
                self._run_and_log(
                    ["git", "push", "-u", push_url, branch_name],
                    repo_dir,
                    access_token,
                    timeout=300,
                )
            )

            payload = self._open_pull_request(
                slug=slug,
                access_token=access_token,
                base_branch=base_branch,
                branch_name=branch_name,
                title=title,
                body=body,
            )
            return GitHubPullRequestResult(
                success=True,
                branch_name=branch_name,
                pr_url=payload.get("html_url", ""),
                pull_number=payload.get("number"),
                logs="\n\n".join(logs).strip(),
            )
        finally:
            shutil.rmtree(repo_dir.parent, ignore_errors=True)

    def _clone_repository(self, slug: str, access_token: str, base_branch: str) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="autodocker-github-"))
        repo_dir = temp_dir / "repo"
        clone_url = f"https://x-access-token:{parse.quote(access_token, safe='')}@github.com/{slug}.git"
        try:
            run_command(
                ["git", "clone", "--depth", "1", "--branch", base_branch, clone_url, str(repo_dir)],
                temp_dir,
                timeout=300,
            )
        except CommandExecutionError as exc:
            raise GitHubPullRequestError(self._sanitize(str(exc), access_token)) from exc
        return repo_dir

    def _configure_git_identity(self, repo_dir: Path, connection: ExternalRepoConnection | None) -> None:
        display_name = (
            connection.account_name or connection.label
            if connection
            else "AutoDocker Bot"
        )
        run_command(["git", "config", "user.name", display_name], repo_dir, timeout=60)
        run_command(["git", "config", "user.email", "autodocker@local.invalid"], repo_dir, timeout=60)

    def _run_and_log(
        self,
        command: list[str],
        cwd: Path,
        access_token: str,
        timeout: int = 120,
    ) -> str:
        result = run_command(command, cwd, timeout=timeout)
        rendered = f"$ {' '.join(command)}\n{result.output}".strip()
        return self._sanitize(rendered, access_token)

    def _open_pull_request(
        self,
        *,
        slug: str,
        access_token: str,
        base_branch: str,
        branch_name: str,
        title: str,
        body: str,
    ) -> dict:
        payload = json.dumps(
            {
                "title": title,
                "head": branch_name,
                "base": base_branch,
                "body": body,
            }
        ).encode("utf-8")
        req = request.Request(
            f"https://api.github.com/repos/{slug}/pulls",
            data=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise GitHubPullRequestError(detail or f"GitHub devolvió HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise GitHubPullRequestError(str(exc)) from exc

    def _github_slug(self, repository_url: str) -> str:
        normalized = repository_url.rstrip("/")
        if normalized.endswith(".git"):
            normalized = normalized[:-4]
        marker = "github.com/"
        if marker not in normalized:
            raise GitHubPullRequestError("Solo se admite integración automática con repositorios GitHub.")
        return normalized.split(marker, maxsplit=1)[1]

    def _sanitize(self, value: str, access_token: str) -> str:
        sanitized = value
        encoded = parse.quote(access_token, safe="")
        for secret in {access_token, encoded}:
            if secret:
                sanitized = sanitized.replace(secret, "***")
        return sanitized.replace("x-access-token:***@", "x-access-token:***@")
