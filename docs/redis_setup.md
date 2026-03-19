# Cài đặt Redis trên Windows

Redis là **tùy chọn** với Trading Lab Pro (app vẫn chạy không cần Redis). Nếu bạn muốn chạy Redis (ví dụ cho cache sau này), chọn một trong các cách sau.

---

## 1. Docker (khuyến nghị)

Đã cài [Docker Desktop](https://www.docker.com/products/docker-desktop/):

```powershell
docker run -d -p 6379:6379 --name trading-lab-redis redis:7
```

Kiểm tra: `docker ps` (thấy container `trading-lab-redis`).

Dừng: `docker stop trading-lab-redis`. Chạy lại: `docker start trading-lab-redis`.

---

## 2. Chocolatey

Đã cài [Chocolatey](https://chocolatey.org/install):

```powershell
choco install redis-64 -y
```

Sau khi cài, Redis thường chạy như service Windows. Kiểm tra port 6379 đã mở (ví dụ dùng script `scripts/ensure_redis.py`).

---

## 3. WSL2 (Ubuntu)

Trong WSL2 (Ubuntu):

```bash
sudo apt update
sudo apt install redis-server -y
redis-server --daemonize yes
```

Kiểm tra: `redis-cli ping` → `PONG`.

Lưu ý: Redis chạy trong WSL, ứng dụng Windows cần kết nối qua `localhost:6379` (WSL2 forward port mặc định).

---

## 4. Memurai (Redis tương thích cho Windows)

[Memurai](https://www.memurai.com/) tương thích Redis, cài bản Windows: tải installer từ trang chủ, cài và chạy service. Mặc định cũng dùng port 6379.

---

## Kiểm tra Redis đã chạy

- **Script**: `python scripts/ensure_redis.py` (kiểm tra và tự khởi chạy qua Docker nếu có).
- **Thủ công**: Mở PowerShell: `Test-NetConnection -ComputerName localhost -Port 6379` (TcpTestSucceeded = True).
