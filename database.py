"""
NSE Flag & Pole Scanner - Database Layer
============================================
SQLite-backed persistence for flag_pole_signals: every signal ever
detected, full geometry + entry/exit + readiness fields. Active
tracking is derived from flag_pole_signals.status, not a separate
table — simpler schema, fewer places for state to drift.

Uses parameterised queries throughout; never f-strings into SQL values.

This is a trimmed, Flag-&-Pole-only version of a larger shared
database layer that also persists a companion pattern scanner's
signals in a separate, parallel table. If/when the two are combined,
this file's DDL and functions can be appended to that one unchanged —
nothing here references anything Flag & Pole specific in its plumbing
(_conn, init_db, make_signal_id are fully generic).
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

import pandas as pd

import config as cfg
from config import SIGNALS_DB
from logger_utils import get_logger

log = get_logger("scanner")

DB_PATH = SIGNALS_DB

DDL = """
CREATE TABLE IF NOT EXISTS flag_pole_signals (
    signal_id               TEXT PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    company_name             TEXT,
    sector                   TEXT,
    scan_date                TEXT NOT NULL,
    timeframe                TEXT NOT NULL,
    pattern_type              TEXT,
    signal_type               TEXT,
    quality_score              REAL,

    pole_start_date             TEXT,
    pole_end_date                TEXT,
    pole_start_price               REAL,
    pole_end_price                  REAL,
    pole_pct_move                    REAL,
    pole_atr_multiple                 REAL,
    pole_duration_bars                 INTEGER,

    flag_start_date                     TEXT,
    flag_end_date                        TEXT,
    flag_high                             REAL,
    flag_low                               REAL,
    flag_retracement_pct                    REAL,
    flag_duration_bars                       INTEGER,
    upper_trendline_slope                     REAL,
    lower_trendline_slope                      REAL,
    volume_contraction_pct                      REAL,

    pivot_point                                  REAL,
    current_price                                 REAL,
    price_vs_pivot_pct                             REAL,

    breakout_date                                   TEXT,
    breakout_volume_ratio                            REAL,

    breakout_readiness_pct                            REAL,
    readiness_near_pivot                               INTEGER DEFAULT 0,
    readiness_flag_tight                                INTEGER DEFAULT 0,
    readiness_volume_dryup                               INTEGER DEFAULT 0,
    readiness_rising_rs                                   INTEGER DEFAULT 0,
    readiness_pole_fresh                                   INTEGER DEFAULT 0,
    readiness_reasons                                       TEXT,

    entry_price                                              REAL,
    entry_type                                                TEXT,

    stop_loss_price                                            REAL,
    stop_loss_pct                                               REAL,
    stop_loss_type                                               TEXT,
    atr_14                                                        REAL,
    risk_per_share                                                 REAL,

    target1                                                         REAL,
    target2                                                          REAL,
    rr_t1                                                             REAL,
    rr_t2                                                              REAL,
    rr_t2_warning                                                       TEXT,

    position_size_shares                                                 INTEGER,
    capital_required                                                      REAL,
    risk_amount                                                            REAL,
    portfolio_risk_pct                                                      REAL,

    volume_ratio                                                             REAL,
    volume_confirmed                                                          INTEGER DEFAULT 0,
    volume_confirmed_label                                                     TEXT,

    rs_rating                                                                   REAL,
    rs_trend                                                                     TEXT,
    rs_tag                                                                       TEXT,
    rsi_val                                                                      REAL,

    market_trend                                                                  TEXT,
    market_note                                                                    TEXT,
    weak_momentum_note                                                              TEXT,

    liquidity_ok                                                                    INTEGER DEFAULT 1,
    liquidity_warning                                                                TEXT,

    sell_notes                                                                        TEXT,
    remarks                                                                            TEXT,

    status                                                                             TEXT DEFAULT 'Watching',
    entry_triggered                                                                     INTEGER DEFAULT 0,
    entry_date                                                                           TEXT,
    t1_achieved                                                                           INTEGER DEFAULT 0,
    t2_achieved                                                                            INTEGER DEFAULT 0,
    stopped_out                                                                             INTEGER DEFAULT 0,
    exit_date                                                                                TEXT,
    exit_price                                                                                REAL,
    exit_type                                                                                  TEXT,
    realised_rr                                                                                 REAL,
    hold_days                                                                                    INTEGER,

    expiry_date                                                                                   TEXT,
    created_at                                                                                     TEXT DEFAULT (datetime('now')),
    last_checked                                                                                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_fp_status ON flag_pole_signals(status);
CREATE INDEX IF NOT EXISTS idx_fp_symbol ON flag_pole_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_fp_scan_date ON flag_pole_signals(scan_date);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(DDL)
    log.info("Database initialised: %s", DB_PATH)


# ─── Signal ID generation ──────────────────────────────────────────────────

def make_signal_id(symbol: str, timeframe: str, pattern_start_date, pivot_point: float) -> str:
    """
    Deterministic ID so the same underlying pattern doesn't get
    duplicated across daily runs. Pivot is rounded to 2dp since small
    floating point drift across runs shouldn't create a new ID.
    """
    raw = f"{symbol}|{timeframe}|{pattern_start_date}|{round(pivot_point, 2)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ═══════════════════════════════════════════════════════════════════════════
# ─── Flag & Pole CRUD                                                    ───
# ═══════════════════════════════════════════════════════════════════════════

def upsert_flag_pole_signal(row: dict) -> None:
    """Insert a new signal row. Existing signal_ids are left untouched
    (INSERT OR IGNORE) — use update_flag_pole_signal_status for live
    tracking updates on already-persisted signals."""
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    sql = f"INSERT OR IGNORE INTO flag_pole_signals ({cols}) VALUES ({placeholders})"
    with _conn() as con:
        con.execute(sql, row)


def flag_pole_signal_exists(signal_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM flag_pole_signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
    return row is not None


def update_flag_pole_signal_status(signal_id: str, **kwargs) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = :{k}" for k in kwargs)
    kwargs["signal_id"] = signal_id
    kwargs["last_checked"] = datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            f"UPDATE flag_pole_signals SET {sets}, last_checked = :last_checked "
            f"WHERE signal_id = :signal_id",
            kwargs,
        )


def get_open_flag_pole_signals() -> list[dict]:
    """Signals that have triggered but not yet hit T2 or stopped out."""
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM flag_pole_signals
               WHERE entry_triggered = 1
                 AND stopped_out = 0
                 AND t2_achieved = 0
                 AND status NOT IN ('Stopped Out', 'Target 2 Achieved', 'Expired')
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_watching_flag_pole_signals() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM flag_pole_signals
               WHERE entry_triggered = 0
                 AND status NOT IN ('Expired', 'Stopped Out')
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_todays_flag_pole_signals_df(scan_date: Optional[str] = None) -> pd.DataFrame:
    scan_date = scan_date or date.today().isoformat()
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE scan_date = ?
                 AND signal_type != 'EARLY STAGE'
               ORDER BY quality_score DESC""",
            con, params=(scan_date,),
        )


def get_todays_early_watch_flag_pole_df(scan_date: Optional[str] = None) -> pd.DataFrame:
    scan_date = scan_date or date.today().isoformat()
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE scan_date = ?
                 AND signal_type = 'EARLY STAGE'
               ORDER BY quality_score DESC""",
            con, params=(scan_date,),
        )


