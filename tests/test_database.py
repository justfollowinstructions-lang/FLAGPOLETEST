"""
Smoke test for database.py's flag_pole_signals table — confirms the
schema is valid SQL, init_db() creates it, and a round-trip
insert/read/update works. Uses a temp DB file so it never touches the
real data/scanner.db.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import database as db


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_scanner.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def _sample_row(signal_id="abc123", scan_date="2024-06-01", signal_type="BREAKOUT NOW"):
    return {
        "signal_id": signal_id,
        "symbol": "TEST.NS",
        "company_name": "Test Co",
        "sector": "Unclassified",
        "scan_date": scan_date,
        "timeframe": "daily",
        "pattern_type": "FLAG_POLE",
        "signal_type": signal_type,
        "quality_score": 82.5,
        "pole_start_date": "2024-05-20",
        "pole_end_date": "2024-05-28",
        "pole_pct_move": 22.0,
        "flag_start_date": "2024-05-29",
        "flag_end_date": "2024-06-01",
        "flag_retracement_pct": 33.0,
        "pivot_point": 150.0,
        "current_price": 155.0,
        "breakout_readiness_pct": 90,
        "entry_price": 151.5,
        "stop_loss_price": 145.0,
        "target1": 175.0,
        "target2": 190.0,
        "rr_t1": 3.6,
        "rr_t2": 5.9,
        "position_size_shares": 100,
        "status": "Watching",
        "entry_triggered": 0,
        "expiry_date": "2024-06-15",
    }


def test_init_db_creates_flag_pole_table(temp_db):
    import sqlite3
    con = sqlite3.connect(str(temp_db))
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    con.close()
    assert "flag_pole_signals" in tables


def test_upsert_and_read_flag_pole_signal(temp_db):
    row = _sample_row()
    db.upsert_flag_pole_signal(row)

    assert db.flag_pole_signal_exists("abc123") is True

    df = db.get_todays_flag_pole_signals_df("2024-06-01")
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "TEST.NS"
    assert df.iloc[0]["quality_score"] == 82.5


def test_duplicate_insert_is_ignored(temp_db):
    row = _sample_row()
    db.upsert_flag_pole_signal(row)
    db.upsert_flag_pole_signal(row)   # same signal_id — should be a no-op
    df = db.get_all_flag_pole_signals_df()
    assert len(df) == 1


def test_update_signal_status(temp_db):
    row = _sample_row()
    db.upsert_flag_pole_signal(row)
    db.update_flag_pole_signal_status("abc123", entry_triggered=1, status="Triggered")

    df = db.get_all_flag_pole_signals_df()
    assert df.iloc[0]["status"] == "Triggered"
    assert df.iloc[0]["entry_triggered"] == 1


def test_early_stage_excluded_from_todays_signals(temp_db):
    db.upsert_flag_pole_signal(_sample_row(signal_id="a", signal_type="BREAKOUT NOW"))
    db.upsert_flag_pole_signal(_sample_row(signal_id="b", signal_type="EARLY STAGE"))

    todays = db.get_todays_flag_pole_signals_df("2024-06-01")
    assert len(todays) == 1
    assert todays.iloc[0]["signal_id"] == "a"


def test_confirmed_breakouts_filter_requires_high_conviction(temp_db):
    # Passes every HCB threshold
    good = _sample_row(signal_id="good", signal_type="BREAKOUT NOW")
    good["breakout_readiness_pct"] = 95
    good["quality_score"] = 90
    good["volume_ratio"] = 3.0
    db.upsert_flag_pole_signal(good)

    # Fails on readiness
    weak = _sample_row(signal_id="weak", signal_type="BREAKOUT NOW")
    weak["breakout_readiness_pct"] = 40
    weak["quality_score"] = 90
    weak["volume_ratio"] = 3.0
    db.upsert_flag_pole_signal(weak)

    confirmed = db.get_confirmed_flag_pole_breakouts_df("2024-06-01")
    assert list(confirmed["signal_id"]) == ["good"]
