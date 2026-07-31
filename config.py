"""
NSE Flag & Pole Scanner - Configuration
===========================================
All tuneable constants live here. Detection thresholds (pole velocity,
flag geometry) are commented with the rationale for keeping them
where they are — read the comments before loosening or tightening.

This config is a trimmed subset of a larger, shared config that also
powers a companion Cup & Handle scanner (same universe/data/RS-Rating/
market-regime pipeline). When the two are combined later, this file
merges cleanly with the Cup & Handle one — every name here is either
generic/shared or prefixed to avoid collisions (FP_*, FPQS_*, FPQ_*,
DAILY_POLE_*, DAILY_FLAG_*, FLAG_*, POLE_*).
"""

from __future__ import annotations

from pathlib import Path

# ─── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR      = Path("data")
DAILY_DIR     = DATA_DIR / "daily"
REPORTS_DIR   = Path("reports")
SIGNALS_DB    = DATA_DIR / "scanner.db"
LOGS_DIR      = Path("logs")

for _d in (DATA_DIR, DAILY_DIR, REPORTS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ─── Benchmarks ────────────────────────────────────────────────────────────
NIFTY50_SYMBOL   = "^NSEI"
NIFTY500_SYMBOL  = "^CNX500"

# ─── Portfolio / position sizing ──────────────────────────────────────────
PORTFOLIO_VALUE       = 500_000     # INR, configurable
RISK_PER_TRADE_PCT    = 1.0         # % of portfolio risked per trade

# ─── Data download ─────────────────────────────────────────────────────────
BATCH_SIZE                = 50
BATCH_DELAY_SECONDS       = 3
MAX_RETRIES                = 5
RATELIMIT_RETRY_WAIT_MIN   = 2      # minutes, base for exponential backoff
EXPONENTIAL_BASE           = 2.0
TIMEOUT_RETRY_WAIT_SEC     = 30

# ─── Minimum bars required to scan a symbol (RS Rating universe) ──────────
MIN_DAILY_BARS    = 150   # used by compute_universe_rs_ratings for a real RS Rating

# ─── Indicator periods ──────────────────────────────────────────────────────
RSI_PERIOD   = 14
ADX_PERIOD   = 14
ATR_PERIOD   = 14
MA_PERIODS_DAILY   = [50, 150, 200]
MA_PERIODS_WEEKLY  = [10, 30, 40]
MA_PERIODS_MONTHLY = [3, 7, 9]

# ─── Signal type thresholds (near/far from pivot) ──────────────────────────
NEAR_BREAKOUT_THRESHOLD = 0.03   # within 3% below pivot
BASING_THRESHOLD        = 0.10   # 3-10% below pivot
# >10% below pivot -> EARLY STAGE

# ─── Breakout Readiness display bands (shared visual convention) ─────────
READINESS_BAND_HIGH      = 80     # >= this -> bold green
READINESS_BAND_MEDIUM    = 50     # >= this -> yellow background

# ─── Entry buffer above pivot ───────────────────────────────────────────────
BREAKOUT_BUFFER_INR          = 0.10
BREAKOUT_BUFFER_PCT          = 0.001   # used instead of flat INR above ₹1000
BREAKOUT_BUFFER_PRICE_CUTOFF = 1000.0

# ─── Volume confirmation (used by check_volume_confirmation) ──────────────
VOLUME_CONFIRM_DAILY   = 1.40    # 140% of 50-bar avg volume
VOLUME_CONFIRM_WEEKLY  = 1.20    # 120% of 10-bar avg volume (unused daily-only, kept for parity)
VOLUME_AVG_BARS_DAILY   = 50
VOLUME_AVG_BARS_WEEKLY  = 10

RSI_WEAK_MOMENTUM_THRESHOLD = 45.0

# ─── Stop loss (STRICT) ─────────────────────────────────────────────────────
MAX_STOP_PCT          = 0.08    # 8% hard cap, never exceeded
STOP_ATR_MULTIPLIER   = 1.0

# ─── RS Rating ───────────────────────────────────────────────────────────────
RS_LEADER_THRESHOLD   = 85
RS_RISING_THRESHOLD   = 70
RS_LAGGARD_THRESHOLD  = 50
RS_TREND_LOOKBACK_WEEKS = 4

# RS weighted-return blend: (weight, lookback_bars) computed in indicators.py
RS_WEIGHTS = {
    "3m":  (0.4, 63),    # ~63 trading days = 3 months
    "6m":  (0.2, 126),
    "9m":  (0.2, 189),
    "12m": (0.2, 252),
}

# ─── Liquidity filter (applied AFTER detection, gates entry/exit only) ────
MIN_PRICE          = 10.0
MIN_AVG_VOLUME     = 50_000
LIQUIDITY_LOOKBACK_BARS = 20

# ─── Watchlist / signal expiry ─────────────────────────────────────────────
WATCHLIST_EXPIRY_DAYS = 90


# ═══════════════════════════════════════════════════════════════════════════
# ─── Flag & Pole detection & scoring                                     ───
# ═══════════════════════════════════════════════════════════════════════════

# ─── Minimum data required to scan a symbol ────────────────────────────────
# Deliberately much lower than MIN_DAILY_BARS (150) — a flag/pole only
# needs enough history for ATR(14) plus the pole+flag windows themselves,
# so newer listings are still eligible here even without a full RS
# Rating (they'll show rs_rating=50.0, neutral default, until they
# have enough history for compute_universe_rs_ratings to rate them).
MIN_DAILY_BARS_FLAG_POLE = 60

# ─── Pole detection — lookback window sizes (in bars) ──────────────────────
# Deliberately short and velocity-gated. If/when this scanner is
# combined with a Cup & Handle scanner (DAILY_CUP_MIN_BARS=30 there),
# do not widen this range to "catch more patterns" — a pole that takes
# 3+ weeks to form is not a pole, it's the early stage of a base.
DAILY_POLE_MIN_BARS = 3
DAILY_POLE_MAX_BARS = 10

POLE_MIN_PCT_MOVE     = 15.0   # minimum % move (close-to-close) to qualify
POLE_MIN_ATR_MULTIPLE = 3.0    # pole move must be >= 3x ATR(14) at pole end
POLE_MIN_UP_BAR_FRACTION = 0.50   # >=50% of bars in the pole window must close up
POLE_MAX_SINGLE_BAR_FRACTION = 0.85  # reject if one bar is >=85% of the whole move

# ─── Flag detection — lookback window sizes (in bars) ──────────────────────
DAILY_FLAG_MIN_BARS = 3
DAILY_FLAG_MAX_BARS = 15

FLAG_MAX_RETRACEMENT_PCT        = 50.0   # flag low can't retrace >50% of pole height
FLAG_PARALLEL_TOLERANCE_DEG     = 10.0   # upper/lower trendline angle difference tolerance
FLAG_MIN_VOLUME_CONTRACTION_PCT = 20.0   # flag avg vol must be >=20% below pole avg vol
FLAG_FAILURE_BREACH_PCT         = 0.02   # close >2% below lower trendline -> pattern failed

# ─── Breakout confirmation ──────────────────────────────────────────────────
BREAKOUT_MIN_VOLUME_MULTIPLE     = 1.5    # breakout volume >= 1.5x flag avg volume
BREAKOUT_CONFIRMED_LOOKBACK_DAYS = 3      # how many bars back a breakout still counts as "live"

# ─── Quality score weights (sum to 100) ────────────────────────────────────
FPQS_POLE_STRENGTH    = 30
FPQS_FLAG_TIGHTNESS   = 25
FPQS_VOLUME_SIGNATURE = 25
FPQS_RS_RATING        = 20
FPQS_SCORE_CAP        = 100

FPQ_BAND_HIGH   = 70   # >= this -> "High Quality" (green)
FPQ_BAND_MEDIUM = 40   # >= this -> "Medium Quality" (yellow)

# ─── Targets (measured move, O'Neil-style fib extension) ──────────────────
FP_TARGET1_MULTIPLE = 1.0     # T1 = breakout + 1x pole height
FP_TARGET2_MULTIPLE = 1.618   # T2 = breakout + 1.618x pole height (fib extension)
FP_MIN_RR_T2         = 2.0    # flag (not exclude) if R:R at T2 is below this

# ─── Breakout Readiness weights ─────────────────────────────────────────────
FP_READINESS_WEIGHTS = {
    "near_pivot":        25,
    "flag_tight":        20,
    "volume_drying_up":  20,
    "rising_rs":         20,
    "pole_freshness":    15,
}
FP_READINESS_NEAR_PIVOT_PCT = 3.0

# ─── High-Conviction "Confirmed Breakouts" filter ─────────────────────────
FP_HCB_MIN_READINESS    = 80
FP_HCB_MIN_QUALITY      = 60
FP_HCB_MIN_VOLUME_RATIO = BREAKOUT_MIN_VOLUME_MULTIPLE

# ─── Pattern recency (skip stale detections) ──────────────────────────────
# A flag that broke out 20+ days ago is old news, the move is likely over.
STALE_FLAG_POLE_MAX_BARS = 20


# ═══════════════════════════════════════════════════════════════════════════
# ─── Market regime                                                       ───
# ═══════════════════════════════════════════════════════════════════════════

DISTRIBUTION_DAY_PCT_THRESHOLD = 0.2   # index down >= this % counts, if...
DISTRIBUTION_DAY_WINDOW        = 25    # ...within this rolling session window
DISTRIBUTION_DAYS_PRESSURE     = 4     # >= this many -> "Uptrend Under Pressure"
DISTRIBUTION_DAYS_CORRECTION   = 6     # >= this many -> "Correction"


# ═══════════════════════════════════════════════════════════════════════════
# ─── Chart export (chart_export.py) — visual review tool, separate from ───
# ─── the scan/detect/report pipeline above. Purely additive: nothing    ───
# ─── else reads these constants.                                        ───
# ═══════════════════════════════════════════════════════════════════════════

CHARTS_DIR = Path("charts")
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

# How many bars of pre/post context to include per timeframe. Fixed
# bar counts (not date-range based) so every symbol gets a consistent
# amount of chart real estate regardless of when its pattern formed.
CHART_LOOKBACK_DAILY_BARS   = 180   # ~9 months
CHART_LOOKBACK_WEEKLY_BARS  = 130   # ~2.5 years
CHART_LOOKBACK_MONTHLY_BARS = 96    # ~8 years

# How many of the best-scoring symbols go in the sidebar's "Top Picks"
# group (ranked by quality_score across today's actionable signals).
CHART_TOP_PICKS_COUNT = 10

# Historical Signals only ever grows (closed trades accumulate forever)
# — unlike every other tab, which is naturally bounded by what's
# currently active. Cap how many of the most recent closed trades get
# charted, so the file doesn't grow without bound after months of use.
CHART_HISTORICAL_LOOKBACK_COUNT = 60
