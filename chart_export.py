"""
NSE Flag & Pole Scanner - Chart Export (visual review tool)
================================================================
Builds ONE self-contained HTML file covering every stock across every
tab of the Excel report — Confirmed Breakouts, Near Breakout
Watchlist, Today's Signals, Active Tracking, Historical Signals — plus
a curated Top Picks group, each stock tagged with which tab(s) it
belongs to (a stock can legitimately appear in more than one, exactly
as it can in the Excel workbook). Every card carries the readiness
reasons, the auto-generated remarks, and a plain-language setup
summary explaining why the scan flagged it — not just numbers.

TradingView-style candlestick viewer: symbol sidebar, 1D/1W/1M
timeframe toggle, a bar-by-bar "replay" control, pole/flag overlay
lines, and an "All Timeframes" button that pops out 1D+1W+1M together
in a separate window for quick comparison.

Deliberately separate from the scan/detect/report pipeline: this
script only READS the DB and the Parquet cache after a scan has
already run. It never touches config thresholds, detection logic, or
the Excel report. Run it any time after main_flag_pole.py:

    python chart_export.py                     # today's signals
    python chart_export.py --scan-date 2026-07-04

Output: charts/flag_pole_charts_<date>.html — a single file, no
server, no external requests at view time (the charting library is
vendored/inlined, not loaded from a CDN). Open it directly in any
browser. Nothing here is hosted; it's a local artifact meant to be
downloaded, opened, and thrown away — see the GitHub Actions workflow,
which uploads it with a 2-day artifact retention so it cleans itself
up automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

import config as cfg
import database as db
import explain_flag_pole as explain
from downloader import load_daily, resample_monthly, resample_weekly
from indicators import macd as macd_fn, rsi as rsi_fn, sma as sma_fn
from logger_utils import get_logger
from market_regime import compute_market_regime

log = get_logger("scanner")

TEMPLATE_PATH = Path(__file__).parent / "chart_viewer" / "template.html"
VENDOR_JS_PATH = Path(__file__).parent / "vendor" / "lightweight-charts.standalone.production.js"
VIEWER_JS_PATH = Path(__file__).parent / "chart_viewer" / "viewer.js"

# Mirrors the Excel report's sheet structure exactly, plus one curated
# group that doesn't correspond to a sheet. Order here is sidebar order.
TAB_LABELS = {
    "top_picks":  "⭐ Top Picks",
    "confirmed":  "🔥 Confirmed Breakouts",
    "watchlist":  "⚡ Near Breakout Watchlist",
    "todays":     "📋 Today's Signals",
    "active":     "📈 Active Tracking",
    "historical": "📚 Historical Signals",
}

MA_PERIODS = [5, 10, 20, 30, 50]


def main() -> None:
    args = _parse_args()
    scan_date = args.scan_date or date.today().isoformat()

    log.info("Chart export: building visual review for scan_date=%s", scan_date)

    db.init_db()
    tabbed_rows = _collect_tabbed_rows(scan_date)

    if not tabbed_rows:
        log.warning(
            "No signals found for scan_date=%s — run main_flag_pole.py first, "
            "or pass --scan-date for an earlier date that has signals.",
            scan_date,
        )
        return

    symbols_payload = []
    skipped = 0
    for signal_id, entry in tabbed_rows.items():
        payload = _build_symbol_payload(entry["row"], sorted(entry["tabs"]))
        if payload is None:
            skipped += 1
            continue
        symbols_payload.append(payload)

    if not symbols_payload:
        log.warning("No symbols had chartable price data — nothing to export.")
        return

    nifty = load_daily(cfg.NIFTY50_SYMBOL)
    regime = compute_market_regime(nifty)

    data_blob = {
        "scan_date": scan_date,
        "market_trend": regime.get("trend", "Unknown"),
        "distribution_days": regime.get("distribution_days_25d", 0),
        "tab_labels": TAB_LABELS,
        "symbols": symbols_payload,
    }

    output_path = _render_html(data_blob)

    counts = {tab: sum(1 for s in symbols_payload if tab in s["tabs"]) for tab in TAB_LABELS}
    size_mb = output_path.stat().st_size / (1024 * 1024)
    log.info(
        "Chart export complete: %d unique symbols charted (%d skipped, no price data) -> %s (%.1f MB)",
        len(symbols_payload), skipped, output_path, size_mb,
    )
    log.info("  Per-tab counts: %s", ", ".join(f"{TAB_LABELS[k]}={v}" for k, v in counts.items()))


# ─── Gathering rows across every tab, deduplicated by signal_id ────────────

def _collect_tabbed_rows(scan_date: str) -> dict:
    """
    Returns {signal_id: {"row": <pd.Series>, "tabs": {"confirmed", ...}}}.
    A signal_id appearing in multiple tab queries (e.g. a fresh
    BREAKOUT NOW that's both Confirmed and Today's Signals, and was
    also auto-triggered into Active Tracking the same run) gets every
    tab it belongs to — this mirrors the Excel workbook, where the
    same signal legitimately shows up on more than one sheet.
    """
    sources = [
        ("confirmed", db.get_confirmed_flag_pole_breakouts_df(scan_date)),
        ("watchlist", db.get_near_breakout_flag_pole_watchlist_df()),
        ("todays", db.get_todays_flag_pole_signals_df(scan_date)),
        ("active", db.get_active_flag_pole_tracking_df()),
        # Historical only ever grows — cap to the most recent N closed
        # trades (already ORDER BY scan_date DESC in the query) so the
        # export doesn't balloon after months of runs.
        ("historical", db.get_historical_flag_pole_signals_df().head(cfg.CHART_HISTORICAL_LOOKBACK_COUNT)),
    ]

    tabbed: dict[str, dict] = {}
    for tab_key, df in sources:
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            sid = row["signal_id"]
            if sid not in tabbed:
                tabbed[sid] = {"row": row, "tabs": set()}
            tabbed[sid]["tabs"].add(tab_key)

    _tag_top_picks(tabbed)
    return tabbed


def _tag_top_picks(tabbed: dict) -> None:
    """Top N by quality_score among today's actionable pool (Confirmed
    + Today's Signals) — not a separate query, just a re-ranking of
    what's already there, so it can never introduce a stock that isn't
    genuinely part of today's scan output."""
    candidates = [
        (sid, entry) for sid, entry in tabbed.items()
        if entry["tabs"] & {"confirmed", "todays"}
    ]
    candidates.sort(key=lambda kv: float(kv[1]["row"].get("quality_score") or 0), reverse=True)
    for sid, entry in candidates[: cfg.CHART_TOP_PICKS_COUNT]:
        entry["tabs"].add("top_picks")


