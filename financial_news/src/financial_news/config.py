from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class AppConfig:
    dsn: str | None
    output_root: Path
    state_path: Path

    @classmethod
    def load(cls, dsn: str | None = None, output_root: str | None = None, state_path: str | None = None) -> "AppConfig":
        base_root = Path(output_root).expanduser().resolve() if output_root else project_root()
        state_file = Path(state_path).expanduser().resolve() if state_path else (base_root / ".state" / "ingest_state.json")
        env_dsn = dsn or os.getenv("FINANCIAL_NEWS_DSN") or os.getenv("DATABASE_URL")
        return cls(
            dsn=env_dsn,
            output_root=base_root,
            state_path=state_file,
        )
