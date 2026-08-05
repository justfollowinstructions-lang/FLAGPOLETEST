# NSE Flag & Pole Scanner

A bull Flag & Pole chart-pattern scanner for NSE equities: a short,
sharp momentum move (the pole) followed by a tight consolidation (the
flag), breaking out in the pole's direction. Detection is deliberately
loose (maximum sensitivity, gated only on pole velocity and flag
geometry); entry and exit rules are strict (capital protection).

This is a standalone package today. It's built so it can merge
cleanly with a companion pattern scanner (e.g. Cup & Handle) later —
every shared module (`universe.py`, `downloader.py`, `indicators.py`,
`market_regime.py`, `logger_utils.py`) is generic and pattern-agnostic
already, and every Flag & Pole specific name is prefixed (`FP_*`,
`FPQS_*`, `DAILY_POLE_*`, `DAILY_FLAG_*`) to avoid collisions when
that day comes. See "Combining with another scanner later" below.

## How it works

1. **Detection** (`flag_pole_detector.py`) — finds a pole (3-10 bars,
   gated on both % move and ATR-multiple so it scales across large-
   and small-caps) followed by a flag (3-15 bars, parallel channel,
   ≤50% retracement, contracting volume), then checks for a confirmed
   breakout in the following bars.
2. **Breakout Readiness** (`readiness_flag_pole.py`) — a separate
   0-100% score for signals near their pivot, answering "is this
   actionable right now?" (near pivot, flag tightness, volume dry-up,
   rising RS, pole freshness).
3. **Entry/Exit** (`entry_exit_flag_pole.py`) — strict rules: 8% max
   stop loss (or flag-low minus 1×ATR, whichever is tighter), targets
   at 1× and 1.618× the pole height (measured move), position sizing
   off portfolio risk %, and a sell-rule checklist.
4. **Market Regime** (`market_regime.py`) — real O'Neil-style
   distribution-day counting on Nifty 50 (rolling 25-session window),
   not a bare price-vs-50MA read. Classifies the market as `Uptrend` /
   `Uptrend Under Pressure` / `Correction` so every signal carries an
   honest read on whether the broader tape actually supports it.
5. **Report** (`report_flag_pole.py`) — a 6-sheet Excel workbook:
   Confirmed Breakouts, Near Breakout Watchlist, Today's Signals,
   Active Tracking, Historical Signals, and Strategy Summary.

## Local setup

```bash
pip install -r requirements.txt

python main_flag_pole.py                       # incremental scan (downloads only new bars)
python main_flag_pole.py --full-refresh        # wipe and redownload full history
python main_flag_pole.py --refresh-universe    # force-refresh the NSE symbol list
python main_flag_pole.py --debug-symbol TCS.NS # verbose single-symbol diagnosis
python main_flag_pole.py --no-telegram         # skip Telegram notification for this run

# Run the test suite
pip install pytest
python -m pytest tests/ -v
```

Reports are written to `reports/flag_pole_report_YYYY-MM-DD.xlsx`.

## Telegram notifications

After each run, the scanner sends a summary message and the Excel
report itself to a Telegram chat — configured via two environment
variables, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. If either is
unset, notifications are silently skipped (logged at INFO, not an
error) — the scan and report still run and complete normally either
way.

**One-time setup:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram -> `/newbot`
   -> follow the prompts. You'll get a token like
   `123456789:ABCdefGhIJKlmnOPQRstuVWXyz`.
2. Send any message to your new bot (or add it to a group/channel and
   post there), then open this URL in a browser:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — find
   `"chat":{"id": ...}` in the response. That number is your chat ID
   (negative for groups/channels).
3. **GitHub Actions:** repo Settings -> Secrets and variables ->
   Actions -> New repository secret. Add both `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID`. The workflow already passes them through to the
   scan step — nothing else to configure.
