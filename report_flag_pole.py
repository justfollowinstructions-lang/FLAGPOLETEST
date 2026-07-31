"""
NSE Flag & Pole Scanner - Excel Report Generator
====================================================
Separate workbook from Cup & Handle's (NSE_Flag_Pole_Scan_*.xlsx), but
built on the SAME formatting primitives (report.build_formats /
write_sheet / round_floats / xl_col) so the two reports look and
behave consistently without duplicating the Excel plumbing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

import config as cfg
from logger_utils import get_logger
from excel_helpers import build_formats, round_floats, write_sheet, xl_col

log = get_logger("scanner")


# ─── Column layouts ─────────────────────────────────────────────────────────

TODAYS_SIGNALS_COLUMNS = [
    ("symbol", "Symbol", 14, None),
    ("company_name", "Company Name", 24, None),
    ("sector", "Sector", 16, None),
    ("signal_type", "Signal Type", 14, None),
    ("quality_score", "Quality Score", 12, "num1"),

    ("pole_start_date", "Pole Start", 12, "date"),
    ("pole_end_date", "Pole End", 12, "date"),
    ("pole_pct_move", "Pole Move %", 11, "num1"),
    ("pole_atr_multiple", "Pole ATR×", 10, "num2"),
    ("pole_duration_bars", "Pole Bars", 10, "int"),

    ("flag_start_date", "Flag Start", 12, "date"),
    ("flag_end_date", "Flag End", 12, "date"),
    ("flag_retracement_pct", "Flag Retrace %", 13, "num1"),
    ("flag_duration_bars", "Flag Bars", 10, "int"),
    ("volume_contraction_pct", "Vol Contraction %", 15, "num1"),

    ("pivot_point", "Pivot Point", 11, "money"),
    ("current_price", "Current Price", 12, "money"),
    ("price_vs_pivot_pct", "vs Pivot %", 11, "num1"),

    ("breakout_date", "Breakout Date", 12, "date"),
    ("breakout_volume_ratio", "Breakout Vol×", 12, "num2"),

    ("breakout_readiness_pct", "Readiness %", 12, "num0"),
    ("readiness_reasons", "Readiness Reasons", 40, None),

    ("entry_price", "Entry Price", 11, "money"),
    ("entry_type", "Entry Type", 16, None),
    ("volume_ratio", "Volume Ratio", 11, "num2"),
    ("volume_confirmed_label", "Volume Confirmed", 14, None),

    ("stop_loss_price", "Stop Loss", 11, "money"),
    ("stop_loss_pct", "Stop %", 9, "num1"),
    ("stop_loss_type", "Stop Type", 20, None),
    ("atr_14", "ATR (14)", 9, "money"),
    ("risk_per_share", "Risk/Share", 10, "money"),
    ("position_size_shares", "Position (shares)", 14, "int"),
    ("capital_required", "Capital Required (INR)", 16, "money0"),
    ("risk_amount", "Risk Amount (INR)", 14, "money0"),
    ("portfolio_risk_pct", "Portfolio Risk %", 13, "pct2"),

    ("target1", "Target 1 (Measured Move)", 16, "money"),
    ("target2", "Target 2 (Fib 1.618×)", 15, "money"),
    ("rr_t1", "R:R at T1", 10, "num2"),
    ("rr_t2", "R:R at T2", 10, "num2"),

    ("rs_rating", "RS Rating", 10, "num0"),
    ("rs_trend", "RS Trend", 11, None),
    ("rs_tag", "RS Tag", 12, None),
    ("rsi_val", "RSI (14)", 9, "num1"),

    ("market_trend", "Market Trend", 14, None),
    ("market_note", "Market Note", 26, None),
    ("weak_momentum_note", "Momentum Note", 16, None),
    ("liquidity_warning", "Liquidity Warning", 26, None),

    ("sell_notes", "Sell Notes (Checklist)", 50, None),
    ("remarks", "Remarks", 30, None),
]

WATCHLIST_COLUMNS = [
    ("symbol", "Symbol", 14, None),
    ("quality_score", "Quality Score", 12, "num1"),
    ("breakout_readiness_pct", "Readiness %", 12, "num0"),
    ("readiness_reasons", "Readiness Reasons", 40, None),
    ("pole_pct_move", "Pole Move %", 11, "num1"),
    ("flag_retracement_pct", "Flag Retrace %", 13, "num1"),
    ("pivot_point", "Pivot Point", 11, "money"),
    ("current_price", "Current Price", 12, "money"),
    ("price_vs_pivot_pct", "vs Pivot %", 11, "num1"),
    ("entry_price", "Entry Price", 11, "money"),
    ("stop_loss_price", "Stop Loss", 11, "money"),
    ("target1", "T1", 10, "money"),
    ("target2", "T2", 10, "money"),
    ("rr_t2", "R:R T2", 9, "num2"),
    ("rs_rating", "RS Rating", 10, "num0"),
    ("remarks", "Remarks", 30, None),
]

CONFIRMED_BREAKOUT_COLUMNS = [
    ("symbol", "Symbol", 14, None),
    ("company_name", "Company", 22, None),
    ("sector", "Sector", 14, None),
    ("signal_type", "Signal", 14, None),

    ("breakout_readiness_pct", "Readiness %", 12, "num0"),
    ("readiness_reasons", "Readiness Factors", 40, None),
    ("quality_score", "Quality Score", 12, "num1"),

    ("pole_pct_move", "Pole Move %", 11, "num1"),
    ("pole_atr_multiple", "Pole ATR×", 10, "num2"),
    ("flag_retracement_pct", "Flag Retrace %", 13, "num1"),
    ("volume_contraction_pct", "Vol Contraction %", 15, "num1"),

    ("pivot_point", "Pivot", 10, "money"),
    ("current_price", "Current Price", 12, "money"),
    ("price_vs_pivot_pct", "vs Pivot %", 10, "num1"),
    ("breakout_date", "Breakout Date", 12, "date"),
    ("breakout_volume_ratio", "Breakout Vol×", 12, "num2"),
    ("volume_confirmed_label", "Vol Confirmed", 13, None),

    ("entry_price", "Entry Price", 11, "money"),
    ("stop_loss_type", "⚠ Stop Type", 20, None),
    ("stop_loss_price", "Stop Loss", 10, "money"),
    ("stop_loss_pct", "Stop %", 9, "num1"),
    ("atr_14", "ATR", 9, "money"),
    ("risk_per_share", "Risk/Share ₹", 12, "money"),

    ("position_size_shares", "Shares", 9, "int"),
    ("capital_required", "Capital ₹", 12, "money0"),
    ("risk_amount", "Risk ₹", 10, "money0"),

    ("target1", "T1 (Measured Move)", 14, "money"),
    ("target2", "T2 (Fib 1.618×)", 13, "money"),
    ("rr_t1", "R:R T1", 9, "num2"),
    ("rr_t2", "R:R T2", 9, "num2"),

    ("rs_rating", "RS Rating", 10, "num0"),
    ("rs_trend", "RS Trend", 11, None),
    ("rs_tag", "RS Tag", 12, None),
    ("rsi_val", "RSI", 8, "num1"),

    ("market_trend", "Market Trend", 14, None),
    ("market_note", "Market Note", 22, None),

    ("sell_notes", "Sell Rules", 50, None),
    ("remarks", "Remarks", 30, None),
]

ACTIVE_TRACKING_COLUMNS = [
    ("symbol", "Symbol", 14, None),
    ("entry_date", "Entry Date", 12, "date"),
    ("entry_price", "Entry Price", 11, "money"),
    ("current_price", "Current Price", 12, "money"),
    ("stop_loss_price", "Stop Loss (Original)", 16, "money"),
    ("target1", "T1", 10, "money"),
    ("target2", "T2", 10, "money"),
    ("t1_achieved", "T1 Hit", 9, "bool"),
    ("t2_achieved", "T2 Hit", 9, "bool"),
    ("status", "Status", 16, None),
    ("sell_notes", "Sell Notes", 50, None),
]

HISTORICAL_COLUMNS = [
    ("symbol", "Symbol", 14, None),
    ("scan_date", "Detection Date", 13, "date"),
    ("entry_date", "Entry Date", 12, "date"),
    ("entry_price", "Entry Price", 11, "money"),
    ("exit_date", "Exit Date", 12, "date"),
    ("exit_price", "Exit Price", 11, "money"),
    ("exit_type", "Exit Type", 14, None),
    ("realised_rr", "R:R Realised", 12, "num2"),
    ("hold_days", "Hold Days", 10, "int"),
    ("quality_score", "Quality Score", 12, "num1"),
    ("rs_rating", "RS at Entry", 10, "num0"),
    ("status", "Status", 16, None),
]


# ─── Report generator ───────────────────────────────────────────────────────

def generate_flag_pole_excel_report(
    confirmed_breakouts: pd.DataFrame,
    todays_signals: pd.DataFrame,
    watchlist: pd.DataFrame,
    active_tracking: pd.DataFrame,
    historical: pd.DataFrame,
    summary_stats: dict,
    output_path: Path,
) -> Path:
    """Build the 6-sheet Flag & Pole workbook and save to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    confirmed_breakouts = round_floats(confirmed_breakouts)
    todays_signals      = round_floats(todays_signals)
    watchlist           = round_floats(watchlist)
    active_tracking     = round_floats(active_tracking)
    historical           = round_floats(historical)

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        workbook = writer.book
        fmts = build_formats(workbook)

        write_sheet(
            writer, workbook, fmts, confirmed_breakouts,
            CONFIRMED_BREAKOUT_COLUMNS,
            "🔥 Confirmed Breakouts",
            sort_cols=["breakout_readiness_pct", "quality_score"],
            sort_asc=[False, False],
            extra_conditional=_apply_confirmed_breakout_formats,
            tab_color="#FF0000",
        )

        write_sheet(
            writer, workbook, fmts, watchlist, WATCHLIST_COLUMNS,
            "⚡ Near Breakout Watchlist",
            sort_cols=["breakout_readiness_pct"], sort_asc=[False],
            extra_conditional=_apply_watchlist_formats,
            tab_color="#FF8C00",
        )

        write_sheet(
            writer, workbook, fmts, todays_signals, TODAYS_SIGNALS_COLUMNS,
            "📋 Today's Signals",
            sort_cols=["quality_score"], sort_asc=[False],
            extra_conditional=_apply_confirmed_breakout_formats,
            tab_color="#375623",
        )

        write_sheet(
            writer, workbook, fmts, active_tracking, ACTIVE_TRACKING_COLUMNS,
            "📈 Active Tracking",
            sort_cols=["entry_date"], sort_asc=[False],
            tab_color="#7030A0",
        )

        write_sheet(
            writer, workbook, fmts, historical, HISTORICAL_COLUMNS,
            "📚 Historical Signals",
            sort_cols=["scan_date"], sort_asc=[False],
            extra_summary=_historical_summary_block(historical),
            tab_color="#7F7F7F",
        )

        _write_strategy_summary(writer, workbook, fmts, summary_stats)

    log.info("Flag & Pole Excel report written: %s", output_path)
    return output_path


