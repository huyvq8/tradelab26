"""
Checklist: .env, DB, Redis (optional), và các cài đặt cần thiết.
Trả về 0 nếu có thể chạy được; in cảnh báo cho mục tùy chọn.
"""
import os
import sys
import socket
from pathlib import Path

# Thêm project root vào path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

def main():
    errors = []
    warnings = []

    # 1. Python version
    if sys.version_info < (3, 10):
        errors.append(f"Python 3.10+ cần thiết, hiện tại: {sys.version}")
    else:
        print(f"[OK] Python {sys.version.split()[0]}")

    # 2. .env tồn tại
    env_path = root / ".env"
    if not env_path.is_file():
        errors.append(".env không tìm thấy. Copy từ .env.example và điền giá trị.")
    else:
        print("[OK] File .env có sẵn")

    # Load .env để kiểm tra biến (không dùng core.config để tránh import DB sớm)
    env_vars = {}
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")

    # 3. DATABASE_URL
    db_url = env_vars.get("DATABASE_URL", "").strip()
    if not db_url:
        errors.append("DATABASE_URL chưa đặt trong .env")
    else:
        print("[OK] DATABASE_URL đã đặt")
        if db_url.startswith("sqlite"):
            db_file = db_url.replace("sqlite:///", "").strip()
            if db_file and not db_file.startswith("/"):
                p = root / db_file
                if p.parent != root and not p.parent.exists():
                    try:
                        p.parent.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        warnings.append(f"Không tạo được thư mục DB: {e}")
        # PostgreSQL: chỉ kiểm tra format
        if db_url.startswith("postgresql") and "@" in db_url:
            print("[OK] PostgreSQL URL có vẻ hợp lệ (kết nối thật khi chạy app)")

    # 4. CMC_API_KEY (cảnh báo nếu trống — chạy được nhưng dùng giá mock)
    cmc = env_vars.get("CMC_API_KEY", "").strip()
    if not cmc:
        warnings.append("CMC_API_KEY trống — app dùng giá mock (vẫn chạy được)")
    else:
        print("[OK] CMC_API_KEY đã đặt")

    # 5. Telegram (cảnh báo nếu chỉ có token hoặc chỉ có chat_id)
    tok = env_vars.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env_vars.get("TELEGRAM_CHAT_ID", "").strip()
    if tok and not chat:
        warnings.append("TELEGRAM_CHAT_ID trống — báo cáo Telegram sẽ không gửi")
    elif not tok and chat:
        warnings.append("TELEGRAM_BOT_TOKEN trống — báo cáo Telegram sẽ không gửi")
    elif tok and chat:
        print("[OK] Telegram đã cấu hình")

    # 6. Redis (tùy chọn — chỉ kiểm tra port 6379)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(("127.0.0.1", 6379))
        sock.close()
        print("[OK] Redis đang chạy trên localhost:6379")
    except (socket.error, OSError):
        warnings.append("Redis không chạy (localhost:6379). Chạy: python scripts/ensure_redis.py — hoặc xem docs/redis_setup.md. App vẫn chạy không cần Redis.")

    # 7. Thư mục và dependencies
    req_file = root / "requirements.txt"
    if not req_file.is_file():
        errors.append("requirements.txt không tìm thấy")
    else:
        print("[OK] requirements.txt có sẵn")

    # In kết quả
    for w in warnings:
        print(f"[CẢNH BÁO] {w}")
    for e in errors:
        print(f"[LỖI] {e}")

    if errors:
        print("\n>>> Sửa các mục [LỖI] trước khi chạy.")
        return 1
    if warnings:
        print("\n>>> Có cảnh báo nhưng có thể chạy thử.")
    print("\n>>> Checklist xong. Có thể chạy: run_test.bat hoặc start_api.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