# ─── Per-symbol payload ────────────────────────────────────────────────────

def _build_symbol_payload(row: pd.Series, tabs: list[str]) -> Optional[dict]:
    symbol = row["symbol"]
    daily = load_daily(symbol)
    if daily is None or daily.empty:
        return None

    timeframes = {
        "1D": _build_timeframe_payload(daily, cfg.CHART_BARS_LOOKBACK_DAILY, cfg.CHART_INDICATOR_LOOKBACK_DAILY),
        "1W": _build_timeframe_payload(resample_weekly(daily), cfg.CHART_BARS_LOOKBACK_WEEKLY, cfg.CHART_INDICATOR_LOOKBACK_WEEKLY),
        "1M": _build_timeframe_payload(resample_monthly(daily), cfg.CHART_BARS_LOOKBACK_MONTHLY, cfg.CHART_INDICATOR_LOOKBACK_MONTHLY),
    }
    if not timeframes["1D"]["bars"]:
        return None

    def g(col):
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return val

    # Retracement % measures how far price pulled back RELATIVE TO THE
    # POLE — a huge pole can retrace only 20% and still leave a wide,
    # choppy flag. Range % is the flag's own high-to-low width as a %
    # of price — the more direct "how narrow was the consolidation
    # itself" number. Both matter; neither alone is "tightness."
    flag_high, flag_low = g("flag_high"), g("flag_low")
    flag_range_pct = None
    if flag_high is not None and flag_low is not None and flag_low > 0:
        flag_range_pct = (flag_high - flag_low) / flag_low * 100.0

    payload = {
        "symbol": symbol,
        "company_name": g("company_name") or "",
        "sector": g("sector") or "",
        "signal_type": g("signal_type") or "",
        "status": g("status") or "",
        "tabs": tabs,
        "scan_date": _iso(g("scan_date")),
        "quality_score": float(g("quality_score") or 0),
        "breakout_readiness_pct": g("breakout_readiness_pct"),
        "readiness_reasons": g("readiness_reasons") or "",
        "readiness_near_pivot": g("readiness_near_pivot"),
        "readiness_flag_tight": g("readiness_flag_tight"),
        "readiness_volume_dryup": g("readiness_volume_dryup"),
        "readiness_rising_rs": g("readiness_rising_rs"),
        "readiness_pole_fresh": g("readiness_pole_fresh"),
        "remarks": g("remarks") or "",
        "buy_thesis": _build_setup_summary(row, g),
        "pole_pct_move": g("pole_pct_move"),
        "pole_atr_multiple": g("pole_atr_multiple"),
        "flag_retracement_pct": g("flag_retracement_pct"),
        "flag_range_pct": flag_range_pct,
        "flag_duration_bars": g("flag_duration_bars"),
        "volume_contraction_pct": g("volume_contraction_pct"),
        "pole_start_date": _iso(g("pole_start_date")),
        "pole_end_date": _iso(g("pole_end_date")),
        "flag_start_date": _iso(g("flag_start_date")),
        "flag_end_date": _iso(g("flag_end_date")),
        "flag_high": g("flag_high"),
        "flag_low": g("flag_low"),
        "breakout_date": _iso(g("breakout_date")),
        "breakout_volume_ratio": g("breakout_volume_ratio"),
        "pivot_point": g("pivot_point"),
        "price_vs_pivot_pct": g("price_vs_pivot_pct"),
        "entry_price": g("entry_price"),
        "entry_date": _iso(g("entry_date")),
        "stop_loss_price": g("stop_loss_price"),
        "stop_loss_type": g("stop_loss_type"),
        "target1": g("target1"),
        "target2": g("target2"),
        "rr_t1": g("rr_t1"),
        "rr_t2": g("rr_t2"),
        "rs_rating": g("rs_rating"),
        "rs_trend": g("rs_trend"),
        "market_trend": g("market_trend"),
        "liquidity_ok": g("liquidity_ok") if g("liquidity_ok") is not None else 1,
        "liquidity_warning": g("liquidity_warning"),
        "sell_notes": g("sell_notes") or "",
        "exit_date": _iso(g("exit_date")),
        "exit_price": g("exit_price"),
        "exit_type": g("exit_type"),
        "realised_rr": g("realised_rr"),
        "timeframes": timeframes,
    }

    # Rich explanation engine (see explain_flag_pole.py) — computed from
    # the SAME payload dict so every field it references is already
    # present and consistent with what's shown elsewhere on the card.
    payload["rating"] = explain.overall_rating(payload)
    payload["scanner_reasons"] = explain.scanner_reasons(payload)
    payload["why_buy"] = explain.why_buy(payload)
    payload["explanation"] = explain.full_explanation(payload)

    return payload


