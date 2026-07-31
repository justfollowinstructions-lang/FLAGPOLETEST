"""
Tests for chart_export.py — builds real HTML files from synthetic
signals and asserts on the data pipeline: every tab is actually
sourced (not just today's signals), reasons/buy-thesis text gets
generated, Top Picks ranking works, the Historical cap is respected,
and duplicate signal_ids across tabs collapse into one card (tagged
with every tab it belongs to) rather than one card per tab.

Does not attempt to execute the page's JavaScript (that needs a real
browser) — validated separately via jsdom during development. This
suite covers the Python-side data pipeline, which is the part most
likely to silently produce a broken or incomplete file without an
obvious crash.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import chart_export as ce
import config as cfg
import database as db
from tests.helpers import build_flag_pole_df


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_scanner.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


@pytest.fixture()
def charts_dir(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    monkeypatch.setattr(cfg, "CHARTS_DIR", d)
    monkeypatch.setattr(ce, "cfg", cfg)
    return d


def _seed_signal(monkeypatch, symbol="AAA.NS", scan_date="2024-06-01", rs_rating=80.0,
                  market_trend="Uptrend", **df_kwargs):
    df, *_ = build_flag_pole_df(**df_kwargs)
    data_map = {symbol: df}

    # main_flag_pole.py and chart_export.py each did their own
    # `from downloader import load_daily`, so each module has its own
    # separate name binding — both need patching independently.
    monkeypatch.setattr("main_flag_pole.load_daily", lambda s, _m=data_map: _m.get(s))
    monkeypatch.setattr("main_flag_pole.get_symbol_meta", lambda s: {"name": f"{s} Co", "sector": "IT"})
    monkeypatch.setattr("chart_export.load_daily", lambda s, _m=data_map: _m.get(s))

    import main_flag_pole as mfp
    by_signal_type, by_rs_band = {}, {"<60": 0, "60-80": 0, ">80": 0}
    mfp._scan_symbol(symbol, {symbol: rs_rating}, {symbol: "Improving"}, market_trend, scan_date, by_signal_type, by_rs_band)
    return df


def _run_export(monkeypatch, scan_date="2024-06-01", trend="Uptrend", dist_days=0):
    with patch("chart_export.compute_market_regime",
               return_value={"trend": trend, "distribution_days_25d": dist_days}):
        monkeypatch.setattr(sys, "argv", ["chart_export.py", "--scan-date", scan_date])
        ce.main()


def _extract_data_blob(charts_dir: Path) -> dict:
    files = list(charts_dir.glob("flag_pole_charts_*.html"))
    assert len(files) == 1
    html = files[0].read_text(encoding="utf-8")
    m = re.search(r"const DATA = (\{.*?\});", html, re.DOTALL)
    assert m is not None, "Could not find `const DATA = {...};` in the generated HTML"
    return json.loads(m.group(1)), html


def test_no_signals_for_date_logs_and_exits_cleanly(temp_db, charts_dir, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chart_export.py", "--scan-date", "2099-01-01"])
    ce.main()   # should not raise
    assert not list(charts_dir.glob("*.html"))


def test_export_produces_well_formed_html(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=2.5)
    _run_export(monkeypatch)

    data, html = _extract_data_blob(charts_dir)

    for placeholder in ("__SCAN_DATE__", "__MARKET_TREND__", "__DISTRIBUTION_DAYS__",
                          "__SYMBOL_COUNT__", "__LIGHTWEIGHT_CHARTS_JS__", "__CHART_DATA_JSON__"):
        assert placeholder not in html, f"Unresolved placeholder: {placeholder}"

    assert "TradingView Lightweight Charts" in html
    assert "<script src=" not in html   # fully self-contained, no external/CDN script tags

    assert data["scan_date"] == "2024-06-01"
    assert data["market_trend"] == "Uptrend"
    assert len(data["symbols"]) == 1

    sym = data["symbols"][0]
    assert sym["symbol"] == "AAA.NS"
    assert sym["signal_type"] == "BREAKOUT NOW"
    assert set(sym["timeframes"].keys()) == {"1D", "1W", "1M"}
    assert len(sym["timeframes"]["1D"]) > 0

    bar = sym["timeframes"]["1D"][0]
    assert set(bar.keys()) == {"time", "o", "h", "l", "c", "v"}
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", bar["time"])


# ─── Requirement 1: every tab, not just today's signals ───────────────────

def test_symbol_is_tagged_with_every_tab_it_belongs_to(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=3.0,
                  pole_move_pct=30, rs_rating=90.0)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]

    # Ground truth: whichever tabs the DB itself says this signal_id
    # belongs to (not an assumption about what a "strong" breakout
    # should qualify for — readiness/confirmed status depends on
    # several interacting thresholds, e.g. how far price has already
    # run past the pivot, that this test shouldn't need to predict).
    todays = set(db.get_todays_flag_pole_signals_df("2024-06-01")["symbol"])
    confirmed = set(db.get_confirmed_flag_pole_breakouts_df("2024-06-01")["symbol"])
    active = set(db.get_active_flag_pole_tracking_df()["symbol"])

    assert ("todays" in sym["tabs"]) == ("AAA.NS" in todays)
    assert ("confirmed" in sym["tabs"]) == ("AAA.NS" in confirmed)
    assert ("active" in sym["tabs"]) == ("AAA.NS" in active)
    # A same-day BREAKOUT NOW should always land in at least Today's Signals
    assert "todays" in sym["tabs"]


def test_watchlist_symbol_from_a_prior_scan_date_is_still_included(temp_db, charts_dir, monkeypatch):
    """The Excel report's watchlist sheet isn't date-filtered — a
    stock detected 3 days ago that's still WATCHING should still show
    up today, exactly like it does in the Excel."""
    _seed_signal(monkeypatch, symbol="OLD.NS", scan_date="2024-05-29", breakout=False)
    # Export for a LATER date — OLD.NS's row still has scan_date=2024-05-29
    _run_export(monkeypatch, scan_date="2024-06-01")

    data, _ = _extract_data_blob(charts_dir)
    symbols = {s["symbol"]: s for s in data["symbols"]}
    assert "OLD.NS" in symbols
    assert "watchlist" in symbols["OLD.NS"]["tabs"]
    assert symbols["OLD.NS"]["scan_date"] == "2024-05-29"   # honestly reflects when it was detected


def test_duplicate_signal_id_across_tabs_produces_one_card_not_several(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=3.0, pole_move_pct=30)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    aaa_cards = [s for s in data["symbols"] if s["symbol"] == "AAA.NS"]
    assert len(aaa_cards) == 1   # one card, tagged with all its tabs — not one card per tab


def test_historical_lookback_is_capped(temp_db, charts_dir, monkeypatch):
    monkeypatch.setattr(cfg, "CHART_HISTORICAL_LOOKBACK_COUNT", 2)
    monkeypatch.setattr(ce.cfg, "CHART_HISTORICAL_LOOKBACK_COUNT", 2)

    # Seed 4 distinct closed (historical) signals directly in the DB —
    # this test only needs to prove the cap is applied to the query
    # result, not that each one is independently chartable.
    for i in range(4):
        db.upsert_flag_pole_signal({
            "signal_id": db.make_signal_id(f"H{i}.NS", "daily", "2024-01-01", 100.0 + i),
            "symbol": f"H{i}.NS", "scan_date": f"2024-05-0{i + 1}",
            "timeframe": "daily", "signal_type": "BREAKOUT NOW", "quality_score": 70.0,
            "status": "Target 1 Achieved", "entry_triggered": 1, "t1_achieved": 1,
            "stopped_out": 0, "t2_achieved": 0,
        })

    all_historical = db.get_historical_flag_pole_signals_df()
    assert len(all_historical) == 4   # sanity: all 4 really are in the DB / would appear in Excel

    tabbed = ce._collect_tabbed_rows("2024-06-01")
    historical_count = sum(1 for e in tabbed.values() if "historical" in e["tabs"])
    assert historical_count == cfg.CHART_HISTORICAL_LOOKBACK_COUNT


# ─── Requirements 2 & 3: reasons + why-buy summary ─────────────────────────

def test_readiness_reasons_and_remarks_are_exported(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=2.5)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    assert "readiness_reasons" in sym
    assert isinstance(sym["readiness_reasons"], str)
    assert "remarks" in sym


def test_buy_thesis_is_a_nonempty_readable_sentence(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=2.8,
                  pole_move_pct=28, rs_rating=88.0)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    thesis = sym["buy_thesis"]
    assert isinstance(thesis, str) and len(thesis) > 30
    assert "quality score" in thesis.lower()
    assert "%" in thesis   # mentions at least one concrete figure, not just vague language


def test_buy_thesis_flags_non_uptrend_market_caution(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, breakout_vol_mult=2.5,
                  market_trend="Correction")
    _run_export(monkeypatch, trend="Correction", dist_days=7)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    assert "Correction" in sym["buy_thesis"]


def test_flag_range_pct_computed_from_high_low(temp_db, charts_dir, monkeypatch):
    """flag_range_pct is a purer 'how narrow was the consolidation'
    number than retracement % alone (which is scaled by the pole's
    height, not the flag's own width) — cross-check it's computed
    correctly against the stored flag_high/flag_low."""
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, retrace_frac=0.30)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    row = db.get_all_flag_pole_signals_df().iloc[0]

    assert sym["flag_range_pct"] is not None
    expected = (row["flag_high"] - row["flag_low"]) / row["flag_low"] * 100.0
    assert sym["flag_range_pct"] == pytest.approx(expected, abs=0.01)
    assert sym["flag_duration_bars"] == row["flag_duration_bars"]


def test_buy_thesis_states_flag_duration_in_trading_days(temp_db, charts_dir, monkeypatch):
    """Detection is daily-bar-only — the thesis text should say so
    explicitly ('trading days'), not leave the timeframe ambiguous."""
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    assert "trading days" in sym["buy_thesis"]
    assert "range" in sym["buy_thesis"].lower()


# ─── Requirement 4: Top Picks ──────────────────────────────────────────────

def test_top_picks_ranks_by_quality_score(temp_db, charts_dir, monkeypatch):
    monkeypatch.setattr(cfg, "CHART_TOP_PICKS_COUNT", 1)
    monkeypatch.setattr(ce.cfg, "CHART_TOP_PICKS_COUNT", 1)

    _seed_signal(monkeypatch, symbol="WEAK.NS", breakout=True, breakout_vol_mult=1.6, pole_move_pct=15)
    strong_df, *_ = build_flag_pole_df(breakout=True, breakout_vol_mult=3.5, pole_move_pct=35)
    # Layer a second symbol's data onto the same patched load_daily map
    import main_flag_pole as mfp
    existing = mfp.load_daily
    combined = {"WEAK.NS": existing("WEAK.NS")}
    combined["STRONG.NS"] = strong_df
    monkeypatch.setattr("main_flag_pole.load_daily", lambda s, _m=combined: _m.get(s))
    monkeypatch.setattr("chart_export.load_daily", lambda s, _m=combined: _m.get(s))

    by_signal_type, by_rs_band = {}, {"<60": 0, "60-80": 0, ">80": 0}
    mfp._scan_symbol("STRONG.NS", {"STRONG.NS": 90.0}, {"STRONG.NS": "Improving"},
                      "Uptrend", "2024-06-01", by_signal_type, by_rs_band)

    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    top_picks = [s for s in data["symbols"] if "top_picks" in s["tabs"]]
    assert len(top_picks) == 1
    assert top_picks[0]["quality_score"] == max(s["quality_score"] for s in data["symbols"])


# ─── Robustness ─────────────────────────────────────────────────────────────

def test_bars_respect_lookback_limits(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True, base_n=250)
    _run_export(monkeypatch)

    data, _ = _extract_data_blob(charts_dir)
    sym = data["symbols"][0]
    assert len(sym["timeframes"]["1D"]) <= cfg.CHART_LOOKBACK_DAILY_BARS
    assert len(sym["timeframes"]["1W"]) <= cfg.CHART_LOOKBACK_WEEKLY_BARS
    assert len(sym["timeframes"]["1M"]) <= cfg.CHART_LOOKBACK_MONTHLY_BARS


def test_symbol_with_no_price_data_is_skipped_not_crashed(temp_db, charts_dir, monkeypatch):
    _seed_signal(monkeypatch, symbol="AAA.NS", breakout=True)
    monkeypatch.setattr("chart_export.load_daily", lambda s: None)
    _run_export(monkeypatch)
    assert not list(charts_dir.glob("*.html"))
