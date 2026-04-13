from __future__ import annotations

import hashlib
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from financial_news.models import SummaryRecord
from financial_news.topics import topic_links_for_record

LOGGER = logging.getLogger(__name__)

SOURCE_PROFILE_TARGETS = {
    "cnbc": "CNBC",
    "fox business": "Fox Business",
    "bbc business": "BBC Business",
    "ap business": "AP Business",
    "yahoo finance": "Yahoo Finance",
    "benzinga": "Benzinga",
}


def _agent_slug(agent: str | None) -> str:
    raw = (agent or "unknown-agent").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "unknown-agent"


def summary_date(record: SummaryRecord) -> datetime:
    return record.effective_timestamp or datetime.now(timezone.utc)


def destination_markdown_path(output_root: Path, record: SummaryRecord) -> Path:
    stamp = summary_date(record)
    month_dir = output_root / stamp.strftime("%Y-%m")
    return month_dir / f"{stamp.strftime('%Y-%m-%d')}_summary.md"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def remap_attachment_path(source: Path, path_remaps: Sequence[tuple[Path, Path]] | None = None) -> Path:
    expanded = source.expanduser()
    for source_prefix, target_prefix in path_remaps or []:
        if _is_relative_to(expanded, source_prefix):
            remapped = target_prefix / expanded.relative_to(source_prefix)
            LOGGER.info("Remapped attachment path %s -> %s", source, remapped)
            return remapped
    return expanded


def source_profile_link(agent: str | None) -> tuple[str, str] | None:
    if not agent:
        return None
    normalized = agent.replace("_", " ").replace("-", " ").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"^agent\s+", "", normalized)
    target = SOURCE_PROFILE_TARGETS.get(normalized)
    if not target:
        return None
    return (f"Sources/{target}", target)


def copy_attachments(
    output_root: Path,
    record: SummaryRecord,
    path_remaps: Sequence[tuple[Path, Path]] | None = None,
) -> list[Path]:
    stamp = summary_date(record)
    attachment_dir = output_root / "attachments" / stamp.strftime("%Y-%m-%d")
    attachment_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for original_source in record.attachments:
        source = remap_attachment_path(original_source, path_remaps)
        if not source.exists() or not source.is_file():
            LOGGER.warning("Skipping missing attachment for row %s: %s", record.row_id, source)
            continue
        digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
        target_name = f"{source.stem}_{digest}{source.suffix}"
        target = attachment_dir / target_name
        if not target.exists():
            shutil.copy2(source, target)
            LOGGER.info("Copied attachment %s -> %s", source, target)
        copied.append(target)
    return copied


def render_summary_block(output_root: Path, record: SummaryRecord, attachments: list[Path]) -> str:
    stamp = summary_date(record)
    timestamp_text = stamp.isoformat()
    source_tag = _agent_slug(record.agent)
    topic_links = topic_links_for_record(record)
    source_profile = source_profile_link(record.agent)
    lines = [
        "\n---\n",
        f"<!-- source-summary-id: {record.row_id} -->\n",
        f"## {timestamp_text} — {record.agent or 'unknown-agent'}\n\n",
        f"**Tags:** #financial-news #daily-summary #source/{source_tag}\n\n",
        f"- Source row id: `{record.row_id}`\n",
    ]
    if record.agent:
        lines.append(f"- Agent: `{record.agent}`\n")
    if source_profile:
        lines.append(f"- Source profile: [[{source_profile[0]}|{source_profile[1]}]]\n")
    if topic_links:
        joined = " · ".join(f"[[{target}|{label}]]" for target, label in topic_links)
        lines.append(f"- Topic MOCs (heuristic): {joined}\n")

    lines.append("\n### Headlines\n")
    if record.headlines:
        lines.extend(f"- {headline}\n" for headline in record.headlines)
    else:
        lines.append("- _No headlines extracted_\n")

    lines.append("\n### Insights\n")
    if record.insights:
        lines.extend(f"- {insight}\n" for insight in record.insights)
    else:
        lines.append("- _No insights extracted_\n")

    if attachments:
        lines.append("\n### Attachments\n")
        for attachment in attachments:
            rel_path = attachment.relative_to(output_root)
            lines.append(f"![[{rel_path.as_posix()}]]\n")

    if not topic_links:
        lines.append("\n> _No topic MOCs matched heuristically; promote this into a manual topic note only if it becomes durable._\n")

    return "".join(lines)


def append_summary(
    output_root: Path,
    record: SummaryRecord,
    path_remaps: Sequence[tuple[Path, Path]] | None = None,
) -> Path:
    destination = destination_markdown_path(output_root, record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    attachments = copy_attachments(output_root, record, path_remaps=path_remaps)
    block = render_summary_block(output_root, record, attachments)

    if not destination.exists():
        stamp = summary_date(record)
        title = stamp.strftime("%Y-%m-%d")
        note_header = (
            "---\n"
            f"date: {title}\n"
            "type: daily-summary\n"
            "generated-by: financial-news-ingest\n"
            "curation: imported\n"
            "tags:\n"
            "  - financial-news\n"
            "  - daily-summary\n"
            f"  - week/{stamp.strftime('%G-W%V')}\n"
            "---\n\n"
            f"# Financial News — {title}\n"
            "\n> Generated import note. Keep durable narratives in weekly notes and topic MOCs instead of turning this page into a hub.\n"
        )
        destination.write_text(note_header, encoding="utf-8")
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return destination
