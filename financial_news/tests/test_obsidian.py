from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from financial_news.models import SummaryRecord
from financial_news.obsidian import (
    append_summary,
    copy_attachments,
    destination_markdown_path,
    remap_attachment_path,
    render_summary_block,
    source_profile_link,
)


def make_record(*, row_id: int, attachment: Path | None = None) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 14, 5, tzinfo=timezone.utc),
        agent="Agent CNBC",
        content_timestamp=None,
        headlines=["Palo Alto Networks leads software strength as Powell calms rates"],
        insights=["Oil/Hormuz headlines remain the macro risk while FNMA and BX respond to easier Fed expectations."],
        attachments=[attachment] if attachment else [],
        raw_content={"ok": True},
    )


def test_destination_markdown_path_uses_month_partition(tmp_path: Path) -> None:
    destination = destination_markdown_path(tmp_path, make_record(row_id=1))
    assert destination == tmp_path / "2026-03" / "2026-03-30_summary.md"


def test_remap_attachment_path_swaps_remote_prefix_for_share_mount(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote-root"
    share_root = tmp_path / "share-root"
    source = remote_root / "captures" / "chart.png"

    remapped = remap_attachment_path(source, [(remote_root, share_root)])

    assert remapped == share_root / "captures" / "chart.png"


def test_copy_attachments_can_use_path_remap_and_skips_missing_ones(tmp_path: Path, caplog) -> None:
    caplog.set_level("INFO")
    remote_root = tmp_path / "remote-root"
    share_root = tmp_path / "share-root"
    mounted_source = share_root / "captures" / "source-chart.png"
    mounted_source.parent.mkdir(parents=True)
    mounted_source.write_bytes(b"png-bytes")
    missing = remote_root / "captures" / "missing-chart.png"
    record = make_record(row_id=2)
    record.attachments = [remote_root / "captures" / "source-chart.png", missing]

    copied = copy_attachments(tmp_path / "vault", record, path_remaps=[(remote_root, share_root)])

    digest = hashlib.sha1(str(mounted_source.resolve()).encode("utf-8")).hexdigest()[:10]
    expected = tmp_path / "vault" / "attachments" / "2026-03-30" / f"source-chart_{digest}.png"
    assert copied == [expected]
    assert expected.read_bytes() == b"png-bytes"
    assert "Remapped attachment path" in caplog.text
    assert "Skipping missing attachment" in caplog.text


def test_source_profile_link_maps_known_agents() -> None:
    assert source_profile_link("Agent_CNBC") == ("Sources/CNBC", "CNBC")
    assert source_profile_link("Agent Fox Business") == ("Sources/Fox Business", "Fox Business")
    assert source_profile_link("Custom Agent") is None


def test_render_summary_block_adds_source_profiles_and_topic_mocs(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    attachment = output_root / "attachments" / "2026-03-30" / "chart.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"png-bytes")

    block = render_summary_block(output_root, make_record(row_id=3), [attachment])

    assert "<!-- source-summary-id: 3 -->" in block
    assert "## 2026-03-30T14:05:00+00:00 — Agent CNBC" in block
    assert "**Tags:** #financial-news #daily-summary #source/agent-cnbc" in block
    assert "- Source profile: [[Sources/CNBC|CNBC]]" in block
    assert "[[Topics/Tech|Tech]]" in block
    assert "[[Topics/Energy|Energy]]" in block
    assert "[[Topics/War and Geopolitics|War and Geopolitics]]" in block
    assert "[[Topics/Rates and Fed|Rates and Fed]]" in block
    assert "[[Topics/Financials|Financials]]" in block
    assert "### Headlines" in block
    assert "### Insights" in block
    assert "![[attachments/2026-03-30/chart.png]]" in block


def test_append_summary_creates_header_once_without_global_home_links(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    source = tmp_path / "chart.png"
    source.write_bytes(b"png-bytes")

    first_path = append_summary(output_root, make_record(row_id=11, attachment=source))
    second_path = append_summary(output_root, make_record(row_id=12, attachment=source))

    assert first_path == second_path
    content = first_path.read_text(encoding="utf-8")
    assert content.startswith("---\ndate: 2026-03-30\ntype: daily-summary\ngenerated-by: financial-news-ingest\ncuration: imported\n")
    assert "Generated import note. Keep durable narratives in weekly notes and topic MOCs" in content
    assert "[[Home]]" not in content
    assert "[[Sources/Home|Sources]]" not in content
    assert "[[Themes/Home|Themes]]" not in content
    assert content.count("# Financial News — 2026-03-30") == 1
    assert "source-summary-id: 11" in content
    assert "source-summary-id: 12" in content
    assert content.count("![[attachments/2026-03-30/") == 2
