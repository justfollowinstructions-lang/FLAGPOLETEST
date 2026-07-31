"""
NSE Flag & Pole Scanner - Breakout Readiness Score
======================================================
Mirrors readiness.py's structure: Quality Score answers "is this
historically a good-looking pattern?", Breakout Readiness answers "is
this actionable RIGHT NOW?" — computed only for signals close enough
to matter (NEAR BREAKOUT, WATCHING, BREAKOUT NOW). EARLY STAGE gets
None, same convention as Cup & Handle's CUP ONLY.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

import config as cfg
from flag_pole_detector import FlagPoleSignal
from indicators import clean_volume

READY_SIGNAL_TYPES = {"NEAR BREAKOUT", "WATCHING", "BREAKOUT NOW"}


def compute_flag_pole_readiness(
    sig: FlagPoleSignal,
    df: pd.DataFrame,
    rs_trend: str,
) -> dict:
    """
    Returns a dict with:
      readiness_pct     : float 0-100, or None if not applicable
      reasons_str        : "✔ Near Pivot | ✘ Flag Tight | ..."
      near_pivot          : bool
      flag_tight           : bool
      volume_drying_up      : bool
      rising_rs              : bool
      pole_freshness          : bool
    """
    if sig.signal_type not in READY_SIGNAL_TYPES:
        return {
            "readiness_pct": None,
            "reasons_str": "N/A — pattern not yet near pivot",
            "near_pivot": False,
            "flag_tight": False,
            "volume_drying_up": False,
            "rising_rs": False,
            "pole_freshness": False,
        }

    near_pivot = _check_near_pivot(sig)
    flag_tight = _check_flag_tight(sig)
    volume_drying_up = _check_volume_drying_up(sig, df)
    rising_rs = rs_trend == "Improving"
    pole_freshness = _check_pole_freshness(sig)

    weights = cfg.FP_READINESS_WEIGHTS
    readiness_pct = 0
    checks = [
        ("near_pivot", near_pivot, "Near Pivot"),
        ("flag_tight", flag_tight, "Flag Tight"),
        ("volume_drying_up", volume_drying_up, "Volume Drying Up"),
        ("rising_rs", rising_rs, "Rising RS"),
        ("pole_freshness", pole_freshness, "Pole Fresh"),
    ]

    reasons_parts = []
    for key, passed, label in checks:
        if passed:
            readiness_pct += weights[key]
        mark = "✔" if passed else "✘"
        reasons_parts.append(f"{mark} {label}")

    return {
        "readiness_pct": readiness_pct,
        "reasons_str": " | ".join(reasons_parts),
        "near_pivot": near_pivot,
        "flag_tight": flag_tight,
        "volume_drying_up": volume_drying_up,
        "rising_rs": rising_rs,
        "pole_freshness": pole_freshness,
    }


def readiness_band(readiness_pct: Optional[float]) -> str:
    if readiness_pct is None:
        return "n/a"
    if readiness_pct >= cfg.READINESS_BAND_HIGH:
        return "high"
    if readiness_pct >= cfg.READINESS_BAND_MEDIUM:
        return "medium"
    return "low"


# ─── Individual checks ──────────────────────────────────────────────────────

def _check_near_pivot(sig: FlagPoleSignal) -> bool:
    return abs(sig.price_vs_pivot_pct) <= cfg.FP_READINESS_NEAR_PIVOT_PCT


def _check_flag_tight(sig: FlagPoleSignal) -> bool:
    # Tight = retracement in the healthy zone (<=38.2%, the classic
    # Fibonacci "shallow flag" threshold) and near-parallel trendlines
    return sig.flag_retracement_pct <= 38.2


def _check_volume_drying_up(sig: FlagPoleSignal, df: pd.DataFrame) -> bool:
    """Most recent 3 bars' volume below the flag's own average — the
    classic 'about to move' tell: dry-up accelerating right before
    breakout."""
    if "Volume" not in df.columns:
        return False
    volume = clean_volume(df["Volume"])
    try:
        flag_start_pos = df.index.get_indexer(
            [pd.Timestamp(sig.flag_start_date)], method="nearest"
        )[0]
        flag_end_pos = df.index.get_indexer(
            [pd.Timestamp(sig.flag_end_date)], method="nearest"
        )[0]
    except Exception:
        return False

    flag_vol = volume.iloc[flag_start_pos:flag_end_pos + 1]
    if flag_vol.empty:
        return False
    flag_avg = flag_vol.mean()
    if pd.isna(flag_avg) or flag_avg <= 0:
        return False

    last3 = volume.iloc[-3:]
    if last3.empty:
        return False
    return bool(last3.mean() < flag_avg)


def _check_pole_freshness(sig: FlagPoleSignal) -> bool:
    """Bonus if the flag is still within the first half of its
    allowed duration window — a flag dragging near DAILY_FLAG_MAX_BARS
    without breaking out is losing conviction."""
    halfway = (cfg.DAILY_FLAG_MIN_BARS + cfg.DAILY_FLAG_MAX_BARS) / 2.0
    return sig.flag_duration_bars <= halfway
