from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from financial_news.config import AttachmentConfig, PathRemap
from financial_news.models import DecisionRecord, SummaryRecord
from financial_news.topics import topic_links_for_record, topic_slug

LOGGER = logging.getLogger(__name__)

FORMAT_SUFFIXES = {"webp": ".webp", "jpeg": ".jpg", "png": ".png"}
OPTIMIZABLE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
WIKI_LINK_PATTERN = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
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


def _safe_note_stem(value: str | None, fallback: str) -> str:
    raw = (value or fallback).strip()
    sanitized = re.sub(r"[\\/:*?\"<>|]+", " - ", raw)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    return sanitized or fallback


def _attachment_stem(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return sanitized or "attachment"


def summary_date(record: SummaryRecord) -> datetime:
    return record.effective_timestamp or datetime.now(timezone.utc)


def destination_markdown_path(output_root: Path, record: SummaryRecord) -> Path:
    stamp = summary_date(record)
    month_dir = output_root / stamp.strftime("%Y-%m")
    return month_dir / f"{stamp.strftime('%Y-%m-%d')}_summary.md"


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


def source_note_path(output_root: Path, agent: str | None) -> Path:
    source_profile = source_profile_link(agent)
    if source_profile:
        return output_root / f"{source_profile[0]}.md"
    return output_root / "Sources" / f"{_safe_note_stem(agent, 'Unknown Source')}.md"


def topic_note_path(output_root: Path, topic_name: str) -> Path:
    return output_root / "Topics" / f"{_safe_note_stem(topic_name, topic_slug(topic_name))}.md"


def _note_link(output_root: Path, note_path: Path, alias: str | None = None) -> str:
    relative = note_path.relative_to(output_root).with_suffix("")
    if alias:
        return f"[[{relative.as_posix()}|{alias}]]"
    return f"[[{relative.as_posix()}]]"


def _path_remap_matches(source: Path, remap: PathRemap) -> tuple[int, Path] | None:
    try:
        remainder = source.relative_to(remap.source)
    except ValueError:
        return None
    return len(remap.source.parts), remap.destination / remainder


def resolve_attachment_source(source: Path, attachment_config: AttachmentConfig | None = None) -> Path:
    config = attachment_config or AttachmentConfig()
    candidate = source.expanduser()
    best_match: tuple[int, int, Path] | None = None
    for index, remap in enumerate(config.path_remaps):
        matched = _path_remap_matches(candidate, remap)
        if matched is None:
            continue
        prefix_length, remapped_path = matched
        score = (prefix_length, index)
        if best_match is None or score >= (best_match[0], best_match[1]):
            best_match = (prefix_length, index, remapped_path)
    if best_match:
        return best_match[2]
    return candidate


def _should_optimize(source: Path, config: AttachmentConfig) -> bool:
    return config.mode == "optimize" and source.suffix.lower() in OPTIMIZABLE_IMAGE_SUFFIXES


def _format_save_kwargs(image_format: str, quality: int) -> dict[str, object]:
    if image_format == "webp":
        return {"format": "WEBP", "quality": quality, "method": 6}
    if image_format == "jpeg":
        return {"format": "JPEG", "quality": quality, "optimize": True, "progressive": True}
    return {"format": "PNG", "optimize": True}


def _prepare_image_for_save(image: Image.Image, image_format: str) -> Image.Image:
    if image_format == "jpeg":
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            background.paste(image.convert("RGBA"), mask=alpha)
            return background
        if image.mode == "P":
            return image.convert("RGB")
        if image.mode not in {"RGB", "L"}:
            return image.convert("RGB")
        return image
    if image_format in {"webp", "png"} and image.mode == "P":
        return image.convert("RGBA") if "transparency" in image.info else image.convert("RGB")
    return image


def _optimize_image(source: Path, target: Path, config: AttachmentConfig) -> bool:
    temp_target = target.with_name(f".{target.name}.tmp")
    try:
        with Image.open(source) as raw_image:
            if getattr(raw_image, "is_animated", False):
                return False
            working = ImageOps.exif_transpose(raw_image)
            working.load()
        working = _prepare_image_for_save(working, config.image_format)
        if max(working.size) > config.max_dimension:
            working = working.copy()
            working.thumbnail((config.max_dimension, config.max_dimension), Image.Resampling.LANCZOS)
        save_kwargs = _format_save_kwargs(config.image_format, config.image_quality)
        temp_target.parent.mkdir(parents=True, exist_ok=True)
        working.save(temp_target, **save_kwargs)
        if temp_target.stat().st_size >= source.stat().st_size:
            temp_target.unlink(missing_ok=True)
            return False
        os.replace(temp_target, target)
        return True
    except (OSError, UnidentifiedImageError) as exc:
        LOGGER.warning("Unable to optimize attachment %s: %s", source, exc)
        temp_target.unlink(missing_ok=True)
        return False


def _copy_single_attachment(
    attachment_dir: Path,
    original_source: Path,
    resolved_source: Path,
    attachment_config: AttachmentConfig,
) -> Path:
    digest = hashlib.sha1(str(original_source.expanduser()).encode("utf-8")).hexdigest()[:10]
    stem = _attachment_stem(original_source.stem or resolved_source.stem)

    if _should_optimize(resolved_source, attachment_config):
        optimized_target = attachment_dir / f"{stem}_{digest}{FORMAT_SUFFIXES[attachment_config.image_format]}"
        if not optimized_target.exists() and not _optimize_image(resolved_source, optimized_target, attachment_config):
            optimized_target.unlink(missing_ok=True)
        if optimized_target.exists():
            LOGGER.info("Optimized attachment %s -> %s", resolved_source, optimized_target)
            return optimized_target

    preserved_target = attachment_dir / f"{stem}_{digest}{resolved_source.suffix.lower()}"
    if not preserved_target.exists():
        preserved_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved_source, preserved_target)
        LOGGER.info("Copied attachment %s -> %s", resolved_source, preserved_target)
    return preserved_target