def _build_setup_summary(row: pd.Series, g) -> str:
    """
    A short, factual, plain-language paragraph explaining what the
    scan saw — not a recommendation, a description of the pattern
    facts driving the flag/quality score, so it's clear at a glance
    why a stock showed up without having to decode every column.
    """
    parts = []

    quality = g("quality_score")
    if quality is not None:
        band = ("a high-quality" if quality >= cfg.FPQ_BAND_HIGH else
                "a moderate-quality" if quality >= cfg.FPQ_BAND_MEDIUM else "a lower-quality")
        parts.append(f"Flagged as {band} bull flag setup ({quality:.0f}/100 quality score)")

    pole_pct = g("pole_pct_move")
    pole_atr = g("pole_atr_multiple")
    if pole_pct is not None:
        strength = ("an explosive" if pole_pct >= 25 else
                    "a strong" if pole_pct >= 18 else "a qualifying")
        atr_note = f", {pole_atr:.1f}x its normal daily range" if pole_atr is not None else ""
        parts.append(f"The pole was {strength} {pole_pct:.1f}% move{atr_note}")

    flag_retr = g("flag_retracement_pct")
    vol_contr = g("volume_contraction_pct")
    flag_high, flag_low = g("flag_high"), g("flag_low")
    flag_days = g("flag_duration_bars")
    if flag_retr is not None:
        tightness = "a tight" if flag_retr <= 30 else "a healthy" if flag_retr <= 42 else "a loose"
        range_note = ""
        if flag_high is not None and flag_low is not None and flag_low > 0:
            range_pct = (flag_high - flag_low) / flag_low * 100.0
            range_note = f", ranging just {range_pct:.1f}% high-to-low" if range_pct <= 8 else \
                         f", ranging {range_pct:.1f}% high-to-low"
        days_note = f" over {int(flag_days)} trading days" if flag_days is not None else ""
        vol_note = f" while volume dried up {vol_contr:.0f}% versus the pole" if vol_contr is not None else ""
        parts.append(
            f"The flag held {tightness} {flag_retr:.1f}% retracement{range_note}{days_note}{vol_note}"
        )

    signal_type = g("signal_type") or ""
    if signal_type == "BREAKOUT NOW":
        vol_ratio = g("breakout_volume_ratio")
        vr_note = f" on {vol_ratio:.1f}x average volume" if vol_ratio is not None else ""
        parts.append(f"It broke out{vr_note}")
    elif signal_type in ("NEAR BREAKOUT", "WATCHING"):
        pvp = g("price_vs_pivot_pct")
        pivot = g("pivot_point")
        if pvp is not None and pivot is not None:
            side = "above" if pvp >= 0 else "below"
            parts.append(f"Still forming — currently {abs(pvp):.1f}% {side} its ₹{pivot:.2f} pivot")

    rs = g("rs_rating")
    rs_trend = g("rs_trend")
    if rs is not None:
        rs_note = " and improving" if rs_trend == "Improving" else ""
        rs_band = ("a sector leader" if rs >= cfg.RS_LEADER_THRESHOLD else
                   "showing above-average strength" if rs >= cfg.RS_RISING_THRESHOLD else
                   "showing below-average relative strength")
        parts.append(f"RS Rating {rs:.0f} — {rs_band}{rs_note}")

    rr2 = g("rr_t2")
    if rr2 is not None:
        parts.append(f"Risk:reward to the second target is {rr2:.1f}:1")

    market_trend = g("market_trend")
    if market_trend and market_trend != "Uptrend":
        parts.append(
            f"Note: the broader market read '{market_trend}' when this was detected — "
            f"treat breakouts more cautiously in this regime"
        )

    status = g("status")
    if status in ("Stopped Out", "Target 1 Achieved", "Target 2 Achieved"):
        rr = g("realised_rr")
        rr_note = f" (realised R:R {rr:.2f})" if rr is not None else ""
        parts.append(f"Outcome: {status}{rr_note}")

    if not parts:
        return "Detected by the scan; not enough data to summarize further."

    return ". ".join(parts) + "."


