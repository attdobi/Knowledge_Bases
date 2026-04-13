from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from financial_news.models import SummaryRecord

AGENT_KEYS = ("agent", "source_agent", "assistant", "author", "bot", "model")
TIMESTAMP_KEYS = ("timestamp", "created_at", "generated_at", "published_at", "datetime", "date", "time")
HEADLINE_KEYS = ("headlines", "headline", "top_headlines", "news", "articles", "stories")
INSIGHT_KEYS = ("insights", "analysis", "key_insights", "takeaways", "observations", "summary", "summaries")
ATTACHMENT_KEYS = (
    "attachments",
    "files",
    "file_paths",
    "file_path",
    "images",
    "image_paths",
    "image_path",
    "charts",
    "screenshots",
    "screenshot_paths",
    "screenshot_path",
    "media",
)
TEXTISH_ITEM_KEYS = ("title", "headline", "name", "text", "summary", "insight", "description", "path", "file", "url")


def normalize_content(content: Any) -> dict[str, Any] | list[Any] | str | None:
    if content is None:
        return None
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return text
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return text
        return decoded
    return str(content)


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                continue
    return None


def flatten_strings(value: Any) -> list[str]:
    items: list[str] = []
    if value is None:
        return items
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            items.append(stripped)
        return items
    if isinstance(value, dict):
        for key in TEXTISH_ITEM_KEYS:
            if key in value:
                items.extend(flatten_strings(value[key]))
        return items
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            items.extend(flatten_strings(item))
    return items


def dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from walk_dicts(item)


def find_first_scalar(content: Any, keys: tuple[str, ...]) -> Any:
    for item in walk_dicts(content):
        for key in keys:
            if key in item and item[key] not in (None, "", [], {}):
                return item[key]
    return None


def find_collection(content: Any, keys: tuple[str, ...]) -> list[str]:
    for item in walk_dicts(content):
        for key in keys:
            if key in item:
                values = dedupe_keep_order(flatten_strings(item[key]))
                if values:
                    return values
    return []


def looks_like_local_path(value: str) -> bool:
    if value.startswith(("http://", "https://", "data:")):
        return False
    return value.startswith(("/", "./", "../", "~")) or len(Path(value).parts) > 1


def find_attachments(content: Any) -> list[Path]:
    found: list[str] = []
    for item in walk_dicts(content):
        for key in ATTACHMENT_KEYS:
            if key in item:
                found.extend(flatten_strings(item[key]))
    return [Path(path).expanduser() for path in dedupe_keep_order(found) if looks_like_local_path(path)]


def parse_summary_record(
    row_id: int,
    row_timestamp: datetime | None,
    content: dict[str, Any] | list[Any] | str | None,
    fallback_agent: str | None = None,
) -> SummaryRecord:
    agent_value = find_first_scalar(content, AGENT_KEYS) or fallback_agent
    timestamp_value = find_first_scalar(content, TIMESTAMP_KEYS)
    headlines = find_collection(content, HEADLINE_KEYS)
    insights = find_collection(content, INSIGHT_KEYS)
    attachments = find_attachments(content)

    if not headlines and isinstance(content, dict):
        headlines = dedupe_keep_order(flatten_strings(content.get("headlines") or content.get("headline")))
    if not insights and isinstance(content, dict):
        insights = dedupe_keep_order(flatten_strings(content.get("insights") or content.get("summary") or content.get("analysis")))

    agent = str(agent_value).strip() if agent_value not in (None, "") else None
    content_timestamp = parse_datetime(timestamp_value)
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=row_timestamp,
        agent=agent,
        content_timestamp=content_timestamp,
        headlines=headlines,
        insights=insights,
        attachments=attachments,
        raw_content=content,
    )