def copy_attachments(
    output_root: Path,
    record: SummaryRecord,
    attachment_config: AttachmentConfig | None = None,
) -> list[Path]:
    config = attachment_config or AttachmentConfig()
    stamp = summary_date(record)
    attachment_dir = output_root / "attachments" / stamp.strftime("%Y-%m-%d")
    copied: list[Path] = []
    for original_source in record.attachments:
        resolved_source = resolve_attachment_source(original_source, config)
        if not resolved_source.exists() or not resolved_source.is_file():
            LOGGER.warning(
                "Skipping missing attachment for row %s: %s (resolved from %s)",
                record.row_id,
                resolved_source,
                original_source,
            )
            continue
        copied.append(_copy_single_attachment(attachment_dir, original_source, resolved_source, config))
    return copied


def _upsert_section_lines(path: Path, heading: str, lines: list[str], *, initial_content: str) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text(encoding="utf-8") if path.exists() else initial_content.rstrip() + "\n"
    marker = f"## {heading}\n"
    if marker not in content:
        content = content.rstrip() + f"\n\n{marker}"

    section_start = content.index(marker) + len(marker)
    section_end = content.find("\n## ", section_start)
    if section_end == -1:
        section_end = len(content)

    section_body = content[section_start:section_end]
    existing_lines = {line.rstrip() for line in section_body.splitlines() if line.strip()}
    additions = [line for line in lines if line.rstrip() not in existing_lines]
    if not additions:
        return

    updated_section = section_body.rstrip("\n")
    if updated_section:
        updated_section += "\n"
    updated_section += "\n".join(additions) + "\n"
    updated_content = content[:section_start] + updated_section + content[section_end:]
    path.write_text(updated_content, encoding="utf-8")


def _source_note_header(agent: str | None) -> str:
    source_profile = source_profile_link(agent)
    display_name = source_profile[1] if source_profile else (agent or "Unknown Source")
    slug = _agent_slug(display_name)
    return (
        "---\n"
        "tags:\n"
        "  - financial-news\n"
        f"  - source/{slug}\n"
        "  - autogenerated\n"
        "---\n\n"
        f"# {display_name}\n\n"
        "Auto-maintained source note created by the ingester.\n"
    )


