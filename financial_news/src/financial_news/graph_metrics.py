from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_INPUT_PATH = Path("Organizer/organizer-report-latest.json")
DEFAULT_OUTPUT_DIR = Path("Organizer/graph-metrics")
DAMPING_FACTOR = 0.85
PAGERANK_TOLERANCE = 1e-12
PAGERANK_MAX_ITERATIONS = 200
RECENT_WINDOWS = (7, 30)

SOURCE_ALIASES = {
    "ap business": "AP Business",
    "bbc business": "BBC Business",
    "benzinga": "Benzinga",
    "cnbc": "CNBC",
    "fox business": "Fox Business",
    "marketbeat": "MarketBeat",
    "yahoo finance": "Yahoo Finance",
}

THEME_ALIASES: dict[str, str] = {
    "artificial intelligence": "AI",
    "a.i.": "AI",
    "tech": "Technology",
    "rates and fed": "Rates & Fed",
    "rates & the fed": "Rates & Fed",
    "war and geopolitics": "War & Geopolitics",
    "geopolitics": "War & Geopolitics",
}

RECENCY_HALF_LIFE_DAYS = 14
RECENT_WINDOW_DAYS = 7
LABEL_PROPAGATION_MAX_ITERATIONS = 50


NodeKey = tuple[str, str]
EdgeKey = tuple[NodeKey, NodeKey]


@dataclass(frozen=True, slots=True)
class NodeRow:
    node_type: str
    label: str
    note_path: str | None
    unique_neighbors: int
    degree: int
    in_degree: int
    out_degree: int
    weighted_in_degree: int
    weighted_out_degree: int
    weighted_degree: int
    two_hop_reach: int
    pagerank: float
    mention_count: int
    block_count: int
    cluster_id: int = 0
    recent_block_count: int = 0
    recency_score: float = 0.0

    @property
    def node_id(self) -> str:
        return f"{self.node_type}:{self.label}"


@dataclass(frozen=True, slots=True)
class GraphArtifacts:
    node_rows: list[NodeRow]
    edge_rows: list[dict[str, Any]]
    summary: dict[str, Any]
    dashboard: str
    dashboard_html: str


class NoteIndex:
    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root
        self._indexes = {
            folder: self._build_folder_index(folder)
            for folder in ("Sources", "Tickers", "Topics", "Themes")
        }

    def resolve(self, node_type: str, label: str) -> str | None:
        if node_type == "source":
            return self._lookup("Sources", label)
        if node_type == "ticker":
            return self._lookup("Tickers", label)
        if node_type == "theme":
            return self._lookup("Topics", label) or self._lookup("Themes", label)
        return None

    def _lookup(self, folder: str, label: str) -> str | None:
        index = self._indexes[folder]
        exact = index["exact"].get(label)
        if exact is not None:
            return exact
        return index["normalized"].get(_normalize_lookup_key(label))

    def _build_folder_index(self, folder: str) -> dict[str, dict[str, str]]:
        exact: dict[str, str] = {}
        normalized: dict[str, str] = {}
        folder_path = self.vault_root / folder
        if not folder_path.exists():
            return {"exact": exact, "normalized": normalized}

        for path in sorted(folder_path.glob("*.md")):
            relative = path.relative_to(self.vault_root).with_suffix("").as_posix()
            stem = path.stem
            exact.setdefault(stem, relative)
            normalized.setdefault(_normalize_lookup_key(stem), relative)
        return {"exact": exact, "normalized": normalized}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build graph metrics from organizer-report-latest.json classified blocks.",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Path to organizer JSON report (default: Organizer/organizer-report-latest.json).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for graph metrics artifacts (default: Organizer/graph-metrics).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="How many nodes to show per leaderboard in dashboard.md (default: 15).",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level (default: INFO).")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    input_path = Path(args.input).expanduser()
    output_dir = Path(args.output_dir).expanduser()

    if not input_path.exists():
        parser.error(f"Organizer report not found: {input_path}")

    organizer_report = load_organizer_report(input_path)
    artifacts = build_graph_artifacts(
        organizer_report=organizer_report,
        vault_root=detect_vault_root(input_path),
        input_path=input_path,
        output_dir=output_dir,
        top_n=max(args.top_n, 1),
    )
    write_artifacts(output_dir, artifacts)

    LOGGER.info(
        "Wrote graph metrics to %s (%s nodes, %s edges)",
        output_dir,
        len(artifacts.node_rows),
        len(artifacts.edge_rows),
    )
    return 0


def detect_vault_root(input_path: Path) -> Path:
    resolved = input_path.resolve()
    if resolved.parent.name == "Organizer":
        return resolved.parent.parent
    return Path.cwd().resolve()


def load_organizer_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Organizer report must be a JSON object: {path}")
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError(f"Organizer report is missing a blocks list: {path}")
    return data


def build_graph_artifacts(
    *,
    organizer_report: dict[str, Any],
    vault_root: Path,
    input_path: Path,
    output_dir: Path,
    top_n: int,
) -> GraphArtifacts:
    note_index = NoteIndex(vault_root)
    node_types: dict[NodeKey, str] = {}
    edge_weights: Counter[EdgeKey] = Counter()
    block_counts: Counter[NodeKey] = Counter()
    mention_counts: Counter[NodeKey] = Counter()
    recency_scores: Counter[NodeKey] = Counter()
    recent_block_counts: Counter[NodeKey] = Counter()
    source_stats: dict[str, dict[str, Any]] = {}

    classified_blocks = list(iter_classified_blocks(organizer_report))
    classified_dates = sorted(
        {
            str(block.get("date")).strip()
            for block in classified_blocks
            if str(block.get("date") or "").strip()
        }
    )

    # Parse dates for recency weighting
    block_dates = [parse_block_date(block) for block in classified_blocks]
    valid_dates = [d for d in block_dates if d is not None]
    max_date = max(valid_dates) if valid_dates else None
    recent_cutoff = (max_date - timedelta(days=RECENT_WINDOW_DAYS)) if max_date else None

    for block, block_date in zip(classified_blocks, block_dates):
        source_label = normalize_source_label(block.get("source"))
        source_node = ("source", source_label) if source_label else None
        ticker_nodes = [("ticker", ticker) for ticker in sorted_unique_tickers(block.get("tickers"))]
        theme_nodes = [("theme", theme) for theme in sorted_unique_theme_labels(block.get("themes"))]

        block_nodes: list[NodeKey] = []
        if source_node is not None:
            node_types[source_node] = "source"
            block_nodes.append(source_node)
        for node in ticker_nodes:
            node_types[node] = "ticker"
            block_nodes.append(node)
        for node in theme_nodes:
            node_types[node] = "theme"
            block_nodes.append(node)

        for node in block_nodes:
            block_counts[node] += 1
            mention_counts[node] += 1

        # Recency tracking
        if block_date is not None and max_date is not None:
            days_ago = (max_date - block_date).total_seconds() / 86400
            weight = recency_weight(days_ago)
            for node in block_nodes:
                recency_scores[node] += weight
        if block_date is not None and recent_cutoff is not None and block_date >= recent_cutoff:
            for node in block_nodes:
                recent_block_counts[node] += 1

        # Source coverage tracking
        if source_label:
            if source_label not in source_stats:
                source_stats[source_label] = {
                    "blocks": 0,
                    "recent_blocks": 0,
                    "tickers": set(),
                    "themes": set(),
                }
            st = source_stats[source_label]
            st["blocks"] += 1
            if block_date is not None and recent_cutoff is not None and block_date >= recent_cutoff:
                st["recent_blocks"] += 1
            for _, ticker in ticker_nodes:
                st["tickers"].add(ticker)
            for _, theme in theme_nodes:
                st["themes"].add(theme)

        if source_node is not None:
            for ticker_node in ticker_nodes:
                edge_weights[(source_node, ticker_node)] += 1
            for theme_node in theme_nodes:
                edge_weights[(source_node, theme_node)] += 1

        for ticker_node in ticker_nodes:
            for theme_node in theme_nodes:
                edge_weights[(ticker_node, theme_node)] += 1
                edge_weights[(theme_node, ticker_node)] += 1

    metrics = compute_metrics(node_types, edge_weights)

    # Community detection via label propagation
    clusters = label_propagation(
        nodes=metrics["nodes"],
        undirected_neighbors=metrics["undirected_neighbors"],
        edge_weights=edge_weights,
    )

    node_rows = build_node_rows(
        node_types=node_types,
        metrics=metrics,
        note_index=note_index,
        block_counts=block_counts,
        mention_counts=mention_counts,
        clusters=clusters,
        recency_scores=recency_scores,
        recent_block_counts=recent_block_counts,
    )
    edge_rows = build_edge_rows(edge_weights)

    # Serialize source_stats (convert sets to sorted lists)
    source_coverage = {
        name: {
            "blocks": sst["blocks"],
            "recent_blocks": sst["recent_blocks"],
            "tickers": sorted(sst["tickers"], key=str.casefold),
            "themes": sorted(sst["themes"], key=str.casefold),
        }
        for name, sst in sorted(source_stats.items())
    }

    recent_window_stats = compute_recent_window_stats(
        classified_blocks=classified_blocks,
        classified_dates=classified_dates,
        top_n=top_n,
    )
    weekly_graph_stats = compute_weekly_graph_stats(
        classified_blocks=classified_blocks,
        top_n=top_n,
    )

    summary = build_summary(
        input_path=input_path,
        output_dir=output_dir,
        organizer_report=organizer_report,
        classified_blocks=classified_blocks,
        classified_dates=classified_dates,
        node_rows=node_rows,
        edge_rows=edge_rows,
        pagerank_iterations=metrics["pagerank_iterations"],
        pagerank_delta=metrics["pagerank_delta"],
        top_n=top_n,
        clusters=clusters,
        source_coverage=source_coverage,
        recent_window_days=RECENT_WINDOW_DAYS,
        recent_window_stats=recent_window_stats,
        weekly_graph_stats=weekly_graph_stats,
    )
    dashboard = build_dashboard(
        input_path=input_path,
        classified_dates=classified_dates,
        node_rows=node_rows,
        edge_rows=edge_rows,
        summary=summary,
        top_n=top_n,
        source_coverage=source_coverage,
    )
    dashboard_html = build_dashboard_html(
        input_path=input_path,
        node_rows=node_rows,
        edge_rows=edge_rows,
        summary=summary,
        top_n=top_n,
    )
    return GraphArtifacts(
        node_rows=node_rows,
        edge_rows=edge_rows,
        summary=summary,
        dashboard=dashboard,
        dashboard_html=dashboard_html,
    )


