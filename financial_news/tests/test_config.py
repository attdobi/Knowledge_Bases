from __future__ import annotations

from pathlib import Path

from financial_news.config import AppConfig, project_root


def test_app_config_load_prefers_explicit_values_and_resolves_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FINANCIAL_NEWS_DSN", "postgresql://env-dsn")
    output_root = tmp_path / "vault"
    state_path = tmp_path / "state" / "custom_state.json"

    config = AppConfig.load(
        dsn="postgresql://flag-dsn",
        output_root=str(output_root),
        state_path=str(state_path),
    )

    assert config.dsn == "postgresql://flag-dsn"
    assert config.output_root == output_root.resolve()
    assert config.state_path == state_path.resolve()


def test_app_config_load_uses_env_fallbacks_and_default_paths(monkeypatch) -> None:
    monkeypatch.delenv("FINANCIAL_NEWS_DSN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://database-url")

    config = AppConfig.load()

    assert config.dsn == "postgresql://database-url"
    assert config.output_root == project_root()
    assert config.state_path == project_root() / ".state" / "ingest_state.json"


def test_app_config_load_prefers_financial_news_dsn_over_database_url(monkeypatch) -> None:
    monkeypatch.setenv("FINANCIAL_NEWS_DSN", "postgresql://financial-news-dsn")
    monkeypatch.setenv("DATABASE_URL", "postgresql://database-url")

    config = AppConfig.load()

    assert config.dsn == "postgresql://financial-news-dsn"
