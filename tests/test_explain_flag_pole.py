"""
Tests for explain_flag_pole.py — the rating/reasons/why-buy/full
explanation engine. All pure functions operating on plain dicts, so
no DB/fixture setup needed, just representative row dicts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import explain_flag_pole as ex


def _base_row(**overrides):
    row = {
        "symbol": "AAA.NS",
        "signal_type": "BREAKOUT NOW",
        "quality_score": 85.0,
        "breakout_readiness_pct": 80.0,
        "pole_pct_move": 25.0,
        "pole_atr_multiple": 5.0,
        "flag_retracement_pct": 30.0,
        "flag_range_pct": 6.0,
        "volume_contraction_pct": 40.0,
        "breakout_volume_ratio": 2.2,
        "rs_rating": 88.0,
        "rs_trend": "Improving",
        "market_trend": "Uptrend",
        "liquidity_ok": 1,
        "liquidity_warning": None,
        "stop_loss_type": "flag_low_minus_1atr",
        "rr_t2": 4.5,
        "price_vs_pivot_pct": 1.2,
        "readiness_near_pivot": 1,
        "readiness_flag_tight": 1,
        "readiness_volume_dryup": 0,
        "readiness_rising_rs": 1,
        "readiness_pole_fresh": 1,
        "tabs": ["confirmed", "todays", "top_picks"],
    }
    row.update(overrides)
    return row


# ─── overall_rating ─────────────────────────────────────────────────────────

def test_strong_buy_for_confirmed_high_quality_breakout():
    rating = ex.overall_rating(_base_row())
    assert rating["label"] == "Strong Buy"
    assert rating["color"] == "green"


def test_illiquid_is_rejected_regardless_of_other_factors():
    rating = ex.overall_rating(_base_row(liquidity_ok=0, liquidity_warning="Avg volume too low"))
    assert rating["label"] == "Rejected — Illiquid"
    assert rating["color"] == "grey"
    assert "too low" in rating["reason"]


def test_weak_quality_scores_below_bar():
    rating = ex.overall_rating(_base_row(quality_score=25.0, signal_type="EARLY STAGE", tabs=[]))
    assert rating["label"] == "Weak"
    assert rating["color"] == "red"


def test_watching_signal_gets_watch_label():
    rating = ex.overall_rating(_base_row(signal_type="WATCHING", quality_score=60.0, tabs=["watchlist"]))
    assert rating["label"] == "Watch"


def test_good_candidate_when_not_yet_confirmed():
    rating = ex.overall_rating(_base_row(tabs=["todays"], quality_score=60.0))  # not in "confirmed" tab
    assert rating["label"] == "Good Candidate"


# ─── scanner_reasons ────────────────────────────────────────────────────────

def test_scanner_reasons_returns_structured_rows():
    reasons = ex.scanner_reasons(_base_row())
    assert len(reasons) > 5
    for r in reasons:
        assert set(r.keys()) == {"rule", "status", "detail"}
        assert r["status"] in ("pass", "fail", "warn")


def test_weak_pole_move_flagged_as_fail_or_warn():
    reasons = ex.scanner_reasons(_base_row(pole_pct_move=15.5))   # just above the 15% floor
    pole_reason = next(r for r in reasons if r["rule"] == "Pole strength")
    assert pole_reason["status"] in ("warn", "fail")


def test_strong_pole_move_passes():
    reasons = ex.scanner_reasons(_base_row(pole_pct_move=30.0))
    pole_reason = next(r for r in reasons if r["rule"] == "Pole strength")
    assert pole_reason["status"] == "pass"


def test_illiquid_flagged_as_fail_in_reasons():
    reasons = ex.scanner_reasons(_base_row(liquidity_ok=0, liquidity_warning="Too illiquid"))
    liq_reason = next(r for r in reasons if r["rule"] == "Liquidity")
    assert liq_reason["status"] == "fail"
    assert "Too illiquid" in liq_reason["detail"]


def test_capped_stop_flagged_as_warn():
    reasons = ex.scanner_reasons(_base_row(stop_loss_type="8pct_cap"))
    stop_reason = next(r for r in reasons if r["rule"] == "Stop loss width")
    assert stop_reason["status"] == "warn"


def test_non_uptrend_market_flagged():
    reasons = ex.scanner_reasons(_base_row(market_trend="Correction"))
    market_reason = next(r for r in reasons if r["rule"] == "Market regime at detection")
    assert market_reason["status"] == "fail"


def test_readiness_subfactors_included_when_present():
    reasons = ex.scanner_reasons(_base_row())
    rule_names = [r["rule"] for r in reasons]
    assert any("Near pivot" in r for r in rule_names)
    assert any("Volume drying up" in r for r in rule_names)
    # readiness_volume_dryup=0 in the base row -> should show as warn, not pass
    vol_dryup = next(r for r in reasons if "Volume drying up" in r["rule"])
    assert vol_dryup["status"] == "warn"


def test_missing_optional_fields_dont_crash():
    minimal_row = {"symbol": "X.NS", "signal_type": "WATCHING", "quality_score": 50.0,
                    "liquidity_ok": 1, "tabs": []}
    reasons = ex.scanner_reasons(minimal_row)
    assert isinstance(reasons, list)   # should not raise, just skip fields it doesn't have


# ─── why_buy ────────────────────────────────────────────────────────────────

def test_why_buy_recommends_strong_setup():
    result = ex.why_buy(_base_row())
    assert result["recommend"] is True
    assert len(result["paragraphs"]) > 2


def test_why_buy_does_not_recommend_illiquid():
    result = ex.why_buy(_base_row(liquidity_ok=0, liquidity_warning="Illiquid"))
    assert result["recommend"] is False
    assert "Illiquid" in result["paragraphs"][0] or "liquidity" in result["paragraphs"][0].lower()


def test_why_buy_does_not_recommend_weak_quality():
    result = ex.why_buy(_base_row(quality_score=30.0))
    assert result["recommend"] is False


def test_why_buy_cautions_on_non_uptrend_market():
    result = ex.why_buy(_base_row(market_trend="Correction"))
    combined = " ".join(result["paragraphs"])
    assert "Correction" in combined
    assert "Caution" in combined


def test_why_buy_notes_unconfirmed_timing():
    result = ex.why_buy(_base_row(signal_type="NEAR BREAKOUT", tabs=["watchlist"]))
    combined = " ".join(result["paragraphs"])
    assert "hasn't broken out" in combined.lower() or "not yet" in combined.lower()


# ─── full_explanation ───────────────────────────────────────────────────────

def test_full_explanation_has_all_sections():
    result = ex.full_explanation(_base_row())
    assert set(result.keys()) == {"why_detected", "strengths", "weaknesses", "institutional_note", "conclusion"}
    assert isinstance(result["strengths"], list) and len(result["strengths"]) > 0
    assert isinstance(result["weaknesses"], list) and len(result["weaknesses"]) > 0
    assert result["symbol"] if "symbol" in result else True   # no crash either way


def test_full_explanation_mentions_symbol():
    result = ex.full_explanation(_base_row(symbol="ZZZTEST.NS"))
    assert "ZZZTEST.NS" in result["why_detected"]


def test_full_explanation_never_empty_lists():
    """Even a row that clears every single gate should still produce
    non-empty strengths/weaknesses lists (falls back to a default
    message) rather than an empty list that would render as nothing."""
    result = ex.full_explanation(_base_row(pole_pct_move=50, flag_retracement_pct=10,
                                             rs_rating=99, breakout_readiness_pct=100))
    assert len(result["weaknesses"]) >= 1
