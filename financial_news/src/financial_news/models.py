from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SummaryRecord:
    row_id: int
    row_timestamp: datetime | None
    agent: str | None
    content_timestamp: datetime | None
    headlines: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    raw_content: dict[str, Any] | list[Any] | str | None = None

    @property
    def effective_timestamp(self) -> datetime | None:
        return self.content_timestamp or self.row_timestamp


@dataclass(slots=True)
class DecisionRecord:
    row_id: int
    row_timestamp: datetime | None
    decision_timestamp: datetime | None
    agent: str | None = None
    ticker: str | None = None
    action: str | None = None
    confidence: str | None = None
    rationale: list[str] = field(default_factory=list)
    raw_content: dict[str, Any] | list[Any] | str | None = None

    @property
    def effective_timestamp(self) -> datetime | None:
        return self.decision_timestamp or self.row_timestamp


@dataclass(slots=True)
class TableSchema:
    table_name: str
    columns: list[dict[str, Any]]
    id_column: str
    content_column: str
    timestamp_column: str | None
