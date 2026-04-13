from __future__ import annotations

import json
from pathlib import Path

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
