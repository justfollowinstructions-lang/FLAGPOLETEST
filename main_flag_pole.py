"""
NSE Flag & Pole Scanner - Main Orchestrator
================================================
Wires the pipeline together:
  universe -> downloader -> indicators (RS Rating) -> market_regime ->
  flag_pole_detector -> readiness_flag_pole -> entry_exit_flag_pole ->
  database -> report_flag_pole

Run modes:
  python main_flag_pole.py                       # incremental daily scan
  python main_flag_pole.py --full-refresh        # wipe and redownload all data
  python main_flag_pole.py --refresh-universe    # force-refresh NSE symbol list
  python main_flag_pole.py --debug-symbol TCS.NS # verbose single-symbol diagnosis

Named main_flag_pole.py (not main.py) on purpose: if this scanner is
combined with a companion pattern scanner later, that scanner's own
main.py can sit right next to this file with no rename needed. If/when
that happens and both entry points run in the same job, calling
run_download() from each is intentional and cheap — the Parquet cache
is incremental (see downloader._incremental_start), so the second call
is a near-instant per-symbol no-op rather than a second full NSE fetch.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

import config as cfg
import database as db
from downloader import load_daily, run_download
from entry_exit_flag_pole import calculate_entry_exit_flag_pole
from flag_pole_detector import FlagPoleSignal, detect_flag_pole
from indicators import compute_universe_rs_ratings, rsi as rsi_fn
from logger_utils import get_logger
from market_regime import compute_market_regime
from readiness_flag_pole import compute_flag_pole_readiness
from report_flag_pole import generate_flag_pole_excel_report
from telegram_notify import notify_scan_complete
from universe import fetch_nse_symbols, get_symbol_meta

log = get_logger("scanner")


def main() -> None:
    args = _parse_args()
    started_at = datetime.now()
    log.info("=" * 70)
    log.info("NSE FLAG & POLE SCANNER — run started %s", started_at.isoformat())
    log.info("=" * 70)

    db.init_db()

    cleaned = db.cleanup_bad_flag_pole_triggers(date.today().isoformat())
    if cleaned:
        log.info("Cleaned up %d incorrectly triggered signals -> reset to Watching", cleaned)

    if args.debug_symbol:
        _run_debug_single_symbol(args.debug_symbol)
        return

    symbols = fetch_nse_symbols(force_refresh=args.refresh_universe)
    log.info("Universe: %d symbols", len(symbols))

    run_download(symbols, full_refresh=args.full_refresh)

    nifty = load_daily(cfg.NIFTY50_SYMBOL)
    regime = compute_market_regime(nifty)
    market_trend = regime["trend"]
    log.info(
        "Market trend: %s (%d distribution days in last %d sessions)",
        market_trend, regime["distribution_days_25d"], cfg.DISTRIBUTION_DAY_WINDOW,
    )

    rs_ratings, rs_trends = compute_universe_rs_ratings(symbols)
    log.info("RS Ratings computed for %d symbols", len(rs_ratings))

    scan_date = date.today().isoformat()
    error_count = 0
    pattern_count = 0
    by_signal_type: dict[str, int] = {}
    by_rs_band = {"<60": 0, "60-80": 0, ">80": 0}

    for i, symbol in enumerate(symbols, 1):
        if i % 200 == 0:
            log.info("Progress: %d/%d symbols scanned", i, len(symbols))
        try:
            found = _scan_symbol(
                symbol, rs_ratings, rs_trends, market_trend, scan_date,
                by_signal_type, by_rs_band,
            )
            pattern_count += found
        except Exception:
            error_count += 1
            log.debug("Error scanning %s:\n%s", symbol, traceback.format_exc())

    error_rate = error_count / len(symbols) if symbols else 0
    if error_rate > 0.10:
        log.warning(
            "High error rate: %d/%d symbols (%.1f%%) failed to scan",
            error_count, len(symbols), error_rate * 100,
        )
    else:
        log.info("Scan complete: %d errors out of %d symbols", error_count, len(symbols))

    db.prune_expired_flag_pole_watchlist()
    _update_active_tracking()

    summary_stats = _build_summary_stats(
        scan_date, len(symbols), pattern_count, by_signal_type, by_rs_band, regime,
    )
    _generate_report(scan_date, summary_stats, skip_telegram=args.no_telegram)

    elapsed = (datetime.now() - started_at).total_seconds()
    log.info("=" * 70)
    log.info("RUN COMPLETE in %.1f minutes — %d patterns detected across %d symbols",
              elapsed / 60, pattern_count, len(symbols))
    log.info("=" * 70)


# ─── Per-symbol scan ────────────────────────────────────────────────────────

def _scan_symbol(
    symbol: str,
    rs_ratings: dict,
    rs_trends: dict,
    market_trend: str,
    scan_date: str,
    by_signal_type: dict,
    by_rs_band: dict,
) -> int:
    daily = load_daily(symbol)
    if daily is None or len(daily) < cfg.MIN_DAILY_BARS_FLAG_POLE:
        return 0

    rs_val = rs_ratings.get(symbol, 50.0)
    rs_trend_label = rs_trends.get(symbol, "Unknown")
    meta = get_symbol_meta(symbol)

    try:
        sigs = detect_flag_pole(daily, symbol, rs_rating=rs_val)
    except Exception:
        log.debug("Detection error %s:\n%s", symbol, traceback.format_exc())
        return 0

    n_persisted = 0
    for sig in sigs:
        if _is_stale(sig, daily):
            continue
        try:
            _finalise_and_persist_signal(
                sig, daily, symbol, meta, rs_val, rs_trend_label,
                market_trend, scan_date,
            )
            by_signal_type[sig.signal_type] = by_signal_type.get(sig.signal_type, 0) + 1
            if rs_val >= cfg.RS_LEADER_THRESHOLD:
                by_rs_band[">80"] += 1
            elif rs_val >= cfg.RS_RISING_THRESHOLD:
                by_rs_band["60-80"] += 1
            else:
                by_rs_band["<60"] += 1
            n_persisted += 1
        except Exception:
            log.debug("Persist error %s:\n%s", symbol, traceback.format_exc())

    return n_persisted


def _is_stale(sig: FlagPoleSignal, df: pd.DataFrame) -> bool:
    """Skip patterns whose flag ended too many bars ago — a flag that
    finished forming 20+ trading days back without a fresh breakout is
    old news (see STALE_FLAG_POLE_MAX_BARS)."""
    try:
        flag_end_pos = df.index.get_indexer(
            [pd.Timestamp(sig.flag_end_date)], method="nearest"
        )[0]
        bars_since = len(df) - 1 - flag_end_pos
        return bars_since > cfg.STALE_FLAG_POLE_MAX_BARS
    except Exception:
        return False


def _finalise_and_persist_signal(
    sig: FlagPoleSignal,
    df: pd.DataFrame,
    symbol: str,
    meta: dict,
    rs_val: float,
    rs_trend_label: str,
    market_trend: str,
    scan_date: str,
) -> None:
    readiness = compute_flag_pole_readiness(sig, df, rs_trend_label)
    plan = calculate_entry_exit_flag_pole(sig, df, market_trend)

    rsi_val = _safe_last(rsi_fn(df["Close"], period=cfg.RSI_PERIOD))

    rs_tag = (
        "RS Leader" if rs_val >= cfg.RS_LEADER_THRESHOLD
        else "RS Rising" if rs_val >= cfg.RS_RISING_THRESHOLD
        else "RS Laggard" if rs_val < cfg.RS_LAGGARD_THRESHOLD
        else "-"
    )

    remarks = _build_remarks(sig, plan, rs_tag, readiness)

    signal_id = db.make_signal_id(symbol, "daily", sig.pole_start_date, sig.pivot_point)

    row = {
        "signal_id": signal_id,
        "symbol": symbol,
        "company_name": meta.get("name", ""),
        "sector": meta.get("sector", ""),
        "scan_date": scan_date,
        "timeframe": "daily",
        "pattern_type": sig.pattern_type,
        "signal_type": sig.signal_type,
        "quality_score": sig.quality_score,

        "pole_start_date": str(sig.pole_start_date),
        "pole_end_date": str(sig.pole_end_date),
        "pole_start_price": sig.pole_start_price,
        "pole_end_price": sig.pole_end_price,
        "pole_pct_move": sig.pole_pct_move,
        "pole_atr_multiple": sig.pole_atr_multiple,
        "pole_duration_bars": sig.pole_duration_bars,

        "flag_start_date": str(sig.flag_start_date),
        "flag_end_date": str(sig.flag_end_date),
        "flag_high": sig.flag_high,
        "flag_low": sig.flag_low,
        "flag_retracement_pct": sig.flag_retracement_pct,
        "flag_duration_bars": sig.flag_duration_bars,
        "upper_trendline_slope": sig.upper_trendline_slope,
        "lower_trendline_slope": sig.lower_trendline_slope,
        "volume_contraction_pct": sig.volume_contraction_pct,

        "pivot_point": sig.pivot_point,
        "current_price": sig.current_price,
        "price_vs_pivot_pct": sig.price_vs_pivot_pct,

        "breakout_date": str(sig.breakout_date) if sig.breakout_date else None,
        "breakout_volume_ratio": sig.breakout_volume_ratio,

        "breakout_readiness_pct": readiness["readiness_pct"],
        "readiness_near_pivot": int(readiness["near_pivot"]),
        "readiness_flag_tight": int(readiness["flag_tight"]),
        "readiness_volume_dryup": int(readiness["volume_drying_up"]),
        "readiness_rising_rs": int(readiness["rising_rs"]),
        "readiness_pole_fresh": int(readiness["pole_freshness"]),
        "readiness_reasons": readiness["reasons_str"],

        "entry_price": plan.entry_price,
        "entry_type": plan.entry_type,

        "stop_loss_price": plan.stop_loss_price,
        "stop_loss_pct": plan.stop_loss_pct,
        "stop_loss_type": plan.stop_loss_type,
        "atr_14": plan.atr_14,
        "risk_per_share": plan.risk_per_share,

        "target1": plan.target1,
        "target2": plan.target2,
        "rr_t1": plan.rr_t1,
        "rr_t2": plan.rr_t2,
        "rr_t2_warning": plan.rr_t2_warning,

        "position_size_shares": plan.position_size_shares,
        "capital_required": plan.capital_required,
        "risk_amount": plan.risk_amount,
        "portfolio_risk_pct": plan.portfolio_risk_pct,

        "volume_ratio": plan.volume_ratio,
        "volume_confirmed": int(plan.volume_confirmed),
        "volume_confirmed_label": plan.volume_confirmed_label,

        "rs_rating": rs_val,
        "rs_trend": rs_trend_label,
        "rs_tag": rs_tag,
        "rsi_val": rsi_val,

        "market_trend": market_trend,
        "market_note": plan.market_note,
        "weak_momentum_note": plan.weak_momentum_note,

        "liquidity_ok": int(plan.liquidity_ok),
        "liquidity_warning": plan.liquidity_warning,

        "sell_notes": plan.sell_notes,
        "remarks": remarks,

        "status": "Watching",
        "entry_triggered": 0,
        "expiry_date": (date.today() + timedelta(days=cfg.WATCHLIST_EXPIRY_DAYS)).isoformat(),
    }

    db.upsert_flag_pole_signal(row)


def _build_remarks(sig: FlagPoleSignal, plan, rs_tag: str, readiness: dict) -> str:
    parts = []
    if rs_tag == "RS Leader":
        parts.append("RS Leader")
    if plan.volume_ratio and plan.volume_ratio >= 2.0:
        parts.append("Vol Surge")
    if plan.stop_loss_type == "8pct_cap":
        parts.append("Stop Capped")
    if not plan.liquidity_ok:
        parts.append("LOW LIQUIDITY")
    if readiness.get("readiness_pct") is not None and readiness["readiness_pct"] >= cfg.READINESS_BAND_HIGH:
        parts.append("High Readiness")
    if plan.market_note:
        parts.append(plan.market_note)
    return ", ".join(parts)


def _safe_last(series: pd.Series) -> Optional[float]:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else None


# ─── Active tracking updates ────────────────────────────────────────────────

def _update_active_tracking() -> None:
    """Mirrors main.py's _update_active_tracking, against the
    flag_pole_signals table and its 2-target (no T3) scheme."""
    open_signals = db.get_open_flag_pole_signals()
    today_str = date.today().isoformat()

    for row in open_signals:
        symbol = row["symbol"]
        daily = load_daily(symbol)
        if daily is None or daily.empty:
            continue
        current_price = float(daily["Close"].iloc[-1])

        updates = {"current_price": round(current_price, 2)}
        stop = row.get("stop_loss_price")
        t1, t2 = row.get("target1"), row.get("target2")

        if stop is not None and current_price <= stop and not row.get("stopped_out"):
            updates.update({
                "stopped_out": 1, "status": "Stopped Out",
                "exit_date": today_str, "exit_price": current_price,
                "exit_type": "Stop",
            })
        else:
            if t2 is not None and current_price >= t2 and not row.get("t2_achieved"):
                updates.update({"t2_achieved": 1, "status": "Target 2 Achieved"})
            elif t1 is not None and current_price >= t1 and not row.get("t1_achieved"):
                updates.update({"t1_achieved": 1, "status": "Target 1 Achieved"})

        db.update_flag_pole_signal_status(row["signal_id"], **updates)

    # ── Auto-trigger BREAKOUT NOW signals from today's scan ──────────────
    watching = db.get_watching_flag_pole_signals()
    for row in watching:
        symbol = row["symbol"]
        signal_type = row.get("signal_type", "")
        scan_date_db = row.get("scan_date", "")

        if signal_type != "BREAKOUT NOW" or scan_date_db != today_str:
            continue

        pivot = row.get("pivot_point")
        if pivot is None:
            continue

        daily = load_daily(symbol)
        if daily is None or daily.empty:
            continue

        current_price = float(daily["Close"].iloc[-1])
        if current_price >= pivot:
            db.update_flag_pole_signal_status(
                row["signal_id"],
                entry_triggered=1,
                entry_date=today_str,
                status="Triggered",
                current_price=round(current_price, 2),
            )


# ─── Summary stats / report ─────────────────────────────────────────────────

def _build_summary_stats(
    scan_date: str, total_symbols: int, total_patterns: int,
    by_signal_type: dict, by_rs_band: dict, regime: dict,
) -> dict:
    confirmed_df = db.get_confirmed_flag_pole_breakouts_df(scan_date)
    todays_df = db.get_todays_flag_pole_signals_df(scan_date)

    return {
        "scan_date": scan_date,
        "total_symbols": total_symbols,
        "total_patterns": total_patterns,
        "n_confirmed": len(confirmed_df) if not confirmed_df.empty else 0,
        "n_actionable": len(todays_df) if not todays_df.empty else 0,
        "by_signal_type": by_signal_type,
        "by_rs_band": by_rs_band,
        "market_trend": regime.get("trend", "Unknown"),
        "distribution_days": regime.get("distribution_days_25d", 0),
    }


def _generate_report(scan_date: str, summary_stats: dict, skip_telegram: bool = False) -> None:
    confirmed_breakouts = db.get_confirmed_flag_pole_breakouts_df(scan_date)
    todays_signals = db.get_todays_flag_pole_signals_df(scan_date)
    watchlist = db.get_near_breakout_flag_pole_watchlist_df()
    active_tracking = db.get_active_flag_pole_tracking_df()
    historical = db.get_historical_flag_pole_signals_df()

    n_confirmed = len(confirmed_breakouts) if not confirmed_breakouts.empty else 0
    log.info("Confirmed Flag & Pole Breakouts today: %d", n_confirmed)

    output_path = cfg.REPORTS_DIR / f"flag_pole_report_{scan_date}.xlsx"
    generate_flag_pole_excel_report(
        confirmed_breakouts, todays_signals, watchlist,
        active_tracking, historical, summary_stats, output_path,
    )

    if skip_telegram:
        log.info("Telegram notification skipped (--no-telegram)")
        return

    confirmed_symbols = (
        list(confirmed_breakouts["symbol"]) if not confirmed_breakouts.empty else []
    )
    try:
        notify_scan_complete(summary_stats, confirmed_symbols, output_path)
    except Exception:
        # notify_scan_complete already catches its own request errors; this
        # is a last-resort guard so nothing here can ever fail the run.
        log.error("Unexpected error sending Telegram notification", exc_info=True)


# ─── Debug single symbol ───────────────────────────────────────────────────

def _run_debug_single_symbol(symbol: str) -> None:
    log.info("DEBUG MODE: %s", symbol)
    if not symbol.endswith(".NS") and not symbol.startswith("^"):
        symbol = f"{symbol}.NS"

    run_download([symbol], full_refresh=False)
    daily = load_daily(symbol)
    if daily is None:
        log.error("No data available for %s", symbol)
        return

    log.info("%s: %d daily bars, %s to %s", symbol, len(daily),
              daily.index[0].date(), daily.index[-1].date())

    nifty = load_daily(cfg.NIFTY50_SYMBOL)
    regime = compute_market_regime(nifty)
    log.info("Market trend: %s | distribution days: %d",
              regime["trend"], regime["distribution_days_25d"])

    rs_ratings, rs_trends = compute_universe_rs_ratings([symbol])
    rs_val = rs_ratings.get(symbol, 50.0)
    rs_trend_label = rs_trends.get(symbol, "Unknown")
    log.info("RS Rating: %.0f (%s)", rs_val, rs_trend_label)

    sigs = detect_flag_pole(daily, symbol, rs_rating=rs_val)
    if not sigs:
        log.info("No Flag & Pole pattern detected.")
        return

    for s in sigs:
        log.info("-" * 50)
        log.info("Signal: %s | Quality: %.1f", s.signal_type, s.quality_score)
        log.info("Pole: %s -> %s | Move: %.1f%% | ATR×: %.2f",
                  s.pole_start_date, s.pole_end_date, s.pole_pct_move, s.pole_atr_multiple)
        log.info("Flag: %s -> %s | Retrace: %.1f%% | Vol Contraction: %.1f%%",
                  s.flag_start_date, s.flag_end_date, s.flag_retracement_pct,
                  s.volume_contraction_pct)
        log.info("Pivot: %.2f | Current: %.2f | vs Pivot: %.2f%%",
                  s.pivot_point, s.current_price, s.price_vs_pivot_pct)

        readiness = compute_flag_pole_readiness(s, daily, rs_trend_label)
        log.info("Readiness: %s | %s", readiness["readiness_pct"], readiness["reasons_str"])

        plan = calculate_entry_exit_flag_pole(s, daily, regime["trend"])
        log.info("Entry: %.2f | Stop: %.2f (%s) | T1/T2: %.2f/%.2f",
                  plan.entry_price, plan.stop_loss_price, plan.stop_loss_type,
                  plan.target1, plan.target2)
        log.info("R:R T1/T2: %.2f/%.2f | Position: %d shares (₹%.0f)",
                  plan.rr_t1, plan.rr_t2, plan.position_size_shares, plan.capital_required)


# ─── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NSE Flag & Pole Scanner")
    p.add_argument("--full-refresh", action="store_true",
                    help="Wipe and redownload all historical data")
    p.add_argument("--refresh-universe", action="store_true",
                    help="Force-refresh the NSE symbol list")
    p.add_argument("--debug-symbol", type=str, default=None,
                    help="Run verbose diagnosis for a single symbol")
    p.add_argument("--no-telegram", action="store_true",
                    help="Skip the Telegram notification for this run even if configured "
                         "(handy for local test runs so you don't spam your channel)")
    return p.parse_args()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        sys.exit(130)
    except Exception:
        log.error("Fatal error:\n%s", traceback.format_exc())
        sys.exit(1)
