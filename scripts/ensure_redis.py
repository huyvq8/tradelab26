"""
Kiểm tra Redis (localhost:6379). Nếu không chạy, thử tự khởi chạy bằng Docker.
Nếu không được, in hướng dẫn cài đặt.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
CONTAINER_NAME = "trading-lab-redis"
DOCKER_IMAGE = "redis:7"


def redis_reachable() -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((REDIS_HOST, REDIS_PORT))
        sock.close()
        return True
    except (socket.error, OSError):
        return False


def try_docker_start() -> bool:
    """Chạy Redis container nếu Docker có sẵn. Trả về True nếu start thành công."""
    try:
        # Kiểm tra Docker
        subprocess.run(
            ["docker", "version"],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    # Container đã tồn tại nhưng stopped -> start lại
    r = subprocess.run(
        ["docker", "start", CONTAINER_NAME],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
    )
    if r.returncode == 0:
        return True

    # Chưa có container -> chạy mới
    r = subprocess.run(
        [
            "docker", "run", "-d",
            "-p", f"{REDIS_PORT}:6379",
            "--name", CONTAINER_NAME,
            DOCKER_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
    )
    return r.returncode == 0


def main():
    print("Đang kiểm tra Redis (localhost:6379)...")
    if redis_reachable():
        print("[OK] Redis đang chạy.")
        return 0

    print("Redis chưa chạy. Đang thử khởi chạy bằng Docker...")
    if try_docker_start():
        for _ in range(10):
            time.sleep(1)
            if redis_reachable():
                print("[OK] Redis đã được khởi chạy (Docker).")
                return 0
        print("[CẢNH BÁO] Container đã chạy nhưng chưa kết nối được. Đợi thêm vài giây rồi thử lại.")
    else:
        print("[HƯỚNG DẪN] Không thể tự khởi chạy Redis.")
        print("  - Cài Docker Desktop rồi chạy lại script này, hoặc")
        print("  - Cài Redis thủ công: xem docs/redis_setup.md")
        print("  - App vẫn chạy được không cần Redis (Redis là tùy chọn).")
    return 0  # Không coi là lỗi vì Redis optional


if __name__ == "__main__":
    sys.exit(main())