def _iso(val) -> Optional[str]:
    if val is None:
        return None
    try:
        return pd.Timestamp(val).date().isoformat()
    except Exception:
        return None


def _build_timeframe_payload(df: pd.DataFrame, bars_lookback: int, indicator_lookback: int) -> dict:
    """
    Computes indicators on the FULL history in df, then trims the
    aligned output to the last `indicator_lookback` bars — critical
    ordering. Computing a 50-period SMA only on an already-trimmed
    window would leave the first 49 visible bars without a valid
    value; computing it on the full series first means every visible
    bar's SMA is correct from its own true preceding history, even
    history that isn't itself shown.

    Candles are trimmed separately to `bars_lookback` (effectively
    full history — see config.py's CHART_BARS_LOOKBACK_* comments) —
    deliberately a much larger window than the indicator lines get.
    """
    if df is None or df.empty:
        return {"bars": [], "indicators": {}}

    close = df["Close"]
    indicators = {}

    for period in MA_PERIODS:
        indicators[f"sma{period}"] = _series_to_points(sma_fn(close, period), indicator_lookback)

    indicators["rsi14"] = _series_to_points(rsi_fn(close, 14), indicator_lookback)

    macd_line, signal_line, hist = macd_fn(close)
    indicators["macd"] = _series_to_points(macd_line, indicator_lookback)
    indicators["macd_signal"] = _series_to_points(signal_line, indicator_lookback)
    indicators["macd_hist"] = _series_to_points(hist, indicator_lookback)

    return {
        "bars": _bars_to_json(df, bars_lookback),
        "indicators": indicators,
    }


