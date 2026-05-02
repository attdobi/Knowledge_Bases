from __future__ import annotations

import csv
import json
from pathlib import Path

import financial_news.graph_metrics as graph_metrics

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "organizer_report_graph.json"


def make_vault(root: Path) -> None:
    for relative_path, title in [
        ("Sources/CNBC.md", "CNBC"),
        ("Sources/Yahoo Finance.md", "Yahoo Finance"),
        ("Tickers/NVDA.md", "NVDA"),
        ("Tickers/MSFT.md", "MSFT"),
        ("Tickers/AAPL.md", "AAPL"),
        ("Topics/AI.md", "AI"),
        ("Themes/Technology.md", "Technology"),
    ]:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n", encoding="utf-8")


def build_fixture_artifacts(vault_root: Path) -> graph_metrics.GraphArtifacts:
    organizer_report = graph_metrics.load_organizer_report(FIXTURE_PATH)
    return graph_metrics.build_graph_artifacts(
        organizer_report=organizer_report,
        vault_root=vault_root,
        input_path=Path("Organizer/organizer-report-latest.json"),
        output_dir=Path("Organizer/graph-metrics"),
        top_n=10,
    )


def test_build_graph_artifacts_uses_classified_blocks_only(tmp_path: Path) -> None:
    make_vault(tmp_path)

    artifacts = build_fixture_artifacts(tmp_path)
    node_rows = {row.node_id: row for row in artifacts.node_rows}
    edge_rows = {
        (row["source_id"], row["target_id"]): row["weight"]
        for row in artifacts.edge_rows
    }

    assert artifacts.summary["classified_block_count"] == 3
    assert artifacts.summary["date_range"] == {"start": "2026-04-10", "end": "2026-04-11"}
    assert artifacts.summary["node_count"] == 7
    assert artifacts.summary["edge_count"] == 16
    assert artifacts.summary["total_edge_weight"] == 20
    assert artifacts.summary["node_counts_by_type"] == {"source": 2, "theme": 2, "ticker": 3}

    assert "ticker:TSLA" not in node_rows
    assert "theme:Autos" not in node_rows
    assert node_rows["source:CNBC"].block_count == 2
    assert node_rows["source:Yahoo Finance"].block_count == 1
    assert node_rows["source:CNBC"].note_path == "Sources/CNBC"
    assert node_rows["source:Yahoo Finance"].note_path == "Sources/Yahoo Finance"

    assert edge_rows[("source:CNBC", "ticker:NVDA")] == 2
    assert edge_rows[("source:CNBC", "theme:AI")] == 2
    assert edge_rows[("theme:AI", "ticker:NVDA")] == 2
    assert edge_rows[("source:Yahoo Finance", "theme:Technology")] == 1
    assert edge_rows[("theme:Technology", "ticker:AAPL")] == 1

    # recent_windows present with both 7d and 30d entries
    assert "recent_windows" in artifacts.summary
    assert len(artifacts.summary["recent_windows"]) == 2
    assert artifacts.summary["recent_windows"][0]["window_days"] == 7
    assert artifacts.summary["recent_windows"][1]["window_days"] == 30


def test_build_graph_artifacts_computes_expected_metrics(tmp_path: Path) -> None:
    make_vault(tmp_path)

    artifacts = build_fixture_artifacts(tmp_path)
    node_rows = {row.node_id: row for row in artifacts.node_rows}

    cnbc = node_rows["source:CNBC"]
    nvda = node_rows["ticker:NVDA"]
    ai = node_rows["theme:AI"]
    technology = node_rows["theme:Technology"]

    assert cnbc.unique_neighbors == 4
    assert cnbc.in_degree == 0
    assert cnbc.out_degree == 4
    assert cnbc.weighted_out_degree == 6
    assert cnbc.two_hop_reach == 5

    assert nvda.note_path == "Tickers/NVDA"
    assert nvda.in_degree == 3
    assert nvda.out_degree == 2
    assert nvda.weighted_in_degree == 5
    assert nvda.weighted_out_degree == 3
    assert nvda.weighted_degree == 8
    assert nvda.two_hop_reach == 4

    assert ai.note_path == "Topics/AI"
    assert ai.weighted_in_degree == 5
    assert ai.weighted_out_degree == 3
    assert ai.weighted_degree == 8

    assert technology.note_path == "Themes/Technology"
    assert technology.unique_neighbors == 5
    assert technology.weighted_in_degree == 5
    assert technology.weighted_out_degree == 3
    assert technology.weighted_degree == 8
    assert technology.pagerank > ai.pagerank > cnbc.pagerank

    assert artifacts.summary["top_nodes"]["pagerank"][0]["label"] == "Technology"
    assert artifacts.summary["top_nodes"]["weighted_degree"][0]["label"] == "AI"