# ─── Conditional formatting ─────────────────────────────────────────────────

def _apply_confirmed_breakout_formats(ws, fmts, cols_present, n_rows) -> None:
    if n_rows == 0:
        return
    col_index = {c[0]: i for i, c in enumerate(cols_present)}

    def rng(name):
        if name not in col_index:
            return None
        return f"{xl_col(col_index[name])}2:{xl_col(col_index[name])}{n_rows + 1}"

    if all(k in col_index for k in ("signal_type", "symbol")):
        sig_idx = col_index["signal_type"]
        ws.conditional_format(rng("symbol"), {
            "type": "formula",
            "criteria": f'=${xl_col(sig_idx)}2="BREAKOUT NOW"',
            "format": fmts["orange_bold"],
        })
        ws.conditional_format(rng("symbol"), {
            "type": "formula",
            "criteria": f'=${xl_col(sig_idx)}2="NEAR BREAKOUT"',
            "format": fmts["blue_bold"],
        })

    if r := rng("breakout_readiness_pct"):
        ws.conditional_format(r, {
            "type": "3_color_scale",
            "min_value": cfg.FP_HCB_MIN_READINESS, "mid_value": 90, "max_value": 100,
            "min_color": "#FFEB9C", "mid_color": "#92D050", "max_color": "#006100",
            "min_type": "num", "mid_type": "num", "max_type": "num",
        })

    if r := rng("quality_score"):
        ws.conditional_format(r, {
            "type": "cell", "criteria": ">=", "value": 80, "format": fmts["green_bg"],
        })
        ws.conditional_format(r, {
            "type": "cell", "criteria": "between",
            "minimum": 60, "maximum": 79.99, "format": fmts["yellow_bg"],
        })

    if r := rng("volume_confirmed_label"):
        ws.conditional_format(r, {
            "type": "cell", "criteria": "==", "value": '"Yes"', "format": fmts["green_bg"],
        })
        ws.conditional_format(r, {
            "type": "cell", "criteria": "==", "value": '"No"', "format": fmts["orange_bold"],
        })

    # Stop capped -> red row, same visual convention as Cup & Handle
    if "stop_loss_type" in col_index:
        stop_col_letter = xl_col(col_index["stop_loss_type"])
        full_row_range = f"A2:{xl_col(len(cols_present) - 1)}{n_rows + 1}"
        ws.conditional_format(full_row_range, {
            "type": "formula",
            "criteria": f'=${stop_col_letter}2="8pct_cap"',
            "format": fmts["stop_capped_row"],
        })

    if r := rng("rr_t2"):
        ws.conditional_format(r, {
            "type": "cell", "criteria": "<", "value": cfg.FP_MIN_RR_T2,
            "format": fmts["orange_bold"],
        })

    if r := rng("market_trend"):
        ws.conditional_format(r, {
            "type": "cell", "criteria": "==", "value": '"Correction"',
            "format": fmts["red_text"],
        })


