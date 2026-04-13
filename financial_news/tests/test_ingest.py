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


def test_parse_path_remaps_accepts_repeatable_from_to_pairs() -> None:
    remaps = ingest.parse_path_remaps(
        [
            "/Users/attila/d-ai-trader=/Volumes/adobi/d-ai-trader",
            "~/captures=/Volumes/adobi/captures",
        ]
    )

    assert remaps[0] == (Path("/Users/attila/d-ai-trader"), Path("/Volumes/adobi/d-ai-trader"))
    assert remaps[1][0].name == "captures"
    assert remaps[1][1] == Path("/Volumes/adobi/captures")


def test_parse_path_remaps_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        ingest.parse_path_remaps(["/from-only"])


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
        ]
    )

    assert result == 0
    assert seen == {"dsn": "postgresql://demo", "since_id": None, "limit": 5}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 101}

    summary_path = output_root / "2026-03" / "2026-03-30_summary.md"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "source-summary-id: 101" in content
    assert "[[Sources/CNBC|CNBC]]" in content
    assert "[[Topics/Rates and Fed|Rates and Fed]]" in content
    assert "![[attachments/2026-03-30/" in content
    assert "[[Home]]" not in content


def test_main_passes_path_remaps_to_append_summary(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    seen = {}

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", lambda conn, schema, since_id=None, limit=None: [make_record(101)])

    def fake_append_summary(output_root_arg, record, path_remaps=None):
        seen["path_remaps"] = path_remaps
        path = output_root_arg / "2026-03" / "2026-03-30_summary.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return path

    monkeypatch.setattr(ingest, "append_summary", fake_append_summary)

    result = ingest.main(
        [
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--path-remap",
            "/Users/attila/d-ai-trader=/Volumes/adobi/d-ai-trader",
        ]
    )

    assert result == 0
    assert seen["path_remaps"] == [(Path("/Users/attila/d-ai-trader"), Path("/Volumes/adobi/d-ai-trader"))]


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


def test_main_since_id_overrides_saved_state(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"last_processed_id": 88}', encoding="utf-8")
    seen = {}

    def fake_fetch_summaries(conn, schema, since_id=None, limit=None):
        seen["since_id"] = since_id
        return []

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", fake_fetch_summaries)

    result = ingest.main(
        [
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--since-id",
            "120",
        ]
    )

    assert result == 0
    assert seen == {"since_id": 120}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 88}
    assert not summary_path_exists(output_root)


def test_main_reset_state_clears_saved_cursor_before_fetch(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "ingest_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"last_processed_id": 88}', encoding="utf-8")
    seen = {}

    def fake_fetch_summaries(conn, schema, since_id=None, limit=None):
        seen["since_id"] = since_id
        return []

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn: make_schema())
    monkeypatch.setattr(ingest, "fetch_summaries", fake_fetch_summaries)

    result = ingest.main(
        [
            "--output-root",
            str(output_root),
            "--state-path",
            str(state_path),
            "--reset-state",
        ]
    )

    assert result == 0
    assert seen == {"since_id": None}
    assert not state_path.exists()
    assert not summary_path_exists(output_root)


def summary_path_exists(output_root: Path) -> bool:
    return any(output_root.rglob("*_summary.md")) if output_root.exists() else False
