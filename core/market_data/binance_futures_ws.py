"""
Binance Futures WebSocket: mark price (và miniTicker) realtime thay vì REST mỗi cycle.
State đọc bởi get_quotes khi bật Binance Futures (document/request bước 2).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from core.config import settings
from core.market_data.client import MarketQuote, _binance_symbol, _symbol_from_binance

logger = logging.getLogger(__name__)

# Testnet: stream.binancefuture.com; Mainnet: fstream.binance.com
FSTREAM_WS_MAIN = "wss://fstream.binance.com"
FSTREAM_WS_TEST = "wss://stream.binancefuture.com"


class BinanceFuturesWSManager:
    """
    Singleton: kết nối WebSocket combined stream markPrice@1s cho danh sách symbol,
    cập nhật _state; get_quotes() đọc từ _state (MarketQuote).
    """

    _instance: Optional["BinanceFuturesWSManager"] = None
    _lock = threading.Lock()
    _state: dict[str, dict] = {}  # symbol (e.g. SIREN) -> {price, percent_change_24h, volume_24h, updated_at}
    _thread: Optional[threading.Thread] = None
    _stop = threading.Event()
    _symbols: tuple[str, ...] = ()
    _ws = None
    _connected_at: float = 0

    @classmethod
    def get_instance(cls) -> "BinanceFuturesWSManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ws_url(self, symbols: list[str]) -> str:
        """Combined stream: streams=btcusdt@markPrice@1s/sirenusdt@markPrice@1s"""
        base = FSTREAM_WS_TEST if getattr(settings, "binance_futures_testnet", True) else FSTREAM_WS_MAIN
        streams = [f"{_binance_symbol(s).lower()}@markPrice@1s" for s in symbols]
        return f"{base}/stream?streams={'/'.join(streams)}"

    def start(self, symbols: list[str]) -> None:
        """Khởi động thread WebSocket cho các symbol. Gọi 1 lần (vd. từ worker khi có watchlist)."""
        symbols = list(sorted(set(s.strip().upper() for s in symbols if (s or "").strip())))
        if not symbols:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._symbols == tuple(symbols):
                return
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._symbols = tuple(symbols)
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("BinanceFuturesWSManager started for %s symbols", len(symbols))

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None

    def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_and_run()
            except Exception as e:
                logger.warning("BinanceFuturesWSManager run error: %s", e)
            if self._stop.is_set():
                break
            time.sleep(min(backoff, 60))
            backoff = min(backoff * 2, 60)

    def _connect_and_run(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError:
            logger.warning("websocket-client not installed; Binance Futures WS disabled. pip install websocket-client")
            return
        symbols = list(self._symbols)
        if not symbols:
            return
        url = self._ws_url(symbols)
        ws = websocket.create_connection(
            url,
            timeout=30,
            skip_utf8_validation=True,
        )
        self._ws = ws
        self._connected_at = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    msg = ws.recv()
                except Exception as e:
                    logger.warning("BinanceFuturesWSManager recv: %s", e)
                    break
                if not msg:
                    break
                try:
                    data = json.loads(msg)
                    payload = data.get("data") or data
                    # data: {"e":"markPriceUpdate","s":"BTCUSDT","p":"50000.0",...}
                    bin_sym = (payload.get("s") or "").strip().upper()
                    if not bin_sym:
                        stream = data.get("stream", "")
                        if stream and "@" in stream:
                            bin_sym = stream.split("@")[0].upper()
                    if not bin_sym:
                        continue
                    sym = _symbol_from_binance(bin_sym)
                    p = payload.get("p") or payload.get("markPrice")
                    if p is not None:
                        try:
                            price = float(p)
                            now = time.monotonic()
                            self._state[sym] = {
                                "price": price,
                                "percent_change_24h": self._state.get(sym, {}).get("percent_change_24h", 0.0),
                                "volume_24h": self._state.get(sym, {}).get("volume_24h", 0.0),
                                "updated_at": now,
                            }
                        except (TypeError, ValueError):
                            pass
                except json.JSONDecodeError:
                    pass
        finally:
            try:
                ws.close()
            except Exception:
                pass
            self._ws = None

    def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        """Lấy quotes từ state WebSocket. Chỉ trả về symbol đã có trong state (thiếu thì caller dùng REST)."""
        result: dict[str, MarketQuote] = {}
        now = time.monotonic()
        for s in symbols:
            sym = (s or "").strip().upper()
            if not sym:
                continue
            st = self._state.get(sym)
            if st is None:
                continue
            # Chỉ dùng nếu đã cập nhật trong 30s (tránh dùng dữ liệu quá cũ khi WS vừa reconnect)
            if now - st.get("updated_at", 0) > 30:
                continue
            result[sym] = MarketQuote(
                symbol=sym,
                price=float(st["price"]),
                percent_change_24h=float(st.get("percent_change_24h", 0)),
                volume_24h=float(st.get("volume_24h", 0)),
                market_cap=0.0,
            )
        return result

    def is_ready(self, symbols: list[str]) -> bool:
        """True nếu mọi symbol đều có trong state và cập nhật gần đây (30s)."""
        if not symbols:
            return False
        now = time.monotonic()
        for s in symbols:
            st = self._state.get((s or "").strip().upper())
            if st is None or (now - st.get("updated_at", 0)) > 30:
                return False
        return True


def get_binance_futures_ws_manager() -> BinanceFuturesWSManager:
    return BinanceFuturesWSManager.get_instance()
