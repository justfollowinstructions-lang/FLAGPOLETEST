"""
Integration test for main_flag_pole._scan_symbol() /
_finalise_and_persist_signal() — exercises detector -> readiness ->
entry/exit -> database wiring together end to end, against a
synthetic breakout dataframe and a temp DB. This is the test that
would catch a mismatched column name or a broken import between the
new modules, which the unit tests above wouldn't necessarily surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import database as db
import main_flag_pole as mfp
from tests.helpers import build_flag_pole_df


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_scanner.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def test_scan_symbol_persists_a_confirmed_breakout(temp_db, monkeypatch):
    df, *_ = build_flag_pole_df(breakout=True, breakout_vol_mult=2.5)
    monkeypatch.setattr(mfp, "load_daily", lambda symbol: df)
    monkeypatch.setattr(mfp, "get_symbol_meta", lambda symbol: {"name": "Test Co", "sector": "IT"})

    by_signal_type: dict = {}
    by_rs_band = {"<60": 0, "60-80": 0, ">80": 0}

    n_found = mfp._scan_symbol(
        "TEST.NS", {"TEST.NS": 85.0}, {"TEST.NS": "Improving"},
        "Uptrend", "2024-06-01", by_signal_type, by_rs_band,
    )

    assert n_found == 1
    assert by_signal_type.get("BREAKOUT NOW") == 1
    assert by_rs_band[">80"] == 1

    row_df = db.get_todays_flag_pole_signals_df("2024-06-01")
    assert len(row_df) == 1
    row = row_df.iloc[0]

    assert row["symbol"] == "TEST.NS"
    assert row["company_name"] == "Test Co"
    assert row["signal_type"] == "BREAKOUT NOW"
    assert row["entry_price"] > 0
    assert row["stop_loss_price"] < row["entry_price"]
    assert row["target1"] > row["entry_price"]
    assert row["target2"] > row["target1"]
    assert row["rr_t1"] > 0
    assert row["position_size_shares"] >= 0
    assert row["breakout_readiness_pct"] is not None
    assert "MANDATORY" in row["sell_notes"]


def test_scan_symbol_skips_short_history(temp_db, monkeypatch):
    df, *_ = build_flag_pole_df(base_n=5)
    monkeypatch.setattr(mfp, "load_daily", lambda symbol: df)

    n_found = mfp._scan_symbol(
        "SHORT.NS", {}, {}, "Uptrend", "2024-06-01", {}, {"<60": 0, "60-80": 0, ">80": 0},
    )
    assert n_found == 0


def test_scan_symbol_handles_missing_data(temp_db, monkeypatch):
    monkeypatch.setattr(mfp, "load_daily", lambda symbol: None)

    n_found = mfp._scan_symbol(
        "MISSING.NS", {}, {}, "Uptrend", "2024-06-01", {}, {"<60": 0, "60-80": 0, ">80": 0},
    )
    assert n_found == 0
