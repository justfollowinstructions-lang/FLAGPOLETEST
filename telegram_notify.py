"""
NSE Flag & Pole Scanner - Telegram Notifications
====================================================
Sends a scan summary message and the Excel report (as a document) to
a Telegram chat after each run. Best-effort and non-fatal: a Telegram
failure is logged but never crashes the scan — the report has already
been written to disk by the time this runs.

Configured via two environment variables, read at call time (not at
import time), so a missing/misconfigured token only disables the
notification, nothing else:

    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Setup (one-time):
  1. Message @BotFather on Telegram -> /newbot -> follow the prompts.
     You get a token that looks like
     123456789:ABCdefGhIJKlmnOPQRstuVWXyz
  2. Send any message to your new bot (or add it to a group/channel
     and send a message there), then open this URL in a browser:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     Find "chat":{"id": ...} in the JSON response — that number
     (it's negative for groups/channels) is your TELEGRAM_CHAT_ID.
  3. GitHub Actions: repo Settings -> Secrets and variables -> Actions
     -> New repository secret, add both TELEGRAM_BOT_TOKEN and
     TELEGRAM_CHAT_ID. The workflow already passes them through to the
     scan step as environment variables — nothing else to configure.
  4. Local runs: export the same two as environment variables before
     running main_flag_pole.py, e.g.
     export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
     If unset, notifications are silently skipped (logged at INFO,
     not an error) so local dev without a bot configured still works.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests

from logger_utils import get_logger

log = get_logger("scanner")

API_BASE = "https://api.telegram.org"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024   # Telegram Bot API hard limit
SEND_MESSAGE_TIMEOUT_SEC = 30
SEND_DOCUMENT_TIMEOUT_SEC = 120


def _get_credentials() -> tuple[Optional[str], Optional[str]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return (token or None), (chat_id or None)


def is_configured() -> bool:
    token, chat_id = _get_credentials()
    return bool(token and chat_id)


def _escape_html(text: str) -> str:
    """Telegram's HTML parse mode only needs these three escaped —
    far less fiddly than MarkdownV2's long list of special characters
    that must be backslash-escaped everywhere, including inside what
    look like plain sentences (e.g. '.', '-', '!' all need escaping in
    MarkdownV2). HTML mode was chosen specifically to avoid that class
    of bug: a stock name or note containing a '.', '-' or '(' should
    never be able to break message formatting."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_summary_message(summary_stats: dict, confirmed_symbols: list[str]) -> str:
    scan_date = summary_stats.get("scan_date", "-")
    market_trend = summary_stats.get("market_trend", "Unknown")
    dist_days = summary_stats.get("distribution_days", 0)
    total_patterns = summary_stats.get("total_patterns", 0)
    n_confirmed = summary_stats.get("n_confirmed", 0)
    n_actionable = summary_stats.get("n_actionable", 0)

    lines = [
        f"<b>NSE Flag &amp; Pole Scanner — {_escape_html(scan_date)}</b>",
        "",
        f"Market trend: <b>{_escape_html(market_trend)}</b> "
        f"({dist_days} distribution days / 25 sessions)",
        f"Patterns detected: {total_patterns}  |  Actionable: {n_actionable}",
        f"🔥 Confirmed Breakouts: <b>{n_confirmed}</b>",
    ]

    if confirmed_symbols:
        lines.append("")
        lines.append("Today's Confirmed Breakouts:")
        for sym in confirmed_symbols[:15]:
            lines.append(f"  • {_escape_html(sym)}")
        if len(confirmed_symbols) > 15:
            lines.append(f"  … and {len(confirmed_symbols) - 15} more (see attached report)")

    lines.append("")
    lines.append("Full report attached below." if n_confirmed or total_patterns
                  else "No patterns met the bar today — full report attached for reference.")
    return "\n".join(lines)


def send_message(text: str) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        return False

    url = f"{API_BASE}/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=SEND_MESSAGE_TIMEOUT_SEC,
        )
    except requests.RequestException:
        log.error("Telegram sendMessage request failed", exc_info=True)
        return False

    if resp.status_code != 200:
        log.error("Telegram sendMessage failed: HTTP %d — %s", resp.status_code, resp.text[:500])
        return False
    return True


def send_document(file_path: Path, caption: Optional[str] = None) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        return False

    if not file_path.exists():
        log.error("Telegram send_document: file not found: %s", file_path)
        return False

    size = file_path.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        log.error(
            "Telegram send_document: %s is %.1f MB, exceeds the Bot API's 50MB "
            "upload limit — skipping. Consider a file-hosting link instead.",
            file_path.name, size / (1024 * 1024),
        )
        return False

    url = f"{API_BASE}/bot{token}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"

    try:
        with open(file_path, "rb") as f:
            files = {
                "document": (
                    file_path.name, f,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            }
            resp = requests.post(url, data=data, files=files, timeout=SEND_DOCUMENT_TIMEOUT_SEC)
    except requests.RequestException:
        log.error("Telegram sendDocument request failed", exc_info=True)
        return False

    if resp.status_code != 200:
        log.error("Telegram sendDocument failed: HTTP %d — %s", resp.status_code, resp.text[:500])
        return False
    return True


def notify_scan_complete(
    summary_stats: dict,
    confirmed_symbols: list[str],
    report_path: Path,
) -> None:
    """
    Best-effort notification: a summary message, then the Excel report
    as a document. Call this as the last step after the report is
    written. Never raises — logs and returns on any failure, since a
    notification problem must never make the scan itself look failed.
    """
    if not is_configured():
        log.info(
            "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not "
            "set) — skipping notification. See telegram_notify.py's docstring "
            "for one-time setup."
        )
        return

    message = build_summary_message(summary_stats, confirmed_symbols)
    if send_message(message):
        log.info("Telegram summary message sent")
    else:
        log.warning("Telegram summary message failed to send (see error above)")

    caption = f"Flag &amp; Pole report — {_escape_html(summary_stats.get('scan_date', ''))}"
    if send_document(report_path, caption=caption):
        log.info("Telegram report document sent: %s", report_path.name)
    else:
        log.warning("Telegram report document failed to send: %s", report_path.name)