def get_all_flag_pole_signals_df() -> pd.DataFrame:
    with _conn() as con:
        return pd.read_sql(
            "SELECT * FROM flag_pole_signals ORDER BY scan_date DESC", con
        )


def get_active_flag_pole_tracking_df() -> pd.DataFrame:
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE entry_triggered = 1
                 AND stopped_out = 0
                 AND t2_achieved = 0
                 AND status NOT IN ('Stopped Out', 'Target 2 Achieved', 'Expired')
               ORDER BY scan_date DESC
            """, con,
        )


def get_historical_flag_pole_signals_df() -> pd.DataFrame:
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE status IN ('Stopped Out', 'Target 1 Achieved',
                                 'Target 2 Achieved', 'Expired')
               ORDER BY scan_date DESC
            """, con,
        )


def get_near_breakout_flag_pole_watchlist_df() -> pd.DataFrame:
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE signal_type IN ('NEAR BREAKOUT', 'WATCHING')
                 AND entry_triggered = 0
               ORDER BY breakout_readiness_pct DESC,
                        quality_score DESC
            """, con,
        )


def get_confirmed_flag_pole_breakouts_df(scan_date: Optional[str] = None) -> pd.DataFrame:
    """
    High-conviction Flag & Pole breakouts — a short, genuinely
    actionable list, not an inflated one. All conditions must hold
    simultaneously; do not loosen these to pad the list.
    """
    scan_date = scan_date or date.today().isoformat()
    with _conn() as con:
        return pd.read_sql(
            """SELECT * FROM flag_pole_signals
               WHERE scan_date = ?
                 AND signal_type = 'BREAKOUT NOW'
                 AND breakout_readiness_pct >= ?
                 AND quality_score >= ?
                 AND volume_ratio >= ?
               ORDER BY breakout_readiness_pct DESC, quality_score DESC""",
            con,
            params=(
                scan_date,
                cfg.FP_HCB_MIN_READINESS,
                cfg.FP_HCB_MIN_QUALITY,
                cfg.FP_HCB_MIN_VOLUME_RATIO,
            ),
        )


def cleanup_bad_flag_pole_triggers(valid_scan_date: str) -> int:
    with _conn() as con:
        cur = con.execute(
            """UPDATE flag_pole_signals
               SET entry_triggered = 0,
                   entry_date      = NULL,
                   status          = 'Watching'
               WHERE entry_triggered = 1
                 AND t1_achieved = 0
                 AND t2_achieved = 0
                 AND stopped_out = 0
                 AND signal_type != 'BREAKOUT NOW'
                 AND (entry_date = ? OR entry_date IS NULL)
            """,
            (valid_scan_date,),
        )
        return cur.rowcount


def prune_expired_flag_pole_watchlist(today: Optional[date] = None) -> int:
    today = today or date.today()
    with _conn() as con:
        cur = con.execute(
            """UPDATE flag_pole_signals SET status = 'Expired'
               WHERE entry_triggered = 0
                 AND expiry_date IS NOT NULL
                 AND expiry_date < ?
                 AND status NOT IN ('Expired', 'Stopped Out')
            """,
            (today.isoformat(),),
        )
        return cur.rowcount