def _series_to_points(series: pd.Series, lookback_bars: int) -> list[dict]:
    """Sparse {time, value} points, trimmed to the last lookback_bars —
    NaN points (not enough warm-up history yet) are simply omitted
    rather than sent as null; lightweight-charts renders a sparse line
    series starting wherever real data begins, no gap-handling needed
    on the JS side."""
    if series is None or series.empty:
        return []
    trimmed = series.tail(lookback_bars)
    out = []
    for ts, v in trimmed.items():
        if pd.notna(v):
            out.append({"time": ts.date().isoformat(), "value": round(float(v), 2)})
    return out


def _bars_to_json(df: pd.DataFrame, lookback_bars: int) -> list[dict]:
    if df is None or df.empty:
        return []
    trimmed = df.tail(lookback_bars)
    out = []
    for ts, r in trimmed.iterrows():
        if pd.isna(r.get("Close")):
            continue
        out.append({
            "time": ts.date().isoformat(),
            "o": round(float(r["Open"]), 2),
            "h": round(float(r["High"]), 2),
            "l": round(float(r["Low"]), 2),
            "c": round(float(r["Close"]), 2),
            "v": int(r["Volume"]) if pd.notna(r.get("Volume")) else 0,
        })
    return out


# ─── HTML rendering ─────────────────────────────────────────────────────────

def _render_html(data_blob: dict) -> Path:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    lwc_js = VENDOR_JS_PATH.read_text(encoding="utf-8")
    viewer_js = VIEWER_JS_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(data_blob, separators=(",", ":"))

    html = (
        template
        .replace("__SCAN_DATE__", data_blob["scan_date"])
        .replace("__MARKET_TREND__", data_blob["market_trend"])
        .replace("__DISTRIBUTION_DAYS__", str(data_blob["distribution_days"]))
        .replace("__SYMBOL_COUNT__", str(len(data_blob["symbols"])))
        .replace("/*__LIGHTWEIGHT_CHARTS_JS__*/", lwc_js)
        .replace("/*__CHART_DATA_JSON__*/", data_json)
        .replace("/*__VIEWER_LOGIC_JS__*/", viewer_js)
    )

    cfg.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = cfg.CHARTS_DIR / f"flag_pole_charts_{data_blob['scan_date']}.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


# ─── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export a visual chart-review HTML file covering every Excel tab")
    p.add_argument("--scan-date", type=str, default=None,
                    help="Date (YYYY-MM-DD) to export charts for. Defaults to today.")
    return p.parse_args()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.error("Chart export failed:\n%s", traceback.format_exc())
        sys.exit(1)