def test_main_writes_graph_metric_outputs_and_dashboard(tmp_path: Path) -> None:
    make_vault(tmp_path)
    organizer_dir = tmp_path / "Organizer"
    organizer_dir.mkdir()
    report_path = organizer_dir / "organizer-report-latest.json"
    report_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    output_dir = organizer_dir / "graph-metrics"

    result = graph_metrics.main(
        [
            "--input",
            str(report_path),
            "--output-dir",
            str(output_dir),
            "--top-n",
            "10",
        ]
    )

    assert result == 0
    assert (output_dir / "nodes.csv").exists()
    assert (output_dir / "edges.csv").exists()
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "dashboard.md").exists()

    with (output_dir / "nodes.csv").open("r", encoding="utf-8", newline="") as handle:
        node_rows = {row["node_id"]: row for row in csv.DictReader(handle)}
    with (output_dir / "edges.csv").open("r", encoding="utf-8", newline="") as handle:
        edge_rows = {
            (row["source_id"], row["target_id"]): row["weight"]
            for row in csv.DictReader(handle)
        }

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    dashboard = (output_dir / "dashboard.md").read_text(encoding="utf-8")

    assert node_rows["ticker:NVDA"]["note_path"] == "Tickers/NVDA"
    assert node_rows["ticker:NVDA"]["weighted_degree"] == "8"
    assert edge_rows[("source:CNBC", "ticker:NVDA")] == "2"

    assert summary["classified_block_count"] == 3
    assert summary["node_count"] == 7
    assert summary["edge_count"] == 16
    assert summary["input_path"].endswith("Organizer/organizer-report-latest.json")

    assert "recent_windows" in summary
    assert len(summary["recent_windows"]) == 2

    assert "# Graph Metrics Dashboard" in dashboard
    assert "Classified blocks: 3" in dashboard
    assert "[[Themes/Technology|Technology]]" in dashboard
    assert "[[Sources/CNBC|CNBC]]" in dashboard
    assert "Recent: 7d" in dashboard
    assert "Recent: 30d" in dashboard
    assert "Graph built only from organizer blocks with `status == classified`." in dashboard
    assert "Metrics come from the structured organizer report, not a vault-wide scrape." in dashboard


def test_normalize_source_label_variants() -> None:
    n = graph_metrics.normalize_source_label
    # Existing agent-prefix behavior
    assert n("Agent CNBC") == "CNBC"
    assert n("Agent Yahoo Finance") == "Yahoo Finance"
    # Underscore + agent prefix
    assert n("agent_cnbc") == "CNBC"
    assert n("Agent_Yahoo_Finance") == "Yahoo Finance"
    # Dash + agent prefix
    assert n("Agent-Fox_Business") == "Fox Business"
    assert n("agent-benzinga") == "Benzinga"
    # source/src prefixes
    assert n("source MarketBeat") == "MarketBeat"
    assert n("source_MarketBeat") == "MarketBeat"
    assert n("src CNBC") == "CNBC"
    assert n("src_fox_business") == "Fox Business"
    # Abbreviation dot drift
    assert n("B.B.C. Business") == "BBC Business"
    assert n("A.P. Business") == "AP Business"
    # Whitespace normalization
    assert n("  Agent   Yahoo Finance  ") == "Yahoo Finance"
    # Empty / None
    assert n("") == ""
    assert n(None) == ""
    # Unknown source passes through cleaned
    assert n("Agent SomeNewSource") == "SomeNewSource"
    assert n("source_SomeNewSource") == "SomeNewSource"