def _apply_watchlist_formats(ws, fmts, cols_present, n_rows) -> None:
    if n_rows == 0:
        return
    col_index = {c[0]: i for i, c in enumerate(cols_present)}
    if "breakout_readiness_pct" in col_index:
        idx = col_index["breakout_readiness_pct"]
        r = f"{xl_col(idx)}2:{xl_col(idx)}{n_rows + 1}"
        ws.conditional_format(r, {
            "type": "cell", "criteria": ">=", "value": cfg.READINESS_BAND_HIGH,
            "format": fmts["green_bold"],
        })
        ws.conditional_format(r, {
            "type": "cell", "criteria": "between",
            "minimum": cfg.READINESS_BAND_MEDIUM, "maximum": cfg.READINESS_BAND_HIGH - 0.01,
            "format": fmts["yellow_bg"],
        })


# ─── Historical summary block ──────────────────────────────────────────────

def _historical_summary_block(historical: pd.DataFrame) -> list[str]:
    if historical is None or historical.empty:
        return ["No historical Flag & Pole signals yet."]

    total = len(historical)
    wins = historical[historical["status"].isin(["Target 1 Achieved", "Target 2 Achieved"])]
    win_rate = (len(wins) / total * 100) if total else 0.0

    realised = historical["realised_rr"].dropna()
    avg_rr = realised.mean() if not realised.empty else 0.0

    gains = historical.loc[historical["realised_rr"] > 0, "realised_rr"].sum()
    losses = abs(historical.loc[historical["realised_rr"] < 0, "realised_rr"].sum())
    profit_factor = (gains / losses) if losses > 0 else float("inf")

    lines = [
        f"Total trades triggered: {total}",
        f"Win rate (hit T1+): {win_rate:.1f}%",
        f"Average R:R realised: {avg_rr:.2f}",
        f"Profit factor: {profit_factor:.2f}" if profit_factor != float("inf") else "Profit factor: N/A (no losses yet)",
    ]
    return lines


