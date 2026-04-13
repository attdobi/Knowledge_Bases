from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_news.state import StateStore


def test_state_store_round_trip_and_reset(tmp_path: Path) -> None:
    state_path = tmp_path / ".state" / "ingest_state.json"
    store = StateStore(state_path)

    assert store.load() == {}

    store.save({"last_processed_id": 42, "status": "ok"})

    assert store.load() == {"last_processed_id": 42, "status": "ok"}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"last_processed_id": 42, "status": "ok"}

    store.reset()
    assert not state_path.exists()


def test_state_store_raises_clear_error_for_invalid_json(tmp_path: Path) -> None:
    state_path = tmp_path / ".state" / "ingest_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"last_processed_id": 42', encoding="utf-8")

    store = StateStore(state_path)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        store.load()
