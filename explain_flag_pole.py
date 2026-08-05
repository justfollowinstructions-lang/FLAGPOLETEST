"""
NSE Flag & Pole Scanner - Explanation Engine
=================================================
Turns the raw numeric row for a signal into a structured, readable
explanation: a one-line rating verdict, a PASS/FAIL/WARN checklist
walking through every gate the pattern did or didn't clear, and a
longer "why does this matter" narrative with strengths/weaknesses.

Adapted from a companion Cup & Handle scanner's explain.py — same
structure (overall_rating / scanner_reasons / why_buy /
full_explanation), reworked for flag/pole terminology (pole strength
and ATR-multiple instead of cup depth, flag tightness instead of
handle quality, no handle-formed/prior-uptrend-tag equivalents since
those are cup-and-handle-specific).

Pure functions only — no I/O, no config mutation. Called once per
symbol from chart_export.py.
"""

from __future__ import annotations

from typing import Optional

import config as cfg


def _f(row, key, default=None):
    val = row.get(key, default)
    if val is None:
        return default
    return val


# ─── Overall rating ─────────────────────────────────────────────────────────

def overall_rating(row: dict) -> dict:
    """
    Returns {"label": str, "color": str, "reason": str} — a single
    scannable verdict, not just a bare quality score. `color` is one
    of green/blue/yellow/orange/red/grey, mapped to hex in the viewer.
    """
    liquidity_ok = bool(_f(row, "liquidity_ok", 1))
    if not liquidity_ok:
        return {
            "label": "Rejected — Illiquid",
            "color": "grey",
            "reason": _f(row, "liquidity_warning") or "Fails the minimum price/volume liquidity floor.",
        }

    quality = _f(row, "quality_score", 0) or 0
    signal_type = _f(row, "signal_type", "") or ""
    readiness = _f(row, "breakout_readiness_pct")
    is_confirmed = "confirmed" in (row.get("tabs") or [])

    if signal_type == "BREAKOUT NOW" and is_confirmed and quality >= 70:
        return {
            "label": "Strong Buy",
            "color": "green",
            "reason": f"Confirmed breakout, quality {quality:.0f}/100, "
                      f"readiness {readiness:.0f}% — clears every high-conviction gate." if readiness is not None
                      else f"Confirmed breakout, quality {quality:.0f}/100.",
        }
    if signal_type in ("BREAKOUT NOW", "NEAR BREAKOUT") and quality >= 55:
        return {
            "label": "Good Candidate",
            "color": "blue",
            "reason": f"{signal_type.title()}, quality {quality:.0f}/100 — a solid but not maximal setup.",
        }
    if signal_type in ("NEAR BREAKOUT", "WATCHING"):
        return {
            "label": "Watch",
            "color": "yellow",
            "reason": "Still forming — pattern is valid but hasn't broken out yet.",
        }
    if quality < 40:
        return {
            "label": "Weak",
            "color": "red",
            "reason": f"Quality score {quality:.0f}/100 — below the bar for a clean setup.",
        }
    return {
        "label": "Early Stage",
        "color": "orange",
        "reason": "Pattern detected but still well below its pivot — early to act on.",
    }


def quality_band_label(score: float) -> str:
    if score >= cfg.FPQ_BAND_HIGH:
        return "High Quality"
    if score >= cfg.FPQ_BAND_MEDIUM:
        return "Medium Quality"
    return "Low Quality"


# ─── "Why did the scanner detect this?" — PASS/FAIL/WARN checklist ────────

