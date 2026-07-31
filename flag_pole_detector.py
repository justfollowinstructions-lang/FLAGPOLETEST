"""
NSE Flag & Pole Scanner - Pattern Detection Engine
=====================================================
Bull flags only.

Philosophy: detection is deliberately maximum-sensitivity within its
hard gates (pole velocity, flag geometry, retracement), and returns
the MOST RECENT valid pattern per symbol. Quality score, readiness,
and entry/exit are computed downstream and never feed back into
whether a pattern is detected.

The pole window (3-10 bars, velocity-gated on both % move AND
ATR-multiple) is deliberately short — if this scanner is ever combined
with a companion longer-base pattern scanner (e.g. Cup & Handle, whose
base window typically starts around 30 bars), do not widen this range
to "catch more patterns." A move that takes 3+ weeks to form isn't a
pole, it's the early stage of a base, and the two detectors should
stay structurally distinct so a stock doesn't trigger both off the
same move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from indicators import atr as atr_fn, clean_volume


# ─── Result container ──────────────────────────────────────────────────────

@dataclass
class FlagPoleSignal:
    symbol: str
    timeframe: str = "daily"          # daily only for v1
    pattern_type: str = "FLAG_POLE"
    signal_type: str = ""             # BREAKOUT NOW / NEAR BREAKOUT / WATCHING / EARLY STAGE

    pole_start_date: Optional[date] = None
    pole_end_date: Optional[date] = None
    pole_start_price: float = 0.0
    pole_end_price: float = 0.0
    pole_pct_move: float = 0.0
    pole_atr_multiple: float = 0.0
    pole_duration_bars: int = 0

    flag_start_date: Optional[date] = None
    flag_end_date: Optional[date] = None
    flag_high: float = 0.0
    flag_low: float = 0.0
    flag_retracement_pct: float = 0.0
    flag_duration_bars: int = 0
    upper_trendline_slope: float = 0.0
    lower_trendline_slope: float = 0.0
    volume_contraction_pct: float = 0.0

    pivot_point: float = 0.0
    current_price: float = 0.0
    price_vs_pivot_pct: float = 0.0

    breakout_date: Optional[date] = None
    breakout_volume_ratio: Optional[float] = None

    quality_score: float = 0.0

    # Internal: (slope, intercept, flag_len) for the upper/lower
    # trendlines, expressed in bar-offsets from flag_start. Used
    # downstream by entry_exit_flag_pole.py to recompute the pivot at
    # an arbitrary future bar without re-fitting. Not persisted to DB.
    _upper_line: tuple = (0.0, 0.0, 0)
    _lower_line: tuple = (0.0, 0.0, 0)


# ─── Public entry point ────────────────────────────────────────────────────

def detect_flag_pole(
    df: pd.DataFrame,
    symbol: str,
    rs_rating: float = 50.0,
) -> list[FlagPoleSignal]:
    """
    Scan df (daily OHLCV, datetime index, ascending) for the most
    recent valid bull Flag & Pole pattern.

    Returns a list with 0 or 1 FlagPoleSignal.
    """
    n = len(df)
    if n < cfg.MIN_DAILY_BARS_FLAG_POLE:
        return []

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    if "Volume" not in df.columns:
        return []
    volume = clean_volume(df["Volume"])

    atr_series = atr_fn(df, period=cfg.ATR_PERIOD)

    best: Optional[dict] = None
    max_lookback = cfg.BREAKOUT_CONFIRMED_LOOKBACK_DAYS

    for flag_end_offset in range(0, max_lookback + 1):
        flag_end = n - 1 - flag_end_offset
        min_needed = cfg.DAILY_FLAG_MIN_BARS + cfg.DAILY_POLE_MIN_BARS
        if flag_end < min_needed:
            continue

        for flag_len in range(cfg.DAILY_FLAG_MIN_BARS, cfg.DAILY_FLAG_MAX_BARS + 1):
            flag_start = flag_end - flag_len + 1
            if flag_start < cfg.DAILY_POLE_MIN_BARS:
                continue

            for pole_len in range(cfg.DAILY_POLE_MIN_BARS, cfg.DAILY_POLE_MAX_BARS + 1):
                pole_end = flag_start - 1
                pole_start = pole_end - pole_len + 1
                if pole_start < 0:
                    continue

                cand = _evaluate_candidate(
                    df, close, high, low, volume, atr_series,
                    pole_start, pole_end, flag_start, flag_end,
                )
                if cand is None:
                    continue
                cand["flag_end_offset"] = flag_end_offset
                if best is None or _is_better(cand, best):
                    best = cand

    if best is None:
        return []

    sig = _build_signal(df, best, symbol, rs_rating)
    return [sig] if sig is not None else []


def _is_better(a: dict, b: dict) -> bool:
    """Prefer: a confirmed breakout candidate over one that has merely
    absorbed the breakout bar into a longer flag window (this matters —
    without the breakout-first priority, a search that tries many flag
    lengths will sometimes find a technically-valid longer flag that
    swallows the very breakout bar that should have ended it) > more
    recent (smaller flag_end_offset) > stronger pole move > longer,
    more-established flag."""
    key_a = (a["breakout_pos"] is not None, -a["flag_end_offset"], a["pole_pct_move"], a["flag_len"])
    key_b = (b["breakout_pos"] is not None, -b["flag_end_offset"], b["pole_pct_move"], b["flag_len"])
    return key_a > key_b


# ─── Candidate evaluation ───────────────────────────────────────────────────

def _evaluate_candidate(
    df: pd.DataFrame,
    close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series,
    atr_series: pd.Series,
    pole_start: int, pole_end: int, flag_start: int, flag_end: int,
) -> Optional[dict]:
    n = len(df)

    # ── POLE ──
    pole_start_price = float(close.iloc[pole_start])
    pole_end_price = float(close.iloc[pole_end])
    if pole_start_price <= 0:
        return None

    pole_pct_move = (pole_end_price - pole_start_price) / pole_start_price * 100.0
    if pole_pct_move < cfg.POLE_MIN_PCT_MOVE:
        return None

    pole_atr = atr_series.iloc[pole_end]
    if pd.isna(pole_atr) or pole_atr <= 0:
        return None
    pole_height = pole_end_price - pole_start_price
    pole_atr_multiple = pole_height / float(pole_atr)
    if pole_atr_multiple < cfg.POLE_MIN_ATR_MULTIPLE:
        return None

    pole_closes = close.iloc[pole_start:pole_end + 1]
    pole_diffs = pole_closes.diff().dropna()
    if len(pole_diffs) == 0:
        return None
    up_fraction = (pole_diffs > 0).sum() / len(pole_diffs)
    if up_fraction < cfg.POLE_MIN_UP_BAR_FRACTION:
        return None
    if len(pole_diffs) > 1 and pole_diffs.max() >= pole_height * cfg.POLE_MAX_SINGLE_BAR_FRACTION:
        return None   # one gap-up bar accounts for the whole move — that's a gap, not a pole

    # ── FLAG ──
    flag_len = flag_end - flag_start + 1
    if flag_len < 2:
        return None

    flag_highs = high.iloc[flag_start:flag_end + 1].values.astype(float)
    flag_lows = low.iloc[flag_start:flag_end + 1].values.astype(float)
    x = np.arange(flag_len, dtype=float)

    upper_slope, upper_intercept = np.polyfit(x, flag_highs, 1)
    lower_slope, lower_intercept = np.polyfit(x, flag_lows, 1)

    avg_price = float(close.iloc[flag_start:flag_end + 1].mean())
    if avg_price <= 0:
        return None
    upper_angle = float(np.degrees(np.arctan(upper_slope / avg_price)))
    lower_angle = float(np.degrees(np.arctan(lower_slope / avg_price)))
    if abs(upper_angle - lower_angle) > cfg.FLAG_PARALLEL_TOLERANCE_DEG:
        return None

    # Flag should be flat-to-down, i.e. a PAUSE against the pole — reject
    # if both trendlines are still climbing meaningfully (that's
    # continuation of the trend, not a flag).
    if upper_angle > 2.0 and lower_angle > 2.0:
        return None

    flag_high = float(flag_highs.max())
    flag_low = float(flag_lows.min())
    if flag_low <= 0 or pole_height <= 0:
        return None
    flag_retracement_pct = (pole_end_price - flag_low) / pole_height * 100.0
    if flag_retracement_pct > cfg.FLAG_MAX_RETRACEMENT_PCT:
        return None
    if flag_low <= pole_start_price:
        return None   # flag erased the entire pole — not a valid continuation pause

    pole_vol_avg = float(volume.iloc[pole_start:pole_end + 1].mean())
    flag_vol_avg = float(volume.iloc[flag_start:flag_end + 1].mean())
    if pole_vol_avg <= 0 or flag_vol_avg <= 0:
        return None
    volume_contraction_pct = (1.0 - (flag_vol_avg / pole_vol_avg)) * 100.0
    if volume_contraction_pct < cfg.FLAG_MIN_VOLUME_CONTRACTION_PCT:
        return None

    def upper_at(bar_offset: int) -> float:
        return float(upper_intercept + upper_slope * bar_offset)

    def lower_at(bar_offset: int) -> float:
        return float(lower_intercept + lower_slope * bar_offset)

    pivot_point = upper_at(flag_len - 1)

    # ── Breakout search in the bars after flag_end (if any) ──
    breakout_pos = None
    breakout_volume_ratio = None
    for offset, pos in enumerate(range(flag_end + 1, n), start=1):
        bar_offset = (flag_len - 1) + offset
        pivot_here = upper_at(bar_offset)
        bar_close = float(close.iloc[pos])

        if bar_close > pivot_here:
            bar_vol = float(volume.iloc[pos])
            vol_ratio = bar_vol / flag_vol_avg if flag_vol_avg > 0 else 0.0
            if vol_ratio >= cfg.BREAKOUT_MIN_VOLUME_MULTIPLE:
                breakout_pos = pos
                breakout_volume_ratio = vol_ratio
            break   # first bar closing above the pivot decides it either way

        lower_here = lower_at(bar_offset)
        if bar_close < lower_here * (1 - cfg.FLAG_FAILURE_BREACH_PCT):
            return None   # flag failed (broke down) before ever breaking out

    return {
        "pole_start": pole_start, "pole_end": pole_end,
        "pole_start_price": pole_start_price, "pole_end_price": pole_end_price,
        "pole_pct_move": pole_pct_move, "pole_atr_multiple": pole_atr_multiple,
        "pole_height": pole_height,

        "flag_start": flag_start, "flag_end": flag_end, "flag_len": flag_len,
        "flag_high": flag_high, "flag_low": flag_low,
        "flag_retracement_pct": flag_retracement_pct,
        "upper_slope": upper_slope, "lower_slope": lower_slope,
        "upper_intercept": upper_intercept, "lower_intercept": lower_intercept,
        "upper_angle": upper_angle, "lower_angle": lower_angle,
        "volume_contraction_pct": volume_contraction_pct,

        "pivot_point": pivot_point,
        "breakout_pos": breakout_pos,
        "breakout_volume_ratio": breakout_volume_ratio,
    }


# ─── Build full signal from the winning candidate ──────────────────────────

def _build_signal(
    df: pd.DataFrame, cand: dict, symbol: str, rs_rating: float,
) -> Optional[FlagPoleSignal]:
    n = len(df)
    close = df["Close"]

    current_price = float(close.iloc[-1])
    pivot_point = cand["pivot_point"]
    price_vs_pivot_pct = ((current_price - pivot_point) / pivot_point) * 100.0 if pivot_point else 0.0

    breakout_pos = cand["breakout_pos"]
    if breakout_pos is not None:
        signal_type = "BREAKOUT NOW" if breakout_pos == n - 1 else "NEAR BREAKOUT"
        breakout_date = _to_date(df.index[breakout_pos])
    else:
        price_vs_pivot_frac = (current_price - pivot_point) / pivot_point if pivot_point else 0.0
        if -cfg.NEAR_BREAKOUT_THRESHOLD <= price_vs_pivot_frac < 0 or current_price >= pivot_point:
            signal_type = "NEAR BREAKOUT"
        elif -cfg.BASING_THRESHOLD <= price_vs_pivot_frac < -cfg.NEAR_BREAKOUT_THRESHOLD:
            signal_type = "WATCHING"
        else:
            signal_type = "EARLY STAGE"
        breakout_date = None

    quality_score = _compute_quality_score(
        pole_pct_move=cand["pole_pct_move"],
        pole_atr_multiple=cand["pole_atr_multiple"],
        flag_retracement_pct=cand["flag_retracement_pct"],
        upper_angle=cand["upper_angle"],
        lower_angle=cand["lower_angle"],
        volume_contraction_pct=cand["volume_contraction_pct"],
        breakout_volume_ratio=cand["breakout_volume_ratio"],
        rs_rating=rs_rating,
    )

    sig = FlagPoleSignal(
        symbol=symbol,
        timeframe="daily",
        signal_type=signal_type,

        pole_start_date=_to_date(df.index[cand["pole_start"]]),
        pole_end_date=_to_date(df.index[cand["pole_end"]]),
        pole_start_price=round(cand["pole_start_price"], 2),
        pole_end_price=round(cand["pole_end_price"], 2),
        pole_pct_move=round(cand["pole_pct_move"], 2),
        pole_atr_multiple=round(cand["pole_atr_multiple"], 2),
        pole_duration_bars=cand["pole_end"] - cand["pole_start"] + 1,

        flag_start_date=_to_date(df.index[cand["flag_start"]]),
        flag_end_date=_to_date(df.index[cand["flag_end"]]),
        flag_high=round(cand["flag_high"], 2),
        flag_low=round(cand["flag_low"], 2),
        flag_retracement_pct=round(cand["flag_retracement_pct"], 2),
        flag_duration_bars=cand["flag_len"],
        upper_trendline_slope=round(float(cand["upper_slope"]), 4),
        lower_trendline_slope=round(float(cand["lower_slope"]), 4),
        volume_contraction_pct=round(cand["volume_contraction_pct"], 2),

        pivot_point=round(pivot_point, 2),
        current_price=round(current_price, 2),
        price_vs_pivot_pct=round(price_vs_pivot_pct, 2),

        breakout_date=breakout_date,
        breakout_volume_ratio=(
            round(cand["breakout_volume_ratio"], 2) if cand["breakout_volume_ratio"] else None
        ),

        quality_score=round(quality_score, 1),
    )
    sig._upper_line = (float(cand["upper_slope"]), float(cand["upper_intercept"]), cand["flag_len"])
    sig._lower_line = (float(cand["lower_slope"]), float(cand["lower_intercept"]), cand["flag_len"])
    return sig


# ─── Quality score (0-100) ──────────────────────────────────────────────────

def _compute_quality_score(
    pole_pct_move: float,
    pole_atr_multiple: float,
    flag_retracement_pct: float,
    upper_angle: float,
    lower_angle: float,
    volume_contraction_pct: float,
    breakout_volume_ratio: Optional[float],
    rs_rating: float,
) -> float:
    # Pole strength: 30%+ move and 6x+ ATR both score full marks
    pct_component = min(pole_pct_move / 30.0, 1.0)
    atr_component = min(pole_atr_multiple / 6.0, 1.0)
    pole_strength = cfg.FPQS_POLE_STRENGTH * ((pct_component + atr_component) / 2.0)

    # Flag tightness: retracement scored against an ideal ~35% (mid of
    # the 25-50% healthy zone); parallelism scored against the tolerance
    ideal_retracement = 35.0
    retr_component = max(0.0, 1.0 - abs(flag_retracement_pct - ideal_retracement) / 35.0)
    parallel_component = max(
        0.0, 1.0 - abs(upper_angle - lower_angle) / cfg.FLAG_PARALLEL_TOLERANCE_DEG
    )
    flag_tightness = cfg.FPQS_FLAG_TIGHTNESS * ((retr_component + parallel_component) / 2.0)

    # Volume signature: contraction in the flag + expansion on breakout
    contraction_component = min(max(volume_contraction_pct, 0.0) / 50.0, 1.0)
    if breakout_volume_ratio:
        breakout_component = min(breakout_volume_ratio / 2.5, 1.0)
    else:
        breakout_component = 0.5   # neutral — hasn't broken out yet, no penalty
    volume_signature = cfg.FPQS_VOLUME_SIGNATURE * ((contraction_component + breakout_component) / 2.0)

    # RS Rating — same bands as Cup & Handle
    if rs_rating >= cfg.RS_LEADER_THRESHOLD:
        rs_component = cfg.FPQS_RS_RATING
    elif rs_rating >= cfg.RS_RISING_THRESHOLD:
        rs_component = cfg.FPQS_RS_RATING * 0.6
    elif rs_rating >= cfg.RS_LAGGARD_THRESHOLD:
        rs_component = cfg.FPQS_RS_RATING * 0.3
    else:
        rs_component = 0.0

    total = pole_strength + flag_tightness + volume_signature + rs_component
    return min(total, cfg.FPQS_SCORE_CAP)


def quality_band(score: float) -> str:
    if score >= cfg.FPQ_BAND_HIGH:
        return "High Quality"
    if score >= cfg.FPQ_BAND_MEDIUM:
        return "Medium Quality"
    return "Low Quality"


# ─── Misc helpers ───────────────────────────────────────────────────────────

def _to_date(ts) -> Optional[date]:
    if ts is None:
        return None
    if isinstance(ts, date) and not isinstance(ts, datetime):
        return ts
    return pd.Timestamp(ts).date()
