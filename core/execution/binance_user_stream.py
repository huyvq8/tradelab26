"""
Binance Futures User Data Stream: nhận ACCOUNT_UPDATE qua WebSocket,
cache balance và positions; REST chỉ dùng reconcile định kỳ 2–5 phút (document/request bước 2).
Singleton process-wide: chỉ 1 listenKey, 1 kết nối WSS; keepalive PUT ~30 phút; không start lại mỗi cycle.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.execution.binance_futures import BinanceFuturesExecutor

logger = logging.getLogger(__name__)

FSTREAM_WS_MAIN = "wss://fstream.binance.com"
FSTREAM_WS_TEST = "wss://stream.binancefuture.com"
KEEPALIVE_INTERVAL = 1800  # 30 phút — PUT /listenKey, không phải POST
RECONCILE_INTERVAL = 150   # 2.5 phút — REST chỉ gọi mỗi RECONCILE_INTERVAL giây
RECV_TIMEOUT_SECONDS = 3600  # 1 giờ — tránh recv timeout khi không có message (Binance đóng sau 60 phút không hoạt động)

# Singleton process-wide: không tạo listenKey/stream mới mỗi cycle
_USER_STREAM_SINGLETON: Optional["BinanceUserStreamManager"] = None
_USER_STREAM_LOCK = threading.Lock()


def ensure_user_stream_started(executor: "BinanceFuturesExecutor") -> Optional["BinanceUserStreamManager"]:
    """Idempotent: nếu stream đang chạy thì không làm gì; chỉ start 1 lần. Trả về manager hoặc None."""
    global _USER_STREAM_SINGLETON
    with _USER_STREAM_LOCK:
        if _USER_STREAM_SINGLETON is not None and _USER_STREAM_SINGLETON.is_running():
            _USER_STREAM_SINGLETON._executor = executor  # cập nhật executor cho keepalive (cùng credentials)
            return _USER_STREAM_SINGLETON
        if _USER_STREAM_SINGLETON is not None:
            _USER_STREAM_SINGLETON.stop()
            _USER_STREAM_SINGLETON = None
        manager = BinanceUserStreamManager(executor)
        if manager.start():
            _USER_STREAM_SINGLETON = manager
            return manager
        return None


def get_user_stream_singleton() -> Optional["BinanceUserStreamManager"]:
    """Trả về singleton (có thể None nếu chưa start)."""
    return _USER_STREAM_SINGLETON


class BinanceUserStreamManager:
    """
    User Data Stream: listenKey + WSS, cập nhật balance và positions từ ACCOUNT_UPDATE.
    Executor gọi get_cached_balance()/get_cached_positions(); nếu quá RECONCILE_INTERVAL thì gọi REST rồi set_from_rest_*.
    """

    def __init__(self, executor: "BinanceFuturesExecutor"):
        self._executor = executor
        self._listen_key: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._balance: Optional[dict[str, Any]] = None
        self._positions: list[dict[str, Any]] = []
        self._last_updated = 0.0
        self._last_keepalive = 0.0
        self._started = False

    def is_running(self) -> bool:
        """True nếu stream đã start và thread vẫn đang chạy (không tạo listenKey/thread mới). Lock-free để tránh deadlock khi gọi từ start() đang giữ lock."""
        return (
            self._started
            and self._thread is not None
            and self._thread.is_alive()
        )

    def start(self) -> bool:
        """Tạo listenKey 1 lần, bắt đầu 1 thread WSS. Trả về True nếu thành công. Idempotent: nếu đã chạy thì return True ngay."""
        with self._lock:
            if self.is_running():
                return True
            if self._thread is not None and not self._thread.is_alive():
                self._started = False
                self._thread = None
            listen_key = self._create_listen_key()
            if not listen_key:
                return False
            self._listen_key = listen_key
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_ws, daemon=True)
            self._thread.start()
            self._started = True
            self._last_keepalive = time.monotonic()
            logger.info("Binance User Data Stream started (single process-wide instance)")
            return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
            self._started = False
            self._listen_key = None

    def _create_listen_key(self) -> Optional[str]:
        try:
            data = self._executor._signed_request("POST", "/fapi/v1/listenKey", {})
            return (data.get("listenKey") or "").strip() or None
        except Exception as e:
            logger.warning("User stream create listenKey failed: %s", e)
            return None

    def _keepalive(self) -> None:
        """PUT /listenKey để gia hạn (không phải POST — POST tạo key mới)."""
        if not self._listen_key:
            return
        try:
            self._executor._signed_request("PUT", "/fapi/v1/listenKey", {})
            self._last_keepalive = time.monotonic()
            logger.info("User stream keepalive OK (PUT /listenKey, next in ~30 min)")
        except Exception as e:
            logger.warning("User stream keepalive failed: %s", e)

    def _run_ws(self) -> None:
        try:
            import websocket
        except ImportError:
            logger.warning("websocket-client not installed; User Data Stream disabled")
            return
        base = FSTREAM_WS_TEST if getattr(self._executor, "base_url", "").find("testnet") >= 0 else FSTREAM_WS_MAIN
        while not self._stop.is_set():
            url = f"{base}/ws/{self._listen_key}"  # rebuild mỗi lần reconnect (sau listenKeyExpired dùng key mới)
            try:
                ws = websocket.create_connection(url, timeout=30, skip_utf8_validation=True)
                try:
                    # Tránh recv timeout mỗi 30s khi không có message — Binance đóng sau 60 phút không hoạt động
                    if hasattr(ws, "settimeout"):
                        ws.settimeout(RECV_TIMEOUT_SECONDS)
                    elif getattr(ws, "sock", None) is not None:
                        ws.sock.settimeout(RECV_TIMEOUT_SECONDS)
                    while not self._stop.is_set():
                        if time.monotonic() - self._last_keepalive >= KEEPALIVE_INTERVAL:
                            self._keepalive()
                        try:
                            msg = ws.recv()
                        except Exception as e:
                            logger.warning("User stream recv: %s", e)
                            break
                        if not msg:
                            break
                        self._on_message(msg)
                finally:
                    try:
                        ws.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("User stream ws error: %s", e)
            if self._stop.is_set():
                break
            # Reconnect với CÙNG listenKey (không tạo key mới)
            time.sleep(2)

    def _on_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return
        e = data.get("e") or data.get("eventType")
        if e == "ACCOUNT_UPDATE":
            with self._lock:
                self._last_updated = time.monotonic()
                # Binance: "a" = update data, "a"."B" = balances, "a"."P" = positions
                update_data = data.get("a") or {}
                balances = update_data.get("B") or []
                for item in balances:
                    if (item.get("a") or "").strip().upper() == "USDT":
                        try:
                            wb = float(item.get("wb", 0) or 0)
                            cw = float(item.get("cw", 0) or 0)  # cross wallet (available)
                            self._balance = {
                                "wallet_balance": wb,
                                "available_balance": cw if cw != 0 else wb,
                                "cross_un_pnl": 0.0,
                                "total_equity": wb,
                            }
                        except (TypeError, ValueError):
                            pass
                        break
                pos_list = update_data.get("P") or []
                if pos_list is not None:
                    # ACCOUNT_UPDATE chỉ gửi positions thay đổi — merge vào _positions (pa=0 = đóng/giảm).
                    for p in pos_list:
                        amt = float(p.get("pa", 0) or 0)
                        sym = (p.get("s") or "").strip()
                        if sym.endswith("USDT"):
                            base = sym[:-4]
                        else:
                            base = sym
                        pos_side = p.get("ps") or p.get("positionSide") or "BOTH"
                        side = "long" if amt > 0 else "short"
                        if amt == 0:
                            self._positions = [x for x in self._positions if not (x.get("symbol") == base and x.get("position_side") == pos_side)]
                        else:
                            entry = {"symbol": base, "position_side": pos_side, "side": side, "quantity": abs(amt), "entry_price": float(p.get("ep", 0) or 0)}
                            found = False
                            for i, x in enumerate(self._positions):
                                if x.get("symbol") == base and x.get("position_side") == pos_side:
                                    self._positions[i] = entry
                                    found = True
                                    break
                            if not found:
                                self._positions.append(entry)
        elif e == "listenKeyExpired":
            logger.warning("User stream listenKey expired; creating new key and reconnecting")
            with self._lock:
                key = self._create_listen_key()
                if key:
                    self._listen_key = key
            # Thoát vòng recv để reconnect với url mới (url rebuild mỗi lần ở _run_ws)
            raise ConnectionError("listenKeyExpired")

    def get_cached_balance(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._balance) if self._balance else None

    def get_cached_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._positions)

    def last_updated(self) -> float:
        with self._lock:
            return self._last_updated

    def set_from_rest_balance(self, info: dict[str, Any]) -> None:
        """Sau khi gọi REST get_balance_info, gọi để seed/cập nhật cache (reconcile)."""
        with self._lock:
            self._balance = dict(info)
            self._last_updated = time.monotonic()

    def set_from_rest_positions(self, positions: list[dict[str, Any]]) -> None:
        """Sau khi gọi REST positionRisk, gọi để seed/cập nhật cache (reconcile)."""
        with self._lock:
            self._positions = list(positions)
            self._last_updated = time.monotonic()

    def should_reconcile(self) -> bool:
        """True nếu đã quá RECONCILE_INTERVAL kể từ lần cập nhật cuối (stream hoặc REST)."""
        return (time.monotonic() - self.last_updated()) >= RECONCILE_INTERVAL
