"""
NSE Scanner Suite - Market Regime Engine
===========================================
Shared by both Cup & Handle and Flag & Pole. Replaces a bare
"price vs 50MA" read with real O'Neil-style distribution-day counting,
so a "Uptrend" label actually means what the CANSLIM sell-rule
checklists in entry_exit.py / entry_exit_flag_pole.py assume it means.

A distribution day = the benchmark index closes down
DISTRIBUTION_DAY_PCT_THRESHOLD or more, on volume higher than the
prior session. Counted over a rolling DISTRIBUTION_DAY_WINDOW-session
window (25 sessions is O'Neil's standard "current market" lookback).

Trend labels (deliberately kept as "Uptrend" / "Correction" for the
base cases, matching the strings main.py has always used downstream —
e.g. entry_exit.py's `market_note` check is `nifty_trend != "Uptrend"`.
"Uptrend Under Pressure" is a new intermediate state; since it isn't
exactly "Uptrend", the existing counter-trend caution note now correctly
fires for it too):

    trend = "Uptrend"                if <PRESSURE dist. days and price > 50MA
    trend = "Uptrend Under Pressure" if PRESSURE..<CORRECTION dist. days
    trend = "Correction"             if >=CORRECTION dist. days or price < 50MA
    trend = "Unknown"                if insufficient data
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config as cfg
from indicators import clean_volume, sma
from logger_utils import get_logger

log = get_logger("scanner")


def compute_market_regime(
    nifty_df: Optional[pd.DataFrame],
    breadth_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Returns:
        {
            "trend": "Uptrend" | "Uptrend Under Pressure" | "Correction" | "Unknown",
            "distribution_days_25d": int,
            "distribution_days_list": list[str],   # ISO date strings, for audit/debug
            "pct_above_50dma": float | None,        # breadth, if breadth_df provided
            "pct_above_200dma": float | None,
        }

    breadth_df is optional for v1: a DataFrame indexed by symbol with
    boolean 'above_50dma' / 'above_200dma' columns, computed by the
    caller across the scanned universe. Pass None to skip breadth.
    """
    empty = {
        "trend": "Unknown",
        "distribution_days_25d": 0,
        "distribution_days_list": [],
        "pct_above_50dma": None,
        "pct_above_200dma": None,
    }

    min_bars_needed = max(cfg.DISTRIBUTION_DAY_WINDOW + 1, 50)
    if nifty_df is None or len(nifty_df) < min_bars_needed:
        log.warning("Market regime: insufficient benchmark data — defaulting to Unknown")
        return empty

    close = nifty_df["Close"]
    ma50 = sma(close, 50).iloc[-1]
    current = close.iloc[-1]
    if pd.isna(ma50):
        return empty
    above_50ma = float(current) > float(ma50)

    dist_days: list[str] = []
    if "Volume" in nifty_df.columns:
        volume = clean_volume(nifty_df["Volume"])
        window = cfg.DISTRIBUTION_DAY_WINDOW + 1   # +1 so we get DISTRIBUTION_DAY_WINDOW diffs
        recent_close = close.iloc[-window:]
        recent_vol = volume.iloc[-window:]

        pct_change = recent_close.pct_change()
        vol_higher = recent_vol.diff() > 0

        threshold = -(cfg.DISTRIBUTION_DAY_PCT_THRESHOLD / 100.0)
        for i in range(1, len(recent_close)):
            day_pct = pct_change.iloc[i]
            if pd.notna(day_pct) and day_pct <= threshold and bool(vol_higher.iloc[i]):
                dist_days.append(str(recent_close.index[i].date()))
    else:
        log.debug("Market regime: no Volume column on benchmark — distribution days unavailable")

    n_dist = len(dist_days)

    if not above_50ma or n_dist >= cfg.DISTRIBUTION_DAYS_CORRECTION:
        trend = "Correction"
    elif n_dist >= cfg.DISTRIBUTION_DAYS_PRESSURE:
        trend = "Uptrend Under Pressure"
    else:
        trend = "Uptrend"

    pct_above_50dma = None
    pct_above_200dma = None
    if breadth_df is not None and not breadth_df.empty:
        if "above_50dma" in breadth_df.columns:
            pct_above_50dma = float(breadth_df["above_50dma"].mean() * 100.0)
        if "above_200dma" in breadth_df.columns:
            pct_above_200dma = float(breadth_df["above_200dma"].mean() * 100.0)

    log.info(
        "Market regime: trend=%s  distribution_days(%dd)=%d  price=%.2f  50MA=%.2f",
        trend, cfg.DISTRIBUTION_DAY_WINDOW, n_dist, float(current), float(ma50),
    )

    return {
        "trend": trend,
        "distribution_days_25d": n_dist,
        "distribution_days_list": dist_days,
        "pct_above_50dma": pct_above_50dma,
        "pct_above_200dma": pct_above_200dma,
    }