def test_recent_window_stats_filters_by_date() -> None:
    report = {
        "generated_at": "2026-04-14T18:00:00+00:00",
        "blocks": [
            {
                "row_id": 1,
                "date": "2026-03-15",
                "source": "CNBC",
                "status": "classified",
                "tickers": ["AAPL"],
                "themes": ["Technology"],
            },
            {
                "row_id": 2,
                "date": "2026-04-10",
                "source": "CNBC",
                "status": "classified",
                "tickers": ["NVDA"],
                "themes": ["AI"],
            },
            {
                "row_id": 3,
                "date": "2026-04-11",
                "source": "Yahoo Finance",
                "status": "classified",
                "tickers": ["MSFT"],
                "themes": ["AI"],
            },
        ],
    }
    classified_blocks = list(graph_metrics.iter_classified_blocks(report))
    classified_dates = sorted(
        {
            str(b.get("date")).strip()
            for b in classified_blocks
            if str(b.get("date") or "").strip()
        }
    )

    windows = graph_metrics.compute_recent_window_stats(classified_blocks, classified_dates)
    assert len(windows) == 2

    # 7d window: anchor 2026-04-11, cutoff 2026-04-05 → only blocks 2,3
    w7 = windows[0]
    assert w7["window_days"] == 7
    assert w7["anchor_date"] == "2026-04-11"
    assert w7["cutoff_date"] == "2026-04-05"
    assert w7["block_count"] == 2
    assert w7["unique_sources"] == 2
    assert w7["unique_tickers"] == 2
    assert w7["unique_themes"] == 1

    # 30d window: anchor 2026-04-11, cutoff 2026-03-13 → all 3 blocks
    w30 = windows[1]
    assert w30["window_days"] == 30
    assert w30["anchor_date"] == "2026-04-11"
    assert w30["cutoff_date"] == "2026-03-13"
    assert w30["block_count"] == 3
    assert w30["unique_sources"] == 2
    assert w30["unique_tickers"] == 3
    assert w30["unique_themes"] == 2

    # Top tickers in 30d window are deterministically sorted
    top_ticker_labels = [t["label"] for t in w30["top_tickers"]]
    assert "AAPL" in top_ticker_labels
    assert "MSFT" in top_ticker_labels
    assert "NVDA" in top_ticker_labels


def test_recent_window_stats_empty_dates() -> None:
    assert graph_metrics.compute_recent_window_stats([], []) == []


# ---------------------------------------------------------------------------
# v2 hardening tests
# ---------------------------------------------------------------------------

EXTENDED_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "organizer_report_graph_extended.json"


def make_extended_vault(root: Path) -> None:
    make_vault(root)
    for relative_path, title in [
        ("Sources/Fox Business.md", "Fox Business"),
        ("Tickers/TSLA.md", "TSLA"),
        ("Tickers/XOM.md", "XOM"),
        ("Themes/Energy.md", "Energy"),
    ]:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n", encoding="utf-8")


def build_extended_fixture_artifacts(vault_root: Path) -> graph_metrics.GraphArtifacts:
    organizer_report = graph_metrics.load_organizer_report(EXTENDED_FIXTURE_PATH)
    return graph_metrics.build_graph_artifacts(
        organizer_report=organizer_report,
        vault_root=vault_root,
        input_path=Path("Organizer/organizer-report-latest.json"),
        output_dir=Path("Organizer/graph-metrics"),
        top_n=10,
    )


