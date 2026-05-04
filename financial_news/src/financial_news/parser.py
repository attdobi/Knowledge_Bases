from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Iterable

from financial_news.models import DecisionRecord, SummaryRecord
from financial_news.topics import classify_topics

AGENT_KEYS = ("agent", "source_agent", "assistant", "author", "bot", "model")
TIMESTAMP_KEYS = ("timestamp", "created_at", "generated_at", "published_at", "datetime", "date", "time")
HEADLINE_KEYS = ("headlines", "headline", "top_headlines", "news", "articles", "stories")
INSIGHT_KEYS = ("insights", "analysis", "key_insights", "takeaways", "observations", "summary", "summaries")
DECISION_AGENT_KEYS = AGENT_KEYS
DECISION_TIMESTAMP_KEYS = TIMESTAMP_KEYS
DECISION_ACTION_KEYS = ("action", "decision", "signal", "recommendation", "stance", "rating")
DECISION_CONFIDENCE_KEYS = ("confidence", "conviction", "score", "probability")
DECISION_RATIONALE_KEYS = ("rationale", "reasoning", "reason", "analysis", "summary", "notes", "explanation")

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
TICKER_KEYS = (
    "ticker",
    "tickers",
    "symbol",
    "symbols",
    "stock_symbol",
    "stock_symbols",
    "ticker_symbol",
    "ticker_symbols",
)
TICKER_SPLIT_RE = re.compile(r"[\s,;|/]+")
TICKER_TOKEN_RE = re.compile(r"\$?[A-Za-z][A-Za-z0-9]{0,5}(?:[.-][A-Za-z0-9]{1,5})?")
PARENTHETICAL_TICKER_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9]{0,5}(?:[.-][A-Za-z0-9]{1,5})?)\)")
EXPLICIT_TICKER_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,5}(?:[.-][A-Za-z0-9]{1,5})?)")


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


def is_ticker_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().lower())
    return any(candidate in normalized for candidate in TICKER_KEYS)


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


def normalize_ticker(value: str) -> str | None:
    candidate = value.strip().upper().lstrip("$").strip("()[]{}<>'\".,:;!?")
    if not candidate or not TICKER_TOKEN_RE.fullmatch(candidate):
        return None
    return candidate


def extract_tickers_from_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        matches: list[str] = []
        if any(separator in stripped for separator in (",", ";", "|", "/", "\n")) or stripped.upper() == stripped:
            matches.extend(filter(None, (normalize_ticker(token) for token in TICKER_SPLIT_RE.split(stripped))))
        matches.extend(filter(None, (normalize_ticker(token) for token in PARENTHETICAL_TICKER_RE.findall(stripped))))
        matches.extend(filter(None, (normalize_ticker(token) for token in EXPLICIT_TICKER_RE.findall(stripped))))
        normalized = normalize_ticker(stripped)
        if normalized:
            matches.append(normalized)
        return dedupe_keep_order(matches)
    if isinstance(value, dict):
        prioritized: list[str] = []
        for key, nested in value.items():
            if is_ticker_key(key):
                prioritized.extend(extract_tickers_from_value(nested))
        if prioritized:
            return dedupe_keep_order(prioritized)
        for fallback_key in ("value", "code", "id"):
            if fallback_key in value:
                extracted = extract_tickers_from_value(value[fallback_key])
                if extracted:
                    return extracted
        return []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        matches: list[str] = []
        for item in value:
            matches.extend(extract_tickers_from_value(item))
        return dedupe_keep_order(matches)
    return []


def find_tickers(content: Any) -> list[str]:
    found: list[str] = []
    if isinstance(content, dict):
        for key, value in content.items():
            if is_ticker_key(key):
                found.extend(extract_tickers_from_value(value))
            found.extend(find_tickers(value))
    elif isinstance(content, list):
        for item in content:
            found.extend(find_tickers(item))
    return dedupe_keep_order(found)


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
    tickers = find_tickers(content)

    if not headlines and isinstance(content, dict):
        headlines = dedupe_keep_order(flatten_strings(content.get("headlines") or content.get("headline")))
    if not insights and isinstance(content, dict):
        insights = dedupe_keep_order(flatten_strings(content.get("insights") or content.get("summary") or content.get("analysis")))

    agent = str(agent_value).strip() if agent_value not in (None, "") else None
    content_timestamp = parse_datetime(timestamp_value)
    categories = classify_topics(agent, headlines, insights, tickers)
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=row_timestamp,
        agent=agent,
        content_timestamp=content_timestamp,
        headlines=headlines,
        insights=insights,
        attachments=attachments,
        tickers=tickers,
        categories=categories,
        raw_content=content,
    )


def _first_string(content: Any, keys: tuple[str, ...]) -> str | None:
    value = find_first_scalar(content, keys)
    if value in (None, "", [], {}):
        return None
    strings = flatten_strings(value)
    return strings[0] if strings else str(value).strip() or None


def parse_decision_record(
    row_id: int,
    row_timestamp: datetime | None,
    content: dict[str, Any] | list[Any] | str | None,
    *,
    fallback_agent: str | None = None,
    fallback_action: str | None = None,
    fallback_ticker: str | None = None,
) -> DecisionRecord:
    """Parse a trading/portfolio decision row into a compact weekly-insight record.

    The real DB has changed shape a few times, so this parser stays intentionally
    tolerant: it prefers obvious structured keys, then falls back to scalar columns
    supplied by the DB adapter.
    """
    agent_value = find_first_scalar(content, DECISION_AGENT_KEYS) or fallback_agent
    timestamp_value = find_first_scalar(content, DECISION_TIMESTAMP_KEYS)
    action = _first_string(content, DECISION_ACTION_KEYS) or fallback_action
    confidence = _first_string(content, DECISION_CONFIDENCE_KEYS)
    rationale = find_collection(content, DECISION_RATIONALE_KEYS)
    tickers = find_tickers(content)
    ticker = tickers[0] if tickers else normalize_ticker(fallback_ticker or "")

    agent = str(agent_value).strip() if agent_value not in (None, "") else None
    return DecisionRecord(
        row_id=row_id,
        row_timestamp=row_timestamp,
        decision_timestamp=parse_datetime(timestamp_value),
        agent=agent,
        ticker=ticker,
        action=action.strip() if action else None,
        confidence=confidence.strip() if confidence else None,
        rationale=rationale,
        raw_content=content,
    )
