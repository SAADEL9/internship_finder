"""State (seen_jobs.json) load/save/prune including legacy v1 format."""
import json
from datetime import timedelta
from pathlib import Path

import scraper
from extractors import now_utc


def test_load_missing_file(tmp_path: Path):
    assert scraper.load_seen(tmp_path / "nope.json") == {}


def test_load_legacy_list(tmp_path: Path):
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"seen": ["aa", "bb"]}), encoding="utf-8")
    seen = scraper.load_seen(path)
    assert set(seen) == {"aa", "bb"}
    assert all(v for v in seen.values())  # every legacy key gets a timestamp


def test_load_v2_dict(tmp_path: Path):
    path = tmp_path / "seen.json"
    payload = {"version": 2, "seen": {"aa": "2026-08-01T00:00:00+00:00"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert scraper.load_seen(path) == {"aa": "2026-08-01T00:00:00+00:00"}


def test_load_corrupt_file(tmp_path: Path):
    path = tmp_path / "seen.json"
    path.write_text("NOT JSON", encoding="utf-8")
    assert scraper.load_seen(path) == {}


def test_save_and_prune_old_entries(tmp_path: Path):
    path = tmp_path / "seen.json"
    seen = {
        "fresh": now_utc().isoformat(),
        "stale": (now_utc() - timedelta(days=200)).isoformat(),
    }
    scraper.save_seen(seen, retention_days=90, path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert set(data["seen"]) == {"fresh"}


def test_save_keeps_unparseable_dates(tmp_path: Path):
    path = tmp_path / "seen.json"
    scraper.save_seen({"weird": "not-a-date"}, retention_days=90, path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "weird" in data["seen"]