def _topic_note_header(topic_name: str) -> str:
    return (
        "---\n"
        "tags:\n"
        "  - financial-news\n"
        "  - topic\n"
        f"  - topic/{topic_slug(topic_name)}\n"
        "  - autogenerated\n"
        "---\n\n"
        f"# {topic_name}\n\n"
        "> Auto-maintained topic MOC created by the ingester. Curate and tighten as themes become durable.\n"
    )


def update_indexes(output_root: Path, record: SummaryRecord, destination: Path) -> None:
    source_path = source_note_path(output_root, record.agent)
    source_link = _note_link(output_root, source_path, alias=source_profile_link(record.agent)[1] if source_profile_link(record.agent) else (record.agent or "Unknown Source"))
    daily_note_link = _note_link(output_root, destination, alias=summary_date(record).strftime("%Y-%m-%d"))
    topic_links = [
        _note_link(output_root, topic_note_path(output_root, label), alias=label)
        for _, label in topic_links_for_record(record)
    ]

    source_daily_line = f"- {daily_note_link} — row `{record.row_id}`"
    if topic_links:
        source_daily_line += f" · {', '.join(topic_links)}"

    _upsert_section_lines(
        source_path,
        "Heuristic topic MOCs",
        [f"- {link}" for link in topic_links],
        initial_content=_source_note_header(record.agent),
    )
    _upsert_section_lines(
        source_path,
        "Daily notes",
        [source_daily_line],
        initial_content=_source_note_header(record.agent),
    )

    for _, topic_name in topic_links_for_record(record):
        topic_path = topic_note_path(output_root, topic_name)
        topic_daily_line = f"- {daily_note_link} — {source_link} · row `{record.row_id}`"
        _upsert_section_lines(
            topic_path,
            "Sources",
            [f"- {source_link}"],
            initial_content=_topic_note_header(topic_name),
        )
        _upsert_section_lines(
            topic_path,
            "Daily notes",
            [topic_daily_line],
            initial_content=_topic_note_header(topic_name),
        )


def render_summary_block(output_root: Path, record: SummaryRecord, attachments: list[Path]) -> str:
    stamp = summary_date(record)
    timestamp_text = stamp.isoformat()
    source_tag = _agent_slug(record.agent)
    source_path = source_note_path(output_root, record.agent)
    source_alias = source_profile_link(record.agent)[1] if source_profile_link(record.agent) else (record.agent or "Unknown Source")
    source_link = _note_link(output_root, source_path, alias=source_alias)
    topic_links = topic_links_for_record(record)
    topic_note_links = [f"[[{target}|{label}]]" for target, label in topic_links]
    topic_tags = [f"#topic/{topic_slug(label)}" for _, label in topic_links]
    tags = ["#financial-news", "#daily-summary", f"#source/{source_tag}", *topic_tags]

    lines = [
        "\n---\n",
        f"<!-- source-summary-id: {record.row_id} -->\n",
        f"## {timestamp_text} — {record.agent or 'unknown-agent'}\n\n",
        f"**Tags:** {' '.join(tags)}\n\n",
        f"- Source row id: `{record.row_id}`\n",
        f"- Source note: {source_link}\n",
    ]
    if record.agent:
        lines.append(f"- Agent: `{record.agent}`\n")
    if record.tickers:
        lines.append(f"- Tickers: {', '.join(f'`{ticker}`' for ticker in record.tickers)}\n")
    if topic_note_links:
        lines.append(f"- Topic MOCs (heuristic): {' · '.join(topic_note_links)}\n")

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

    if not topic_note_links:
        lines.append("\n> _No topic MOCs matched heuristically; promote this into a manual topic note only if it becomes durable._\n")

    return "".join(lines)


