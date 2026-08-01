"""
Tests for downloader.resample_weekly / resample_monthly — specifically
a regression test for a real bug found via user testing: an explicit
closed='left', label='left' override on the weekly resample shifted
each week's Friday bar into the FOLLOWING week's bucket and left the
most recent week as a broken single-day stub instead of a complete
Mon-Fri bar. See downloader.py's resample_weekly docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from downloader import resample_monthly, resample_weekly


def _make_clean_weeks(n_weeks=3, start="2026-06-01"):
    """n_weeks of clean Mon-Fri daily bars, no holidays, closing price
    increasing by 1 each day so each week's expected OHLC is obvious."""
    dates = pd.bdate_range(start, periods=n_weeks * 5)
    closes = list(range(100, 100 + len(dates)))
    df = pd.DataFrame({
        "Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
        "Close": closes, "Volume": [1000] * len(dates),
    }, index=dates)
    return df


def test_weekly_bar_contains_all_five_trading_days():
    df = _make_clean_weeks(n_weeks=3)
    weekly = resample_weekly(df)

    assert len(weekly) == 3
    for _, row in weekly.iterrows():
        assert row["Volume"] == 5000   # 5 trading days x 1000, not a partial week


def test_weekly_bar_labeled_with_its_own_friday_close():
    df = _make_clean_weeks(n_weeks=2, start="2026-06-01")
    weekly = resample_weekly(df)

    week1 = weekly.iloc[0]
    assert weekly.index[0] == pd.Timestamp("2026-06-05")   # the Friday of week 1
    assert week1["Open"] == 100    # Monday's open
    assert week1["Close"] == 104   # Friday's close, not Thursday's


def test_most_recent_week_is_not_a_broken_stub():
    """The bug this regression-tests: the last bar used to come back
    as a single trading day (whatever day the data happened to end
    on) instead of the full week up to that point."""
    df = _make_clean_weeks(n_weeks=3)
    weekly = resample_weekly(df)

    last_week = weekly.iloc[-1]
    assert last_week["Volume"] == 5000
    assert last_week["Close"] == df["Close"].iloc[-1]   # ends on the actual last trading day's close


def test_weekly_resample_handles_a_holiday_gap():
    """A Monday holiday should just mean 4 trading days in that week's
    bar, not break the bucketing — resample naturally aggregates
    whatever rows exist within each calendar week."""
    dates = pd.bdate_range("2026-06-01", periods=5)  # Mon-Fri
    df = pd.DataFrame({
        "Open": [100, 101, 102, 103, 104], "High": [101, 102, 103, 104, 105],
        "Low": [99, 100, 101, 102, 103], "Close": [100, 101, 102, 103, 104],
        "Volume": [1000] * 5,
    }, index=dates)
    df = df.drop(df.index[0])   # simulate Monday being a holiday (no row at all)

    weekly = resample_weekly(df)
    assert len(weekly) == 1
    assert weekly.iloc[0]["Volume"] == 4000
    assert weekly.iloc[0]["Open"] == 101   # Tuesday's open, since Monday didn't trade
    assert weekly.iloc[0]["Close"] == 104  # Friday's close


def test_monthly_resample_groups_full_calendar_month():
    dates = pd.bdate_range("2026-06-01", "2026-07-31")
    closes = list(range(100, 100 + len(dates)))
    df = pd.DataFrame({
        "Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
        "Close": closes, "Volume": [1000] * len(dates),
    }, index=dates)

    monthly = resample_monthly(df)
    assert len(monthly) == 2
    assert monthly.index[0] == pd.Timestamp("2026-06-01")
    assert monthly.index[1] == pd.Timestamp("2026-07-01")
