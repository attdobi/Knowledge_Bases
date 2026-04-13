from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from financial_news.config import AttachmentConfig, PathRemap
from financial_news.models import SummaryRecord
from financial_news.obsidian import (
    append_summary,
    category_note_path,
    copy_attachments,
    destination_markdown_path,
    prune_orphan_attachments,
    render_summary_block,
    resolve_attachment_source,
    source_note_path,
)


def make_record(
    *,
    row_id: int,
    attachment: Path | None = None,
    categories: list[str] | None = None,
) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 14, 5, tzinfo=timezone.utc),
        agent="Agent BBC Business",
        content_timestamp=None,
        headlines=["European stocks close higher"],
        insights=["Investors rotated back into cyclicals."],
        attachments=[attachment] if attachment else [],
        categories=categories or ["Rates/Fed", "Financials"],
        raw_content={"ok": True},
    )


def write_noisy_png(path: Path, size: tuple[int, int] = (1200, 900)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = os.urandom(size[0] * size[1] * 3)
    image = Image.frombytes("RGB", size, payload)
    image.save(path, format="PNG")


def test_destination_markdown_path_uses_month_partition(tmp_path: Path) -> None:
    destination = destination_markdown_path(tmp_path, make_record(row_id=1))
    assert destination == tmp_path / "2026-03" / "2026-03-30_summary.md"


def test_resolve_attachment_source_prefers_longest_matching_remap() -> None:
    config = AttachmentConfig(
        path_remaps=(
            PathRemap(Path("/Users/adobi"), Path("/Volumes/adobi")),
            PathRemap(Path("/Users/adobi/d-ai-trader"), Path("/Volumes/adobi/d-ai-trader")),
        )
    )

    resolved = resolve_attachment_source(Path("/Users/adobi/d-ai-trader/captures/chart.png"), config)

    assert resolved == Path("/Volumes/adobi/d-ai-trader/captures/chart.png")


def test_copy_attachments_optimizes_existing_files_and_skips_missing_ones(tmp_path: Path, caplog) -> None:
    source = tmp_path / "source-chart.png"
    write_noisy_png(source)
    missing = tmp_path / "missing-chart.png"
    record = make_record(row_id=2)
    record.attachments = [source, missing]

    copied = copy_attachments(
        tmp_path / "vault",
        record,
        AttachmentConfig(mode="optimize", image_format="webp", image_quality=72, max_dimension=900),
    )

    expected = copied[0]
    assert expected.suffix == ".webp"
    assert expected.exists()
    assert expected.stat().st_size < source.stat().st_size
    assert "Skipping missing attachment" in caplog.text


def test_render_summary_block_uses_targeted_links_and_obsidian_embed_syntax(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    attachment = output_root / "attachments" / "2026-03-30" / "chart.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"png-bytes")

    block = render_summary_block(output_root, make_record(row_id=3), [attachment])

    assert "<!-- source-summary-id: 3 -->" in block
    assert "## 2026-03-30T14:05:00+00:00 — Agent BBC Business" in block
    assert "#source/agent-bbc-business" in block
    assert "#topic/rates-fed" in block
    assert "#topic/financials" in block
    assert "[[Sources/Agent BBC Business|Agent BBC Business]]" in block
    assert "[[Categories/rates-fed|Rates/Fed]]" in block
    assert "![[attachments/2026-03-30/chart.png]]" in block


def test_append_summary_creates_indexes_without_global_home_links(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    source = tmp_path / "chart.png"
    write_noisy_png(source, size=(640, 480))

    first_path = append_summary(output_root, make_record(row_id=11, attachment=source), AttachmentConfig(mode="preserve"))
    second_path = append_summary(output_root, make_record(row_id=12, attachment=source), AttachmentConfig(mode="preserve"))

    assert first_path == second_path
    content = first_path.read_text(encoding="utf-8")
    assert content.startswith("---\ndate: 2026-03-30\ntype: daily-summary\n")
    assert "[[Home]]" not in content
    assert content.count("# Financial News — 2026-03-30") == 1
    assert "source-summary-id: 11" in content
    assert "source-summary-id: 12" in content
    assert content.count("![[attachments/2026-03-30/") == 2

    source_index = source_note_path(output_root, "Agent BBC Business")
    category_index = category_note_path(output_root, "Rates/Fed")
    assert source_index.exists()
    assert category_index.exists()
    assert "## Heuristic categories" in source_index.read_text(encoding="utf-8")
    assert "[[2026-03/2026-03-30_summary|2026-03-30]]" in source_index.read_text(encoding="utf-8")
    category_text = category_index.read_text(encoding="utf-8")
    assert "classification: heuristic" in category_text
    assert "[[Sources/Agent BBC Business|Agent BBC Business]]" in category_text


def test_prune_orphan_attachments_only_removes_unreferenced_files(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    keep = output_root / "attachments" / "2026-03-30" / "keep.png"
    drop = output_root / "attachments" / "2026-03-30" / "drop.png"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"keep")
    drop.write_bytes(b"drop")
    note = output_root / "2026-03" / "2026-03-30_summary.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("![[attachments/2026-03-30/keep.png]]\n", encoding="utf-8")

    pruned = prune_orphan_attachments(output_root)

    assert pruned == [drop]
    assert keep.exists()
    assert not drop.exists()
