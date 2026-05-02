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
    .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
    .card .value { margin-top: 8px; font-size: 28px; font-weight: 750; letter-spacing: -0.03em; }
    section { padding: 18px; min-width: 0; }
    .two-col { grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr); align-items: start; }
    .three-col { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--muted); margin-top: 12px; }
    .pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 999px; padding: 3px 9px; color: #cbd5e1; background: rgba(15,23,42,0.48); }
    .dot { width: 8px; height: 8px; border-radius: 999px; display: inline-block; }
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
    #network { width: 100%; min-height: 520px; border-radius: 14px; background: rgba(15,23,42,0.58); border: 1px solid var(--border); }
    .network-note { margin: 10px 0 0; color: var(--muted); font-size: 13px; }
    .type-source { color: var(--source); }
    .type-ticker { color: var(--ticker); }
    .type-theme { color: var(--theme); }
    .recent-list { display: grid; gap: 10px; }
    .recent-card { padding: 12px; border: 1px solid var(--border); border-radius: 14px; background: rgba(15,23,42,0.35); }
    .recent-card strong { color: #f8fafc; }
    .small { color: var(--muted); font-size: 13px; }
    .artifacts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    @media (max-width: 900px) { .shell { padding: 18px; } .two-col { grid-template-columns: 1fr; } }
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

    <div class=\"grid cards\">
      <div class=\"card\"><div class=\"label\">Classified blocks</div><div class=\"value\">__BLOCKS__</div></div>
      <div class=\"card\"><div class=\"label\">Nodes</div><div class=\"value\">__NODES__</div></div>
      <div class=\"card\"><div class=\"label\">Edges</div><div class=\"value\">__EDGES__</div></div>
      <div class=\"card\"><div class=\"label\">Clusters</div><div class=\"value\">__CLUSTERS__</div></div>
      <div class=\"card\"><div class=\"label\">Edge weight</div><div class=\"value\">__EDGE_WEIGHT__</div></div>
    </div>

    <div class=\"grid two-col\">
      <section>
        <h2>Network overview</h2>
        <div id=\"network\"></div>
        <p class=\"network-note\">Showing a focused subgraph from top weighted-degree, PageRank, and recent nodes. Circle size follows weighted degree; edge width follows co-mention weight.</p>
      </section>
      <section>
        <h2>Node mix</h2>
        <p class=\"small\">__NODE_TYPES__</p>
        <div id=\"type-bars\"></div>
        <h2 style=\"margin-top:22px\">Recent windows</h2>
        <div id=\"recent-windows\" class=\"recent-list\">__RECENT_WINDOWS_STATIC__</div>
      </section>
    </div>

    <div class=\"grid three-col\" style=\"margin-top:16px\">
      <section><h2>Top PageRank</h2><div id=\"pagerank-bars\"></div></section>
      <section><h2>Top weighted degree</h2><div id=\"degree-bars\"></div></section>
      <section><h2>Top recency</h2><div id=\"recency-bars\"></div></section>
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
  <script>
    (() => {
      const data = JSON.parse(document.getElementById('graph-data').textContent);
      const nodes = data.nodes || [];
      const edges = data.edges || [];
      const summary = data.summary || {};
      const topN = Math.max(1, data.top_n || 15);
      const typeColors = { source: '#38bdf8', ticker: '#a78bfa', theme: '#34d399' };

      const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
      const metricValue = (node, metric) => Number(node[metric] || 0);
      const byMetric = (metric) => nodes.slice().sort((a, b) => metricValue(b, metric) - metricValue(a, metric) || a.node_id.localeCompare(b.node_id));
      const topNodes = (metric, limit = topN) => byMetric(metric).slice(0, limit);
      const maxOf = (items, metric) => Math.max(1e-12, ...items.map((node) => metricValue(node, metric)));

      function clear(element) { while (element.firstChild) element.removeChild(element.firstChild); }
      function appendText(parent, text) { parent.appendChild(document.createTextNode(text)); }
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

      function renderBars(containerId, metric, formatter = (value) => fmt.format(value)) {
        const container = document.getElementById(containerId);
        if (!container) return;
        clear(container);
        const items = topNodes(metric);
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
          fill.style.width = `${Math.max(2, metricValue(node, metric) / maxValue * 100)}%`;
          track.appendChild(fill);
          const value = document.createElement('div');
          value.className = 'bar-value';
          value.textContent = formatter(metricValue(node, metric));
          row.append(label, track, value);
          container.appendChild(row);
        }
      }

      function renderTypeBars() {
        const container = document.getElementById('type-bars');
        if (!container) return;
        clear(container);
        const counts = summary.node_counts_by_type || {};
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

      function renderTopTable() {
        const table = document.getElementById('top-table');
        if (!table) return;
        clear(table);
        const thead = table.createTHead();
        const head = thead.insertRow();
        ['Rank', 'Node', 'Type', 'PageRank', 'Weighted degree', 'Two-hop reach', 'Blocks', 'Recent', 'Cluster'].forEach((name, index) => {
          const th = document.createElement('th');
          th.textContent = name;
          if (index > 2) th.className = 'num';
          head.appendChild(th);
        });
        const tbody = table.createTBody();
        topNodes('weighted_degree', topN).forEach((node, index) => {
          const row = tbody.insertRow();
          const values = [index + 1, null, node.node_type, node.pagerank.toFixed(6), node.weighted_degree, node.two_hop_reach, node.block_count, node.recent_block_count, node.cluster_id];
          values.forEach((value, cellIndex) => {
            const cell = row.insertCell();
            if (cellIndex === 1) cell.appendChild(nodeLink(node));
            else cell.textContent = value;
            if (cellIndex > 2 || cellIndex === 0) cell.className = 'num';
          });
        });
      }

      function renderSourceTable() {
        const table = document.getElementById('source-table');
        if (!table) return;
        clear(table);
        const coverage = summary.source_coverage || {};
        const rows = Object.entries(coverage).sort((a, b) => b[1].blocks - a[1].blocks || a[0].localeCompare(b[0]));
        const thead = table.createTHead();
        const head = thead.insertRow();
        ['Source', 'Blocks', 'Recent', 'Tickers', 'Themes'].forEach((name, index) => {
          const th = document.createElement('th');
          th.textContent = name;
          if (index > 0) th.className = 'num';
          head.appendChild(th);
        });
        const tbody = table.createTBody();
        for (const [name, stats] of rows) {
          const row = tbody.insertRow();
          [name, stats.blocks, stats.recent_blocks, (stats.tickers || []).length, (stats.themes || []).length].forEach((value, index) => {
            const cell = row.insertCell();
            cell.textContent = value;
            if (index > 0) cell.className = 'num';
          });
        }
      }

      function renderRecentWindows() {
        const container = document.getElementById('recent-windows');
        if (!container || container.dataset.rendered === '1') return;
        clear(container);
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
          tickers.textContent = `Top tickers: ${(window.top_tickers || []).slice(0, 5).map((item) => `${item.label} (${item.mentions})`).join(', ') || 'n/a'}`;
          const themes = document.createElement('div');
          themes.className = 'small';
          themes.textContent = `Top themes: ${(window.top_themes || []).slice(0, 5).map((item) => `${item.label} (${item.mentions})`).join(', ') || 'n/a'}`;
          card.append(title, counts, tickers, themes);
          container.appendChild(card);
        }
        container.dataset.rendered = '1';
      }

      function drawNetwork() {
        const container = document.getElementById('network');
        if (!container) return;
        clear(container);
        const selected = [];
        const seen = new Set();
        const add = (items) => {
          for (const node of items) {
            if (selected.length >= 48) return;
            if (!seen.has(node.node_id)) {
              seen.add(node.node_id);
              selected.push(node);
            }
          }
        };
        add(topNodes('weighted_degree', 28));
        add(topNodes('pagerank', 14));
        add(topNodes('recency_score', 10));
        const selectedIds = new Set(selected.map((node) => node.node_id));
        const nodeById = new Map(selected.map((node) => [node.node_id, node]));
        const graphEdges = edges
          .filter((edge) => selectedIds.has(edge.source_id) && selectedIds.has(edge.target_id))
          .sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))
          .slice(0, 140);
        const width = 960;
        const height = 560;
        const cx = width / 2;
        const cy = height / 2;
        const radius = Math.min(width, height) * 0.38;
        const maxDegree = Math.max(1, ...selected.map((node) => Number(node.weighted_degree || 0)));
        const maxWeight = Math.max(1, ...graphEdges.map((edge) => Number(edge.weight || 0)));
        const positions = new Map();
        selected.forEach((node, index) => {
          const angle = -Math.PI / 2 + (index / Math.max(1, selected.length)) * Math.PI * 2;
          const clusterOffset = ((Number(node.cluster_id || 0) % 5) - 2) * 16;
          positions.set(node.node_id, {
            x: cx + Math.cos(angle) * (radius + clusterOffset),
            y: cy + Math.sin(angle) * (radius + clusterOffset),
          });
        });
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'Financial news graph network visualization');
        svg.style.width = '100%';
        svg.style.height = '520px';

        for (const edge of graphEdges) {
          const source = positions.get(edge.source_id);
          const target = positions.get(edge.target_id);
          if (!source || !target) continue;
          const line = document.createElementNS(svg.namespaceURI, 'line');
          line.setAttribute('x1', source.x);
          line.setAttribute('y1', source.y);
          line.setAttribute('x2', target.x);
          line.setAttribute('y2', target.y);
          line.setAttribute('stroke', '#64748b');
          line.setAttribute('stroke-opacity', '0.34');
          line.setAttribute('stroke-width', String(0.8 + Number(edge.weight || 0) / maxWeight * 4.2));
          const title = document.createElementNS(svg.namespaceURI, 'title');
          title.textContent = `${edge.source_id} → ${edge.target_id}: ${edge.weight}`;
          line.appendChild(title);
          svg.appendChild(line);
        }

        for (const node of selected) {
          const pos = positions.get(node.node_id);
          if (!pos) continue;
          const group = document.createElementNS(svg.namespaceURI, 'g');
          const circle = document.createElementNS(svg.namespaceURI, 'circle');
          const size = 7 + Math.sqrt(Number(node.weighted_degree || 0) / maxDegree) * 17;
          circle.setAttribute('cx', pos.x);
          circle.setAttribute('cy', pos.y);
          circle.setAttribute('r', String(size));
          circle.setAttribute('fill', typeColors[node.node_type] || '#94a3b8');
          circle.setAttribute('fill-opacity', '0.86');
          circle.setAttribute('stroke', '#e2e8f0');
          circle.setAttribute('stroke-opacity', '0.72');
          circle.setAttribute('stroke-width', '1.2');
          const title = document.createElementNS(svg.namespaceURI, 'title');
          title.textContent = `${node.node_id}\nweighted degree: ${node.weighted_degree}\nPageRank: ${Number(node.pagerank || 0).toFixed(6)}\ncluster: ${node.cluster_id}`;
          circle.appendChild(title);
          group.appendChild(circle);
          if (size >= 12 || selected.length <= 34) {
            const label = document.createElementNS(svg.namespaceURI, 'text');
            label.setAttribute('x', pos.x + size + 4);
            label.setAttribute('y', pos.y + 4);
            label.setAttribute('fill', '#e5e7eb');
            label.setAttribute('font-size', '11');
            label.setAttribute('paint-order', 'stroke');
            label.setAttribute('stroke', '#0f172a');
            label.setAttribute('stroke-width', '3');
            label.textContent = node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label;
            group.appendChild(label);
          }
          svg.appendChild(group);
        }

        container.appendChild(svg);
      }

      renderTypeBars();
      renderRecentWindows();
      renderBars('pagerank-bars', 'pagerank', (value) => value.toFixed(6));
      renderBars('degree-bars', 'weighted_degree', (value) => fmt.format(value));
      renderBars('recency-bars', 'recency_score', (value) => value.toFixed(4));
      renderTopTable();
      renderSourceTable();
      drawNetwork();
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