# ─── Strategy Summary sheet ─────────────────────────────────────────────────

def _write_strategy_summary(writer, workbook, fmts, stats: dict) -> None:
    ws = workbook.add_worksheet("Strategy Summary")
    writer.sheets["Strategy Summary"] = ws

    ws.set_column(0, 0, 36)
    ws.set_column(1, 1, 50)

    row = 0
    ws.write(row, 0, "NSE Flag & Pole Scanner — Strategy Summary", fmts["title"])
    row += 2

    ws.write(row, 0, "Scan Date", fmts["subtitle"])
    ws.write(row, 1, stats.get("scan_date", date.today().isoformat()))
    row += 1
    ws.write(row, 0, "Total Symbols Scanned")
    ws.write(row, 1, stats.get("total_symbols", 0))
    row += 1
    ws.write(row, 0, "Total Patterns Detected")
    ws.write(row, 1, stats.get("total_patterns", 0))
    row += 1
    ws.write(row, 0, "  ↳ 🔥 Confirmed Breakouts (act today)", fmts["subtitle"])
    ws.write(row, 1, stats.get("n_confirmed", 0))
    row += 1
    ws.write(row, 0, "  ↳ Actionable (Today's Signals sheet)")
    ws.write(row, 1, stats.get("n_actionable", 0))
    row += 2

    ws.write(row, 0, "Breakdown by Signal Type", fmts["subtitle"])
    row += 1
    for st, count in stats.get("by_signal_type", {}).items():
        ws.write(row, 0, f"  {st}")
        ws.write(row, 1, count)
        row += 1
    row += 1

    ws.write(row, 0, "Breakdown by RS Rating Band", fmts["subtitle"])
    row += 1
    for band, count in stats.get("by_rs_band", {}).items():
        ws.write(row, 0, f"  {band}")
        ws.write(row, 1, count)
        row += 1
    row += 1

    ws.write(row, 0, "Market Condition (shared with Cup & Handle)", fmts["subtitle"])
    row += 1
    ws.write(row, 0, "  Market Trend")
    ws.write(row, 1, stats.get("market_trend", "Unknown"))
    row += 1
    ws.write(row, 0, "  Distribution Days (25-session window)")
    ws.write(row, 1, stats.get("distribution_days", 0))
    row += 2

    ws.write(row, 0, "Configuration Used", fmts["subtitle"])
    row += 1
    config_lines = [
        ("Portfolio Value", f"₹{cfg.PORTFOLIO_VALUE:,.0f}"),
        ("Risk per Trade", f"{cfg.RISK_PER_TRADE_PCT}%"),
        ("Pole Window (bars)", f"{cfg.DAILY_POLE_MIN_BARS}-{cfg.DAILY_POLE_MAX_BARS}"),
        ("Flag Window (bars)", f"{cfg.DAILY_FLAG_MIN_BARS}-{cfg.DAILY_FLAG_MAX_BARS}"),
        ("Pole Min Move", f"{cfg.POLE_MIN_PCT_MOVE}% / {cfg.POLE_MIN_ATR_MULTIPLE}x ATR"),
        ("Max Flag Retracement", f"{cfg.FLAG_MAX_RETRACEMENT_PCT:.0f}%"),
        ("Max Stop Loss", f"{cfg.MAX_STOP_PCT*100:.0f}%"),
        ("Breakout Volume Confirm", f"{cfg.BREAKOUT_MIN_VOLUME_MULTIPLE}x flag avg"),
        ("T1 / T2 Multiple", f"{cfg.FP_TARGET1_MULTIPLE}x / {cfg.FP_TARGET2_MULTIPLE}x pole height"),
        ("Min R:R at T2 (flag threshold)", f"{cfg.FP_MIN_RR_T2}:1"),
    ]
    for label, val in config_lines:
        ws.write(row, 0, f"  {label}")
        ws.write(row, 1, val)
        row += 1
