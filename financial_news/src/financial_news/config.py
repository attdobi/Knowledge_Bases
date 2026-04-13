from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_output_root() -> Path:
    return project_root() / ".generated" / "vault"


@dataclass(frozen=True, slots=True)
class PathRemap:
    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class AttachmentConfig:
    mode: str = "optimize"
    image_format: str = "webp"
    image_quality: int = 82
    max_dimension: int = 2200
    path_remaps: tuple[PathRemap, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.mode not in {"preserve", "optimize"}:
            raise ValueError(f"Unsupported attachment mode: {self.mode}")
        if self.image_format not in {"webp", "jpeg", "png"}:
            raise ValueError(f"Unsupported attachment image format: {self.image_format}")
        if not 1 <= self.image_quality <= 100:
            raise ValueError("Attachment image quality must be between 1 and 100")
        if self.max_dimension < 256:
            raise ValueError("Attachment max dimension must be at least 256 pixels")


@dataclass(slots=True)
class AppConfig:
    dsn: str | None
    output_root: Path
    state_path: Path
    attachments: AttachmentConfig

    @classmethod
    def load(
        cls,
        dsn: str | None = None,
        output_root: str | None = None,
        state_path: str | None = None,
        path_remaps: Iterable[str] | None = None,
        attachment_mode: str | None = None,
        attachment_format: str | None = None,
        attachment_quality: int | None = None,
        attachment_max_dimension: int | None = None,
    ) -> "AppConfig":
        configured_output_root = output_root or os.getenv("FINANCIAL_NEWS_OUTPUT_ROOT")
        base_root = Path(configured_output_root).expanduser().resolve() if configured_output_root else default_output_root().resolve()
        state_file = Path(state_path).expanduser().resolve() if state_path else (base_root / ".state" / "ingest_state.json")
        env_dsn = dsn or os.getenv("FINANCIAL_NEWS_DSN") or os.getenv("DATABASE_URL")

        env_path_remaps = [
            entry
            for entry in os.getenv("FINANCIAL_NEWS_PATH_REMAPS", "").split(os.pathsep)
            if entry.strip()
        ]
        all_path_remaps = tuple(parse_path_remap(entry) for entry in [*env_path_remaps, *(path_remaps or [])])

        mode = (attachment_mode or os.getenv("FINANCIAL_NEWS_ATTACHMENT_MODE") or "optimize").strip().lower()
        image_format = (attachment_format or os.getenv("FINANCIAL_NEWS_ATTACHMENT_FORMAT") or "webp").strip().lower()
        image_quality = attachment_quality if attachment_quality is not None else int(os.getenv("FINANCIAL_NEWS_ATTACHMENT_QUALITY", "82"))
        max_dimension = (
            attachment_max_dimension
            if attachment_max_dimension is not None
            else int(os.getenv("FINANCIAL_NEWS_ATTACHMENT_MAX_DIMENSION", "2200"))
        )

        return cls(
            dsn=env_dsn,
            output_root=base_root,
            state_path=state_file,
            attachments=AttachmentConfig(
                mode=mode,
                image_format=image_format,
                image_quality=image_quality,
                max_dimension=max_dimension,
                path_remaps=all_path_remaps,
            ),
        )


def parse_path_remap(value: str) -> PathRemap:
    source_text, separator, destination_text = value.partition("=")
    if separator != "=" or not source_text.strip() or not destination_text.strip():
        raise ValueError(f"Invalid path remap {value!r}; expected SOURCE=DEST")
    return PathRemap(
        source=Path(source_text.strip()).expanduser(),
        destination=Path(destination_text.strip()).expanduser(),
    )
