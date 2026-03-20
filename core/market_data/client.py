from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import httpx

from core.config import settings

# Khi bật đánh thật Binance Futures: chỉ gọi fapi (bỏ spot) để tránh 400 cho symbol chỉ có trên Futures (vd. SIRENUSDT).
def _use_futures_only_for_quotes() -> bool:
    try:
        from core.config import get_effective_enable_live_binance_futures
        return bool(get_effective_enable_live_binance_futures())
    except Exception:
        return False

CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
# Binance Spot (nhiều cặp phổ biến: BTC, ETH, ...)
BINANCE_SPOT_TICKER_24HR = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_SPOT_TICKER_PRICE = "https://api.binance.com/api/v3/ticker/price"
# Binance Futures USD-M (một số cặp chỉ có trên Futures, ví dụ SIRENUSDT)
BINANCE_FUTURES_TICKER_24HR = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BINANCE_FUTURES_TICKER_PRICE = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
CACHE_TTL_SECONDS = 10
# Cache nến: TTL theo interval (document/request — không cần fetch mỗi 10s cho 1h/4h).
KLINES_CACHE_TTL = {"1h": 120, "5m": 30, "4h": 600, "15m": 45}
DEFAULT_KLINES_TTL = 120
# Cache 24h stats khi dùng WS (WS chỉ có giá; regime cần change_24h + volume_24h)
_FUTURES_24H_CACHE: dict[str, tuple[float, float, float]] = {}  # symbol -> (expiry_monotonic, pct, volume)
_FUTURES_24H_CACHE_TTL = 60.0


def _binance_symbol(symbol: str) -> str:
    """Convert BTC -> BTCUSDT; leave BTCUSDT as is."""
    s = symbol.strip().upper()
    if s.endswith("USDT") or s.endswith("BUSD"):
        return s
    return f"{s}USDT"


def _symbol_from_binance(binance_symbol: str) -> str:
    """BTCUSDT -> BTC."""
    s = binance_symbol.strip().upper()
    if s.endswith("USDT"):
        return s[:-4]
    if s.endswith("BUSD"):
        return s[:-4]
    return s


@dataclass
class MarketQuote:
    symbol: str
    price: float
    percent_change_24h: float
    volume_24h: float
    market_cap: float


