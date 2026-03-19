"""Gui 1 tin nhan thu den Telegram de kiem tra cau hinh."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from core.config import settings

def main():
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("TELEGRAM_BOT_TOKEN hoac TELEGRAM_CHAT_ID trong .env chua dat.")
        return 1
    try:
        from integrations.telegram.report import send_report
        msg = "Trading Lab Pro - test. Neu ban thay tin nay thi Telegram da hoat dong."
        send_report(settings.telegram_bot_token, settings.telegram_chat_id, msg)
        print("Da gui tin thu thanh cong. Kiem tra nhom Telegram.")
        return 0
    except Exception as e:
        print(f"Loi gui Telegram: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