def scanner_reasons(row: dict) -> list[dict]:
    """Each item: {"rule": str, "status": "pass"|"fail"|"warn", "detail": str}."""
    reasons = []

    def add(rule, status, detail):
        reasons.append({"rule": rule, "status": status, "detail": detail})

    pole_pct = _f(row, "pole_pct_move")
    pole_atr = _f(row, "pole_atr_multiple")
    if pole_pct is not None:
        status = "pass" if pole_pct >= 20 else "warn" if pole_pct >= cfg.POLE_MIN_PCT_MOVE else "fail"
        add(
            "Pole strength",
            status,
            f"{pole_pct:.1f}% move" + (f", {pole_atr:.1f}x its normal daily range" if pole_atr is not None else "") +
            f" — the strategy's floor is {cfg.POLE_MIN_PCT_MOVE:.0f}% and {cfg.POLE_MIN_ATR_MULTIPLE:.0f}x ATR.",
        )

    flag_retr = _f(row, "flag_retracement_pct")
    if flag_retr is not None:
        status = "pass" if flag_retr <= 38.2 else "warn" if flag_retr <= cfg.FLAG_MAX_RETRACEMENT_PCT else "fail"
        add(
            "Flag retracement",
            status,
            f"{flag_retr:.1f}% pullback from the pole's peak — the healthy zone is under "
            f"38.2%, the hard ceiling is {cfg.FLAG_MAX_RETRACEMENT_PCT:.0f}%.",
        )

    flag_range = _f(row, "flag_range_pct")
    if flag_range is not None:
        status = "pass" if flag_range <= 8 else "warn" if flag_range <= 15 else "fail"
        add(
            "Flag tightness (range)",
            status,
            f"The flag's own high-to-low width was {flag_range:.1f}% of price — tighter "
            f"consolidations are generally the higher-quality setups.",
        )

    vol_contr = _f(row, "volume_contraction_pct")
    if vol_contr is not None:
        status = "pass" if vol_contr >= 30 else "warn" if vol_contr >= cfg.FLAG_MIN_VOLUME_CONTRACTION_PCT else "fail"
        add(
            "Volume dry-up in flag",
            status,
            f"Flag-average volume ran {vol_contr:.0f}% below the pole's — sellers stepping "
            f"back is what a genuine pause looks like, not a distribution top.",
        )

    signal_type = _f(row, "signal_type", "") or ""
    breakout_ratio = _f(row, "breakout_volume_ratio")
    if signal_type == "BREAKOUT NOW":
        if breakout_ratio is not None:
            status = "pass" if breakout_ratio >= cfg.BREAKOUT_MIN_VOLUME_MULTIPLE else "warn"
            add(
                "Breakout volume",
                status,
                f"{breakout_ratio:.2f}x the flag's average volume on the breakout bar — "
                f"the strategy's floor is {cfg.BREAKOUT_MIN_VOLUME_MULTIPLE:.1f}x.",
            )
    elif signal_type in ("NEAR BREAKOUT", "WATCHING"):
        pvp = _f(row, "price_vs_pivot_pct")
        add(
            "Breakout",
            "warn",
            f"Not yet triggered — price is currently "
            f"{abs(pvp):.1f}% {'above' if (pvp or 0) >= 0 else 'below'} the pivot." if pvp is not None
            else "Not yet triggered.",
        )

    rs_rating = _f(row, "rs_rating")
    rs_trend = _f(row, "rs_trend")
    if rs_rating is not None:
        status = "pass" if rs_rating >= cfg.RS_LEADER_THRESHOLD else "warn" if rs_rating >= cfg.RS_RISING_THRESHOLD else "fail"
        add(
            "Relative Strength",
            status,
            f"RS Rating {rs_rating:.0f}/99 — measures this stock's return against the whole "
            f"NSE universe, not just the index." +
            (f" Trend has been {rs_trend.lower()} over the last 4 weeks." if rs_trend and rs_trend != "Unknown" else ""),
        )

    readiness = _f(row, "breakout_readiness_pct")
    if readiness is not None:
        readiness_checks = [
            ("near_pivot", "Near pivot"),
            ("flag_tight", "Flag tight (Fibonacci zone)"),
            ("volume_dryup", "Volume drying up"),
            ("rising_rs", "RS Rating rising"),
            ("pole_fresh", "Pole still fresh"),
        ]
        for field_key, label in readiness_checks:
            val = row.get(f"readiness_{field_key}")
            if val is None:
                continue
            add(
                f"Readiness: {label}",
                "pass" if bool(val) else "warn",
                "Confirmed." if bool(val) else "Not currently the case — pulls the readiness score down.",
            )

    market_trend = _f(row, "market_trend")
    if market_trend:
        status = "pass" if market_trend == "Uptrend" else "warn" if market_trend == "Uptrend Under Pressure" else "fail"
        add(
            "Market regime at detection",
            status,
            f"Broader market read '{market_trend}' when this was detected." +
            (" A pure momentum pattern like this is more prone to false breakouts outside a "
             "confirmed uptrend." if market_trend != "Uptrend" else ""),
        )

    liquidity_ok = bool(_f(row, "liquidity_ok", 1))
    add(
        "Liquidity",
        "pass" if liquidity_ok else "fail",
        _f(row, "liquidity_warning") or "Price and average volume both clear the minimum floor for safe entry/exit sizing.",
    )

    stop_type = _f(row, "stop_loss_type")
    if stop_type:
        if stop_type == "8pct_cap":
            add("Stop loss width", "warn",
                f"Stop is capped at the maximum allowed {cfg.MAX_STOP_PCT*100:.0f}% below entry — "
                f"the natural stop (flag low minus 1x ATR) was wider than that.")
        else:
            add("Stop loss width", "pass",
                f"Stop placed at the natural flag-low level, comfortably inside the "
                f"{cfg.MAX_STOP_PCT*100:.0f}% maximum.")

    rr_t2 = _f(row, "rr_t2")
    if rr_t2 is not None:
        add(
            "Risk:Reward at Target 2",
            "pass" if rr_t2 >= cfg.FP_MIN_RR_T2 else "warn",
            f"{rr_t2:.2f}:1 measured to the 1.618x pole-height projected target." +
            (f" Below the {cfg.FP_MIN_RR_T2:.0f}:1 minimum the strategy prefers — size "
             f"position carefully if taking this trade." if rr_t2 < cfg.FP_MIN_RR_T2 else ""),
        )

    return reasons