def append_summary(
    output_root: Path,
    record: SummaryRecord,
    attachment_config: AttachmentConfig | None = None,
) -> Path:
    destination = destination_markdown_path(output_root, record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    attachments = copy_attachments(output_root, record, attachment_config)
    block = render_summary_block(output_root, record, attachments)

    if not destination.exists():
        stamp = summary_date(record)
        title = stamp.strftime("%Y-%m-%d")
        note_header = (
            "---\n"
            f"date: {title}\n"
            "type: daily-summary\n"
            f"week: {stamp.strftime('%G-W%V')}\n"
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
    update_indexes(output_root, record, destination)
    return destination


WEEKLY_INSIGHTS_START = "<!-- financial-news-weekly-insights:start -->"
WEEKLY_INSIGHTS_END = "<!-- financial-news-weekly-insights:end -->"


def week_note_path(output_root: Path, week_key: str) -> Path:
    return output_root / "Themes" / f"{week_key}.md"


def _display_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "undated"


def _top_items(counter: Counter[str], limit: int = 6) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold(), item[0]))[:limit]


def _first_sentence(values: list[str], fallback: str = "No extracted detail") -> str:
    for value in values:
        text = re.sub(r"\s+", " ", value).strip()
        if text:
            return text
    return fallback


def build_weekly_insights_section(
    *,
    week_key: str,
    start_date: str,
    end_date: str,
    summaries: list[SummaryRecord],
    decisions: list[DecisionRecord] | None = None,
    generated_at: datetime | None = None,
) -> str:
    decisions = decisions or []
    generated_at = generated_at or datetime.now(timezone.utc)
    source_counts: Counter[str] = Counter(record.agent or "Unknown Source" for record in summaries)
    ticker_counts: Counter[str] = Counter(ticker for record in summaries for ticker in record.tickers)
    topic_counts: Counter[str] = Counter(topic for record in summaries for topic in record.categories)
    decision_action_counts: Counter[str] = Counter(decision.action or "Unspecified" for decision in decisions)

    lines = [
        WEEKLY_INSIGHTS_START,
        f"> Generated {generated_at.isoformat()} from `{len(summaries)}` summary rows and `{len(decisions)}` decision rows for `{week_key}` ({start_date} → {end_date}).",
        "",
        "### Executive takeaways",
    ]
    if summaries:
        top_topics = ", ".join(f"{name} ({count})" for name, count in _top_items(topic_counts, 4)) or "no durable topic concentration"
        top_tickers = ", ".join(f"`{name}` ({count})" for name, count in _top_items(ticker_counts, 5)) or "no extracted tickers"
        top_sources = ", ".join(f"{name} ({count})" for name, count in _top_items(source_counts, 4))
        lines.extend(
            [
                f"- **Theme concentration:** {top_topics}.",
                f"- **Ticker focus:** {top_tickers}.",
                f"- **Source coverage:** {top_sources}.",
            ]
        )
    else:
        lines.append("- No summaries were available from the database for this week.")

    if decisions:
        actions = ", ".join(f"{name} ({count})" for name, count in _top_items(decision_action_counts, 4))
        decision_tickers = ", ".join(
            f"`{name}` ({count})" for name, count in _top_items(Counter(d.ticker for d in decisions if d.ticker), 5)
        ) or "no ticker-specific decisions"
        lines.append(f"- **Decision context:** {actions}; ticker exposure: {decision_tickers}.")
    else:
        lines.append("- **Decision context:** no decision rows were available for this week.")

    lines.extend(["", "### Key themes"])
    if topic_counts:
        for topic, count in _top_items(topic_counts, 8):
            lines.append(f"- [[Topics/{topic}|{topic}]] — {count} mentions")
    else:
        lines.append("- _No heuristic themes extracted._")

    lines.extend(["", "### Most-mentioned tickers"])
    if ticker_counts:
        lines.append("- " + ", ".join(f"`{ticker}` ({count})" for ticker, count in _top_items(ticker_counts, 12)))
    else:
        lines.append("- _No tickers extracted._")

    lines.extend(["", "### Summary highlights"])
    if summaries:
        for record in sorted(summaries, key=lambda item: ((item.effective_timestamp.isoformat() if item.effective_timestamp else ""), item.row_id))[:12]:
            headline = _first_sentence(record.headlines, fallback="No headline extracted")
            insight = _first_sentence(record.insights, fallback="No insight extracted")
            day = _display_date(record.effective_timestamp)
            source = source_profile_link(record.agent)
            source_text = f"[[{source[0]}|{source[1]}]]" if source else (record.agent or "Unknown Source")
            lines.append(f"- **{day}** — {source_text}: {headline} — {insight}")
    else:
        lines.append("- _No summary rows available._")

    lines.extend(["", "### Decisions"])
    if decisions:
        for decision in sorted(decisions, key=lambda item: ((item.effective_timestamp.isoformat() if item.effective_timestamp else ""), item.row_id))[:12]:
            day = _display_date(decision.effective_timestamp)
            ticker = f"`{decision.ticker}` " if decision.ticker else ""
            action = decision.action or "decision"
            confidence = f" · confidence: {decision.confidence}" if decision.confidence else ""
            rationale = _first_sentence(decision.rationale, fallback="No rationale extracted")
            lines.append(f"- **{day}** — {ticker}{action}{confidence}: {rationale}")
    else:
        lines.append("- _No decision rows available._")

    lines.append(WEEKLY_INSIGHTS_END)
    return "\n".join(lines) + "\n"


