from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
import re

from financial_news.models import SummaryRecord

LOGGER = logging.getLogger(__name__)


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


def copy_attachments(output_root: Path, record: SummaryRecord) -> list[Path]:
    stamp = summary_date(record)
    attachment_dir = output_root / "attachments" / stamp.strftime("%Y-%m-%d")
    attachment_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for source in record.attachments:
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
    lines = [
        "\n---\n",
        f"<!-- source-summary-id: {record.row_id} -->\n",
        f"## {timestamp_text} — {record.agent or 'unknown-agent'}\n\n",
        f"**Tags:** #financial-news #daily-summary #source/{source_tag}\n\n",
        f"- Source row id: `{record.row_id}`\n",
    ]
    if record.agent:
        lines.append(f"- Agent: `{record.agent}`\n")

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

    return "".join(lines)


def append_summary(output_root: Path, record: SummaryRecord) -> Path:
    destination = destination_markdown_path(output_root, record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    attachments = copy_attachments(output_root, record)
    block = render_summary_block(output_root, record, attachments)

    if not destination.exists():
        stamp = summary_date(record)
        title = stamp.strftime("%Y-%m-%d")
        note_header = (
            "---\n"
            f"date: {title}\n"
            "type: daily-summary\n"
            "tags:\n"
            "  - financial-news\n"
            "  - daily-summary\n"
            f"  - week/{stamp.strftime('%G-W%V')}\n"
            "---\n\n"
            "[[Home]] · [[Sources/Home|Sources]] · [[Themes/Home|Themes]] · [[Templates/Daily Summary Template|Template]]\n\n"
            f"# Financial News — {title}\n"
        )
        destination.write_text(note_header, encoding="utf-8")
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return destination
