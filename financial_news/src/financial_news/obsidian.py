from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil

from PIL import Image, ImageOps, UnidentifiedImageError

from financial_news.config import AttachmentConfig, PathRemap
from financial_news.models import SummaryRecord
from financial_news.topics import topic_slug

LOGGER = logging.getLogger(__name__)

FORMAT_SUFFIXES = {"webp": ".webp", "jpeg": ".jpg", "png": ".png"}
OPTIMIZABLE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
WIKI_LINK_PATTERN = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


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


def source_note_path(output_root: Path, agent: str | None) -> Path:
    return output_root / "Sources" / f"{_safe_note_stem(agent, 'Unknown Source')}.md"


def category_note_path(output_root: Path, category: str) -> Path:
    return output_root / "Categories" / f"{topic_slug(category)}.md"


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
    display_name = agent or "Unknown Source"
    slug = _agent_slug(agent)
    return (
        "---\n"
        "type: source-index\n"
        f"source: {display_name}\n"
        "tags:\n"
        "  - financial-news\n"
        "  - source-index\n"
        f"  - source/{slug}\n"
        "---\n\n"
        f"# {display_name}\n\n"
        "Auto-maintained source index for ingested financial-news summaries.\n"
    )


def _category_note_header(category: str) -> str:
    return (
        "---\n"
        "type: category-index\n"
        f"category: {category}\n"
        "classification: heuristic\n"
        "tags:\n"
        "  - financial-news\n"
        "  - category-index\n"
        f"  - topic/{topic_slug(category)}\n"
        "---\n\n"
        f"# {category}\n\n"
        "> Heuristic topic bucket maintained by the ingester. Expect keyword-driven false positives/negatives.\n"
    )


def update_indexes(output_root: Path, record: SummaryRecord, destination: Path) -> None:
    source_path = source_note_path(output_root, record.agent)
    daily_note_link = _note_link(output_root, destination, alias=summary_date(record).strftime("%Y-%m-%d"))
    category_links = [_note_link(output_root, category_note_path(output_root, category), alias=category) for category in record.categories]

    source_daily_line = f"- {daily_note_link} — row `{record.row_id}`"
    if category_links:
        source_daily_line += f" · {', '.join(category_links)}"

    _upsert_section_lines(
        source_path,
        "Heuristic categories",
        [f"- {link}" for link in category_links],
        initial_content=_source_note_header(record.agent),
    )
    _upsert_section_lines(
        source_path,
        "Daily notes",
        [source_daily_line],
        initial_content=_source_note_header(record.agent),
    )

    source_link = _note_link(output_root, source_path, alias=record.agent or "Unknown Source")
    for category in record.categories:
        category_path = category_note_path(output_root, category)
        category_daily_line = f"- {daily_note_link} — {source_link} · row `{record.row_id}`"
        _upsert_section_lines(
            category_path,
            "Sources",
            [f"- {source_link}"],
            initial_content=_category_note_header(category),
        )
        _upsert_section_lines(
            category_path,
            "Daily notes",
            [category_daily_line],
            initial_content=_category_note_header(category),
        )


def render_summary_block(output_root: Path, record: SummaryRecord, attachments: list[Path]) -> str:
    stamp = summary_date(record)
    timestamp_text = stamp.isoformat()
    source_tag = _agent_slug(record.agent)
    topic_tags = [f"#topic/{topic_slug(category)}" for category in record.categories]
    source_link = _note_link(output_root, source_note_path(output_root, record.agent), alias=record.agent or "Unknown Source")
    category_links = [_note_link(output_root, category_note_path(output_root, category), alias=category) for category in record.categories]
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
    if category_links:
        lines.append(f"- Topics (heuristic): {', '.join(category_links)}\n")

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
            "tags:\n"
            "  - financial-news\n"
            "  - daily-summary\n"
            f"  - week/{stamp.strftime('%G-W%V')}\n"
            "---\n\n"
            f"# Financial News — {title}\n"
        )
        destination.write_text(note_header, encoding="utf-8")
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(block)
    update_indexes(output_root, record, destination)
    return destination


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
