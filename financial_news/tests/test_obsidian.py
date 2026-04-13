from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from financial_news.models import SummaryRecord
from financial_news.obsidian import append_summary, copy_attachments, destination_markdown_path, render_summary_block


def make_record(*, row_id: int, attachment: Path | None = None) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 14, 5, tzinfo=timezone.utc),
        agent="Agent BBC Business",
        content_timestamp=None,
        headlines=["European stocks close higher"],
        insights=["Investors rotated back into cyclicals."],
        attachments=[attachment] if attachment else [],
        raw_content={"ok": True},
    )


def test_destination_markdown_path_uses_month_partition(tmp_path: Path) -> None:
    destination = destination_markdown_path(tmp_path, make_record(row_id=1))
    assert destination == tmp_path / "2026-03" / "2026-03-30_summary.md"


def test_copy_attachments_copies_existing_files_and_skips_missing_ones(tmp_path: Path, caplog) -> None:
    source = tmp_path / "source-chart.png"
    source.write_bytes(b"png-bytes")
    missing = tmp_path / "missing-chart.png"
    record = make_record(row_id=2)
    record.attachments = [source, missing]

    copied = copy_attachments(tmp_path / "vault", record)

    digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
    expected = tmp_path / "vault" / "attachments" / "2026-03-30" / f"source-chart_{digest}.png"
    assert copied == [expected]
    assert expected.read_bytes() == b"png-bytes"
    assert "Skipping missing attachment" in caplog.text


def test_render_summary_block_uses_obsidian_embed_syntax(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    attachment = output_root / "attachments" / "2026-03-30" / "chart.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"png-bytes")

    block = render_summary_block(output_root, make_record(row_id=3), [attachment])

    assert "<!-- source-summary-id: 3 -->" in block
    assert "## 2026-03-30T14:05:00+00:00 — Agent BBC Business" in block
    assert "**Tags:** #financial-news #daily-summary #source/agent-bbc-business" in block
    assert "### Headlines" in block
    assert "- European stocks close higher" in block
    assert "### Insights" in block
    assert "![[attachments/2026-03-30/chart.png]]" in block


def test_append_summary_creates_header_once_and_appends_blocks(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    source = tmp_path / "chart.png"
    source.write_bytes(b"png-bytes")

    first_path = append_summary(output_root, make_record(row_id=11, attachment=source))
    second_path = append_summary(output_root, make_record(row_id=12, attachment=source))

    assert first_path == second_path
    content = first_path.read_text(encoding="utf-8")
    assert content.startswith("---\ndate: 2026-03-30\ntype: daily-summary\n")
    assert "[[Home]] · [[Sources/Home|Sources]] · [[Themes/Home|Themes]]" in content
    assert content.count("# Financial News — 2026-03-30") == 1
    assert "source-summary-id: 11" in content
    assert "source-summary-id: 12" in content
    assert content.count("![[attachments/2026-03-30/") == 2
