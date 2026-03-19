"""
Cấu hình logging: ghi ra file logs/trading_lab.log và console.
Dùng cho Worker và Dashboard để job đọc log có thể kiểm soát lỗi.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_app_logging(
    log_dir: str | Path | None = None,
    log_file: str = "trading_lab.log",
    level: int = logging.INFO,
) -> None:
    """
    Cấu hình root logger: FileHandler (logs/trading_lab.log) + StreamHandler (console).
    Gọi 1 lần khi khởi động Worker hoặc app.
    """
    root = Path(__file__).resolve().parent.parent
    if log_dir is None:
        log_dir = root / "logs"
    else:
        log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_path = log_dir / log_file

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Tránh thêm handler trùng khi gọi nhiều lần
    if any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(file_path) for h in root_logger.handlers):
        return

    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(formatter)
    root_logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(formatter)
    root_logger.addHandler(sh)

    return None


def get_log_path(log_dir: str | Path | None = None, log_file: str = "trading_lab.log") -> Path:
    """Đường dẫn file log (để job đọc log dùng)."""
    root = Path(__file__).resolve().parent.parent
    if log_dir is None:
        log_dir = root / "logs"
    return Path(log_dir) / log_file
