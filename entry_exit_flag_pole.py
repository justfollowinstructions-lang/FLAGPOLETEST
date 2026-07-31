"""
NSE Flag & Pole Scanner - Entry & Exit Calculator
=====================================================
Detection decides WHETHER a stock is shown, this module decides HOW to
trade it. check_liquidity()/check_volume_confirmation() live here
directly (this package doesn't ship a Cup & Handle entry_exit.py to
import them from) — if this scanner is combined with a companion
pattern scanner later, promote these two back to a shared module and
both scanners can import from there instead of duplicating.

Targets are measured-move (pole height projected from the breakout),
not a cup-depth/Fibonacci scheme — a flag's whole thesis is "the pole
repeats," so the pole height IS the target basis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config as cfg
from flag_pole_detector import FlagPoleSignal
from indicators import atr as atr_series_fn, clean_volume, rsi as rsi_fn


# ─── Volume confirmation ───────────────────────────────────────────────────

def check_volume_confirmation(
    sig, df: pd.DataFrame
) -> tuple[Optional[float], bool, str]:
    if "Volume" not in df.columns:
        return None, False, "N/A"

    volume = clean_volume(df["Volume"])

    if sig.timeframe == "daily":
        avg_bars = cfg.VOLUME_AVG_BARS_DAILY
        threshold = cfg.VOLUME_CONFIRM_DAILY
    else:
        avg_bars = cfg.VOLUME_AVG_BARS_WEEKLY
        threshold = cfg.VOLUME_CONFIRM_WEEKLY

    if len(volume) < avg_bars + 1:
        return None, False, "N/A"

    avg_vol = volume.iloc[-(avg_bars + 1):-1].mean()
    breakout_vol = volume.iloc[-1]

    if avg_vol <= 0 or pd.isna(avg_vol):
        return None, False, "N/A"

    ratio = breakout_vol / avg_vol
    confirmed = ratio >= threshold

    if sig.signal_type != "BREAKOUT NOW":
        # Volume confirmation only strictly applies to live breakouts;
        # for watching/basing signals we still show the ratio for context
        return ratio, confirmed, "N/A"

    return ratio, confirmed, ("Yes" if confirmed else "No")


# ─── Liquidity gate ─────────────────────────────────────────────────────────

def check_liquidity(df: pd.DataFrame, current_price: float) -> tuple[bool, Optional[str]]:
    if current_price < cfg.MIN_PRICE:
        return False, f"Price below ₹{cfg.MIN_PRICE} — too illiquid for entry/exit sizing"

    if "Volume" not in df.columns or len(df) < cfg.LIQUIDITY_LOOKBACK_BARS:
        return True, None

    volume = clean_volume(df["Volume"])
    avg_vol = volume.iloc[-cfg.LIQUIDITY_LOOKBACK_BARS:].mean()

    if pd.isna(avg_vol) or avg_vol < cfg.MIN_AVG_VOLUME:
        return False, f"Avg volume below {cfg.MIN_AVG_VOLUME:,} shares — too illiquid"

    return True, None


@dataclass
class FlagPoleEntryExitPlan:
    entry_price: float
    entry_type: str                 # Breakout / Pullback-to-Flag / Wait

    stop_loss_price: float
    stop_loss_pct: float
    stop_loss_type: str             # flag_low_minus_1atr / 8pct_cap
    atr_14: float
    risk_per_share: float

    target1: float                  # breakout + 1x pole height (measured move)
    target2: float                  # breakout + FP_TARGET2_MULTIPLE x pole height
    rr_t1: float
    rr_t2: float
    rr_t2_warning: Optional[str]

    position_size_shares: int
    capital_required: float
    risk_amount: float
    portfolio_risk_pct: float

    volume_ratio: float
    volume_confirmed: bool
    volume_confirmed_label: str

    market_note: Optional[str]
    weak_momentum_note: Optional[str]

    sell_notes: str
    liquidity_ok: bool
    liquidity_warning: Optional[str]


def calculate_entry_exit_flag_pole(
    sig: FlagPoleSignal,
    df: pd.DataFrame,
    market_trend: str,     # "Uptrend" / "Uptrend Under Pressure" / "Correction" / "Unknown"
) -> FlagPoleEntryExitPlan:
    """
    Always returns a plan (never None) — same "every detected setup
    gets entry/exit pre-computed" convention as Cup & Handle.
    """
    close = df["Close"]
    current_price = float(close.iloc[-1])

    # ── Entry price & buffer (same convention as Cup & Handle) ──
    buffer = (
        cfg.BREAKOUT_BUFFER_PCT * sig.pivot_point
        if sig.pivot_point > cfg.BREAKOUT_BUFFER_PRICE_CUTOFF
        else cfg.BREAKOUT_BUFFER_INR
    )
    entry_price = sig.pivot_point + buffer
    entry_type = _classify_entry_type(sig)

    # ── ATR ──
    atr_series = atr_series_fn(df, period=cfg.ATR_PERIOD)
    atr_14 = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0

    # ── Stop loss: flag low minus 1x ATR, capped at MAX_STOP_PCT ──
    raw_stop = sig.flag_low - (cfg.STOP_ATR_MULTIPLIER * atr_14)
    hard_cap_stop = entry_price * (1 - cfg.MAX_STOP_PCT)
    if raw_stop < hard_cap_stop:
        stop_loss_price, stop_loss_type = hard_cap_stop, "8pct_cap"
    else:
        stop_loss_price, stop_loss_type = raw_stop, "flag_low_minus_1atr"

    stop_loss_pct = ((entry_price - stop_loss_price) / entry_price) * 100.0 if entry_price else 0.0
    risk_per_share = max(entry_price - stop_loss_price, 0.01)

    # ── Targets: measured move off the pole height ──
    pole_height = sig.pole_end_price - sig.pole_start_price
    target1 = entry_price + pole_height * cfg.FP_TARGET1_MULTIPLE
    target2 = entry_price + pole_height * cfg.FP_TARGET2_MULTIPLE

    rr_t1 = (target1 - entry_price) / risk_per_share
    rr_t2 = (target2 - entry_price) / risk_per_share
    rr_t2_warning = (
        f"R:R below {cfg.FP_MIN_RR_T2}:1 at T2 — position size carefully"
        if rr_t2 < cfg.FP_MIN_RR_T2 else None
    )

    # ── Position sizing (identical formula/config to Cup & Handle) ──
    max_risk_amount = cfg.PORTFOLIO_VALUE * (cfg.RISK_PER_TRADE_PCT / 100.0)
    position_size_shares = int(max_risk_amount // risk_per_share) if risk_per_share > 0 else 0
    capital_required = position_size_shares * entry_price
    risk_amount = position_size_shares * risk_per_share
    portfolio_risk_pct = (risk_amount / cfg.PORTFOLIO_VALUE) * 100.0 if cfg.PORTFOLIO_VALUE else 0.0

    # ── Volume confirmation ──
    volume_ratio, volume_confirmed, volume_confirmed_label = check_volume_confirmation(sig, df)

    # ── Soft conditions ──
    market_note = (
        "Counter-trend or weakening market — higher false-breakout risk"
        if market_trend != "Uptrend" else None
    )
    weak_momentum_note = None
    try:
        rsi_val = rsi_fn(close, period=cfg.RSI_PERIOD).iloc[-1]
        if pd.notna(rsi_val) and float(rsi_val) < cfg.RSI_WEAK_MOMENTUM_THRESHOLD:
            weak_momentum_note = "Weak momentum"
    except Exception:
        pass

    # ── Liquidity gate ──
    liquidity_ok, liquidity_warning = check_liquidity(df, current_price)

    sell_notes = _build_sell_notes()

    return FlagPoleEntryExitPlan(
        entry_price=round(entry_price, 2),
        entry_type=entry_type,
        stop_loss_price=round(stop_loss_price, 2),
        stop_loss_pct=round(stop_loss_pct, 2),
        stop_loss_type=stop_loss_type,
        atr_14=round(atr_14, 2),
        risk_per_share=round(risk_per_share, 2),
        target1=round(target1, 2),
        target2=round(target2, 2),
        rr_t1=round(rr_t1, 2),
        rr_t2=round(rr_t2, 2),
        rr_t2_warning=rr_t2_warning,
        position_size_shares=position_size_shares,
        capital_required=round(capital_required, 2),
        risk_amount=round(risk_amount, 2),
        portfolio_risk_pct=round(portfolio_risk_pct, 3),
        volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else 0.0,
        volume_confirmed=volume_confirmed,
        volume_confirmed_label=volume_confirmed_label,
        market_note=market_note,
        weak_momentum_note=weak_momentum_note,
        sell_notes=sell_notes,
        liquidity_ok=liquidity_ok,
        liquidity_warning=liquidity_warning,
    )


# ─── Entry classification ──────────────────────────────────────────────────

def _classify_entry_type(sig: FlagPoleSignal) -> str:
    if sig.signal_type == "BREAKOUT NOW":
        return "Breakout"
    if sig.signal_type == "NEAR BREAKOUT" and sig.breakout_date is not None:
        return "Breakout"   # confirmed within the lookback window, just not today
    if sig.price_vs_pivot_pct < 0:
        return "Pullback-to-Flag"
    return "Wait"


# ─── Sell rules checklist (flag/pole specific) ─────────────────────────────

def _build_sell_notes() -> str:
    mandatory = [
        "Stop loss hit -> exit same session, no exceptions",
        "Closes back inside the flag channel after a confirmed breakout -> pattern failed, exit",
    ]
    recommended = [
        f"Gives back more than 50% of the post-breakout gain within "
        f"{cfg.BREAKOUT_CONFIRMED_LOOKBACK_DAYS} days (failed follow-through)",
        "Market regime moves to Correction -> tighten stops / reduce size",
        f"No progress toward T1 within {cfg.STALE_FLAG_POLE_MAX_BARS} bars of breakout (stalled move)",
    ]
    hold = [
        "T1 hit fast (within a few days of breakout) on strong volume -> consider holding for T2",
    ]

    parts = ["MANDATORY: " + "; ".join(mandatory)]
    parts.append("RECOMMENDED: " + "; ".join(recommended))
    parts.append("HOLD IF: " + "; ".join(hold))
    return " || ".join(parts)
