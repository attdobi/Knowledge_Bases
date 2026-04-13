from __future__ import annotations

from pathlib import Path

import pytest

from financial_news.config import AppConfig, default_output_root, parse_path_remap


def test_app_config_load_supports_env_cli_and_safe_default_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FINANCIAL_NEWS_DSN", "postgresql://env")
    monkeypatch.setenv("FINANCIAL_NEWS_PATH_REMAPS", "/Users/adobi=/Volumes/adobi")
    monkeypatch.setenv("FINANCIAL_NEWS_OUTPUT_ROOT", str(tmp_path / "vault-from-env"))

    config = AppConfig.load(
        path_remaps=["/Users/adobi/d-ai-trader=/Volumes/adobi/d-ai-trader"],
        attachment_mode="preserve",
        attachment_format="png",
        attachment_quality=91,
        attachment_max_dimension=4096,
    )

    assert config.dsn == "postgresql://env"
    assert config.output_root == (tmp_path / "vault-from-env").resolve()
    assert config.state_path == (tmp_path / "vault-from-env" / ".state" / "ingest_state.json").resolve()
    assert config.attachments.mode == "preserve"
    assert config.attachments.image_format == "png"
    assert config.attachments.image_quality == 91
    assert config.attachments.max_dimension == 4096
    assert config.attachments.path_remaps == (
        parse_path_remap("/Users/adobi=/Volumes/adobi"),
        parse_path_remap("/Users/adobi/d-ai-trader=/Volumes/adobi/d-ai-trader"),
    )


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


def test_app_config_load_prefers_financial_news_dsn_over_database_url(monkeypatch) -> None:
    monkeypatch.setenv("FINANCIAL_NEWS_DSN", "postgresql://financial-news-dsn")
    monkeypatch.setenv("DATABASE_URL", "postgresql://database-url")

    config = AppConfig.load()

    assert config.dsn == "postgresql://financial-news-dsn"


def test_default_output_root_stays_under_generated_directory() -> None:
    assert default_output_root().as_posix().endswith("/.generated/vault")


@pytest.mark.parametrize("value", ["", "missing-separator", "=/tmp/out", "/tmp/in="])
def test_parse_path_remap_rejects_invalid_specs(value: str) -> None:
    with pytest.raises(ValueError):
        parse_path_remap(value)