def iter_classified_blocks(organizer_report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for block in organizer_report.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("status") == "classified":
            yield block


def sorted_unique_tickers(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    tickers = {normalize_ticker_label(value) for value in values}
    return sorted((ticker for ticker in tickers if ticker), key=str.casefold)


def sorted_unique_labels(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    labels = {clean_label(value) for value in values}
    return sorted((label for label in labels if label), key=str.casefold)


def sorted_unique_theme_labels(values: Any) -> list[str]:
    """Like sorted_unique_labels but applies THEME_ALIASES normalization."""
    if not isinstance(values, list):
        return []
    labels = {normalize_theme_label(value) for value in values}
    return sorted((label for label in labels if label), key=str.casefold)


def clean_label(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def normalize_ticker_label(value: Any) -> str:
    return clean_label(value).upper()


def normalize_source_label(value: Any) -> str:
    raw = clean_label(value)
    if not raw:
        return ""
    # Normalize separators: underscores and hyphens → spaces
    raw = raw.replace("_", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    # Strip known agent/source prefixes
    raw = re.sub(r"^(?:agent|source|src)\s+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+", " ", raw).strip()
    # Try direct alias lookup
    alias = SOURCE_ALIASES.get(raw.casefold())
    if alias is not None:
        return alias
    # Try with periods removed (handles abbreviation drift like "B.B.C.")
    collapsed = raw.replace(".", "")
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    if collapsed:
        alias = SOURCE_ALIASES.get(collapsed.casefold())
        if alias is not None:
            return alias
    # No alias match — return the cleaned form
    return raw


def normalize_theme_label(value: Any) -> str:
    """Normalize a theme label through THEME_ALIASES, preserving originals when no alias matches."""
    raw = clean_label(value)
    if not raw:
        return ""
    return THEME_ALIASES.get(raw.casefold(), raw)


def parse_block_date(block: dict[str, Any]) -> datetime | None:
    """Parse the date field from a classified block as a UTC datetime."""
    raw = str(block.get("date") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def recency_weight(days_ago: float, half_life: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential decay weight. Returns 1.0 at day 0, 0.5 at half_life days."""
    if days_ago <= 0:
        return 1.0
    return math.exp(-math.log(2) * days_ago / half_life)


def compute_metrics(node_types: dict[NodeKey, str], edge_weights: Counter[EdgeKey]) -> dict[str, Any]:
    nodes = sorted(node_types, key=_node_sort_key)
    in_neighbors: dict[NodeKey, set[NodeKey]] = defaultdict(set)
    out_neighbors: dict[NodeKey, set[NodeKey]] = defaultdict(set)
    undirected_neighbors: dict[NodeKey, set[NodeKey]] = defaultdict(set)
    incoming_weights: dict[NodeKey, dict[NodeKey, int]] = defaultdict(dict)
    weighted_in_degree: Counter[NodeKey] = Counter()
    weighted_out_degree: Counter[NodeKey] = Counter()

    for (source_node, target_node), weight in edge_weights.items():
        out_neighbors[source_node].add(target_node)
        in_neighbors[target_node].add(source_node)
        undirected_neighbors[source_node].add(target_node)
        undirected_neighbors[target_node].add(source_node)
        weighted_out_degree[source_node] += weight
        weighted_in_degree[target_node] += weight
        incoming_weights[target_node][source_node] = weight

    pagerank, iterations, delta = compute_pagerank(nodes, incoming_weights, weighted_out_degree)

    two_hop_reach: dict[NodeKey, int] = {}
    for node in nodes:
        reachable = set(out_neighbors.get(node, set()))
        for neighbor in sorted(out_neighbors.get(node, set()), key=_node_sort_key):
            reachable.update(out_neighbors.get(neighbor, set()))
        reachable.discard(node)
        two_hop_reach[node] = len(reachable)

    return {
        "nodes": nodes,
        "in_neighbors": in_neighbors,
        "out_neighbors": out_neighbors,
        "undirected_neighbors": undirected_neighbors,
        "weighted_in_degree": weighted_in_degree,
        "weighted_out_degree": weighted_out_degree,
        "two_hop_reach": two_hop_reach,
        "pagerank": pagerank,
        "pagerank_iterations": iterations,
        "pagerank_delta": delta,
    }


def compute_pagerank(
    nodes: list[NodeKey],
    incoming_weights: dict[NodeKey, dict[NodeKey, int]],
    weighted_out_degree: Counter[NodeKey],
    damping: float = DAMPING_FACTOR,
    tolerance: float = PAGERANK_TOLERANCE,
    max_iterations: int = PAGERANK_MAX_ITERATIONS,
) -> tuple[dict[NodeKey, float], int, float]:
    if not nodes:
        return {}, 0, 0.0

    node_count = len(nodes)
    rank = {node: 1.0 / node_count for node in nodes}
    delta = math.inf

    for iteration in range(1, max_iterations + 1):
        dangling_rank = sum(rank[node] for node in nodes if weighted_out_degree[node] == 0)
        base_rank = (1.0 - damping) / node_count + damping * dangling_rank / node_count
        next_rank: dict[NodeKey, float] = {}
        delta = 0.0

        for node in nodes:
            score = base_rank
            for source_node, weight in incoming_weights.get(node, {}).items():
                source_weight = weighted_out_degree[source_node]
                if source_weight <= 0:
                    continue
                score += damping * rank[source_node] * (weight / source_weight)
            next_rank[node] = score
            delta += abs(score - rank[node])

        rank = next_rank
        if delta <= tolerance:
            return rank, iteration, delta

    return rank, max_iterations, delta


def label_propagation(
    nodes: list[NodeKey],
    undirected_neighbors: dict[NodeKey, set[NodeKey]],
    edge_weights: Counter[EdgeKey],
    max_iterations: int = LABEL_PROPAGATION_MAX_ITERATIONS,
) -> dict[NodeKey, int]:
    """Deterministic asynchronous label propagation for community detection.

    Processes nodes in sorted order, uses edge weights for neighbor influence,
    and breaks ties by smallest label index for full reproducibility.
    """
    if not nodes:
        return {}

    labels = {node: i for i, node in enumerate(nodes)}

    for _ in range(max_iterations):
        changed = False
        for node in nodes:
            neighbors = undirected_neighbors.get(node, set())
            if not neighbors:
                continue

            label_weights: Counter[int] = Counter()
            for neighbor in neighbors:
                w = edge_weights.get((node, neighbor), 0) + edge_weights.get((neighbor, node), 0)
                w = max(w, 1)
                label_weights[labels[neighbor]] += w

            max_weight = max(label_weights.values())
            best_label = min(lbl for lbl, wt in label_weights.items() if wt == max_weight)

            if best_label != labels[node]:
                labels[node] = best_label
                changed = True

        if not changed:
            break

    # Renumber contiguously from 0
    unique_labels = sorted(set(labels.values()))
    remap = {old: new for new, old in enumerate(unique_labels)}
    return {node: remap[label] for node, label in labels.items()}


def build_node_rows(
    *,
    node_types: dict[NodeKey, str],
    metrics: dict[str, Any],
    note_index: NoteIndex,
    block_counts: Counter[NodeKey],
    mention_counts: Counter[NodeKey],
    clusters: dict[NodeKey, int],
    recency_scores: Counter[NodeKey],
    recent_block_counts: Counter[NodeKey],
) -> list[NodeRow]:
    rows: list[NodeRow] = []
    for node in metrics["nodes"]:
        node_type, label = node
        unique_neighbors = len(metrics["undirected_neighbors"].get(node, set()))
        in_degree = len(metrics["in_neighbors"].get(node, set()))
        out_degree = len(metrics["out_neighbors"].get(node, set()))
        weighted_in = int(metrics["weighted_in_degree"].get(node, 0))
        weighted_out = int(metrics["weighted_out_degree"].get(node, 0))
        rows.append(
            NodeRow(
                node_type=node_type,
                label=label,
                note_path=note_index.resolve(node_type, label),
                unique_neighbors=unique_neighbors,
                degree=unique_neighbors,
                in_degree=in_degree,
                out_degree=out_degree,
                weighted_in_degree=weighted_in,
                weighted_out_degree=weighted_out,
                weighted_degree=weighted_in + weighted_out,
                two_hop_reach=int(metrics["two_hop_reach"].get(node, 0)),
                pagerank=float(metrics["pagerank"].get(node, 0.0)),
                mention_count=int(mention_counts.get(node, 0)),
                block_count=int(block_counts.get(node, 0)),
                cluster_id=clusters.get(node, 0),
                recent_block_count=int(recent_block_counts.get(node, 0)),
                recency_score=round(float(recency_scores.get(node, 0.0)), 6),
            )
        )
    return rows


def build_edge_rows(edge_weights: Counter[EdgeKey]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (source_node, target_node), weight in sorted(
        edge_weights.items(),
        key=lambda item: (_node_sort_key(item[0][0]), _node_sort_key(item[0][1])),
    ):
        source_type, source_label = source_node
        target_type, target_label = target_node
        rows.append(
            {
                "source_id": f"{source_type}:{source_label}",
                "source_type": source_type,
                "source_label": source_label,
                "target_id": f"{target_type}:{target_label}",
                "target_type": target_type,
                "target_label": target_label,
                "weight": int(weight),
            }
        )
    return rows


def compute_recent_window_stats(
    classified_blocks: list[dict[str, Any]],
    classified_dates: list[str],
    windows: Sequence[int] = RECENT_WINDOWS,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Compute per-window summary stats anchored at the latest classified date."""
    if not classified_dates:
        return []
    latest_date = classified_dates[-1]
    try:
        anchor = datetime.strptime(latest_date, "%Y-%m-%d")
    except ValueError:
        return []

    results: list[dict[str, Any]] = []
    for window_days in windows:
        cutoff = anchor - timedelta(days=window_days - 1)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        source_counts: Counter[str] = Counter()
        ticker_counts: Counter[str] = Counter()
        theme_counts: Counter[str] = Counter()
        block_count = 0

        for block in classified_blocks:
            block_date = str(block.get("date") or "").strip()
            if not block_date or block_date < cutoff_str or block_date > latest_date:
                continue
            block_count += 1
            src = normalize_source_label(block.get("source"))
            if src:
                source_counts[src] += 1
            for t in sorted_unique_tickers(block.get("tickers")):
                ticker_counts[t] += 1
            for th in sorted_unique_theme_labels(block.get("themes")):
                theme_counts[th] += 1

        ticker_items = sorted(ticker_counts.items(), key=lambda x: (-x[1], x[0].casefold()))[:top_n]
        theme_items = sorted(theme_counts.items(), key=lambda x: (-x[1], x[0].casefold()))[:top_n]

        results.append(
            {
                "window_days": window_days,
                "anchor_date": latest_date,
                "cutoff_date": cutoff_str,
                "block_count": block_count,
                "unique_sources": len(source_counts),
                "unique_tickers": len(ticker_counts),
                "unique_themes": len(theme_counts),
                "top_tickers": [{"label": label, "mentions": count} for label, count in ticker_items],
                "top_themes": [{"label": label, "mentions": count} for label, count in theme_items],
            }
        )
    return results


def iso_week_key(block_date: datetime) -> str:
    """Return a stable ISO year-week key for a classified block date."""
    iso_year, iso_week, _ = block_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def iso_week_bounds(block_date: datetime) -> tuple[str, str]:
    """Return Monday/Sunday date strings for the ISO week containing ``block_date``."""
    week_start = block_date - timedelta(days=block_date.isoweekday() - 1)
    week_end = week_start + timedelta(days=6)
    return week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")


def node_key_to_id(node: NodeKey) -> str:
    node_type, label = node
    return f"{node_type}:{label}"


def compute_weekly_graph_stats(
    classified_blocks: list[dict[str, Any]],
    top_n: int = 10,
) -> dict[str, Any]:
    """Aggregate graph node/edge counts into deterministic ISO-week buckets.

    The dashboard uses this compact payload for client-side week filtering without
    shipping raw article blocks. Each bucket stores counts for the same directed
    edge model as the global graph: source→ticker, source→theme, and ticker↔theme.
    """
    buckets: dict[str, dict[str, Any]] = {}
    undated_block_count = 0

    for block in classified_blocks:
        block_date = parse_block_date(block)
        if block_date is None:
            undated_block_count += 1
            continue

        key = iso_week_key(block_date)
        start_date, end_date = iso_week_bounds(block_date)
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "label": key,
                "start_date": start_date,
                "end_date": end_date,
                "block_count": 0,
                "nodes": Counter(),
                "node_types": {},
                "edges": Counter(),
                "sources": Counter(),
                "tickers": Counter(),
                "themes": Counter(),
            },
        )
        bucket["block_count"] += 1

        source_label = normalize_source_label(block.get("source"))
        source_node = ("source", source_label) if source_label else None
        ticker_nodes = [("ticker", ticker) for ticker in sorted_unique_tickers(block.get("tickers"))]
        theme_nodes = [("theme", theme) for theme in sorted_unique_theme_labels(block.get("themes"))]

        block_nodes: list[NodeKey] = []
        if source_node is not None:
            block_nodes.append(source_node)
            bucket["sources"][source_label] += 1
        for node in ticker_nodes:
            block_nodes.append(node)
            bucket["tickers"][node[1]] += 1
        for node in theme_nodes:
            block_nodes.append(node)
            bucket["themes"][node[1]] += 1

        for node in block_nodes:
            node_id = node_key_to_id(node)
            bucket["nodes"][node_id] += 1
            bucket["node_types"][node_id] = node[0]

        if source_node is not None:
            for ticker_node in ticker_nodes:
                bucket["edges"][(node_key_to_id(source_node), node_key_to_id(ticker_node))] += 1
            for theme_node in theme_nodes:
                bucket["edges"][(node_key_to_id(source_node), node_key_to_id(theme_node))] += 1

        for ticker_node in ticker_nodes:
            for theme_node in theme_nodes:
                bucket["edges"][(node_key_to_id(ticker_node), node_key_to_id(theme_node))] += 1
                bucket["edges"][(node_key_to_id(theme_node), node_key_to_id(ticker_node))] += 1

    serialized_buckets: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        node_counter: Counter[str] = bucket["nodes"]
        node_types: dict[str, str] = bucket["node_types"]
        edge_counter: Counter[tuple[str, str]] = bucket["edges"]
        node_counts_by_type = Counter(node_types[node_id] for node_id in node_counter)
        serialized_buckets.append(
            {
                "key": key,
                "label": bucket["label"],
                "start_date": bucket["start_date"],
                "end_date": bucket["end_date"],
                "block_count": int(bucket["block_count"]),
                "unique_sources": len(bucket["sources"]),
                "unique_tickers": len(bucket["tickers"]),
                "unique_themes": len(bucket["themes"]),
                "node_counts_by_type": dict(sorted(node_counts_by_type.items())),
                "top_sources": counter_to_ranked_items(bucket["sources"], top_n),
                "top_tickers": counter_to_ranked_items(bucket["tickers"], top_n),
                "top_themes": counter_to_ranked_items(bucket["themes"], top_n),
                "nodes": [
                    {
                        "node_id": node_id,
                        "node_type": node_types.get(node_id, node_id.split(":", 1)[0]),
                        "count": int(count),
                    }
                    for node_id, count in sorted(
                        node_counter.items(),
                        key=lambda item: (-item[1], item[0].casefold(), item[0]),
                    )
                ],
                "edges": [
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "weight": int(weight),
                    }
                    for (source_id, target_id), weight in sorted(
                        edge_counter.items(),
                        key=lambda item: (item[0][0].casefold(), item[0][0], item[0][1].casefold(), item[0][1]),
                    )
                ],
            }
        )

    return {
        "bucket_count": len(serialized_buckets),
        "undated_block_count": undated_block_count,
        "buckets": serialized_buckets,
    }


def counter_to_ranked_items(counter: Counter[str], top_n: int) -> list[dict[str, Any]]:
    return [
        {"label": label, "mentions": int(count)}
        for label, count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )[:top_n]
    ]


def build_summary(
    *,
    input_path: Path,
    output_dir: Path,
    organizer_report: dict[str, Any],
    classified_blocks: list[dict[str, Any]],
    classified_dates: list[str],
    node_rows: list[NodeRow],
    edge_rows: list[dict[str, Any]],
    pagerank_iterations: int,
    pagerank_delta: float,
    top_n: int,
    clusters: dict[NodeKey, int],
    source_coverage: dict[str, dict[str, Any]],
    recent_window_days: int,
    recent_window_stats: list[dict[str, Any]],
    weekly_graph_stats: dict[str, Any],
) -> dict[str, Any]:
    nodes_by_type = Counter(row.node_type for row in node_rows)
    edge_weight_total = sum(int(row["weight"]) for row in edge_rows)

    # Cluster summary
    cluster_members: dict[int, list[str]] = defaultdict(list)
    for node, cid in sorted(clusters.items(), key=lambda item: (item[1], _node_sort_key(item[0]))):
        cluster_members[cid].append(f"{node[0]}:{node[1]}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_path": input_path.as_posix(),
        "output_dir": output_dir.as_posix(),
        "source_report_generated_at": organizer_report.get("generated_at"),
        "classified_block_count": len(classified_blocks),
        "date_range": {
            "start": classified_dates[0] if classified_dates else None,
            "end": classified_dates[-1] if classified_dates else None,
        },
        "node_count": len(node_rows),
        "edge_count": len(edge_rows),
        "total_edge_weight": edge_weight_total,
        "node_counts_by_type": dict(sorted(nodes_by_type.items())),
        "pagerank": {
            "damping": DAMPING_FACTOR,
            "tolerance": PAGERANK_TOLERANCE,
            "iterations": pagerank_iterations,
            "final_delta": pagerank_delta,
        },
        "top_nodes": {
            "pagerank": summarize_top_nodes(node_rows, top_n, key=lambda row: row.pagerank),
            "weighted_degree": summarize_top_nodes(node_rows, top_n, key=lambda row: row.weighted_degree),
            "two_hop_reach": summarize_top_nodes(node_rows, top_n, key=lambda row: row.two_hop_reach),
            "recency_score": summarize_top_nodes(node_rows, top_n, key=lambda row: row.recency_score),
        },
        "cluster_count": len(cluster_members),
        "clusters": {str(k): v for k, v in sorted(cluster_members.items())},
        "source_coverage": source_coverage,
        "recent_window_days": recent_window_days,
        "recent_windows": recent_window_stats,
        "weekly_graph": weekly_graph_stats,
    }


def summarize_top_nodes(node_rows: list[NodeRow], top_n: int, key: Any) -> list[dict[str, Any]]:
    selected = sorted(
        node_rows,
        key=lambda row: (-float(key(row)), row.node_type, row.label.casefold(), row.label),
    )[:top_n]
    return [
        {
            "node_id": row.node_id,
            "node_type": row.node_type,
            "label": row.label,
            "note_path": row.note_path,
            "pagerank": row.pagerank,
            "weighted_degree": row.weighted_degree,
            "two_hop_reach": row.two_hop_reach,
            "block_count": row.block_count,
            "mention_count": row.mention_count,
            "cluster_id": row.cluster_id,
            "recent_block_count": row.recent_block_count,
            "recency_score": row.recency_score,
        }
        for row in selected
    ]


def build_dashboard(
    *,
    input_path: Path,
    classified_dates: list[str],
    node_rows: list[NodeRow],
    edge_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    top_n: int,
    source_coverage: dict[str, dict[str, Any]],
) -> str:
    date_range = summary["date_range"]
    lines = [
        "# Graph Metrics Dashboard",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Input: `{input_path.as_posix()}`",
        f"- Classified blocks: {summary['classified_block_count']}",
        f"- Nodes: {summary['node_count']} ({format_type_counts(summary['node_counts_by_type'])})",
        f"- Edges: {summary['edge_count']} (total weight {summary['total_edge_weight']})",
        (
            f"- Date range: {date_range['start']} \u2192 {date_range['end']}"
            if date_range["start"] and date_range["end"]
            else "- Date range: n/a"
        ),
        f"- Clusters: {summary['cluster_count']}",
        "",
        "## Top Nodes by PageRank",
        "",
        render_leaderboard(
            node_rows=node_rows,
            top_n=top_n,
            metric_key=lambda row: row.pagerank,
            metric_name="pagerank",
        ),
        "",
        "## Top Nodes by Weighted Degree",
        "",
        render_leaderboard(
            node_rows=node_rows,
            top_n=top_n,
            metric_key=lambda row: row.weighted_degree,
            metric_name="weighted_degree",
        ),
        "",
        "## Top Nodes by Two-Hop Reach",
        "",
        render_leaderboard(
            node_rows=node_rows,
            top_n=top_n,
            metric_key=lambda row: row.two_hop_reach,
            metric_name="two_hop_reach",
        ),
        "",
        "## Recent Activity",
        "",
        render_leaderboard(
            node_rows=node_rows,
            top_n=top_n,
            metric_key=lambda row: row.recency_score,
            metric_name="recency_score",
        ),
    ]
    recent_section = render_recent_windows(summary)
    if recent_section:
        lines.extend(["", recent_section])
    lines.extend([
        "",
        "## Source Coverage",
        "",
        render_source_coverage(source_coverage),
        "",
        "## Clusters",
        "",
        render_clusters(summary.get("clusters", {}), node_rows),
    ])
    if classified_dates:
        lines.extend(
            [
                "",
                "## Notes",
                "",
                "- Graph built only from organizer blocks with `status == classified`.",
                "- Edges are directed and weighted: source\u2192ticker, source\u2192theme, plus ticker\u2194theme co-mentions.",
                "- Metrics come from the structured organizer report, not a vault-wide scrape.",
                f"- Recent window: {summary.get('recent_window_days', RECENT_WINDOW_DAYS)} days from latest date.",
                "- Clusters via label propagation on the undirected weighted graph.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_dashboard_html(
    *,
    input_path: Path,
    node_rows: list[NodeRow],
    edge_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    top_n: int,
) -> str:
    """Build a self-contained browser dashboard with embedded graph data.

    The output intentionally uses only HTML/CSS/SVG/vanilla JavaScript so it can be
    opened directly from disk or served with ``python -m http.server``.
    """
    date_range = summary.get("date_range", {})
    date_range_text = (
        f"{date_range.get('start')} → {date_range.get('end')}"
        if date_range.get("start") and date_range.get("end")
        else "n/a"
    )
    dashboard_data = {
        "input_path": input_path.as_posix(),
        "summary": summary,
        "weekly_graph": summary.get("weekly_graph", {}),
        "nodes": [node_row_to_dict(row) for row in node_rows],
        "edges": edge_rows,
        "top_n": top_n,
    }
    graph_data_json = json.dumps(
        dashboard_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    static_recent = render_recent_windows_html(summary)
    template = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Financial News Graph Metrics</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f172a;
      --panel: #111827;
      --panel-2: #172033;
      --border: #263244;
      --muted: #94a3b8;
      --text: #e5e7eb;
      --accent: #38bdf8;
      --source: #38bdf8;
      --ticker: #a78bfa;
      --theme: #34d399;
      --warn: #fbbf24;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top left, #1e3a5f 0, var(--bg) 34rem);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
    }
    a { color: #7dd3fc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    button, input { font: inherit; }
    .shell { max-width: 1280px; margin: 0 auto; padding: 28px; }
    header { margin-bottom: 22px; }
    h1 { margin: 0 0 6px; font-size: clamp(28px, 5vw, 48px); letter-spacing: -0.04em; }
    h2 { margin: 0 0 14px; font-size: 20px; letter-spacing: -0.02em; }
    h3 { margin: 0 0 10px; font-size: 15px; color: #cbd5e1; }
    .subtitle { margin: 0; color: var(--muted); }
    .grid { display: grid; gap: 16px; }
    .cards { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 20px 0; }
    .card, section {
      background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.018));
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(0,0,0,0.24);
    }
    .card { padding: 16px; }
    .card .label, .control-label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
    .card .value { margin-top: 8px; font-size: 28px; font-weight: 750; letter-spacing: -0.03em; }
    section { padding: 18px; min-width: 0; }
    .two-col { grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr); align-items: start; }
    .three-col { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--muted); margin-top: 12px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 999px; padding: 3px 9px; color: #cbd5e1; background: rgba(15,23,42,0.48); }
    .dot { width: 8px; height: 8px; border-radius: 999px; display: inline-block; }
    .controls-panel { margin-bottom: 16px; }
    .controls-grid { display: grid; grid-template-columns: minmax(260px, 0.55fr) minmax(320px, 1fr); gap: 18px; align-items: end; }
    .segmented { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .segmented button {
      color: #cbd5e1;
      background: rgba(15,23,42,0.5);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 8px 13px;
      cursor: pointer;
    }
    .segmented button.active { color: #06121f; border-color: var(--accent); background: linear-gradient(90deg, var(--accent), #818cf8); font-weight: 750; }
    .week-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin: 8px 0 6px; }
    #week-label { color: #f8fafc; font-weight: 750; }
    #week-slider { width: 100%; accent-color: var(--accent); }
    .bar-row { display: grid; grid-template-columns: minmax(105px, 0.95fr) minmax(150px, 2fr) 70px; gap: 10px; align-items: center; margin: 8px 0; }
    .bar-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bar-track { height: 12px; border-radius: 999px; background: rgba(148,163,184,0.16); overflow: hidden; }
    .bar-fill { height: 100%; border-radius: inherit; min-width: 2px; background: linear-gradient(90deg, var(--accent), #818cf8); }
    .bar-value { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .table-wrap { overflow-x: auto; }
    #network { width: 100%; min-height: 520px; border-radius: 14px; background: rgba(15,23,42,0.58); border: 1px solid var(--border); display: grid; place-items: center; position: relative; overflow: hidden; touch-action: none; }
    #network svg { display: block; }
    .network-tooltip { position: absolute; pointer-events: none; z-index: 10; max-width: 280px; padding: 9px 11px; border: 1px solid var(--border); border-radius: 12px; background: rgba(2,6,23,0.94); color: var(--text); box-shadow: 0 12px 34px rgba(0,0,0,0.35); opacity: 0; transform: translate3d(0,0,0); transition: opacity 120ms ease; }
    .network-tooltip strong { color: #f8fafc; }
    .network-tooltip .small { margin-top: 3px; }
    .network-note { margin: 10px 0 0; color: var(--muted); font-size: 13px; }
    .type-source { color: var(--source); }
    .type-ticker { color: var(--ticker); }
    .type-theme { color: var(--theme); }
    .recent-list { display: grid; gap: 10px; }
    .recent-card { padding: 12px; border: 1px solid var(--border); border-radius: 14px; background: rgba(15,23,42,0.35); }
    .recent-card strong { color: #f8fafc; }
    .small { color: var(--muted); font-size: 13px; }
    .empty { color: var(--muted); padding: 24px; text-align: center; }
    .artifacts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    @media (max-width: 900px) { .shell { padding: 18px; } .two-col, .controls-grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class=\"shell\">
    <header>
      <h1>Graph Metrics Dashboard</h1>
      <p class=\"subtitle\">Browser-viewable financial_news source/ticker/theme graph metrics.</p>
      <div class=\"meta\">
        <span>Generated: __GENERATED__</span>
        <span>Input: <code>__INPUT__</code></span>
        <span>Date range: __DATE_RANGE__</span>
      </div>
      <div class=\"artifacts\">
        <a class=\"pill\" href=\"nodes.csv\">nodes.csv</a>
        <a class=\"pill\" href=\"edges.csv\">edges.csv</a>
        <a class=\"pill\" href=\"summary.json\">summary.json</a>
        <a class=\"pill\" href=\"dashboard.md\">dashboard.md</a>
      </div>
    </header>

    <section class=\"controls-panel\">
      <h2>Graph view + Week slider</h2>
      <div class=\"controls-grid\">
        <div>
          <div class=\"control-label\">Graph view</div>
          <div id=\"view-controls\" class=\"segmented\" role=\"tablist\" aria-label=\"Graph view\">
            <button type=\"button\" data-view=\"combined\" class=\"active\">Combined</button>
            <button type=\"button\" data-view=\"themes\">Themes</button>
            <button type=\"button\" data-view=\"tickers\">Tickers</button>
          </div>
        </div>
        <div>
          <div class=\"control-label\">Week slider</div>
          <div class=\"week-head\"><span id=\"week-label\">All weeks</span><span id=\"week-position\" class=\"small\">All</span></div>
          <input id=\"week-slider\" type=\"range\" min=\"0\" max=\"0\" step=\"1\" value=\"0\" aria-label=\"Filter dashboard by ISO week\">
          <div id=\"week-meta\" class=\"small\">Use the slider to inspect evolution week by week, or leave it on all weeks.</div>
        </div>
      </div>
    </section>

    <div class=\"grid cards\">
      <div class=\"card\"><div class=\"label\">Classified blocks</div><div id=\"card-blocks\" class=\"value\">__BLOCKS__</div></div>
      <div class=\"card\"><div class=\"label\">Visible nodes</div><div id=\"card-nodes\" class=\"value\">__NODES__</div></div>
      <div class=\"card\"><div class=\"label\">Visible edges</div><div id=\"card-edges\" class=\"value\">__EDGES__</div></div>
      <div class=\"card\"><div class=\"label\">Visible clusters</div><div id=\"card-clusters\" class=\"value\">__CLUSTERS__</div></div>
      <div class=\"card\"><div class=\"label\">Edge weight</div><div id=\"card-edge-weight\" class=\"value\">__EDGE_WEIGHT__</div></div>
    </div>

    <div class=\"grid two-col\">
      <section>
        <h2>Network overview</h2>
        <div id=\"network\"></div>
        <p class=\"network-note\">Filtered by graph view and week. Drag nodes, scroll/trackpad zoom, pan the canvas, and hover nodes/edges for tooltips. Theme view shows source→theme edges; ticker view shows source→ticker edges; combined also includes ticker↔theme co-mentions.</p>
      </section>
      <section>
        <h2>Node mix</h2>
        <p id=\"node-type-summary\" class=\"small\">__NODE_TYPES__</p>
        <div id=\"type-bars\"></div>
        <h2 style=\"margin-top:22px\">Period highlights</h2>
        <div id=\"period-highlights\" class=\"recent-list\">__RECENT_WINDOWS_STATIC__</div>
      </section>
    </div>

    <div class=\"grid three-col\" style=\"margin-top:16px\">
      <section><h2>Top PageRank</h2><div id=\"pagerank-bars\"></div></section>
      <section><h2>Top weighted degree</h2><div id=\"degree-bars\"></div></section>
      <section><h2>Top activity</h2><div id=\"activity-bars\"></div></section>
    </div>

    <section style=\"margin-top:16px\">
      <h2>Top nodes table</h2>
      <div class=\"table-wrap\"><table id=\"top-table\"></table></div>
    </section>

    <section style=\"margin-top:16px\">
      <h2>Source coverage</h2>
      <div class=\"table-wrap\"><table id=\"source-table\"></table></div>
    </section>

    <section style=\"margin-top:16px\">
      <h2>Method notes</h2>
      <ul class=\"small\">
        <li>Graph built only from organizer blocks with <code>status == classified</code>.</li>
        <li>Edges are directed and weighted: source→ticker, source→theme, plus ticker↔theme co-mentions.</li>
        <li>Metrics come from the structured organizer report, not a vault-wide scrape.</li>
        <li>Open this file directly, or serve the vault root with <code>python -m http.server</code> and browse to <code>Organizer/graph-metrics/dashboard.html</code>.</li>
      </ul>
    </section>
  </div>

  <script id=\"graph-data\" type=\"application/json\">__GRAPH_DATA__</script>
  <script src=\"https://cdn.jsdelivr.net/npm/d3@7\"></script>
  <script>
    (() => {
      const data = JSON.parse(document.getElementById('graph-data').textContent);
      const nodes = data.nodes || [];
      const edges = data.edges || [];
      const summary = data.summary || {};
      const weeklyGraph = data.weekly_graph || summary.weekly_graph || {};
      const weekBuckets = weeklyGraph.buckets || [];
      const topN = Math.max(1, data.top_n || 15);
      const typeColors = { source: '#38bdf8', ticker: '#a78bfa', theme: '#34d399' };
      const viewLabels = { combined: 'Combined', themes: 'Themes', tickers: 'Tickers' };
      const allowedTypes = {
        combined: new Set(['source', 'ticker', 'theme']),
        themes: new Set(['source', 'theme']),
        tickers: new Set(['source', 'ticker']),
      };
      const state = { view: 'combined', weekIndex: 0 };
      const nodeById = new Map(nodes.map((node) => [node.node_id, node]));
      const edgeByPair = new Map(edges.map((edge) => [`${edge.source_id}\u0000${edge.target_id}`, edge]));
      const baseEdgeWeight = (edge) => Number(edge.weight || 0);
      const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });

      function clear(element) { while (element.firstChild) element.removeChild(element.firstChild); }
      function nodeHref(node) { return node.note_path ? '../../' + encodeURI(node.note_path + '.md') : null; }
      function nodeLabel(node) { return `${node.label} (${node.node_type})`; }
      function nodeLink(node) {
        const href = nodeHref(node);
        if (!href) return document.createTextNode(node.label);
        const link = document.createElement('a');
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = node.label;
        return link;
      }
      function currentBucket() { return state.weekIndex === 0 ? null : weekBuckets[state.weekIndex - 1] || null; }
      function edgeMatchesView(edge) {
        if (state.view === 'combined') return true;
        if (state.view === 'themes') return edge.source_type === 'source' && edge.target_type === 'theme';
        if (state.view === 'tickers') return edge.source_type === 'source' && edge.target_type === 'ticker';
        return true;
      }
      function nodeAllowed(node) { return (allowedTypes[state.view] || allowedTypes.combined).has(node.node_type); }
      function typeCountsToText(counts) {
        const parts = Object.keys(counts).sort().map((type) => `${type}: ${counts[type]}`);
        return parts.length ? parts.join(', ') : 'none';
      }
      function emptyBlock(text) {
        const div = document.createElement('div');
        div.className = 'empty';
        div.textContent = text;
        return div;
      }

      function buildFilteredGraph() {
        const bucket = currentBucket();
        const countMap = new Map();
        let candidateEdges;
        if (bucket) {
          for (const item of bucket.nodes || []) countMap.set(item.node_id, Number(item.count || 0));
          candidateEdges = (bucket.edges || []).map((edge) => {
            const base = edgeByPair.get(`${edge.source_id}\u0000${edge.target_id}`) || {};
            const source = nodeById.get(edge.source_id) || {};
            const target = nodeById.get(edge.target_id) || {};
            return {
              ...base,
              source_id: edge.source_id,
              target_id: edge.target_id,
              source_type: base.source_type || source.node_type || edge.source_id.split(':', 1)[0],
              target_type: base.target_type || target.node_type || edge.target_id.split(':', 1)[0],
              source_label: base.source_label || source.label || edge.source_id,
              target_label: base.target_label || target.label || edge.target_id,
              weight: Number(edge.weight || 0),
            };
          });
        } else {
          for (const node of nodes) countMap.set(node.node_id, Number(node.block_count || 0));
          candidateEdges = edges.map((edge) => ({ ...edge, weight: baseEdgeWeight(edge) }));
        }

        const filteredEdges = candidateEdges
          .filter((edge) => baseEdgeWeight(edge) > 0 && edgeMatchesView(edge))
          .sort((a, b) => baseEdgeWeight(b) - baseEdgeWeight(a) || a.source_id.localeCompare(b.source_id) || a.target_id.localeCompare(b.target_id));

        const weightedIn = new Map();
        const weightedOut = new Map();
        for (const edge of filteredEdges) {
          const weight = baseEdgeWeight(edge);
          weightedOut.set(edge.source_id, (weightedOut.get(edge.source_id) || 0) + weight);
          weightedIn.set(edge.target_id, (weightedIn.get(edge.target_id) || 0) + weight);
        }

        const filteredNodes = nodes
          .filter((node) => nodeAllowed(node) && (!bucket || countMap.has(node.node_id)))
          .map((node) => {
            const periodCount = Number(countMap.get(node.node_id) || 0);
            const inWeight = Number(weightedIn.get(node.node_id) || 0);
            const outWeight = Number(weightedOut.get(node.node_id) || 0);
            return {
              ...node,
              period_count: periodCount,
              visible_block_count: bucket ? periodCount : Number(node.block_count || 0),
              weighted_in_degree_filtered: inWeight,
              weighted_out_degree_filtered: outWeight,
              weighted_degree_filtered: inWeight + outWeight,
              activity_score: bucket ? periodCount : Number(node.recency_score || node.recent_block_count || node.block_count || 0),
            };
          })
          .sort((a, b) => b.weighted_degree_filtered - a.weighted_degree_filtered || b.pagerank - a.pagerank || a.node_id.localeCompare(b.node_id));

        const visibleIds = new Set(filteredNodes.map((node) => node.node_id));
        return {
          bucket,
          nodes: filteredNodes,
          edges: filteredEdges.filter((edge) => visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id)),
          countMap,
        };
      }

      function topNodes(filtered, metric, limit = topN) {
        return filtered.nodes.slice().sort((a, b) => Number(b[metric] || 0) - Number(a[metric] || 0) || a.node_id.localeCompare(b.node_id)).slice(0, limit);
      }
      function maxOf(items, metric) { return Math.max(1e-12, ...items.map((node) => Number(node[metric] || 0))); }

      function renderBars(containerId, filtered, metric, formatter = (value) => fmt.format(value)) {
        const container = document.getElementById(containerId);
        if (!container) return;
        clear(container);
        const items = topNodes(filtered, metric);
        if (!items.length) {
          container.appendChild(emptyBlock('No nodes match this view/week.'));
          return;
        }
        const maxValue = maxOf(items, metric);
        for (const node of items) {
          const row = document.createElement('div');
          row.className = 'bar-row';
          const label = document.createElement('div');
          label.className = `bar-label type-${node.node_type}`;
          label.title = nodeLabel(node);
          label.appendChild(nodeLink(node));
          const track = document.createElement('div');
          track.className = 'bar-track';
          const fill = document.createElement('div');
          fill.className = 'bar-fill';
          fill.style.background = `linear-gradient(90deg, ${typeColors[node.node_type] || '#38bdf8'}, #818cf8)`;
          fill.style.width = `${Math.max(2, Number(node[metric] || 0) / maxValue * 100)}%`;
          track.appendChild(fill);
          const value = document.createElement('div');
          value.className = 'bar-value';
          value.textContent = formatter(Number(node[metric] || 0));
          row.append(label, track, value);
          container.appendChild(row);
        }
      }

      function renderTypeBars(filtered) {
        const container = document.getElementById('type-bars');
        if (!container) return;
        clear(container);
        const counts = {};
        for (const node of filtered.nodes) counts[node.node_type] = (counts[node.node_type] || 0) + 1;
        const summaryNode = document.getElementById('node-type-summary');
        if (summaryNode) summaryNode.textContent = typeCountsToText(counts);
        const maxValue = Math.max(1, ...Object.values(counts));
        for (const type of Object.keys(counts).sort()) {
          const row = document.createElement('div');
          row.className = 'bar-row';
          const label = document.createElement('div');
          label.className = `bar-label type-${type}`;
          label.innerHTML = `<span class=\"dot\" style=\"background:${typeColors[type] || '#94a3b8'}\"></span> ${type}`;
          const track = document.createElement('div');
          track.className = 'bar-track';
          const fill = document.createElement('div');
          fill.className = 'bar-fill';
          fill.style.background = typeColors[type] || '#94a3b8';
          fill.style.width = `${Math.max(2, counts[type] / maxValue * 100)}%`;
          track.appendChild(fill);
          const value = document.createElement('div');
          value.className = 'bar-value';
          value.textContent = fmt.format(counts[type]);
          row.append(label, track, value);
          container.appendChild(row);
        }
      }

      function renderTopTable(filtered) {
        const table = document.getElementById('top-table');
        if (!table) return;
        clear(table);
        const thead = table.createTHead();
        const head = thead.insertRow();
        ['Rank', 'Node', 'Type', 'PageRank', 'Visible degree', 'Period blocks', 'Global blocks', 'Cluster'].forEach((name, index) => {
          const th = document.createElement('th');
          th.textContent = name;
          if (index > 2 || index === 0) th.className = 'num';
          head.appendChild(th);
        });
        const tbody = table.createTBody();
        const rows = topNodes(filtered, 'weighted_degree_filtered', topN);
        if (!rows.length) {
          const row = tbody.insertRow();
          const cell = row.insertCell();
          cell.colSpan = 8;
          cell.className = 'empty';
          cell.textContent = 'No visible nodes.';
          return;
        }
        rows.forEach((node, index) => {
          const row = tbody.insertRow();
          const values = [index + 1, null, node.node_type, Number(node.pagerank || 0).toFixed(6), node.weighted_degree_filtered, node.visible_block_count, node.block_count, node.cluster_id];
          values.forEach((value, cellIndex) => {
            const cell = row.insertCell();
            if (cellIndex === 1) cell.appendChild(nodeLink(node));
            else cell.textContent = value;
            if (cellIndex > 2 || cellIndex === 0) cell.className = 'num';
          });
        });
      }

      function renderSourceTable(filtered) {
        const table = document.getElementById('source-table');
        if (!table) return;
        clear(table);
        const rowsBySource = new Map();
        for (const node of filtered.nodes.filter((item) => item.node_type === 'source')) {
          rowsBySource.set(node.node_id, { name: node.label, blocks: node.visible_block_count, tickers: new Set(), themes: new Set() });
        }
        for (const edge of filtered.edges) {
          if (edge.source_type !== 'source') continue;
          const source = rowsBySource.get(edge.source_id) || { name: edge.source_label || edge.source_id, blocks: filtered.countMap.get(edge.source_id) || 0, tickers: new Set(), themes: new Set() };
          if (edge.target_type === 'ticker') source.tickers.add(edge.target_label || edge.target_id);
          if (edge.target_type === 'theme') source.themes.add(edge.target_label || edge.target_id);
          rowsBySource.set(edge.source_id, source);
        }
        const rows = Array.from(rowsBySource.values()).sort((a, b) => b.blocks - a.blocks || a.name.localeCompare(b.name));
        const thead = table.createTHead();
        const head = thead.insertRow();
        ['Source', 'Period blocks', 'Tickers', 'Themes'].forEach((name, index) => {
          const th = document.createElement('th');
          th.textContent = name;
          if (index > 0) th.className = 'num';
          head.appendChild(th);
        });
        const tbody = table.createTBody();
        if (!rows.length) {
          const row = tbody.insertRow();
          const cell = row.insertCell();
          cell.colSpan = 4;
          cell.className = 'empty';
          cell.textContent = 'No source coverage for this view/week.';
          return;
        }
        for (const rowData of rows) {
          const row = tbody.insertRow();
          [rowData.name, rowData.blocks, rowData.tickers.size, rowData.themes.size].forEach((value, index) => {
            const cell = row.insertCell();
            cell.textContent = value;
            if (index > 0) cell.className = 'num';
          });
        }
      }

      function rankedText(items) {
        return (items || []).slice(0, 6).map((item) => `${item.label} (${item.mentions})`).join(', ') || 'n/a';
      }
      function renderPeriodHighlights(filtered) {
        const container = document.getElementById('period-highlights');
        if (!container) return;
        clear(container);
        const bucket = filtered.bucket;
        if (!bucket) {
          for (const window of summary.recent_windows || []) {
            const card = document.createElement('div');
            card.className = 'recent-card';
            const title = document.createElement('strong');
            title.textContent = `${window.window_days}d: ${window.cutoff_date} → ${window.anchor_date}`;
            const counts = document.createElement('div');
            counts.className = 'small';
            counts.textContent = `Blocks ${window.block_count} · Sources ${window.unique_sources} · Tickers ${window.unique_tickers} · Themes ${window.unique_themes}`;
            const tickers = document.createElement('div');
            tickers.className = 'small';
            tickers.textContent = `Top tickers: ${rankedText(window.top_tickers)}`;
            const themes = document.createElement('div');
            themes.className = 'small';
            themes.textContent = `Top themes: ${rankedText(window.top_themes)}`;
            card.append(title, counts, tickers, themes);
            container.appendChild(card);
          }
          if (!container.childNodes.length) container.appendChild(emptyBlock('No recent-window data available.'));
          return;
        }
        const card = document.createElement('div');
        card.className = 'recent-card';
        const title = document.createElement('strong');
        title.textContent = `${bucket.label}: ${bucket.start_date} → ${bucket.end_date}`;
        const counts = document.createElement('div');
        counts.className = 'small';
        counts.textContent = `Blocks ${bucket.block_count} · Sources ${bucket.unique_sources} · Tickers ${bucket.unique_tickers} · Themes ${bucket.unique_themes}`;
        card.append(title, counts);
        const lines = [];
        if (state.view !== 'tickers') lines.push(['Top themes', bucket.top_themes]);
        if (state.view !== 'themes') lines.push(['Top tickers', bucket.top_tickers]);
        lines.unshift(['Top sources', bucket.top_sources]);
        for (const [label, items] of lines) {
          const div = document.createElement('div');
          div.className = 'small';
          div.textContent = `${label}: ${rankedText(items)}`;
          card.appendChild(div);
        }
        container.appendChild(card);
      }

      function drawNetwork(filtered) {
        const container = document.getElementById('network');
        if (!container) return;
        clear(container);
        const selected = [];
        const seen = new Set();
        const add = (items) => {
          for (const node of items) {
            if (selected.length >= 56) return;
            if (!seen.has(node.node_id)) {
              seen.add(node.node_id);
              selected.push(node);
            }
          }
        };
        add(topNodes(filtered, 'weighted_degree_filtered', 32));
        add(topNodes(filtered, 'pagerank', 14));
        add(topNodes(filtered, 'activity_score', 12));
        if (!selected.length) {
          container.appendChild(emptyBlock('No network data for this view/week.'));
          return;
        }
        if (!window.d3) {
          container.appendChild(emptyBlock('D3 did not load. Serve this dashboard with network access or allow the jsDelivr d3@7 script.'));
          return;
        }

        const selectedById = new Map(selected.map((node) => [node.node_id, { ...node }]));
        const selectedIds = new Set(selectedById.keys());
        const graphEdges = filtered.edges
          .filter((edge) => selectedIds.has(edge.source_id) && selectedIds.has(edge.target_id))
          .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
          .slice(0, 160)
          .map((edge) => ({
            ...edge,
            source: selectedById.get(edge.source_id),
            target: selectedById.get(edge.target_id),
          }));

        const width = 960;
        const height = 560;
        const maxDegree = Math.max(1, ...selected.map((node) => Number(node.weighted_degree_filtered || 0)));
        const maxWeight = Math.max(1, ...graphEdges.map((edge) => Number(edge.weight || 0)));
        const nodeRadius = (node) => 7 + Math.sqrt(Number(node.weighted_degree_filtered || 0) / maxDegree) * 17;
        const tooltip = document.createElement('div');
        tooltip.className = 'network-tooltip';
        container.appendChild(tooltip);

        const showTooltip = (event, html) => {
          tooltip.innerHTML = html;
          const rect = container.getBoundingClientRect();
          tooltip.style.left = `${Math.min(rect.width - 18, event.clientX - rect.left + 14)}px`;
          tooltip.style.top = `${Math.max(8, event.clientY - rect.top + 14)}px`;
          tooltip.style.opacity = '1';
        };
        const hideTooltip = () => { tooltip.style.opacity = '0'; };
        const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

        const svg = d3.select(container)
          .append('svg')
          .attr('viewBox', [0, 0, width, height])
          .attr('role', 'img')
          .attr('aria-label', 'Interactive force-directed financial news graph')
          .style('width', '100%')
          .style('height', '520px')
          .style('cursor', 'grab');

        const defs = svg.append('defs');
        defs.append('marker')
          .attr('id', 'arrow')
          .attr('viewBox', '0 -5 10 10')
          .attr('refX', 18)
          .attr('refY', 0)
          .attr('markerWidth', 6)
          .attr('markerHeight', 6)
          .attr('orient', 'auto')
          .append('path')
          .attr('d', 'M0,-5L10,0L0,5')
          .attr('fill', '#64748b')
          .attr('opacity', 0.55);

        const root = svg.append('g');
        svg.call(
          d3.zoom()
            .scaleExtent([0.35, 4])
            .on('start', () => svg.style('cursor', 'grabbing'))
            .on('zoom', (event) => root.attr('transform', event.transform))
            .on('end', () => svg.style('cursor', 'grab'))
        );

        const link = root.append('g')
          .attr('stroke', '#64748b')
          .attr('stroke-opacity', 0.38)
          .selectAll('line')
          .data(graphEdges)
          .join('line')
          .attr('stroke-width', (edge) => 0.8 + Number(edge.weight || 0) / maxWeight * 4.2)
          .attr('marker-end', 'url(#arrow)')
          .on('mousemove', (event, edge) => showTooltip(event, `<strong>${esc(edge.source_label || edge.source_id)} → ${esc(edge.target_label || edge.target_id)}</strong><div class="small">Weight: ${esc(edge.weight)}</div><div class="small">${esc(edge.source_type)} → ${esc(edge.target_type)}</div>`))
          .on('mouseleave', hideTooltip);

        const node = root.append('g')
          .selectAll('g')
          .data(Array.from(selectedById.values()))
          .join('g')
          .attr('class', 'force-node')
          .style('cursor', 'grab');

        node.append('circle')
          .attr('r', nodeRadius)
          .attr('fill', (node) => typeColors[node.node_type] || '#94a3b8')
          .attr('fill-opacity', 0.88)
          .attr('stroke', '#e2e8f0')
          .attr('stroke-opacity', 0.72)
          .attr('stroke-width', 1.2)
          .on('mousemove', (event, node) => showTooltip(event, `<strong>${esc(node.label)}</strong><div class="small">${esc(node.node_type)} · cluster ${esc(node.cluster_id)}</div><div class="small">Visible degree: ${esc(node.weighted_degree_filtered)} · Period blocks: ${esc(node.visible_block_count)}</div><div class="small">PageRank: ${Number(node.pagerank || 0).toFixed(6)}</div>`))
          .on('mouseleave', hideTooltip)
          .on('dblclick', (event, node) => {
            const href = nodeHref(node);
            if (href) window.open(href, '_blank', 'noopener');
          });

        node.append('text')
          .attr('x', (node) => nodeRadius(node) + 4)
          .attr('y', 4)
          .attr('fill', '#e5e7eb')
          .attr('font-size', 11)
          .attr('paint-order', 'stroke')
          .attr('stroke', '#0f172a')
          .attr('stroke-width', 3)
          .text((node) => (nodeRadius(node) >= 12 || selected.length <= 34) ? (node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label) : '');

        const simulation = d3.forceSimulation(Array.from(selectedById.values()))
          .force('link', d3.forceLink(graphEdges).id((node) => node.node_id).distance((edge) => 56 + Math.max(0, 12 - Number(edge.weight || 0)) * 5).strength(0.34))
          .force('charge', d3.forceManyBody().strength(-260))
          .force('center', d3.forceCenter(width / 2, height / 2))
          .force('collision', d3.forceCollide().radius((node) => nodeRadius(node) + 6))
          .on('tick', () => {
            link
              .attr('x1', (edge) => edge.source.x)
              .attr('y1', (edge) => edge.source.y)
              .attr('x2', (edge) => edge.target.x)
              .attr('y2', (edge) => edge.target.y);
            node.attr('transform', (node) => `translate(${node.x},${node.y})`);
          });

        node.call(
          d3.drag()
            .on('start', (event, node) => {
              if (!event.active) simulation.alphaTarget(0.25).restart();
              node.fx = node.x;
              node.fy = node.y;
              d3.select(event.sourceEvent?.target?.closest?.('g.force-node') || event.sourceEvent?.target).style('cursor', 'grabbing');
            })
            .on('drag', (event, node) => {
              node.fx = event.x;
              node.fy = event.y;
            })
            .on('end', (event, node) => {
              if (!event.active) simulation.alphaTarget(0);
              node.fx = null;
              node.fy = null;
              d3.select(event.sourceEvent?.target?.closest?.('g.force-node') || event.sourceEvent?.target).style('cursor', 'grab');
            })
        );
      }

      function updateCards(filtered) {
        const edgeWeight = filtered.edges.reduce((sum, edge) => sum + Number(edge.weight || 0), 0);
        const visibleClusters = new Set(filtered.nodes.map((node) => node.cluster_id));
        const blocks = filtered.bucket ? filtered.bucket.block_count : summary.classified_block_count || 0;
        const values = {
          'card-blocks': blocks,
          'card-nodes': filtered.nodes.length,
          'card-edges': filtered.edges.length,
          'card-clusters': visibleClusters.size,
          'card-edge-weight': edgeWeight,
        };
        for (const [id, value] of Object.entries(values)) {
          const element = document.getElementById(id);
          if (element) element.textContent = fmt.format(value);
        }
      }

      function updateWeekControls() {
        const slider = document.getElementById('week-slider');
        const label = document.getElementById('week-label');
        const position = document.getElementById('week-position');
        const meta = document.getElementById('week-meta');
        if (slider) {
          slider.max = String(weekBuckets.length);
          slider.value = String(state.weekIndex);
          slider.disabled = weekBuckets.length === 0;
        }
        const bucket = currentBucket();
        if (label) label.textContent = bucket ? bucket.label : 'All weeks';
        if (position) position.textContent = bucket ? `${state.weekIndex}/${weekBuckets.length}` : 'All';
        if (meta) {
          meta.textContent = bucket
            ? `${bucket.start_date} → ${bucket.end_date} · ${bucket.block_count} classified blocks · ${viewLabels[state.view]} view`
            : `All ${summary.classified_block_count || 0} classified blocks · ${viewLabels[state.view]} view`;
        }
      }

      function render() {
        updateWeekControls();
        document.querySelectorAll('#view-controls button').forEach((button) => {
          button.classList.toggle('active', button.dataset.view === state.view);
        });
        const filtered = buildFilteredGraph();
        updateCards(filtered);
        renderTypeBars(filtered);
        renderPeriodHighlights(filtered);
        renderBars('pagerank-bars', filtered, 'pagerank', (value) => value.toFixed(6));
        renderBars('degree-bars', filtered, 'weighted_degree_filtered', (value) => fmt.format(value));
        renderBars('activity-bars', filtered, 'activity_score', (value) => fmt.format(value));
        renderTopTable(filtered);
        renderSourceTable(filtered);
        drawNetwork(filtered);
      }

      document.querySelectorAll('#view-controls button').forEach((button) => {
        button.addEventListener('click', () => {
          state.view = button.dataset.view || 'combined';
          render();
        });
      });
      const slider = document.getElementById('week-slider');
      if (slider) {
        slider.addEventListener('input', () => {
          state.weekIndex = Number(slider.value || 0);
          render();
        });
      }
      render();
    })();
  </script>
</body>
</html>
"""
    replacements = {
        "__GENERATED__": html_lib.escape(str(summary.get("generated_at", ""))),
        "__INPUT__": html_lib.escape(input_path.as_posix()),
        "__DATE_RANGE__": html_lib.escape(date_range_text),
        "__BLOCKS__": html_lib.escape(str(summary.get("classified_block_count", 0))),
        "__NODES__": html_lib.escape(str(summary.get("node_count", 0))),
        "__EDGES__": html_lib.escape(str(summary.get("edge_count", 0))),
        "__CLUSTERS__": html_lib.escape(str(summary.get("cluster_count", 0))),
        "__EDGE_WEIGHT__": html_lib.escape(str(summary.get("total_edge_weight", 0))),
        "__NODE_TYPES__": html_lib.escape(format_type_counts(summary.get("node_counts_by_type", {}))),
        "__RECENT_WINDOWS_STATIC__": static_recent,
        "__GRAPH_DATA__": graph_data_json,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template

def node_row_to_dict(row: NodeRow) -> dict[str, Any]:
    return {
        "node_id": row.node_id,
        "node_type": row.node_type,
        "label": row.label,
        "note_path": row.note_path,
        "unique_neighbors": row.unique_neighbors,
        "degree": row.degree,
        "in_degree": row.in_degree,
        "out_degree": row.out_degree,
        "weighted_in_degree": row.weighted_in_degree,
        "weighted_out_degree": row.weighted_out_degree,
        "weighted_degree": row.weighted_degree,
        "two_hop_reach": row.two_hop_reach,
        "pagerank": row.pagerank,
        "mention_count": row.mention_count,
        "block_count": row.block_count,
        "cluster_id": row.cluster_id,
        "recent_block_count": row.recent_block_count,
        "recency_score": row.recency_score,
    }


def render_recent_windows_html(summary: dict[str, Any]) -> str:
    windows = summary.get("recent_windows", [])
    if not windows:
        return '<p class="small">No recent-window data available.</p>'
    cards: list[str] = []
    for window in windows:
        top_tickers = ", ".join(
            f"{html_lib.escape(str(item.get('label', '')))} ({html_lib.escape(str(item.get('mentions', 0)))})"
            for item in window.get("top_tickers", [])[:5]
        ) or "n/a"
        top_themes = ", ".join(
            f"{html_lib.escape(str(item.get('label', '')))} ({html_lib.escape(str(item.get('mentions', 0)))})"
            for item in window.get("top_themes", [])[:5]
        ) or "n/a"
        cards.append(
            "".join(
                [
                    '<div class="recent-card">',
                    "<strong>",
                    html_lib.escape(str(window.get("window_days", ""))),
                    "d: ",
                    html_lib.escape(str(window.get("cutoff_date", ""))),
                    " → ",
                    html_lib.escape(str(window.get("anchor_date", ""))),
                    "</strong>",
                    '<div class="small">Blocks ',
                    html_lib.escape(str(window.get("block_count", 0))),
                    " · Sources ",
                    html_lib.escape(str(window.get("unique_sources", 0))),
                    " · Tickers ",
                    html_lib.escape(str(window.get("unique_tickers", 0))),
                    " · Themes ",
                    html_lib.escape(str(window.get("unique_themes", 0))),
                    "</div>",
                    '<div class="small">Top tickers: ',
                    top_tickers,
                    "</div>",
                    '<div class="small">Top themes: ',
                    top_themes,
                    "</div>",
                    "</div>",
                ]
            )
        )
    return "\n".join(cards)


def render_leaderboard(
    *,
    node_rows: list[NodeRow],
    top_n: int,
    metric_key: Any,
    metric_name: str,
) -> str:
    selected = sorted(
        node_rows,
        key=lambda row: (-float(metric_key(row)), row.node_type, row.label.casefold(), row.label),
    )[:top_n]
    header = "| Rank | Node | Type | Value | Weighted Degree | Two-Hop Reach | Blocks | Recent | Cluster |"
    divider = "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    rows = [header, divider]
    for index, row in enumerate(selected, start=1):
        value = metric_key(row)
        if metric_name == "pagerank":
            rendered_value = f"{value:.6f}"
        elif metric_name == "recency_score":
            rendered_value = f"{value:.4f}"
        else:
            rendered_value = str(int(value))
        rows.append(
            "| {rank} | {node} | {node_type} | {value} | {weighted_degree} | {two_hop_reach} | {blocks} | {recent} | {cluster} |".format(
                rank=index,
                node=render_node_link(row),
                node_type=row.node_type,
                value=rendered_value,
                weighted_degree=row.weighted_degree,
                two_hop_reach=row.two_hop_reach,
                blocks=row.block_count,
                recent=row.recent_block_count,
                cluster=row.cluster_id,
            )
        )
    return "\n".join(rows)


def render_node_link(row: NodeRow) -> str:
    if row.note_path:
        return f"[[{row.note_path}|{row.label}]]"
    return row.label


def render_recent_windows(summary: dict[str, Any]) -> str:
    windows = summary.get("recent_windows", [])
    if not windows:
        return ""
    sections: list[str] = []
    for window in windows:
        lines = [
            "## Recent: {days}d ({cutoff} \u2192 {anchor})".format(
                days=window["window_days"],
                cutoff=window["cutoff_date"],
                anchor=window["anchor_date"],
            ),
            "",
            "- Blocks: {blocks} | Sources: {sources} | Tickers: {tickers} | Themes: {themes}".format(
                blocks=window["block_count"],
                sources=window["unique_sources"],
                tickers=window["unique_tickers"],
                themes=window["unique_themes"],
            ),
        ]
        top_tickers = window.get("top_tickers", [])
        if top_tickers:
            items = ", ".join(f"{t['label']} ({t['mentions']})" for t in top_tickers[:5])
            lines.append(f"- Top tickers: {items}")
        top_themes = window.get("top_themes", [])
        if top_themes:
            items = ", ".join(f"{t['label']} ({t['mentions']})" for t in top_themes[:5])
            lines.append(f"- Top themes: {items}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def render_source_coverage(source_coverage: dict[str, dict[str, Any]]) -> str:
    header = "| Source | Blocks | Recent | Tickers | Themes |"
    divider = "| --- | ---: | ---: | ---: | ---: |"
    rows = [header, divider]
    for name, stats in sorted(source_coverage.items()):
        rows.append(
            f"| {name} | {stats['blocks']} | {stats['recent_blocks']} "
            f"| {len(stats['tickers'])} | {len(stats['themes'])} |"
        )
    return "\n".join(rows)


def render_clusters(clusters: dict[str, list[str]], node_rows: list[NodeRow]) -> str:
    if not clusters:
        return "_No clusters detected._"
    node_map = {row.node_id: row for row in node_rows}
    header = "| Cluster | Size | Members |"
    divider = "| ---: | ---: | --- |"
    rows = [header, divider]
    for cid, members in sorted(clusters.items(), key=lambda item: int(item[0])):
        member_labels = []
        for node_id in members:
            row = node_map.get(node_id)
            if row:
                member_labels.append(render_node_link(row))
            else:
                member_labels.append(node_id)
        rows.append(f"| {cid} | {len(members)} | {', '.join(member_labels)} |")
    return "\n".join(rows)


def format_type_counts(node_counts_by_type: dict[str, int]) -> str:
    parts = [f"{node_type}: {count}" for node_type, count in sorted(node_counts_by_type.items())]
    return ", ".join(parts) if parts else "none"


def write_artifacts(output_dir: Path, artifacts: GraphArtifacts) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_nodes_csv(output_dir / "nodes.csv", artifacts.node_rows)
    write_edges_csv(output_dir / "edges.csv", artifacts.edge_rows)
    (output_dir / "summary.json").write_text(json.dumps(artifacts.summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "dashboard.md").write_text(artifacts.dashboard, encoding="utf-8")
    (output_dir / "dashboard.html").write_text(artifacts.dashboard_html, encoding="utf-8")


def write_nodes_csv(path: Path, node_rows: list[NodeRow]) -> None:
    fieldnames = [
        "node_id",
        "node_type",
        "label",
        "note_path",
        "unique_neighbors",
        "degree",
        "in_degree",
        "out_degree",
        "weighted_in_degree",
        "weighted_out_degree",
        "weighted_degree",
        "two_hop_reach",
        "pagerank",
        "mention_count",
        "block_count",
        "cluster_id",
        "recent_block_count",
        "recency_score",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in node_rows:
            writer.writerow(
                {
                    "node_id": row.node_id,
                    "node_type": row.node_type,
                    "label": row.label,
                    "note_path": row.note_path or "",
                    "unique_neighbors": row.unique_neighbors,
                    "degree": row.degree,
                    "in_degree": row.in_degree,
                    "out_degree": row.out_degree,
                    "weighted_in_degree": row.weighted_in_degree,
                    "weighted_out_degree": row.weighted_out_degree,
                    "weighted_degree": row.weighted_degree,
                    "two_hop_reach": row.two_hop_reach,
                    "pagerank": f"{row.pagerank:.12f}",
                    "mention_count": row.mention_count,
                    "block_count": row.block_count,
                    "cluster_id": row.cluster_id,
                    "recent_block_count": row.recent_block_count,
                    "recency_score": f"{row.recency_score:.6f}",
                }
            )


def write_edges_csv(path: Path, edge_rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "source_id",
        "source_type",
        "source_label",
        "target_id",
        "target_type",
        "target_label",
        "weight",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(edge_rows)


def _normalize_lookup_key(value: str) -> str:
    text = value.casefold().replace("&", " and ").replace("/", " and ")
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _node_sort_key(node: NodeKey) -> tuple[str, str, str]:
    node_type, label = node
    return (node_type, label.casefold(), label)


if __name__ == "__main__":
    raise SystemExit(main())
