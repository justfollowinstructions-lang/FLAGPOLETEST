"""
Tests for telegram_notify.py — every test mocks requests.post, so this
suite never makes a real network call or requires real credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telegram_notify as tg


def test_is_configured_false_when_env_vars_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.is_configured() is False


def test_is_configured_true_when_both_env_vars_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert tg.is_configured() is True


def test_is_configured_false_when_only_one_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.is_configured() is False


def test_escape_html_escapes_special_chars():
    assert tg._escape_html("A & B <tag> price -3.5%") == "A &amp; B &lt;tag&gt; price -3.5%"


def test_build_summary_message_includes_key_fields():
    stats = {
        "scan_date": "2026-07-04",
        "market_trend": "Uptrend Under Pressure",
        "distribution_days": 5,
        "total_patterns": 278,
        "n_confirmed": 5,
        "n_actionable": 277,
    }
    msg = tg.build_summary_message(stats, ["EIEL.NS", "MCLOUD.NS"])
    assert "2026-07-04" in msg
    assert "Uptrend Under Pressure" in msg
    assert "278" in msg
    assert "EIEL.NS" in msg
    assert "MCLOUD.NS" in msg


def test_build_summary_message_handles_no_confirmed_breakouts():
    stats = {
        "scan_date": "2026-07-04", "market_trend": "Correction",
        "distribution_days": 8, "total_patterns": 0, "n_confirmed": 0, "n_actionable": 0,
    }
    msg = tg.build_summary_message(stats, [])
    assert "No patterns met the bar today" in msg


def test_send_message_returns_false_when_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.send_message("hello") is False


def test_send_message_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    fake_response = MagicMock(status_code=200, text="ok")
    with patch("telegram_notify.requests.post", return_value=fake_response) as mock_post:
        result = tg.send_message("hello world")

    assert result is True
    args, kwargs = mock_post.call_args
    assert "sendMessage" in args[0]
    assert kwargs["data"]["chat_id"] == "42"
    assert kwargs["data"]["text"] == "hello world"
    assert kwargs["data"]["parse_mode"] == "HTML"


def test_send_message_handles_non_200(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    fake_response = MagicMock(status_code=401, text="Unauthorized")
    with patch("telegram_notify.requests.post", return_value=fake_response):
        result = tg.send_message("hello")

    assert result is False


def test_send_message_handles_network_exception(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    import requests
    with patch("telegram_notify.requests.post", side_effect=requests.ConnectionError("boom")):
        result = tg.send_message("hello")

    assert result is False


def test_send_document_returns_false_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    missing = tmp_path / "does_not_exist.xlsx"
    assert tg.send_document(missing) is False


def test_send_document_rejects_oversized_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(tg, "MAX_DOCUMENT_BYTES", 10)   # force the size check to trip

    big_file = tmp_path / "big.xlsx"
    big_file.write_bytes(b"x" * 100)

    with patch("telegram_notify.requests.post") as mock_post:
        result = tg.send_document(big_file)

    assert result is False
    mock_post.assert_not_called()


def test_send_document_success(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    report = tmp_path / "flag_pole_report_2026-07-04.xlsx"
    report.write_bytes(b"fake xlsx bytes")

    fake_response = MagicMock(status_code=200, text="ok")
    with patch("telegram_notify.requests.post", return_value=fake_response) as mock_post:
        result = tg.send_document(report, caption="test caption")

    assert result is True
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["chat_id"] == "42"
    assert kwargs["data"]["caption"] == "test caption"
    assert "document" in kwargs["files"]


def test_notify_scan_complete_skips_silently_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with patch("telegram_notify.requests.post") as mock_post:
        tg.notify_scan_complete({"scan_date": "2026-07-04"}, [], tmp_path / "report.xlsx")

    mock_post.assert_not_called()


def test_notify_scan_complete_sends_message_and_document(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    report = tmp_path / "flag_pole_report_2026-07-04.xlsx"
    report.write_bytes(b"fake xlsx bytes")

    stats = {
        "scan_date": "2026-07-04", "market_trend": "Uptrend",
        "distribution_days": 2, "total_patterns": 10, "n_confirmed": 2, "n_actionable": 9,
    }

    fake_response = MagicMock(status_code=200, text="ok")
    with patch("telegram_notify.requests.post", return_value=fake_response) as mock_post:
        tg.notify_scan_complete(stats, ["ABC.NS", "XYZ.NS"], report)

    assert mock_post.call_count == 2   # one sendMessage + one sendDocument
    urls_called = [c.args[0] for c in mock_post.call_args_list]
    assert any("sendMessage" in u for u in urls_called)
    assert any("sendDocument" in u for u in urls_called)
