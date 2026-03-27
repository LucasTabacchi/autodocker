from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import unified_diff

from core.models import ProjectAnalysis
from core.services.ingestion import materialize_analysis_source


@dataclass(slots=True)
class ArtifactDiffEntry:
    path: str
    kind: str
    status: str
    existing_path: str
    existing_content: str
    generated_content: str
    diff: str

    def to_dict(self) -> dict:
        return asdict(self)


class ArtifactDiffService:
    REQUIRED_DOCKERFILE_TOKENS = ("FROM", "WORKDIR", "COPY", "CMD")

    def build_diff(self, analysis: ProjectAnalysis) -> list[ArtifactDiffEntry]:
        entries: list[ArtifactDiffEntry] = []
        with materialize_analysis_source(analysis) as source_root:
            for artifact in analysis.artifacts.all():
                existing_path = source_root / artifact.path
                existing_content = (
                    existing_path.read_text(encoding="utf-8", errors="ignore")
                    if existing_path.exists()
                    else ""
                )
                status = self._classify(artifact.path, existing_content, artifact.content)
                diff = "\n".join(
                    unified_diff(
                        existing_content.splitlines(),
                        artifact.content.splitlines(),
                        fromfile=f"existing/{artifact.path}",
                        tofile=f"generated/{artifact.path}",
                        lineterm="",
                    )
                )
                entries.append(
                    ArtifactDiffEntry(
                        path=artifact.path,
                        kind=artifact.kind,
                        status=status,
                        existing_path=artifact.path if existing_path.exists() else "",
                        existing_content=existing_content,
                        generated_content=artifact.content,
                        diff=diff,
                    )
                )
        return entries

    def _classify(self, path: str, existing_content: str, generated_content: str) -> str:
        if not existing_content:
            return "new"
        if existing_content.strip() == generated_content.strip():
            return "same"
        if path.endswith("Dockerfile") or path.endswith(".dockerignore") or path.endswith(".yml"):
            if self._looks_like_improvement(existing_content, generated_content):
                return "improvement"
        return "conflict"

    def _looks_like_improvement(self, existing_content: str, generated_content: str) -> bool:
        existing_score = sum(token in existing_content for token in self.REQUIRED_DOCKERFILE_TOKENS)
        generated_score = sum(token in generated_content for token in self.REQUIRED_DOCKERFILE_TOKENS)
        return generated_score > existing_score
