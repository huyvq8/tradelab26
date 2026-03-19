"""
Chạy job đọc log một lần: quét file logs/trading_lab.log, tìm dòng lỗi, gửi Telegram nếu có.
Dùng: python scripts/run_log_monitor.py
Cron: */10 * * * * cd /path/to/trading-lab-pro-v3 && python scripts/run_log_monitor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from core.log_monitor import scan_log_and_report, get_log_path


def main():
    log_path = get_log_path()
    if not log_path.exists():
        print(f"Log file chua ton tai: {log_path}")
        print("Chay Worker it nhat 1 lan de tao file log.")
        return 0
    errors = scan_log_and_report(last_n_lines=500, send_telegram=True)
    if errors:
        print(f"Tim thay {len(errors)} dong loi. Da gui Telegram (neu cau hinh).")
    else:
        print("Khong co dong loi trong 500 dong gan nhat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