4. **Local runs:** `export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...`
   before running `main_flag_pole.py`, or pass `--no-telegram` to skip
   notification for a single run even if configured (useful while
   testing, so you don't spam the channel).

## Visual chart review

After each run, `chart_export.py` builds one self-contained HTML file
covering **every stock across every tab of the Excel report, with
full price history since each stock's listing** — a TradingView-style
candlestick viewer with a **1D / 1W / 1M timeframe toggle**, moving
averages/RSI/MACD, and a **📊 Analysis panel** that explains, in plain
language, exactly why the scanner flagged (or didn't fully endorse)
each stock.

- **📊 Analysis panel**: a rating verdict (Strong Buy / Good Candidate
  / Watch / Weak / Rejected — Illiquid, color-coded), a PASS/FAIL/WARN
  checklist walking through every gate the pattern did or didn't
  clear (with the actual numbers, not just a checkmark), a
  strengths/weaknesses breakdown, a "why should I buy this" verdict
  with supporting reasoning, and the sell-rules checklist — all
  computed in `explain_flag_pole.py` from the same data already in the
  Excel report, not a separate analysis.
- **Sidebar, grouped exactly like the Excel workbook**: ⭐ Top Picks
  (the best-scoring stocks from today's scan, ranked), 🔥 Confirmed
  Breakouts, ⚡ Near Breakout Watchlist, 📋 Today's Signals, 📈 Active
  Tracking, 📚 Historical Signals (capped to the most recent 60 closed
  trades — that tab only grows over time, everything else is naturally
  bounded). A stock shows up in every group it genuinely belongs to,
  same as it would across multiple Excel sheets — search box included.
- **"Why This Setup" panel**: a plain-language summary of what the
  scan actually saw — pole strength, flag tightness, volume signature,
  RS Rating, risk:reward, and a market-regime caution note when the
  detection happened outside a clean uptrend — plus the readiness
  checklist (✔/✘) and the short remark tags (e.g. "RS Leader", "Vol
  Surge"), so the reasoning is visible without decoding columns.
- **Chart**: full price history since listing on all three timeframes,
  candlesticks + volume + Volume MA(20), toggleable MA(5/10/20/30/50),
  RSI(14) and MACD(12,26,9) as synced sub-panes, a solid line tracing
  the actual pole move, a dashed channel marking the flag's high/low,
  dashed price lines for pivot/stop/target 1/target 2, and markers on
  the pole start, flag start, and breakout dates — everything shown at
  once, fit to screen, no need to scrub through a replay to see it.
  ⚙ Settings lets you recolor any line or change an MA's period.
- **Flag tightness, quantified**: the stats row and "Why This Setup"
  panel show both **Flag Range %** (the flag's own high-to-low width
  as a % of price — the direct "how narrow" number) and **Flag
  Retrace %** (how far it pulled back relative to the pole — a
  different, complementary measure), plus the duration in trading
  days. Detection runs on **daily bars only**; the 1W/1M views are the
  same daily-detected pattern zoomed out for context, not separately
  measured on those timeframes.
- **⊞ Grid**: 1D + 1W + 1M side by side, in the same window (not a
  popup) — three full-height panes, each fit to its own content.
- **☰ / ℹ toggles**: collapse the sidebar or the stats/reasons panel
  to give the chart more room; the layout reflows automatically.
- Hover any candle for a TradingView-style OHLCV readout. The symbol
  name and Prev/Next controls sit at a fixed width, so switching
  between a short ticker and a long one never shifts the buttons.

**No hosting, nothing to install.** The charting library is vendored
into the repo (`vendor/`) and gets inlined directly into the HTML file
at export time — it's a single file with zero external requests, works
completely offline, and opens in any browser by double-clicking it.

**It expires on its own.** The GitHub Actions workflow uploads it as a
separate artifact — `flag-pole-charts-<run>` — with a **2-day
retention period**, shorter than the Excel report's 90 days. Nothing
to clean up manually; GitHub deletes it automatically.

To use it: after a run, go to the Actions run page → Artifacts →
download `flag-pole-charts-<run>` → unzip → open the `.html` file.

Run it locally any time after a scan:
```bash
python chart_export.py                     # today's signals

python chart_export.py --scan-date 2026-07-04
```

## GitHub Actions

`.github/workflows/daily_scan.yml` runs the scanner automatically at
4:00 PM IST on trading days. It restores the previous run's Parquet
cache (incremental after the first run), runs the scan, exports the
chart viewer, saves the updated cache, and uploads two artifacts: the
Excel report (90-day retention) and the chart viewer (2-day
retention). Trigger manually from the Actions tab to set
`full_refresh`, `refresh_universe`, or `debug_symbol` — the chart
export step is skipped in `debug_symbol` mode since there's no full
scan to chart.

## Configuration

All thresholds live in `config.py` — portfolio size, risk per trade,
pole/flag detection windows, entry/exit rules, and Breakout Readiness
weights. Detection thresholds are commented with the rationale for
keeping them where they are; read the comments before changing them.

Two numbers worth understanding before you trust the output:

- **`breakout_volume_ratio`** (vs. the flag's own average volume) is
  the real breakout-confirmation gate used during detection.
- **`volume_ratio`** (vs. a 50-day average) is a secondary check
  reused from a more general convention — for this pattern, the
  50-day baseline gets inflated by the pole's own already-elevated
  volume, so it reads lower than you'd expect. Trust
  `breakout_volume_ratio` for Flag & Pole specifically; both are shown
  in the report for context.

## File overview

| File | Purpose |
|---|---|
| `config.py` | All tuneable constants |
| `logger_utils.py` | Shared logging setup |
| `universe.py` | NSE symbol list fetcher/cache |
| `downloader.py` | Parquet data download, incremental updates |
| `indicators.py` | RSI, ADX, ATR, MAs, RS Rating |
| `market_regime.py` | Distribution-day counting / market trend classification |
| `flag_pole_detector.py` | Core pattern detection engine |
| `readiness_flag_pole.py` | Breakout Readiness scoring |
| `entry_exit_flag_pole.py` | Entry/exit/position-sizing calculator |
| `excel_helpers.py` | Generic xlsxwriter formatting/writing primitives |
| `report_flag_pole.py` | Excel report generator |
| `database.py` | SQLite persistence for signals and tracking |
| `telegram_notify.py` | Sends the scan summary + Excel report to Telegram |
| `chart_export.py` | Builds the self-contained HTML chart viewer |
| `explain_flag_pole.py` | Rating verdicts, PASS/FAIL/WARN checklist, strengths/weaknesses, why-buy reasoning |
| `chart_viewer/template.html` | The viewer's HTML/CSS/JS shell (data gets injected at export time) |
| `vendor/lightweight-charts.standalone.production.js` | Vendored charting library, inlined into the export — no CDN dependency |
| `main_flag_pole.py` | Orchestrator / CLI entry point |
| `tests/` | pytest suite — detector rules, market regime, DB, full pipeline integration |

## Combining with another scanner later

This package was deliberately factored so a second pattern scanner
(e.g. Cup & Handle) can be dropped in alongside it with minimal
friction:

- `universe.py`, `downloader.py`, `indicators.py`, `market_regime.py`,
  `logger_utils.py` are already generic — a second scanner imports
  them as-is, no changes needed.
- `config.py` — a second scanner's constants append underneath this
  file's Flag & Pole block; nothing here needs renaming since it's
  already prefixed.
- `database.py` — a second scanner adds its own `CREATE TABLE` block
  and a parallel set of CRUD functions to this same file, alongside
  `flag_pole_signals`, rather than replacing anything.
- `excel_helpers.py` — a second scanner's report module imports these
  same four functions instead of re-implementing them.
- `check_liquidity()`/`check_volume_confirmation()` currently live
  inside `entry_exit_flag_pole.py` (there's no shared entry/exit module
  to import them from yet). If a second scanner needs the same checks,
  promote these two back out to a small shared module at that point.
- Naming: `main_flag_pole.py`, `readiness_flag_pole.py`,
  `entry_exit_flag_pole.py`, `report_flag_pole.py` keep their `_flag_pole`
  suffix even standalone, on purpose — so a second scanner's
  `main.py`/`readiness.py`/`entry_exit.py`/`report.py` can sit right
  next to these with no rename required later.