# ─── "Why should I buy this?" ───────────────────────────────────────────────

def why_buy(row: dict) -> dict:
    """
    Returns {"recommend": bool, "paragraphs": [str, ...]}. If the scan
    considers the setup weak, recommend=False and the text explains
    why buying is NOT currently advisable rather than forcing bullish
    language onto a weak setup.
    """
    quality = _f(row, "quality_score", 0) or 0
    signal_type = _f(row, "signal_type", "") or ""
    rs_rating = _f(row, "rs_rating")
    rs_trend = _f(row, "rs_trend")
    breakout_ratio = _f(row, "breakout_volume_ratio")
    liquidity_ok = bool(_f(row, "liquidity_ok", 1))
    rr_t2 = _f(row, "rr_t2")
    market_trend = _f(row, "market_trend")

    recommend = (
        liquidity_ok
        and signal_type in ("BREAKOUT NOW", "NEAR BREAKOUT")
        and quality >= 55
    )

    paragraphs = []

    if not liquidity_ok:
        paragraphs.append(
            _f(row, "liquidity_warning") or
            "This stock fails the liquidity floor — price or average volume is too low to "
            "size and exit a position safely. Buying is not recommended regardless of how "
            "the pattern looks."
        )
        return {"recommend": False, "paragraphs": paragraphs}

    pole_pct = _f(row, "pole_pct_move")
    if pole_pct is not None:
        if pole_pct >= 25:
            paragraphs.append(
                f"Pattern: an explosive {pole_pct:.1f}% pole move — the kind of velocity "
                f"that, when followed by a genuinely tight flag, tends to continue rather "
                f"than reverse."
            )
        else:
            paragraphs.append(
                f"Pattern: a {pole_pct:.1f}% pole move — qualifies for the pattern but isn't "
                f"an especially forceful one. Treat this as a lower-conviction version of "
                f"the setup."
            )

    if rs_rating is not None:
        if rs_rating >= cfg.RS_LEADER_THRESHOLD:
            paragraphs.append(
                f"Momentum: RS Rating of {rs_rating:.0f} marks this as a genuine "
                f"relative-strength leader against the rest of the NSE universe, not just "
                f"the index."
            )
        elif rs_rating >= cfg.RS_RISING_THRESHOLD:
            paragraphs.append(
                f"Momentum: RS Rating of {rs_rating:.0f} shows building relative strength, "
                f"without yet being a clear leader."
            )
        else:
            paragraphs.append(
                f"Momentum: RS Rating of {rs_rating:.0f} is on the weaker side — this stock "
                f"hasn't been a relative-strength leader recently, which lowers the odds of "
                f"a fast, powerful move even if the chart pattern looks clean." +
                (f" The trend is at least {rs_trend.lower()}." if rs_trend and rs_trend != "Unknown" else "")
            )

    if signal_type == "BREAKOUT NOW" and breakout_ratio is not None:
        if breakout_ratio >= 2.0:
            paragraphs.append(
                f"Volume: {breakout_ratio:.2f}x average on the breakout — real participation "
                f"behind the move, the kind of confirmation heavier buying tends to leave "
                f"behind."
            )
        else:
            paragraphs.append(
                f"Volume: {breakout_ratio:.2f}x average on the breakout — present but not "
                f"surging. Worth watching for a stronger volume day before treating this as "
                f"a fully confirmed move."
            )
    elif signal_type in ("NEAR BREAKOUT", "WATCHING"):
        paragraphs.append(
            "Timing: this hasn't broken out yet. Buying now means entering ahead of "
            "confirmation — a pullback-to-flag entry, not a breakout entry. Waiting for the "
            "actual breakout (with volume) is the lower-risk sequencing."
        )

    if rr_t2 is not None:
        if rr_t2 >= cfg.FP_MIN_RR_T2:
            paragraphs.append(f"Risk:Reward: {rr_t2:.2f}:1 to Target 2 — favourable relative to the risk taken.")
        else:
            paragraphs.append(
                f"Risk:Reward: {rr_t2:.2f}:1 to Target 2 — below the {cfg.FP_MIN_RR_T2:.0f}:1 "
                f"the strategy prefers. Consider a smaller position size if taking this trade."
            )

    if market_trend and market_trend != "Uptrend":
        paragraphs.append(
            f"Caution: the broader market read '{market_trend}' when this was detected. "
            f"Flag & Pole is a pure momentum pattern — it depends on the move continuing, "
            f"which is measurably less likely outside a confirmed uptrend. Tighter position "
            f"sizing and a closer eye on the stop are warranted here."
        )

    verdict = (
        "Overall: this clears the bar the scanner uses for a recommended entry."
        if recommend else
        "Overall: this doesn't currently clear the bar for a recommended entry — quality, "
        "signal maturity, or liquidity is holding it back. Worth watching, not necessarily "
        "buying yet."
    )
    paragraphs.append(verdict)

    return {"recommend": recommend, "paragraphs": paragraphs}