def upsert_weekly_insights(
    output_root: Path,
    *,
    week_key: str,
    start_date: str,
    end_date: str,
    summaries: list[SummaryRecord],
    decisions: list[DecisionRecord] | None = None,
) -> Path:
    path = week_note_path(output_root, week_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    section = build_weekly_insights_section(
        week_key=week_key,
        start_date=start_date,
        end_date=end_date,
        summaries=summaries,
        decisions=decisions or [],
    )
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = (
            "---\n"
            "tags:\n"
            "  - financial-news\n"
            "  - weekly-summary\n"
            f"  - week/{week_key}\n"
            "generated-by: financial-news-ingest\n"
            "curation: generated\n"
            "---\n\n"
            f"# Financial News Weekly Summary — {week_key}\n\n"
        )
    if WEEKLY_INSIGHTS_START in content and WEEKLY_INSIGHTS_END in content:
        start = content.index(WEEKLY_INSIGHTS_START)
        end = content.index(WEEKLY_INSIGHTS_END, start) + len(WEEKLY_INSIGHTS_END)
        content = content[:start] + section.rstrip() + content[end:]
        if not content.endswith("\n"):
            content += "\n"
    else:
        content = content.rstrip() + "\n\n" + section
    path.write_text(content, encoding="utf-8")
    return path


def _normalize_attachment_reference(reference: str) -> str | None:
    cleaned = reference.strip().strip("<>")
    if not cleaned:
        return None
    cleaned = cleaned.split("|", 1)[0].strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned.startswith("attachments/"):
        return cleaned
    return None


def collect_attachment_references(output_root: Path) -> set[str]:
    references: set[str] = set()
    for note in output_root.rglob("*.md"):
        if note.is_relative_to(output_root / "attachments"):
            continue
        content = note.read_text(encoding="utf-8")
        for raw_reference in WIKI_LINK_PATTERN.findall(content):
            normalized = _normalize_attachment_reference(raw_reference)
            if normalized:
                references.add(normalized)
        for raw_reference in MARKDOWN_LINK_PATTERN.findall(content):
            normalized = _normalize_attachment_reference(raw_reference)
            if normalized:
                references.add(normalized)
    return references


def prune_orphan_attachments(output_root: Path) -> list[Path]:
    attachment_root = output_root / "attachments"
    if not attachment_root.exists():
        return []

    referenced = collect_attachment_references(output_root)
    pruned: list[Path] = []
    for candidate in sorted(path for path in attachment_root.rglob("*") if path.is_file()):
        relative = candidate.relative_to(output_root).as_posix()
        if relative in referenced:
            continue
        candidate.unlink()
        pruned.append(candidate)

    for directory in sorted((path for path in attachment_root.rglob("*") if path.is_dir()), reverse=True):
        if any(directory.iterdir()):
            continue
        directory.rmdir()
    return pruned