def test_normalize_theme_label_aliases() -> None:
    n = graph_metrics.normalize_theme_label
    assert n("artificial intelligence") == "AI"
    assert n("Artificial Intelligence") == "AI"
    assert n("AI") == "AI"
    assert n("tech") == "Technology"
    assert n("Technology") == "Technology"
    assert n("rates and fed") == "Rates & Fed"
    assert n("rates & the fed") == "Rates & Fed"
    assert n("war and geopolitics") == "War & Geopolitics"
    assert n("geopolitics") == "War & Geopolitics"
    assert n("Some New Theme") == "Some New Theme"
    assert n("") == ""
    assert n(None) == ""


def test_sorted_unique_theme_labels_deduplicates_aliases() -> None:
    labels = graph_metrics.sorted_unique_theme_labels(["AI", "artificial intelligence", "Technology"])
    assert labels == ["AI", "Technology"]


def test_recency_weight_function() -> None:
    assert graph_metrics.recency_weight(0) == 1.0
    assert graph_metrics.recency_weight(-1) == 1.0
    half_life = graph_metrics.RECENCY_HALF_LIFE_DAYS
    w = graph_metrics.recency_weight(half_life)
    assert abs(w - 0.5) < 1e-10
    w2 = graph_metrics.recency_weight(half_life * 2)
    assert abs(w2 - 0.25) < 1e-10
    assert graph_metrics.recency_weight(1) > graph_metrics.recency_weight(7)


def test_parse_block_date() -> None:
    d = graph_metrics.parse_block_date({"date": "2026-04-10"})
    assert d is not None
    assert d.year == 2026
    assert d.month == 4
    assert d.day == 10
    assert graph_metrics.parse_block_date({"date": ""}) is None
    assert graph_metrics.parse_block_date({"date": None}) is None
    assert graph_metrics.parse_block_date({}) is None
    assert graph_metrics.parse_block_date({"date": "not-a-date"}) is None


def test_label_propagation_disconnected() -> None:
    from collections import Counter

    nodes = [("a", "1"), ("a", "2"), ("b", "1"), ("b", "2")]
    neighbors: dict[tuple[str, str], set[tuple[str, str]]] = {
        ("a", "1"): {("a", "2")},
        ("a", "2"): {("a", "1")},
        ("b", "1"): {("b", "2")},
        ("b", "2"): {("b", "1")},
    }
    edge_weights: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter({
        (("a", "1"), ("a", "2")): 1,
        (("a", "2"), ("a", "1")): 1,
        (("b", "1"), ("b", "2")): 1,
        (("b", "2"), ("b", "1")): 1,
    })
    clusters = graph_metrics.label_propagation(nodes, neighbors, edge_weights)
    # Same-component nodes share a cluster
    assert clusters[("a", "1")] == clusters[("a", "2")]
    assert clusters[("b", "1")] == clusters[("b", "2")]
    # Different components get different clusters
    assert clusters[("a", "1")] != clusters[("b", "1")]


def test_label_propagation_empty() -> None:
    from collections import Counter
    assert graph_metrics.label_propagation([], {}, Counter()) == {}


def test_extended_fixture_theme_alias_normalization(tmp_path: Path) -> None:
    """Block 7 has 'artificial intelligence' which should normalize to 'AI'."""
    make_extended_vault(tmp_path)
    artifacts = build_extended_fixture_artifacts(tmp_path)
    node_rows = {row.node_id: row for row in artifacts.node_rows}

    # 'artificial intelligence' from block 7 collapses into existing 'AI' node
    assert "theme:AI" in node_rows
    assert "theme:artificial intelligence" not in node_rows