# ─── Full narrative explanation ────────────────────────────────────────────

def full_explanation(row: dict) -> dict:
    """
    Returns {"why_detected": str, "strengths": [str,...],
    "weaknesses": [str,...], "institutional_note": str, "conclusion": str}.
    """
    symbol = _f(row, "symbol", "This stock")
    quality = _f(row, "quality_score", 0) or 0
    pole_pct = _f(row, "pole_pct_move")
    flag_retr = _f(row, "flag_retracement_pct")
    rs_rating = _f(row, "rs_rating")
    signal_type = _f(row, "signal_type", "") or ""

    why_detected = (
        f"{symbol} was flagged because its price action matched the Flag & Pole "
        f"template: a short, sharp directional move (the pole) that met both the minimum "
        f"percentage-move and ATR-multiple gates, followed by a contained consolidation "
        f"(the flag) that didn't retrace too much of the pole and showed volume drying up "
        f"— the combination the strategy treats as 'a pause, not a reversal.'"
    )

    strengths, weaknesses = [], []

    if pole_pct is not None:
        (strengths if pole_pct >= 20 else weaknesses).append(
            f"Pole moved {pole_pct:.1f}%" + (" — a genuinely forceful move." if pole_pct >= 20 else
                                               " — meets the bar but isn't a standout.")
        )
    if flag_retr is not None:
        (strengths if flag_retr <= 38.2 else weaknesses).append(
            f"Flag retracement {flag_retr:.1f}%" + (" — in the healthy zone." if flag_retr <= 38.2 else
                                                       " — deeper than ideal, a looser consolidation.")
        )
    if rs_rating is not None:
        (strengths if rs_rating >= cfg.RS_RISING_THRESHOLD else weaknesses).append(
            f"RS Rating {rs_rating:.0f}" + (" — real relative strength." if rs_rating >= cfg.RS_RISING_THRESHOLD else
                                              " — below-average, a headwind for a fast move.")
        )
    readiness = _f(row, "breakout_readiness_pct")
    if readiness is not None:
        (strengths if readiness >= cfg.READINESS_BAND_HIGH else weaknesses).append(
            f"Breakout readiness {readiness:.0f}%" + (" — actionable right now." if readiness >= cfg.READINESS_BAND_HIGH else
                                                         " — not yet at the high-conviction bar.")
        )
    if not bool(_f(row, "liquidity_ok", 1)):
        weaknesses.append("Fails the liquidity floor — sizing/exiting safely is a real concern.")

    breakout_ratio = _f(row, "breakout_volume_ratio")
    if signal_type == "BREAKOUT NOW" and breakout_ratio is not None:
        institutional_note = (
            f"Breakout volume ran {breakout_ratio:.2f}x the flag's average — "
            + ("consistent with real institutional-scale participation behind the move, not "
               "a low-volume drift through the pivot." if breakout_ratio >= 2.0 else
               "present but modest; this alone wouldn't be read as strong institutional "
               "conviction without corroborating volume on subsequent days.")
        )
    else:
        institutional_note = (
            "No breakout has occurred yet, so there's no volume signature to read for "
            "institutional participation — that's the single most informative thing to "
            "watch for when (if) this breaks out."
        )

    rating = overall_rating(row)
    conclusion = (
        f"{rating['label']} ({quality:.0f}/100 quality). " + rating["reason"]
    )

    return {
        "why_detected": why_detected,
        "strengths": strengths or ["No standout strengths beyond meeting the base pattern criteria."],
        "weaknesses": weaknesses or ["No significant weaknesses identified against the strategy's own thresholds."],
        "institutional_note": institutional_note,
        "conclusion": conclusion,
    }
