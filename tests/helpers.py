"""
Shared synthetic-OHLCV builders for the Flag & Pole test suite.

These are deliberately hand-constructed (not fetched/mocked market
data) so every test asserts against a known, controllable ground
truth — a clean pole, a clean flag, a known retracement, a known
volume profile.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_flat(n: int, price: float = 100.0, seed: int = 1, vol: float = 100_000.0):
    """n bars of small-noise, non-trending price action around `price`."""
    rng = np.random.default_rng(seed)
    closes = price + rng.normal(0, price * 0.003, n)
    highs = closes + np.abs(rng.normal(0, price * 0.002, n))
    lows = closes - np.abs(rng.normal(0, price * 0.002, n))
    opens = closes + rng.normal(0, price * 0.001, n)
    vols = np.full(n, float(vol))
    return opens, highs, lows, closes, vols


def build_flag_pole_df(
    base_n: int = 60,
    pole_len: int = 6,
    pole_move_pct: float = 25.0,
    flag_len: int = 6,
    retrace_frac: float = 0.30,
    pole_vol: float = 220_000.0,
    flag_vol: float = 60_000.0,
    base_vol: float = 100_000.0,
    breakout: bool = False,
    breakout_vol_mult: float = 2.0,
    tail_extra: int = 0,
):
    """
    Build a base -> pole -> flag [-> breakout bar] [-> tail] OHLCV
    series and return (df, pole_start_idx, pole_end_idx, flag_start_idx,
    flag_end_idx) — the exact integer bar positions of each segment,
    so tests can call _evaluate_candidate directly with unambiguous
    boundaries instead of relying on detect_flag_pole's exhaustive
    search to rediscover them (the search can legitimately find a
    different, shorter valid sub-window than the one a test intends
    to exercise — see the direct-vs-end-to-end test split in
    tests/test_flag_pole_detector.py).

    base_n defaults to 60 so the total series length comfortably
    exceeds MIN_DAILY_BARS_FLAG_POLE for end-to-end detect_flag_pole
    tests; direct _evaluate_candidate tests don't care about the
    overall length, only ATR(14) warm-up (>=14 bars before pole_end).

    pole_closes spans pole_len bars from pole_start_price to
    pole_end_price INCLUSIVE at both ends (so close.iloc[pole_start_idx]
    == pole_start_price and close.iloc[pole_end_idx] == pole_end_price
    exactly) — tests assert against these exact values.
    """
    o1, h1, l1, c1, v1 = make_flat(base_n, price=100.0, vol=base_vol)

    pole_start = c1[-1]
    pole_end = pole_start * (1 + pole_move_pct / 100.0)
    pole_closes = np.linspace(pole_start, pole_end, pole_len)
    pole_opens = np.concatenate([[c1[-1]], pole_closes[:-1]])
    pole_highs = pole_closes * 1.004
    pole_lows = np.minimum(pole_opens, pole_closes) * 0.997
    pole_vols = np.full(pole_len, float(pole_vol))

    pole_height = pole_end - pole_start
    flag_low_level = pole_end - pole_height * retrace_frac
    flag_closes = np.linspace(pole_end, flag_low_level, flag_len)
    flag_highs = flag_closes + pole_height * 0.015
    flag_lows = flag_closes - pole_height * 0.015
    flag_opens = np.concatenate([[pole_end], flag_closes[:-1]])
    flag_vols = np.full(flag_len, float(flag_vol))

    frames_o = [o1, pole_opens, flag_opens]
    frames_h = [h1, pole_highs, flag_highs]
    frames_l = [l1, pole_lows, flag_lows]
    frames_c = [c1, pole_closes, flag_closes]
    frames_v = [v1, pole_vols, flag_vols]

    if breakout:
        last_flag_close = flag_closes[-1]
        bo_close = pole_end * 1.03
        bo_open = last_flag_close
        bo_high = bo_close * 1.01
        bo_low = min(bo_open, bo_close) * 0.995
        bo_vol = flag_vol * breakout_vol_mult
        frames_o.append(np.array([bo_open]))
        frames_h.append(np.array([bo_high]))
        frames_l.append(np.array([bo_low]))
        frames_c.append(np.array([bo_close]))
        frames_v.append(np.array([bo_vol]))

    if tail_extra:
        o2, h2, l2, c2, v2 = make_flat(tail_extra, price=frames_c[-1][-1], seed=3, vol=base_vol)
        frames_o.append(o2); frames_h.append(h2); frames_l.append(l2)
        frames_c.append(c2); frames_v.append(v2)

    o = np.concatenate(frames_o)
    h = np.concatenate(frames_h)
    l = np.concatenate(frames_l)
    c = np.concatenate(frames_c)
    v = np.concatenate(frames_v)

    idx = pd.bdate_range("2024-01-01", periods=len(c))
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)

    pole_start_idx = base_n
    pole_end_idx = base_n + pole_len - 1
    flag_start_idx = pole_end_idx + 1
    flag_end_idx = flag_start_idx + flag_len - 1
    return df, pole_start_idx, pole_end_idx, flag_start_idx, flag_end_idx
