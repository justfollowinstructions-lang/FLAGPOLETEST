"""
Tests for market_regime.py — distribution-day counting and trend
classification, against hand-crafted benchmark series with a known
number of distribution days.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

import config as cfg
from market_regime import compute_market_regime


def _make_benchmark(n=60, start=20000.0, down_day_positions=None, above_50ma=True):
    """
    Build a benchmark series that is flat/gently up (so price stays
    above its 50MA when above_50ma=True), then inject exact
    distribution days (down >= threshold on higher volume) at the
    given bar positions within the last DISTRIBUTION_DAY_WINDOW bars.
    """
    down_day_positions = down_day_positions or []
    rng = np.random.default_rng(42)

    drift = 0.0006 if above_50ma else -0.004
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.001)))
    closes = np.array(closes)
    vols = np.full(n, 1_000_000.0) * (1 + rng.normal(0, 0.02, n))

    for pos in down_day_positions:
        closes[pos] = closes[pos - 1] * (1 - cfg.DISTRIBUTION_DAY_PCT_THRESHOLD / 100.0 - 0.001)
        vols[pos] = vols[pos - 1] * 1.3   # higher volume than prior day

    highs = closes * 1.002
    lows = closes * 0.998
    opens = closes
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def test_uptrend_with_no_distribution_days():
    df = _make_benchmark(n=60, down_day_positions=[])
    regime = compute_market_regime(df)
    assert regime["trend"] == "Uptrend"
    assert regime["distribution_days_25d"] == 0


def test_uptrend_under_pressure_with_pressure_threshold_days():
    n = 60
    window_start = n - cfg.DISTRIBUTION_DAY_WINDOW
    positions = [window_start + i * 4 for i in range(cfg.DISTRIBUTION_DAYS_PRESSURE)]
    df = _make_benchmark(n=n, down_day_positions=positions)
    regime = compute_market_regime(df)
    assert regime["distribution_days_25d"] >= cfg.DISTRIBUTION_DAYS_PRESSURE
    assert regime["trend"] in ("Uptrend Under Pressure", "Correction")


def test_correction_when_price_below_50ma():
    df = _make_benchmark(n=60, above_50ma=False)
    regime = compute_market_regime(df)
    assert regime["trend"] == "Correction"


def test_correction_with_many_distribution_days():
    n = 60
    window_start = n - cfg.DISTRIBUTION_DAY_WINDOW
    positions = [window_start + i * 3 for i in range(cfg.DISTRIBUTION_DAYS_CORRECTION + 1)]
    df = _make_benchmark(n=n, down_day_positions=positions)
    regime = compute_market_regime(df)
    assert regime["distribution_days_25d"] >= cfg.DISTRIBUTION_DAYS_CORRECTION
    assert regime["trend"] == "Correction"


def test_insufficient_data_returns_unknown():
    df = _make_benchmark(n=10)
    regime = compute_market_regime(df)
    assert regime["trend"] == "Unknown"


def test_none_benchmark_returns_unknown():
    regime = compute_market_regime(None)
    assert regime["trend"] == "Unknown"


def test_no_volume_column_still_returns_trend_without_distribution_days():
    df = _make_benchmark(n=60).drop(columns=["Volume"])
    regime = compute_market_regime(df)
    assert regime["distribution_days_25d"] == 0
    assert regime["trend"] in ("Uptrend", "Uptrend Under Pressure", "Correction")