def test_extended_fixture_two_clusters(tmp_path: Path) -> None:
    make_extended_vault(tmp_path)
    artifacts = build_extended_fixture_artifacts(tmp_path)
    summary = artifacts.summary

    assert summary["cluster_count"] == 2
    clusters = summary["clusters"]

    # Find which cluster each source is in
    fox_cluster = None
    cnbc_cluster = None
    for cid, members in clusters.items():
        if "source:Fox Business" in members:
            fox_cluster = cid
        if "source:CNBC" in members:
            cnbc_cluster = cid

    assert fox_cluster is not None
    assert cnbc_cluster is not None
    assert fox_cluster != cnbc_cluster

    # Fox Business cluster contains energy-related nodes
    assert "ticker:TSLA" in clusters[fox_cluster]
    assert "ticker:XOM" in clusters[fox_cluster]
    assert "theme:Energy" in clusters[fox_cluster]

    # CNBC cluster contains tech/AI nodes
    assert "ticker:NVDA" in clusters[cnbc_cluster]
    assert "theme:AI" in clusters[cnbc_cluster]


def test_extended_fixture_recency(tmp_path: Path) -> None:
    make_extended_vault(tmp_path)
    artifacts = build_extended_fixture_artifacts(tmp_path)
    node_rows = {row.node_id: row for row in artifacts.node_rows}

    # Stale nodes (2026-03-25 blocks, 17 days old) have 0 recent blocks
    assert node_rows["ticker:TSLA"].recent_block_count == 0
    assert node_rows["ticker:XOM"].recent_block_count == 0
    assert node_rows["theme:Energy"].recent_block_count == 0
    assert node_rows["source:Fox Business"].recent_block_count == 0

    # Recent nodes have > 0 recent blocks
    assert node_rows["source:CNBC"].recent_block_count > 0
    assert node_rows["ticker:NVDA"].recent_block_count > 0
    assert node_rows["theme:AI"].recent_block_count > 0

    # Recency score ordering: recent > stale
    assert node_rows["ticker:NVDA"].recency_score > node_rows["ticker:TSLA"].recency_score
    assert node_rows["source:CNBC"].recency_score > node_rows["source:Fox Business"].recency_score


def test_source_coverage_in_summary(tmp_path: Path) -> None:
    make_vault(tmp_path)
    artifacts = build_fixture_artifacts(tmp_path)
    coverage = artifacts.summary["source_coverage"]

    assert "CNBC" in coverage
    assert "Yahoo Finance" in coverage
    assert coverage["CNBC"]["blocks"] == 2
    assert coverage["Yahoo Finance"]["blocks"] == 1
    assert len(coverage["CNBC"]["tickers"]) >= 2  # NVDA, MSFT
    assert len(coverage["CNBC"]["themes"]) >= 1  # AI (+ Technology)


def test_dashboard_v2_sections(tmp_path: Path) -> None:
    make_vault(tmp_path)
    artifacts = build_fixture_artifacts(tmp_path)
    dashboard = artifacts.dashboard

    assert "## Recent Activity" in dashboard
    assert "## Source Coverage" in dashboard
    assert "## Clusters" in dashboard
    assert "Recent window:" in dashboard
    assert "Clusters via label propagation" in dashboard
    assert "Clusters:" in dashboard  # header line


def test_v2_node_fields_present(tmp_path: Path) -> None:
    make_vault(tmp_path)
    artifacts = build_fixture_artifacts(tmp_path)
    for row in artifacts.node_rows:
        assert isinstance(row.cluster_id, int)
        assert isinstance(row.recent_block_count, int)
        assert isinstance(row.recency_score, float)


def test_v2_csv_fields(tmp_path: Path) -> None:
    make_vault(tmp_path)
    organizer_dir = tmp_path / "Organizer"
    organizer_dir.mkdir()
    report_path = organizer_dir / "organizer-report-latest.json"
    report_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    output_dir = organizer_dir / "graph-metrics"

    graph_metrics.main(["--input", str(report_path), "--output-dir", str(output_dir)])

    with (output_dir / "nodes.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)

    assert "cluster_id" in fields
    assert "recent_block_count" in fields
    assert "recency_score" in fields

    nvda = next(r for r in rows if r["node_id"] == "ticker:NVDA")
    assert int(nvda["cluster_id"]) >= 0
    assert int(nvda["recent_block_count"]) >= 0
    assert float(nvda["recency_score"]) > 0
