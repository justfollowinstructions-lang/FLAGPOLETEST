"""
Tests for flag_pole_detector.py

Two layers, deliberately:
  1. Direct tests of _evaluate_candidate() with hand-specified pole/flag
     boundaries — precise, unambiguous, no risk of the exhaustive
     search finding a different valid sub-window than the one a test
     intends to exercise.
  2. End-to-end tests of detect_flag_pole() for the scenarios where
     the intended outcome is unambiguous regardless of what the search
     explores (a clean confirmed breakout, a still-forming flag, and
     inputs with no valid pattern anywhere in the series).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

import config as cfg
from flag_pole_detector import _evaluate_candidate, detect_flag_pole, quality_band
from indicators import atr as atr_fn, clean_volume
from tests.helpers import build_flag_pole_df


def _eval(df, ps, pe, fs, fe):
    close, high, low = df["Close"], df["High"], df["Low"]
    volume = clean_volume(df["Volume"])
    atr_series = atr_fn(df, period=cfg.ATR_PERIOD)
    return _evaluate_candidate(df, close, high, low, volume, atr_series, ps, pe, fs, fe)


# ─── _evaluate_candidate: acceptance ────────────────────────────────────────

def test_clean_flag_is_accepted():
    df, ps, pe, fs, fe = build_flag_pole_df(retrace_frac=0.30)
    cand = _eval(df, ps, pe, fs, fe)
    assert cand is not None
    assert cand["pole_pct_move"] == pytest.approx(25.0, abs=0.5)
    assert 0 < cand["flag_retracement_pct"] <= cfg.FLAG_MAX_RETRACEMENT_PCT
    assert cand["volume_contraction_pct"] >= cfg.FLAG_MIN_VOLUME_CONTRACTION_PCT


# ─── _evaluate_candidate: rejections ────────────────────────────────────────

def test_rejects_excessive_retracement():
    df, ps, pe, fs, fe = build_flag_pole_df(retrace_frac=0.70)
    assert _eval(df, ps, pe, fs, fe) is None


def test_rejects_weak_pole_pct_move():
    df, ps, pe, fs, fe = build_flag_pole_df(pole_move_pct=8.0)
    assert _eval(df, ps, pe, fs, fe) is None


def test_rejects_flag_with_no_volume_contraction():
    df, ps, pe, fs, fe = build_flag_pole_df(retrace_frac=0.30, flag_vol=220_000.0)
    assert _eval(df, ps, pe, fs, fe) is None


def test_rejects_flag_that_erases_the_pole():
    df, ps, pe, fs, fe = build_flag_pole_df(retrace_frac=1.05)
    assert _eval(df, ps, pe, fs, fe) is None


def test_rejects_gap_dominated_pole():
    """A single gap-up bar accounting for (almost) the entire pole
    move should be rejected — that's a gap, not a pole."""
    df, ps, pe, fs, fe = build_flag_pole_df()
    close = df["Close"].copy()
    # Collapse the pole into a single-bar gap: first pole bar jumps
    # straight to the pole's end price, remaining pole bars flat.
    pole_end_price = close.iloc[pe]
    for i in range(ps, pe + 1):
        close.iloc[i] = pole_end_price if i > ps else close.iloc[ps]
    df["Close"] = close
    cand = _eval(df, ps, pe, fs, fe)
    assert cand is None


# ─── detect_flag_pole: end-to-end ──────────────────────────────────────────

def test_end_to_end_confirmed_breakout():
    df, *_ = build_flag_pole_df(breakout=True, breakout_vol_mult=2.5)
    sigs = detect_flag_pole(df, "TEST.NS", rs_rating=80)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.signal_type == "BREAKOUT NOW"
    assert sig.breakout_date is not None
    assert sig.breakout_volume_ratio >= cfg.BREAKOUT_MIN_VOLUME_MULTIPLE
    assert sig.quality_score > 0


def test_end_to_end_still_forming_flag_is_watchlist():
    df, *_ = build_flag_pole_df(breakout=False)
    sigs = detect_flag_pole(df, "TEST.NS", rs_rating=60)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.signal_type in ("NEAR BREAKOUT", "WATCHING", "EARLY STAGE")
    assert sig.breakout_date is None


def test_end_to_end_insufficient_data_returns_empty():
    df, *_ = build_flag_pole_df(base_n=5)   # well under MIN_DAILY_BARS_FLAG_POLE
    assert detect_flag_pole(df, "TEST.NS") == []


def test_end_to_end_no_volume_column_returns_empty():
    df, *_ = build_flag_pole_df()
    df = df.drop(columns=["Volume"])
    assert detect_flag_pole(df, "TEST.NS") == []


def test_end_to_end_flat_series_finds_nothing():
    from tests.helpers import make_flat
    o, h, l, c, v = make_flat(80, price=100.0)
    import pandas as pd
    idx = pd.bdate_range("2024-01-01", periods=80)
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    assert detect_flag_pole(df, "FLAT.NS") == []


# ─── quality_band ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [
    (85, "High Quality"),
    (55, "Medium Quality"),
    (10, "Low Quality"),
])
def test_quality_band(score, expected):
    assert quality_band(score) == expected
