"""
Tests for the new macd() function in indicators.py — sma/ema/rsi
already existed and are exercised indirectly elsewhere; macd() is new
for the chart viewer's indicator toggles, so it gets its own direct
unit test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from indicators import ema, macd


def _make_trending_series(n=200, start=100.0, drift=0.003, seed=1):
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, 0.01, n)
    prices = start * np.cumprod(1 + returns)
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.Series(prices, index=idx)


def test_macd_returns_three_series_same_length_as_input():
    close = _make_trending_series()
    macd_line, signal_line, hist = macd(close)
    assert len(macd_line) == len(close)
    assert len(signal_line) == len(close)
    assert len(hist) == len(close)


def test_macd_line_equals_fast_ema_minus_slow_ema():
    close = _make_trending_series()
    macd_line, _, _ = macd(close, fast=12, slow=26)
    expected = ema(close, 12) - ema(close, 26)
    pd.testing.assert_series_equal(macd_line, expected, check_names=False)


def test_histogram_equals_macd_minus_signal():
    close = _make_trending_series()
    macd_line, signal_line, hist = macd(close)
    valid = hist.dropna().index
    assert len(valid) > 0
    diff = (hist.loc[valid] - (macd_line.loc[valid] - signal_line.loc[valid])).abs()
    assert (diff < 1e-9).all()


def test_macd_positive_in_a_sustained_uptrend():
    """A steadily rising series should show a positive MACD line once
    both EMAs have warmed up — the fast EMA sits above the slow EMA
    during a sustained uptrend."""
    close = _make_trending_series(n=200, drift=0.004)
    macd_line, _, _ = macd(close)
    tail = macd_line.dropna().tail(30)
    assert (tail > 0).mean() > 0.7   # mostly positive, allow some noise


def test_macd_custom_periods_respected():
    close = _make_trending_series()
    macd_line, signal_line, _ = macd(close, fast=5, slow=13, signal=4)
    expected_macd = ema(close, 5) - ema(close, 13)
    pd.testing.assert_series_equal(macd_line, expected_macd, check_names=False)
    # signal warms up later than macd since it's an EMA of the MACD line
    assert signal_line.first_valid_index() >= macd_line.first_valid_index()
