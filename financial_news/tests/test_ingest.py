from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import financial_news.ingest as ingest
from financial_news.models import SummaryRecord, TableSchema


class DummyConnection:
    def __enter__(self) -> "DummyConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def make_record(row_id: int, attachment: Path | None = None) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 16, 0, tzinfo=timezone.utc),
        agent="Agent CNBC",
        content_timestamp=None,
        headlines=["Treasuries steady after CPI"],
        insights=["Rate-cut pricing held roughly flat."],
        attachments=[attachment] if attachment else [],
        categories=["Rates/Fed"],
        raw_content={"row_id": row_id},
    )


def make_schema() -> TableSchema:
    return TableSchema(
        table_name="summaries",
        columns=[
            {"column_name": "id", "data_type": "integer"},
            {"column_name": "created_at", "data_type": "timestamp with time zone"},
            {"column_name": "content", "data_type": "jsonb"},
        ],
        id_column="id",
        content_column="content",
        timestamp_column="created_at",
    )


def test_main_ingests_records_and_updates_state(tmp_path: Path, monkeypatch) -> None:
    attachment = tmp_path / "source-chart.png"
    attachment.write_bytes(b"png-bytes")
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    seen = {}

    def fake_connect(dsn: str | None) -> DummyConnection:
        seen["dsn"] = dsn
        return DummyConnection()

    def fake_fetch_summaries(conn, schema, since_id=None, limit=None):
        seen["since_id"] = since_id
        seen["limit"] = limit
        return [make_record(101, attachment)]

    monkeypatch.setattr(ingest, "connect", fake_connect)
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", fake_fetch_summaries)

    result = ingest.main(
        [
            "--dsn",
            "postgresql://demo",
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--limit",
            "5",
            "--path-remap",
            "/Users/adobi/d-ai-trader=/Volumes/adobi/d-ai-trader",
        ]
    )

    assert result == 0
    assert seen == {"dsn": "postgresql://demo", "since_id": None, "limit": 5}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 101}

    summary_path = output_root / "2026-03" / "2026-03-30_summary.md"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "source-summary-id: 101" in content
    assert "Treasuries steady after CPI" in content
    assert "[[Sources/Agent CNBC|Agent CNBC]]" in content
    assert "[[Categories/rates-fed|Rates/Fed]]" in content
    assert "![[attachments/2026-03-30/" in content


def test_main_uses_saved_state_and_skips_writes_during_dry_run(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"last_processed_id": 88}', encoding="utf-8")
    seen = {}

    def fake_fetch_summaries(conn, schema, since_id=None, limit=None):
        seen["since_id"] = since_id
        return [make_record(99)]

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", fake_fetch_summaries)

    def fail_append(*args, **kwargs):
        raise AssertionError("append_summary should not be called during --dry-run")

    monkeypatch.setattr(ingest, "append_summary", fail_append)

    result = ingest.main(
        [
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--dry-run",
        ]
    )

    assert result == 0
    assert seen == {"since_id": 88}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 88}
    assert not summary_path_exists(output_root)


def test_main_checkpoints_each_successful_record_before_failure(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    written_row_ids: list[int] = []
    records = [make_record(101), make_record(102)]

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", lambda conn, schema, since_id=None, limit=None: records)

    def fake_append_summary(output_root: Path, record: SummaryRecord, attachment_config) -> Path:
        if record.row_id == 102:
            raise RuntimeError("disk full")
        written_row_ids.append(record.row_id)
        destination = output_root / f"{record.row_id}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"row {record.row_id}", encoding="utf-8")
        return destination

    monkeypatch.setattr(ingest, "append_summary", fake_append_summary)

    with pytest.raises(RuntimeError, match="disk full"):
        ingest.main(
            [
                "--output-root",
                str(output_root),
                "--state-path",
                str(state_path),
            ]
        )

    assert written_row_ids == [101]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 101}


def test_main_can_prune_orphan_attachments_after_ingest(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    pruned = [output_root / "attachments" / "2026-03-30" / "old.webp"]
    seen = {}

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", lambda conn, schema, since_id=None, limit=None: [])

    def fake_prune(root: Path) -> list[Path]:
        seen["root"] = root
        return pruned

    monkeypatch.setattr(ingest, "prune_orphan_attachments", fake_prune)

    result = ingest.main(
        [
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--prune-orphan-attachments",
        ]
    )

    assert result == 0
    assert seen == {"root": output_root}


def summary_path_exists(output_root: Path) -> bool:
    return any(output_root.rglob("*_summary.md")) if output_root.exists() else False
