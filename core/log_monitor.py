"""
Job chuyên đọc log: quét file log tìm dòng lỗi, có thể gửi Telegram để kiểm soát.
Dùng: gọi scan_log_and_report() định kỳ (vd. từ Worker) hoặc chạy scripts/run_log_monitor.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from core.logging_config import get_log_path


# Mẫu dòng log được coi là lỗi (case-insensitive)
ERROR_PATTERNS = [
    r"error",
    r"exception",
    r"traceback",
    r"failed",
    r"429",  # rate limit
    r"418",  # IP ban
    r"\bLoi\b",
    r"FAIL",
    r"raise ",
    r"AssertionError",
    r"ValueError",
    r"KeyError",
    r"TimeoutError",
    r"ConnectionError",
    r"BinanceFutures.*failed",
    r"Telegram.*Loi",
    r"Cycle.*Loi",
]


def scan_log_file(
    log_path: str | Path | None = None,
    last_n_lines: int = 500,
    patterns: list[str] | None = None,
) -> list[str]:
    """
    Đọc last_n_lines của file log, trả về danh sách dòng khớp ít nhất một pattern lỗi.
    Nếu file không tồn tại hoặc rỗng, trả về [].
    """
    path = Path(log_path) if log_path else get_log_path()
    if not path.exists():
        return []
    patterns = patterns or ERROR_PATTERNS
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []
    if not lines:
        return []
    tail = lines[-last_n_lines:] if len(lines) > last_n_lines else lines
    out = []
    for line in tail:
        line = line.rstrip("\n\r")
        if not line:
            continue
        for pat in compiled:
            if pat.search(line):
                out.append(line)
                break
    return out


def format_report(error_lines: list[str], max_lines: int = 15) -> str:
    """Format danh sách dòng lỗi thành bản tóm tắt (gửi Telegram)."""
    if not error_lines:
        return ""
    head = "⚠️ **Log lỗi gần đây**\n\n"
    sample = error_lines[-max_lines:] if len(error_lines) > max_lines else error_lines
    body = "\n".join(sample[:max_lines])
    if len(error_lines) > max_lines:
        body += f"\n\n... và {len(error_lines) - max_lines} dòng khác."
    return head + body[:3500]


def scan_log_and_report(
    log_path: str | Path | None = None,
    last_n_lines: int = 500,
    send_telegram: bool = True,
    on_errors: Callable[[list[str]], None] | None = None,
) -> list[str]:
    """
    Quét file log, nếu có dòng lỗi thì (1) gọi on_errors(nếu có), (2) gửi Telegram nếu send_telegram.
    Trả về danh sách dòng lỗi.
    """
    errors = scan_log_file(log_path=log_path, last_n_lines=last_n_lines)
    if not errors:
        return []
    if on_errors:
        try:
            on_errors(errors)
        except Exception:
            pass
    if send_telegram:
        try:
            from core.config import settings
            if settings.telegram_bot_token and settings.telegram_chat_id:
                from integrations.telegram.report import send_report
                report = format_report(errors)
                if report:
                    send_report(settings.telegram_bot_token, settings.telegram_chat_id, report[:4000])
        except Exception:
            pass
    return errors
