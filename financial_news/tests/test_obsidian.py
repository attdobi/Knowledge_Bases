from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from financial_news.config import AttachmentConfig, PathRemap
from financial_news.models import SummaryRecord
from financial_news.obsidian import (
    append_summary,
    copy_attachments,
    destination_markdown_path,
    prune_orphan_attachments,
    render_summary_block,
    resolve_attachment_source,
    source_note_path,
    source_profile_link,
    topic_note_path,
)


def make_record(
    *,
    row_id: int,
    agent: str = "Agent CNBC",
    attachment: Path | None = None,
    categories: list[str] | None = None,
    tickers: list[str] | None = None,
) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 14, 5, tzinfo=timezone.utc),
        agent=agent,
        content_timestamp=None,
        headlines=["Palo Alto Networks leads software strength as Powell calms rates"],
        insights=["Oil/Hormuz headlines remain the macro risk while FNMA and BX respond to easier Fed expectations."],
        attachments=[attachment] if attachment else [],
        tickers=tickers or ["MSFT", "NVDA"],
        categories=categories or ["Rates and Fed", "Financials"],
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


def test_source_profile_link_maps_known_agents() -> None:
    assert source_profile_link("Agent_CNBC") == ("Sources/CNBC", "CNBC")
    assert source_profile_link("Agent Fox Business") == ("Sources/Fox Business", "Fox Business")
    assert source_profile_link("Custom Agent") is None


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
    caplog.set_level("INFO")
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


def test_copy_attachments_can_use_path_remap(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote-root"
    share_root = tmp_path / "share-root"
    original_source = remote_root / "captures" / "source-chart.png"
    mounted_source = share_root / "captures" / "source-chart.png"
    mounted_source.parent.mkdir(parents=True)
    mounted_source.write_bytes(b"png-bytes")
    record = make_record(row_id=2)
    record.attachments = [original_source]

    copied = copy_attachments(
        tmp_path / "vault",
        record,
        AttachmentConfig(
            mode="preserve",
            path_remaps=(PathRemap(remote_root, share_root),),
        ),
    )

    digest = hashlib.sha1(str(original_source.expanduser()).encode("utf-8")).hexdigest()[:10]
    expected = tmp_path / "vault" / "attachments" / "2026-03-30" / f"source-chart_{digest}.png"
    assert copied == [expected]
    assert expected.read_bytes() == b"png-bytes"


def test_render_summary_block_uses_targeted_links_and_obsidian_embed_syntax(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    attachment = output_root / "attachments" / "2026-03-30" / "chart.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"png-bytes")

    block = render_summary_block(
        output_root,
        make_record(row_id=3, categories=["Tech", "Energy", "War and Geopolitics", "Rates and Fed", "Financials"]),
        [attachment],
    )

    assert "<!-- source-summary-id: 3 -->" in block
    assert "## 2026-03-30T14:05:00+00:00 — Agent CNBC" in block
    assert "**Tags:** #financial-news #daily-summary #source/agent-cnbc" in block
    assert "#topic/tech" in block
    assert "#topic/energy" in block
    assert "#topic/war-geopolitics" in block
    assert "#topic/rates-fed" in block
    assert "#topic/financials" in block
    assert "[[Sources/CNBC|CNBC]]" in block
    assert "[[Topics/Tech|Tech]]" in block
    assert "[[Topics/Energy|Energy]]" in block
    assert "[[Topics/War and Geopolitics|War and Geopolitics]]" in block
    assert "[[Topics/Rates and Fed|Rates and Fed]]" in block
    assert "[[Topics/Financials|Financials]]" in block
    assert "- Tickers: `MSFT`, `NVDA`" in block
    assert "![[attachments/2026-03-30/chart.png]]" in block


def test_append_summary_creates_indexes_without_global_home_links(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    source = tmp_path / "chart.png"
    write_noisy_png(source, size=(640, 480))

    first_path = append_summary(output_root, make_record(row_id=11, attachment=source), AttachmentConfig(mode="preserve"))
    second_path = append_summary(output_root, make_record(row_id=12, attachment=source), AttachmentConfig(mode="preserve"))

    assert first_path == second_path
    content = first_path.read_text(encoding="utf-8")
    assert content.startswith(
        "---\ndate: 2026-03-30\ntype: daily-summary\nweek: 2026-W14\ngenerated-by: financial-news-ingest\ncuration: imported\n"
    )
    assert "Generated import note. Keep durable narratives in weekly notes and topic MOCs" in content
    assert "[[Home]]" not in content
    assert "[[Sources/Home|Sources]]" not in content
    assert "[[Themes/Home|Themes]]" not in content
    assert content.count("# Financial News — 2026-03-30") == 1
    assert "source-summary-id: 11" in content
    assert "source-summary-id: 12" in content
    assert content.count("![[attachments/2026-03-30/") == 2

    source_index = source_note_path(output_root, "Agent CNBC")
    rates_topic = topic_note_path(output_root, "Rates and Fed")
    financials_topic = topic_note_path(output_root, "Financials")
    assert source_index.exists()
    assert rates_topic.exists()
    assert financials_topic.exists()
    source_text = source_index.read_text(encoding="utf-8")
    assert "## Heuristic topic MOCs" in source_text
    assert "[[2026-03/2026-03-30_summary|2026-03-30]]" in source_text
    assert "[[Topics/Rates and Fed|Rates and Fed]]" in source_text
    rates_text = rates_topic.read_text(encoding="utf-8")
    assert "## Sources" in rates_text
    assert "## Daily notes" in rates_text
    assert "[[Sources/CNBC|CNBC]]" in rates_text


def test_append_summary_creates_source_note_for_unknown_agents(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    append_summary(output_root, make_record(row_id=21, agent="Agent Custom Desk"), AttachmentConfig(mode="preserve"))

    source_index = source_note_path(output_root, "Agent Custom Desk")
    assert source_index.exists()
    assert "# Agent Custom Desk" in source_index.read_text(encoding="utf-8")


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