@dataclass
class Kline1h:
    """Một cây nến 1h: O, H, L, C, volume."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_time_ms: int


class BinanceClient:
    """Lấy giá từ Binance (ticker/price hoặc ticker/24hr). Có cache quotes và klines để tránh vượt rate limit."""

    _cache: dict[tuple, tuple[float, dict[str, MarketQuote]]] = {}
    _klines_cache: dict[tuple[str, str, int], tuple[float, list["Kline1h"]]] = {}

    def get_quotes(self, symbols: Iterable[str]) -> dict[str, MarketQuote]:
        symbols_tuple = tuple(sorted(set(symbols)))
        now = time.monotonic()
        if symbols_tuple in BinanceClient._cache:
            cached_at, cached_result = BinanceClient._cache[symbols_tuple]
            if now - cached_at < CACHE_TTL_SECONDS:
                return cached_result
        result: dict[str, MarketQuote] = {}
        binance_symbols = [_binance_symbol(s) for s in symbols_tuple]
        # Khi chạy Binance Futures: chỉ gọi fapi, tránh 2 request spot 400 mỗi cycle (document/request).
        endpoints = (
            [(BINANCE_FUTURES_TICKER_24HR, BINANCE_FUTURES_TICKER_PRICE)]
            if _use_futures_only_for_quotes()
            else [
                (BINANCE_SPOT_TICKER_24HR, BINANCE_SPOT_TICKER_PRICE),
                (BINANCE_FUTURES_TICKER_24HR, BINANCE_FUTURES_TICKER_PRICE),
            ]
        )
        with httpx.Client(timeout=20) as client:
            for sym, bin_sym in zip(symbols_tuple, binance_symbols):
                for url_24hr, url_price in endpoints:
                    try:
                        r = client.get(url_24hr, params={"symbol": bin_sym})
                        if r.status_code == 429:
                            retry_after = int(r.headers.get("Retry-After", 60))
                            time.sleep(min(max(retry_after, 10), 120))
                            r = client.get(url_24hr, params={"symbol": bin_sym})
                        r.raise_for_status()
                        data = r.json()
                        result[sym] = MarketQuote(
                            symbol=sym,
                            price=float(data["lastPrice"]),
                            percent_change_24h=float(data.get("priceChangePercent", 0.0)),
                            volume_24h=float(data.get("quoteVolume", 0.0)),
                            market_cap=0.0,
                        )
                        break
                    except Exception:
                        try:
                            r2 = client.get(url_price, params={"symbol": bin_sym})
                            r2.raise_for_status()
                            data = r2.json()
                            result[sym] = MarketQuote(
                                symbol=sym,
                                price=float(data["price"]),
                                percent_change_24h=0.0,
                                volume_24h=0.0,
                                market_cap=0.0,
                            )
                            break
                        except Exception:
                            continue
        BinanceClient._cache[symbols_tuple] = (now, result)
        return result

    def _parse_klines_response(self, arr: list) -> list["Kline1h"]:
        out = []
        for k in arr:
            if len(k) >= 6:
                out.append(Kline1h(
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    open_time_ms=int(k[0]),
                ))
        return out

    def get_klines(self, symbol: str, interval: str = "1h", limit: int = 5) -> list["Kline1h"]:
        """Lấy nến theo interval (1h, 5m, 4h, ...) từ Binance. Có cache theo TTL để tránh vượt rate limit."""
        cache_key = (symbol.strip().upper(), interval, limit)
        now = time.monotonic()
        ttl = KLINES_CACHE_TTL.get(interval, DEFAULT_KLINES_TTL)
        if cache_key in BinanceClient._klines_cache:
            cached_at, cached_out = BinanceClient._klines_cache[cache_key]
            if now - cached_at < ttl and cached_out:
                return cached_out
        bin_sym = _binance_symbol(symbol)
        last_cached: list["Kline1h"] = BinanceClient._klines_cache.get(cache_key, (0, []))[1]
        for base in [BINANCE_FUTURES_KLINES, BINANCE_SPOT_KLINES]:
            try:
                with httpx.Client(timeout=15) as client:
                    r = client.get(base, params={"symbol": bin_sym, "interval": interval, "limit": limit})
                    if r.status_code == 429:
                        retry_after = min(max(int(r.headers.get("Retry-After", 60)), 10), 120)
                        time.sleep(retry_after)
                        r = client.get(base, params={"symbol": bin_sym, "interval": interval, "limit": limit})
                    r.raise_for_status()
                    arr = r.json()
            except Exception:
                if last_cached:
                    return last_cached
                continue
            out = self._parse_klines_response(arr)
            if out:
                BinanceClient._klines_cache[cache_key] = (now, out)
                return out
        if last_cached:
            return last_cached
        return []

    def get_klines_1h(self, symbol: str, limit: int = 5) -> list["Kline1h"]:
        """Lấy nến 1h từ Binance (Futures rồi Spot). Cache 60s."""
        return self.get_klines(symbol, interval="1h", limit=limit)

    def get_klines_5m(self, symbol: str, limit: int = 12) -> list["Kline1h"]:
        """Lấy nến 5m (cùng cấu trúc OHLCV). Cache 30s."""
        return self.get_klines(symbol, interval="5m", limit=limit)

    def get_klines_4h(self, symbol: str, limit: int = 6) -> list["Kline1h"]:
        """Lấy nến 4h cho xu hướng (trend filter). Cache 300s để tiết kiệm API."""
        return self.get_klines(symbol, interval="4h", limit=limit)

    def get_top_quotes_by_volume(self, top_n: int = 100, min_volume_usd: float = 500_000) -> dict[str, MarketQuote]:
        """Lấy top symbol (USDT) theo volume 24h từ Binance Spot. Dùng cho discovery token có dấu hiệu."""
        try:
            with httpx.Client(timeout=30) as client:
                r = client.get(BINANCE_SPOT_TICKER_24HR)
                r.raise_for_status()
                arr = r.json()
        except Exception:
            return {}
        out: list[tuple[str, MarketQuote]] = []
        for item in arr:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = _symbol_from_binance(sym)
            try:
                quote_vol = float(item.get("quoteVolume", 0))
                if quote_vol < min_volume_usd:
                    continue
                out.append((
                    base,
                    MarketQuote(
                        symbol=base,
                        price=float(item.get("lastPrice", 0)),
                        percent_change_24h=float(item.get("priceChangePercent", 0)),
                        volume_24h=quote_vol,
                        market_cap=0.0,
                    ),
                ))
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda x: x[1].volume_24h, reverse=True)
        return dict(out[:top_n])


class CoinMarketCapClient:
    _cache: dict[tuple, tuple[float, dict[str, "MarketQuote"]]] = {}

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.cmc_api_key

    def get_quotes(self, symbols: Iterable[str]) -> dict[str, MarketQuote]:
        symbols_tuple = tuple(sorted(set(symbols)))
        now = time.monotonic()
        if symbols_tuple in CoinMarketCapClient._cache:
            cached_at, cached_result = CoinMarketCapClient._cache[symbols_tuple]
            if now - cached_at < CACHE_TTL_SECONDS:
                return cached_result
        symbols_csv = ",".join(symbols_tuple)
        if not self.api_key:
            result = {
                s: MarketQuote(
                    symbol=s,
                    price=100.0,
                    percent_change_24h=0.0,
                    volume_24h=1_000_000,
                    market_cap=1_000_000_000,
                )
                for s in symbols
            }
            return result
        params = {"symbol": symbols_csv, "convert": "USD"}
        headers = {"X-CMC_PRO_API_KEY": self.api_key}
        with httpx.Client(timeout=20) as client:
            response = client.get(CMC_QUOTES_URL, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()["data"]
        result: dict[str, MarketQuote] = {}
        for symbol, value in payload.items():
            usd = value["quote"]["USD"]
            result[symbol] = MarketQuote(
                symbol=symbol,
                price=float(usd["price"]),
                percent_change_24h=float(usd.get("percent_change_24h", 0.0)),
                volume_24h=float(usd.get("volume_24h", 0.0)),
                market_cap=float(usd.get("market_cap", 0.0)),
            )
        CoinMarketCapClient._cache[symbols_tuple] = (now, result)
        return result


def get_market_client():
    """Trả về client lấy giá theo cấu hình PRICE_SOURCE (cmc | binance)."""
    if (getattr(settings, "price_source", "cmc") or "cmc").strip().lower() == "binance":
        return BinanceClient()
    return CoinMarketCapClient()


def get_klines_1h(symbol: str, limit: int = 5) -> list[Kline1h]:
    """Lấy nến 1h cho symbol (Binance Futures rồi Spot). Dùng cho phân tích tình huống."""
    return BinanceClient().get_klines_1h(symbol, limit)


def get_klines_5m(symbol: str, limit: int = 12) -> list[Kline1h]:
    """Lấy nến 5m cho symbol. Dùng kèm nến 1h trong phân tích tình huống. Cache 30s."""
    return BinanceClient().get_klines_5m(symbol, limit)


def get_klines_4h(symbol: str, limit: int = 6) -> list[Kline1h]:
    """Lấy nến 4h cho xu hướng (trend filter). Cache 5 phút để tiết kiệm API."""
    return BinanceClient().get_klines_4h(symbol, limit)


def get_top_symbols_quotes_by_volume(
    top_n: int = 100,
    min_volume_usd: float = 500_000,
) -> dict[str, MarketQuote]:
    """
    Public helper cho discovery scanner.
    Dùng BinanceClient để lấy top symbol USDT theo quoteVolume 24h.
    """
    return BinanceClient().get_top_quotes_by_volume(
        top_n=int(top_n),
        min_volume_usd=float(min_volume_usd),
    )


def _fetch_futures_24h_for_symbols(symbols_list: list[str]) -> dict[str, tuple[float, float]]:
    """Lấy priceChangePercent và quoteVolume 24h từ fapi/v1/ticker/24hr. Cache 60s. Trả về {symbol: (pct, volume)}."""
    global _FUTURES_24H_CACHE
    now = time.monotonic()
    out: dict[str, tuple[float, float]] = {}
    to_fetch = [s for s in symbols_list if s not in _FUTURES_24H_CACHE or _FUTURES_24H_CACHE[s][0] < now]
    if to_fetch:
        try:
            with httpx.Client(timeout=15) as client:
                for sym in to_fetch:
                    try:
                        r = client.get(BINANCE_FUTURES_TICKER_24HR, params={"symbol": _binance_symbol(sym)})
                        r.raise_for_status()
                        data = r.json()
                        pct = float(data.get("priceChangePercent", 0) or 0)
                        vol = float(data.get("quoteVolume", 0) or 0)
                        _FUTURES_24H_CACHE[sym] = (now + _FUTURES_24H_CACHE_TTL, pct, vol)
                    except Exception:
                        _FUTURES_24H_CACHE[sym] = (now + _FUTURES_24H_CACHE_TTL, 0.0, 0.0)
        except Exception:
            pass
    for sym in symbols_list:
        if sym in _FUTURES_24H_CACHE:
            _, pct, vol = _FUTURES_24H_CACHE[sym]
            out[sym] = (pct, vol)
    return out


def get_quotes_with_fallback(symbols: Iterable[str]) -> dict[str, MarketQuote]:
    """
    Lấy giá từ nguồn chính (theo PRICE_SOURCE). Chỉ fallback khi nguồn chính là CMC
    (symbol thiếu thì thử Binance). Khi PRICE_SOURCE=binance thì không gọi CMC.
    """
    symbols_list = list(sorted(set(symbols)))
    if not symbols_list:
        return {}
    # Bước 2 (document/request): Binance Futures — ưu tiên WebSocket (mark price), thiếu thì REST.
    if _use_futures_only_for_quotes():
        try:
            from core.market_data.binance_futures_ws import get_binance_futures_ws_manager
            ws_manager = get_binance_futures_ws_manager()
            result = ws_manager.get_quotes(symbols_list)
            if result:
                # WS chỉ có giá; regime cần change_24h + volume_24h → bổ sung từ REST 24h (cache 60s)
                stats_24h = _fetch_futures_24h_for_symbols(list(result.keys()))
                for sym, q in list(result.items()):
                    if sym in stats_24h:
                        pct, vol = stats_24h[sym]
                        result[sym] = MarketQuote(
                            symbol=q.symbol,
                            price=q.price,
                            percent_change_24h=pct,
                            volume_24h=vol,
                            market_cap=q.market_cap,
                        )
            missing = [s for s in symbols_list if s not in result]
            if not missing:
                return result
            primary = get_market_client()
            rest_fallback = primary.get_quotes(missing)
            return {**result, **rest_fallback}
        except Exception:
            pass
    primary = get_market_client()
    result = primary.get_quotes(symbols_list)
    missing = [s for s in symbols_list if s not in result]
    if not missing:
        return result
    if isinstance(primary, BinanceClient):
        return result
    try:
        fallback = BinanceClient().get_quotes(missing)
        result = {**result, **fallback}
    except Exception:
        pass
    return result


__all__ = [
    "MarketQuote",
    "Kline1h",
    "CoinMarketCapClient",
    "BinanceClient",
    "get_market_client",
    "get_quotes_with_fallback",
    "get_klines_1h",
    "get_klines_5m",
    "get_klines_4h",
    "get_top_symbols_quotes_by_volume",
]
