"""
Thực thi lệnh thật trên Binance Futures (USD-M).
Đặt lệnh MARKET mở vị thế, kèm TAKE_PROFIT_MARKET và STOP_MARKET để tối đa lợi nhuận và giới hạn thua lỗ.
"""
from __future__ import annotations

import logging
import hashlib
import hmac
import time
from decimal import Decimal
from datetime import datetime
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from core.config import settings
from core.portfolio.models import Portfolio, Position, Trade
from core.risk.daily_r import MIN_RISK_USD_FOR_R_AGGREGATION
from core.risk.trade_r_metrics import attach_open_trade_risk_fields
from core.portfolio.capital_split import normalize_bucket
from core.profit.thesis_profiles import apply_thesis_fields_to_position
from core.strategies.base import StrategySignal
from core.execution.binance_user_stream import (
    RECONCILE_INTERVAL,
    ensure_user_stream_started,
    get_user_stream_singleton,
)

# Cache filters per symbol: base_url -> symbol -> (expiry_monotonic, data). TTL 24h — không fetch lúc mở từng lệnh (document/request).
_exchange_info_cache: dict[str, dict[str, tuple[float, dict]]] = {}
_EXCHANGE_INFO_TTL = 86400.0  # 24 giờ

# Throttle cập nhật TP/SL: tối đa 1 lần mỗi N giây per (symbol, side) — tránh spam sàn và -4130.
_LAST_SL_TP_UPDATE_INTERVAL = 120  # giây
_last_sl_tp_update: dict[tuple[str, str], float] = {}

# document/request: đồng bộ thời gian 1 lần rồi dùng offset — không gọi /fapi/v1/time trước mọi request.
_SERVER_TIME_OFFSET_MS: dict[str, tuple[float, int]] = {}  # base_url -> (expiry_monotonic, offset_ms)
_SERVER_TIME_OFFSET_TTL = 600.0  # 10 phút

# positionSide/dual là cấu hình tài khoản, không cần check mỗi cycle (document/request).
_HEDGE_MODE_CACHE: dict[str, tuple[float, bool]] = {}  # base_url -> (expiry_monotonic, dual_side)
_HEDGE_MODE_CACHE_TTL = 1800.0  # 30 phút

# balance: TTL 60s; invalidate khi open/close/reduce (document/request).
_BALANCE_CACHE: dict[str, tuple[float, dict]] = {}  # base_url -> (expiry_monotonic, balance_info)
_BALANCE_CACHE_TTL = 60.0  # giây — refresh sau 60s hoặc ngay khi có trade (open_position/close_position/reduce_position pop cache)

# allAlgoOrders: cache 5s trong cùng flow update TP/SL để tránh gọi nhiều lần (document/request).
_ALGO_ORDERS_CACHE: dict[tuple[str, str, str], tuple[float, list]] = {}  # (base_url, symbol_b, position_side) -> (expiry, list)
_ALGO_ORDERS_CACHE_TTL = 5.0

_logger = logging.getLogger(__name__)


def _binance_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s.endswith("USDT") or s.endswith("BUSD"):
        return s
    return f"{s}USDT"


def _round_quantity_to_lot_size(qty: float, min_qty: float, step_size: str) -> tuple[float, str]:
    """
    Làm tròn quantity theo đúng rule Binance: (quantity - minQty) % stepSize == 0.
    Ref: https://dev.binance.vision/t/status-400-error-code-1013-filter-failure-lot-size/18377
    Trả về (float, str) để format chuỗi đúng số chữ số thập phân theo stepSize.
    """
    if not step_size or float(step_size) <= 0:
        return (float(qty), f"{qty:.8f}".rstrip("0").rstrip("."))
    step = Decimal(step_size)
    min_d = Decimal(str(min_qty))
    q = Decimal(str(max(float(qty), min_qty)))
    # valid = minQty + floor((q - minQty) / stepSize) * stepSize
    valid = min_d + ((q - min_d) // step) * step
    valid_f = float(valid)
    # Số chữ số thập phân = từ stepSize (vd. "0.01" -> 2, "1" -> 0)
    step_str = step_size.rstrip("0").rstrip(".")
    if "." in step_str:
        dec = len(step_str.split(".")[-1])
        out_str = f"{valid_f:.{dec}f}"
    else:
        out_str = str(int(valid_f))
    return (valid_f, out_str)


def try_exchange_lot_for_executor(executor: object, symbol: str) -> dict | None:
    """
    If executor is Binance Futures, return LOT_SIZE / minNotional dict for dashboard + sizing diagnostics.
    Otherwise None (paper / other backends).
    """
    if type(executor).__name__ != "BinanceFuturesExecutor":
        return None
    try:
        symbol_b = _binance_symbol(symbol)
        base_url = getattr(executor, "base_url", "") or ""
        return _get_lot_size(base_url, symbol_b)
    except Exception:
        return None


def _get_lot_size(base_url: str, symbol_b: str) -> dict:
    """Lấy LOT_SIZE + PRICE_FILTER (tickSize) + minNotional. Cache 24h theo (base_url, symbol) — không fetch mỗi lệnh."""
    now = time.monotonic()
    if base_url not in _exchange_info_cache:
        _exchange_info_cache[base_url] = {}
    if symbol_b in _exchange_info_cache[base_url]:
        exp, out = _exchange_info_cache[base_url][symbol_b]
        if now < exp:
            return out
    default = {"minQty": 0.001, "maxQty": 1000000.0, "stepSize": "0.01", "minNotional": 0.0, "quantityPrecision": 2, "tickSize": "0.00001", "minPrice": 0.0, "maxPrice": 0.0}
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{base_url}/fapi/v1/exchangeInfo")
            r.raise_for_status()
            data = r.json()
        for s in data.get("symbols", []):
            if s.get("symbol") != symbol_b:
                continue
            out = {"minQty": 0.001, "maxQty": 1000000.0, "stepSize": "0.01", "minNotional": 0.0, "quantityPrecision": 2, "tickSize": "0.00001", "minPrice": 0.0, "maxPrice": 0.0}
            out["quantityPrecision"] = int(s.get("quantityPrecision", 2))
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    out["minQty"] = float(f.get("minQty", 0.001))
                    out["maxQty"] = float(f.get("maxQty", 1000000))
                    out["stepSize"] = str(f.get("stepSize", "0.01"))
                elif f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
                    out["minNotional"] = float(f.get("minNotional", f.get("notional", 0)) or 0)
                elif f.get("filterType") == "PRICE_FILTER":
                    out["tickSize"] = str(f.get("tickSize", "0.00001"))
                    out["minPrice"] = float(f.get("minPrice", 0) or 0)
                    out["maxPrice"] = float(f.get("maxPrice", 0) or 0)
            _exchange_info_cache[base_url][symbol_b] = (now + _EXCHANGE_INFO_TTL, out)
            return out
    except Exception:
        pass
    _exchange_info_cache[base_url][symbol_b] = (now + _EXCHANGE_INFO_TTL, default)
    return default


def _round_stop_price(price: float, tick_size: str) -> str:
    """Làm tròn giá theo PRICE_FILTER tickSize: (price - minPrice) % tickSize == 0. Trả về chuỗi phù hợp."""
    if not tick_size or float(tick_size) <= 0:
        return str(round(price, 8))
    tick = Decimal(tick_size)
    p = Decimal(str(price))
    # Binance: (price - minPrice) % tickSize == 0. Với minPrice=0: price % tickSize == 0
    rounded = (p // tick) * tick
    return str(rounded)


def _quantity_to_string(qty: float, lot: dict) -> str:
    """Ép quantity vào [minQty, maxQty], làm tròn theo rule Binance (quantity - minQty) % stepSize == 0."""
    step_size = lot.get("stepSize", "0.01")
    min_qty = lot.get("minQty", 0.001)
    max_qty = lot.get("maxQty", 1000000.0)
    q = max(min_qty, min(max_qty, float(qty)))
    _, s = _round_quantity_to_lot_size(q, min_qty, step_size)
    if float(s) < min_qty:
        _, s = _round_quantity_to_lot_size(min_qty, min_qty, step_size)
    return s


def _is_hedge_mode(executor: "BinanceFuturesExecutor") -> bool:
    """Binance Futures: Hedge Mode (dualSidePosition=true) cần positionSide LONG/SHORT; One-Way cần BOTH. Cache 30 phút (document/request)."""
    base = getattr(executor, "base_url", "") or ""
    now = time.monotonic()
    if base in _HEDGE_MODE_CACHE:
        exp, val = _HEDGE_MODE_CACHE[base]
        if now < exp:
            return val
    try:
        data = executor._signed_request("GET", "/fapi/v1/positionSide/dual", {})
        val = data.get("dualSidePosition") is True
        _HEDGE_MODE_CACHE[base] = (now + _HEDGE_MODE_CACHE_TTL, val)
        return val
    except Exception:
        return False


class BinanceFuturesExecutor:
    """Đặt lệnh thật trên Binance USD-M Futures: MARKET + TP + SL. User Data Stream cho balance/positions (reconcile 2–5 phút)."""

    def __init__(self, use_testnet: bool | None = None):
        self.api_key = (settings.binance_api_key or "").strip()
        self.secret = (settings.binance_api_secret or "").strip()
        testnet = use_testnet if use_testnet is not None else settings.binance_futures_testnet
        if testnet:
            self.base_url = "https://testnet.binancefuture.com"
        else:
            self.base_url = "https://fapi.binance.com"
        self._hedge_mode: bool | None = None  # cache dualSidePosition
        # User stream là singleton process-wide (binance_user_stream.ensure_user_stream_started); không lưu ref tại instance.

    @staticmethod
    def test_connection(use_testnet: bool | None = None) -> tuple[bool, str]:
        """
        Kiểm tra kết nối API (API key + quyền). Trả về (thành_công, thông_báo).
        Dùng khi chưa bật đánh thật để xác nhận .env đúng.
        """
        from core.config import settings
        api_key = (settings.binance_api_key or "").strip()
        secret = (settings.binance_api_secret or "").strip()
        if not api_key or not secret:
            return (False, "Chưa cấu hình BINANCE_API_KEY hoặc BINANCE_API_SECRET trong .env.")
        testnet = use_testnet if use_testnet is not None else getattr(settings, "binance_futures_testnet", True)
        base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        try:
            executor = BinanceFuturesExecutor(use_testnet=testnet)
            executor._signed_request("GET", "/fapi/v2/account", {})
            env_name = "Testnet" if testnet else "Mainnet"
            return (True, f"Kết nối {env_name} OK. API key có quyền Futures.")
        except Exception as e:
            err = str(e).strip()
            if "401" in err or "Unauthorized" in err:
                return (False, "401 Unauthorized: Sai API key/secret hoặc key chưa bật quyền Futures.")
            return (False, err or repr(e))

    def _get_server_time(self) -> int:
        """Server time (ms). Cache offset 10 phút — không gọi /time trước mọi request (document/request)."""
        base = getattr(self, "base_url", "") or ""
        now_mono = time.monotonic()
        now_local_ms = int(time.time() * 1000)
        if base in _SERVER_TIME_OFFSET_MS:
            exp, offset_ms = _SERVER_TIME_OFFSET_MS[base]
            if now_mono < exp:
                return now_local_ms + offset_ms
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{self.base_url}/fapi/v1/time")
                r.raise_for_status()
                server_ms = int(r.json().get("serverTime", now_local_ms))
                offset_ms = server_ms - now_local_ms
                _SERVER_TIME_OFFSET_MS[base] = (now_mono + _SERVER_TIME_OFFSET_TTL, offset_ms)
                return server_ms
        except Exception:
            return now_local_ms

    def _signed_request(self, method: str, path: str, params: dict) -> dict:
        if not self.api_key or not self.secret:
            raise ValueError("BINANCE_API_KEY và BINANCE_API_SECRET bắt buộc khi bật lệnh thật Futures.")
        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = self._get_server_time()
        params["recvWindow"] = settings.binance_recv_window
        query = urlencode(params)
        sig = hmac.new(
            self.secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30) as client:
            if method == "GET":
                r = client.get(url, params=params, headers={"X-MBX-APIKEY": self.api_key})
            elif method == "DELETE":
                r = client.delete(url, params=params, headers={"X-MBX-APIKEY": self.api_key})
            else:
                r = client.post(url, params=params, headers={"X-MBX-APIKEY": self.api_key})
            # Đọc body trước, không gọi r.raise_for_status() để luôn lấy được msg từ Binance khi 4xx
            body_text = r.text
            if r.status_code != 200:
                try:
                    import json as _json
                    body = _json.loads(body_text)
                    msg = body.get("msg", body.get("message", body_text or f"{r.status_code}"))
                    code = body.get("code", "")
                    if code:
                        msg = f"code={code} {msg}"
                except Exception:
                    msg = body_text or f"HTTP {r.status_code}"
                raise RuntimeError(f"Binance API {r.status_code}: {msg}")
            return r.json()

    def open_position(
        self,
        db: Session,
        portfolio_id: int,
        signal: StrategySignal,
        size_usd: float,
    ) -> Position:
        """Đặt lệnh thật: đặt đòn bẩy → MARKET mở vị thế + TAKE_PROFIT_MARKET + STOP_MARKET."""
        symbol_b = _binance_symbol(signal.symbol)
        side = "BUY" if signal.side == "long" else "SELL"
        leverage = max(1, min(125, getattr(settings, "binance_futures_leverage", 20)))

        # 0) Đặt đòn bẩy cho symbol (Binance yêu cầu đặt trước khi đặt lệnh)
        self._signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol_b, "leverage": leverage})

        # 0b) Position side: Hedge Mode cần LONG/SHORT, One-Way cần BOTH (tránh lỗi -4061)
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        position_side = ("LONG" if signal.side == "long" else "SHORT") if self._hedge_mode else "BOTH"

        # Quantity theo LOT_SIZE (minQty, maxQty, stepSize) + minNotional
        lot = _get_lot_size(self.base_url, symbol_b)
        quantity = size_usd / signal.entry_price
        min_notional = lot.get("minNotional") or 0
        if min_notional > 0 and quantity * signal.entry_price < min_notional:
            quantity = min_notional / signal.entry_price
        qty_str = _quantity_to_string(quantity, lot)
        if float(qty_str) < lot["minQty"]:
            raise ValueError(
                f"Quantity {qty_str} < minQty {lot['minQty']} cho {symbol_b}. "
                f"size_usd={size_usd:.2f}, entry={signal.entry_price} → cần tăng risk hoặc vốn."
            )

        # 1) Lệnh MARKET mở vị thế
        order_params = {
            "symbol": symbol_b,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
            "positionSide": position_side,
            "newOrderRespType": "RESULT",
        }
        try:
            order_res = self._signed_request("POST", "/fapi/v1/order", order_params)
        except RuntimeError as e:
            if "400" in str(e):
                cache_key = self.base_url
                if cache_key in _exchange_info_cache and symbol_b in _exchange_info_cache[cache_key]:
                    del _exchange_info_cache[cache_key][symbol_b]
            raise
        # Lấy giá fill thực tế nếu có
        avg_price = float(order_res.get("avgPrice") or order_res.get("price") or signal.entry_price)
        exec_qty = float(order_res.get("executedQty") or order_res.get("origQty") or qty_str)
        exec_qty_str = _quantity_to_string(exec_qty, lot)
        order_id = order_res.get("orderId", "")
        quantity_saved = float(exec_qty_str) if exec_qty_str else exec_qty

        # Ghi vào DB ngay sau khi lệnh MARKET thành công → "Lệnh đang mở" luôn đồng bộ với Binance
        bucket = normalize_bucket(getattr(signal, "capital_bucket", None))
        position = Position(
            portfolio_id=portfolio_id,
            symbol=signal.symbol,
            side=signal.side,
            strategy_name=signal.strategy_name,
            entry_price=avg_price,
            quantity=quantity_saved,
            stop_loss=signal.stop_loss,
            initial_stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            opened_at=datetime.utcnow(),
            is_open=True,
            scale_in_count=0,
            initial_entry_price=avg_price,
            entry_regime=(getattr(signal, "regime", None) or None),
            capital_bucket=bucket,
        )
        apply_thesis_fields_to_position(position, signal)
        db.add(position)
        db.flush()
        use_trailing = (
            getattr(settings, "binance_use_trailing_stop", False)
            and (
                not getattr(settings, "binance_trailing_only_momentum", True)
                or (getattr(signal, "regime", "") == "high_momentum")
            )
        )
        trade_note_parts = [f"Binance Futures #{order_id} | TP={signal.take_profit} SL={signal.stop_loss}"]
        if use_trailing:
            trade_note_parts.append(" Trailing Stop thay TP cố định.")

        # Đợi ngắn để sàn ghi nhận vị thế trước khi đặt TP/SL/Trailing (tránh lỗi do position chưa có).
        time.sleep(0.5)

        # Hủy sẵn TP/SL/Trailing cũ cho symbol+side (khi mở thêm lệnh cùng symbol, sàn chỉ cho phép 1 bộ TP/SL closePosition → tránh -4130).
        cancelled = self._cancel_all_algo_for_position(signal.symbol, position_side)
        if cancelled > 0:
            time.sleep(1.0)

        close_side = "SELL" if signal.side == "long" else "BUY"
        tick_size = lot.get("tickSize", "0.00001")
        stop_price_sl = _round_stop_price(signal.stop_loss, tick_size)

        # 2) Chốt lời và 3) Cắt lỗ — dùng Algo Order API (POST /fapi/v1/algoOrder) vì -4120 yêu cầu endpoint Algo cho STOP_MARKET/TAKE_PROFIT_MARKET/TRAILING_STOP_MARKET.
        def _place_algo(side: str, order_type: str, trigger_price: str | None, quantity_str: str | None, callback_rate: str | None) -> str | None:
            p = {"algoType": "CONDITIONAL", "symbol": symbol_b, "side": side, "type": order_type}
            if self._hedge_mode:
                p["positionSide"] = position_side
            if trigger_price is not None:
                p["triggerPrice"] = trigger_price
            if quantity_str is not None:
                p["quantity"] = quantity_str
            if callback_rate is not None:
                p["callbackRate"] = callback_rate
            if order_type in ("TAKE_PROFIT_MARKET", "STOP_MARKET") and trigger_price is not None:
                p["closePosition"] = "true"
            try:
                r = self._signed_request("POST", "/fapi/v1/algoOrder", p)
                return str(r.get("algoId", ""))
            except Exception as e:
                err_str = str(e)
                if "-4130" in err_str or "4130" in err_str:
                    self._cancel_all_algo_for_position(signal.symbol, position_side)
                    time.sleep(1.0)
                    try:
                        r = self._signed_request("POST", "/fapi/v1/algoOrder", p)
                        return str(r.get("algoId", ""))
                    except Exception as e2:
                        print(f"[BinanceFutures] Algo {order_type} failed for {symbol_b}: {e2}")
                        return None
                print(f"[BinanceFutures] Algo {order_type} failed for {symbol_b}: {e}")
                return None

        if use_trailing:
            callback_pct = max(0.1, min(10.0, getattr(settings, "binance_trailing_callback_pct", 1.5)))
            trail_oid = _place_algo(close_side, "TRAILING_STOP_MARKET", None, exec_qty_str, str(callback_pct))
            if trail_oid:
                print(f"[BinanceFutures] TRAILING_STOP_MARKET placed for {symbol_b} algoId={trail_oid} callbackRate={callback_pct}%")
            else:
                trade_note_parts.append(" Trailing order failed.")
        else:
            stop_price_tp = _round_stop_price(signal.take_profit, tick_size)
            tp_oid = _place_algo(close_side, "TAKE_PROFIT_MARKET", stop_price_tp, None, None)
            if tp_oid:
                print(f"[BinanceFutures] TAKE_PROFIT_MARKET placed for {symbol_b} algoId={tp_oid} triggerPrice={stop_price_tp}")
            else:
                trade_note_parts.append(" TP order failed.")

        sl_oid = _place_algo(close_side, "STOP_MARKET", stop_price_sl, None, None)
        if sl_oid:
            print(f"[BinanceFutures] STOP_MARKET placed for {symbol_b} algoId={sl_oid} triggerPrice={stop_price_sl}")
        else:
            trade_note_parts.append(" SL order failed.")

        trade = Trade(
            portfolio_id=portfolio_id,
            position_id=position.id,
            symbol=signal.symbol,
            side=signal.side,
            strategy_name=signal.strategy_name,
            action="open",
            price=avg_price,
            quantity=quantity_saved,
            fee_usd=0.0,
            pnl_usd=0.0,
            note="".join(trade_note_parts),
            capital_bucket=bucket,
        )
        db.add(trade)
        db.flush()
        attach_open_trade_risk_fields(
            trade,
            entry_price=avg_price,
            quantity=quantity_saved,
            signal=signal,
        )
        db.flush()
        base = getattr(self, "base_url", "") or ""
        _BALANCE_CACHE.pop(base, None)  # refresh balance sau khi mở lệnh
        return position

    def _live_position_quantity(self, position: Position) -> float | None:
        """REST positionAmt for this symbol/side — tránh reduceOnly 400 do qty > thực tế sau partial/fee drift."""
        symbol_b = _binance_symbol(position.symbol)
        try:
            data = self._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol_b})
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        want_long = (position.side or "").lower() == "long"
        for item in data:
            if (item.get("symbol") or "") != symbol_b:
                continue
            try:
                amt = float(item.get("positionAmt", 0) or 0)
            except (TypeError, ValueError):
                continue
            if abs(amt) < 1e-12:
                continue
            ps = str(item.get("positionSide") or "BOTH").upper()
            if self._hedge_mode:
                if want_long and ps == "LONG" and amt > 0:
                    return abs(amt)
                if not want_long and ps == "SHORT" and amt < 0:
                    return abs(amt)
            else:
                if (amt > 0 and want_long) or (amt < 0 and not want_long):
                    return abs(amt)
        return None

    def close_position(
        self, db: Session, position: Position, exit_price: float, note: str = ""
    ) -> Trade:
        """Đóng vị thế trên Binance bằng lệnh MARKET (reduceOnly), rồi cập nhật DB."""
        symbol_b = _binance_symbol(position.symbol)
        close_side = "SELL" if position.side == "long" else "BUY"
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        position_side = ("LONG" if position.side == "long" else "SHORT") if self._hedge_mode else "BOTH"
        lot = _get_lot_size(self.base_url, symbol_b)
        live_qty = self._live_position_quantity(position)
        want = float(position.quantity or 0)
        if live_qty is not None:
            want = min(want, live_qty)
        if want <= 0:
            raise RuntimeError(
                f"No closable quantity for {position.symbol} (db_qty={position.quantity}, live_qty={live_qty})"
            )
        qty_str = _quantity_to_string(want, lot)
        qf = float(qty_str)
        min_q = float(lot.get("minQty", 0.001) or 0.001)
        if qf + 1e-12 < min_q:
            raise RuntimeError(f"Close qty {qty_str} below minQty {min_q} for {symbol_b}")
        try:
            res = self._signed_request("POST", "/fapi/v1/order", {
                "symbol": symbol_b,
                "side": close_side,
                "type": "MARKET",
                "quantity": qty_str,
                "reduceOnly": "true",
                "positionSide": position_side,
                "newOrderRespType": "RESULT",
            })
            exit_px = float(res.get("avgPrice") or res.get("price") or exit_price)
            exec_qty = float(res.get("executedQty") or qty_str)
            exec_qty = float(_quantity_to_string(exec_qty, lot))
        except Exception as e:
            _logger.warning("Binance close_position failed symbol=%s: %s", position.symbol, e)
            raise
        _BALANCE_CACHE.pop(getattr(self, "base_url", "") or "", None)
        position.quantity = exec_qty
        from core.execution.simulator import PaperExecutionSimulator

        sim = PaperExecutionSimulator()
        return sim.close_position(db, position, exit_px, note or "Đóng (Futures)")

    def reduce_position(
        self,
        db: Session,
        position: Position,
        reduce_quantity: float,
        exit_price: float,
        note: str = "",
    ) -> Trade | None:
        """Chốt một phần vị thế (partial TP) trên Binance: lệnh MARKET reduceOnly, cập nhật DB."""
        if reduce_quantity <= 0:
            return None
        symbol_b = _binance_symbol(position.symbol)
        close_side = "SELL" if position.side == "long" else "BUY"
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        position_side = ("LONG" if position.side == "long" else "SHORT") if self._hedge_mode else "BOTH"
        lot = _get_lot_size(self.base_url, symbol_b)
        min_qty = float(lot.get("minQty", 0.001) or 0.001)
        live_qty = self._live_position_quantity(position)
        cap = float(position.quantity or 0)
        if live_qty is not None:
            cap = min(cap, live_qty)
        if cap <= 0:
            return None
        rq = min(float(reduce_quantity), cap)
        if rq <= 0:
            return None
        qty_str = _quantity_to_string(rq, lot)
        q_round = float(qty_str)
        if q_round <= 0 or q_round + 1e-12 < min_qty:
            return None
        if q_round >= cap - 1e-12:
            return None
        try:
            res = self._signed_request("POST", "/fapi/v1/order", {
                "symbol": symbol_b,
                "side": close_side,
                "type": "MARKET",
                "quantity": qty_str,
                "reduceOnly": "true",
                "positionSide": position_side,
                "newOrderRespType": "RESULT",
            })
            exit_price = float(res.get("avgPrice") or res.get("price") or exit_price)
            exec_qty = float(res.get("executedQty") or qty_str)
            exec_qty = float(_quantity_to_string(exec_qty, lot))
        except Exception as e:
            _logger.warning("Binance reduce_position failed symbol=%s: %s", position.symbol, e)
            return None
        position.quantity = round(position.quantity - exec_qty, 8)
        direction = 1 if position.side == "long" else -1
        gross_pnl = (exit_price - position.entry_price) * exec_qty * direction
        sl_for_r = getattr(position, "initial_stop_loss", None)
        if sl_for_r is None:
            sl_for_r = position.stop_loss
        risk_usd = abs(position.entry_price - sl_for_r) * exec_qty if sl_for_r is not None else None
        if risk_usd is not None and float(risk_usd) < MIN_RISK_USD_FOR_R_AGGREGATION:
            risk_usd = None
        bpart = normalize_bucket(getattr(position, "capital_bucket", None))
        trade = Trade(
            portfolio_id=position.portfolio_id,
            position_id=position.id,
            symbol=position.symbol,
            side=position.side,
            strategy_name=position.strategy_name,
            action="partial_close",
            price=exit_price,
            quantity=exec_qty,
            fee_usd=0.0,
            pnl_usd=round(gross_pnl, 4),
            risk_usd=round(risk_usd, 4) if risk_usd is not None else None,
            note=note or "Partial TP (Binance reduceOnly)",
            capital_bucket=bpart,
        )
        db.add(trade)
        db.flush()
        _BALANCE_CACHE.pop(getattr(self, "base_url", "") or "", None)
        return trade

    def add_to_position(
        self,
        db: Session,
        position: Position,
        add_qty: float,
        current_price: float,
        signal: StrategySignal,
    ) -> Trade | None:
        """
        Scale-in: gui them lenh cung chieu de tang size vi the (Binance 1 position/symbol/side).
        Cap nhat position.quantity, entry_price (avg moi), scale_in_count; ghi Trade action='scale_in'.
        """
        if add_qty <= 0:
            return None
        symbol_b = _binance_symbol(position.symbol)
        side = "BUY" if position.side == "long" else "SELL"
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        position_side = ("LONG" if position.side == "long" else "SHORT") if self._hedge_mode else "BOTH"
        lot = _get_lot_size(self.base_url, symbol_b)
        qty_str = _quantity_to_string(add_qty, lot)
        if float(qty_str) < lot["minQty"]:
            return None
        try:
            order_res = self._signed_request("POST", "/fapi/v1/order", {
                "symbol": symbol_b,
                "side": side,
                "type": "MARKET",
                "quantity": qty_str,
                "positionSide": position_side,
                "newOrderRespType": "RESULT",
            })
        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.warning("add_to_position order failed: %s", e)
            return None
        avg_price = float(order_res.get("avgPrice") or order_res.get("price") or current_price)
        exec_qty = float(order_res.get("executedQty") or order_res.get("origQty") or qty_str)
        exec_qty = float(_quantity_to_string(exec_qty, lot))
        prev_qty = float(position.quantity or 0)
        prev_entry = float(position.entry_price or 0)
        new_qty = prev_qty + exec_qty
        new_entry = (prev_entry * prev_qty + avg_price * exec_qty) / new_qty if new_qty > 0 else prev_entry
        position.quantity = round(new_qty, 8)
        position.entry_price = round(new_entry, 8)
        position.scale_in_count = (getattr(position, "scale_in_count", 0) or 0) + 1
        if getattr(position, "initial_entry_price", None) is None:
            position.initial_entry_price = prev_entry
        bscale = normalize_bucket(getattr(position, "capital_bucket", None))
        trade = Trade(
            portfolio_id=position.portfolio_id,
            position_id=position.id,
            symbol=position.symbol,
            side=position.side,
            strategy_name=signal.strategy_name,
            action="scale_in",
            price=avg_price,
            quantity=exec_qty,
            fee_usd=0.0,
            pnl_usd=0.0,
            note=f"Scale-in Binance add qty={exec_qty} avg={avg_price}",
            capital_bucket=bscale,
        )
        db.add(trade)
        db.flush()
        return trade

    def get_open_orders(self, symbol: str, position_side: str | None = None) -> list[dict]:
        """Lấy lệnh thường (không bao gồm Algo). Dùng get_open_algo_orders cho TP/SL/Trailing."""
        symbol_b = _binance_symbol(symbol)
        try:
            data = self._signed_request("GET", "/fapi/v1/openOrders", {"symbol": symbol_b})
        except Exception:
            return []
        out = list(data) if isinstance(data, list) else []
        if position_side is not None:
            out = [o for o in out if o.get("positionSide") == position_side]
        return out

    def get_open_algo_orders(self, symbol: str, position_side: str | None = None) -> list[dict]:
        """Lấy algo orders đang mở (TP/SL/Trailing). Cache 5s để cùng flow update không gọi lặp."""
        symbol_b = _binance_symbol(symbol)
        ps = position_side or ""
        key = (self.base_url, symbol_b, ps)
        now = time.monotonic()
        if key in _ALGO_ORDERS_CACHE:
            exp, out = _ALGO_ORDERS_CACHE[key]
            if now < exp:
                return list(out)
        try:
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - 7 * 24 * 3600 * 1000
            data = self._signed_request("GET", "/fapi/v1/allAlgoOrders", {
                "symbol": symbol_b,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 100,
            })
        except Exception:
            return []
        out = list(data) if isinstance(data, list) else []
        algo_types = ("STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET", "STOP", "TAKE_PROFIT")
        out = [o for o in out if o.get("orderType") in algo_types and o.get("algoStatus") not in ("CANCELED", "EXPIRED", "TRIGGERED", "FINISHED")]
        if position_side is not None:
            out = [o for o in out if o.get("positionSide") == position_side]
        _ALGO_ORDERS_CACHE[key] = (now + _ALGO_ORDERS_CACHE_TTL, out)
        return out

    def _cancel_order(self, symbol: str, order_id: int) -> bool:
        """Hủy lệnh thường theo orderId."""
        symbol_b = _binance_symbol(symbol)
        try:
            self._signed_request("DELETE", "/fapi/v1/order", {"symbol": symbol_b, "orderId": order_id})
            return True
        except Exception as e:
            print(f"[BinanceFutures] Cancel order {order_id} failed: {e}")
            return False

    def _cancel_algo_order(self, algo_id: int) -> bool:
        """Hủy algo order theo algoId. Trả về True nếu thành công."""
        try:
            self._signed_request("DELETE", "/fapi/v1/algoOrder", {"algoId": algo_id})
            return True
        except Exception as e:
            print(f"[BinanceFutures] Cancel algo order {algo_id} failed: {e}")
            return False

    def get_recent_realized_pnl_for_symbol(self, symbol: str, limit: int = 20) -> list[dict]:
        """
        Lấy lịch sử REALIZED_PNL gần nhất cho symbol (để ghi đúng PnL khi sync vị thế đã đóng trên sàn).
        Trả về list [{"income": float, "time": int}, ...] sắp xếp mới nhất trước.
        """
        symbol_b = _binance_symbol(symbol)
        try:
            data = self._signed_request("GET", "/fapi/v1/income", {
                "symbol": symbol_b,
                "incomeType": "REALIZED_PNL",
                "limit": min(limit, 100),
            })
        except Exception:
            return []
        out = []
        for item in (data if isinstance(data, list) else []):
            if (item.get("symbol") or "") != symbol_b:
                continue
            try:
                inc = float(item.get("income", 0) or 0)
                t = int(item.get("time", 0) or 0)
            except (TypeError, ValueError):
                continue
            out.append({"income": inc, "time": t})
        out.sort(key=lambda x: -x["time"])
        return out

    def start_user_stream_if_enabled(self) -> None:
        """Đảm bảo User Data Stream đang chạy (singleton process-wide). Idempotent: không tạo listenKey/thread mới mỗi cycle."""
        ensure_user_stream_started(self)

    def get_binance_open_positions(self) -> list[dict]:
        """Vị thế đang mở: từ User Data Stream cache (singleton) nếu còn mới; không thì REST (reconcile 2–5 phút)."""
        us = get_user_stream_singleton()
        if us and us.is_running() and us.last_updated() > 0 and not us.should_reconcile():
            cached = us.get_cached_positions()
            if cached is not None:
                return cached
        try:
            data = self._signed_request("GET", "/fapi/v2/positionRisk", {})
        except Exception:
            return []
        out = []
        for item in data if isinstance(data, list) else []:
            amt = float(item.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            sym = item.get("symbol", "")
            if sym.endswith("USDT"):
                base = sym[:-4]
            else:
                base = sym
            out.append({
                "symbol": base,
                "position_side": item.get("positionSide", "BOTH"),
                "side": "long" if amt > 0 else "short",
                "quantity": abs(amt),
                "entry_price": float(item.get("entryPrice", 0) or 0),
            })
        if us and us.is_running():
            us.set_from_rest_positions(out)
        return out

    def get_available_balance_usd(self) -> float | None:
        """Lấy số dư khả dụng (USDT) trên sàn — dùng làm 'số tiền có thể cược' khi đánh thật, tránh DB lệch. Trả về None nếu lỗi."""
        info = self.get_balance_info()
        return info.get("available_balance") if info else None

    def get_balance_info(self) -> dict | None:
        """Số dư USDT: từ User Data Stream (singleton) nếu còn mới; không thì REST (reconcile 2–5 phút). Vẫn có cache 60s khi dùng REST."""
        us = get_user_stream_singleton()
        if us and us.is_running() and us.last_updated() > 0 and not us.should_reconcile():
            cached = us.get_cached_balance()
            if cached:
                return cached
        base = getattr(self, "base_url", "") or ""
        now = time.monotonic()
        if base in _BALANCE_CACHE:
            exp, info = _BALANCE_CACHE[base]
            if now < exp:
                if us and us.is_running():
                    us.set_from_rest_balance(info)
                return info
        try:
            data = self._signed_request("GET", "/fapi/v2/balance", {})
        except Exception:
            return None
        for item in (data if isinstance(data, list) else []):
            if (item.get("asset") or "").strip().upper() == "USDT":
                try:
                    wallet = float(item.get("balance", 0) or 0)
                    available = float(item.get("availableBalance", 0) or 0)
                    cross_un = float(item.get("crossUnPnl", 0) or 0)
                    info = {
                        "wallet_balance": wallet,
                        "available_balance": available,
                        "cross_un_pnl": cross_un,
                        "total_equity": wallet + cross_un,
                    }
                    _BALANCE_CACHE[base] = (now + _BALANCE_CACHE_TTL, info)
                    if us and us.is_running():
                        us.set_from_rest_balance(info)
                    return info
                except (TypeError, ValueError):
                    return None
        return None

    def get_current_sl_tp_from_binance(self, symbol: str, position_side: str) -> tuple[float | None, float | None]:
        """Lấy TP/SL hiện tại trên sàn (từ algo orders) cho symbol + position_side. Trả về (sl, tp) hoặc (None, None)."""
        orders = self.get_open_algo_orders(symbol, position_side)
        sl, tp = None, None
        for o in orders:
            try:
                trigger = float(o.get("triggerPrice") or 0)
            except (TypeError, ValueError):
                continue
            ot = o.get("orderType", "")
            if ot == "STOP_MARKET":
                sl = trigger
            elif ot == "TAKE_PROFIT_MARKET":
                tp = trigger
        return (sl, tp)

    def _cancel_all_algo_for_position(self, symbol: str, position_side: str) -> int:
        """Hủy tất cả algo orders (TP/SL/Trailing) cho symbol + position_side. Trả về số lệnh đã hủy."""
        cancelled = 0
        for o in self.get_open_algo_orders(symbol, position_side):
            aid = o.get("algoId")
            if aid is not None:
                if self._cancel_algo_order(int(aid)):
                    cancelled += 1
                else:
                    print(f"[BinanceFutures] Cancel algoId={aid} (type={o.get('orderType')}) failed or already gone.")
        # Invalidate cache để lần get_open_algo_orders tiếp theo (nếu có) lấy state mới
        for k in list(_ALGO_ORDERS_CACHE.keys()):
            if k[0] == self.base_url and k[1] == _binance_symbol(symbol) and k[2] == position_side:
                _ALGO_ORDERS_CACHE.pop(k, None)
                break
        return cancelled

    def update_position_sl_tp(
        self,
        db: Session,
        position: Position,
        new_sl: float | None,
        new_tp: float | None,
        note: str = "",
    ) -> None:
        """Cập nhật SL/TP trên sàn: hủy algo cũ, đặt lại. Có throttle 2 phút/symbol+side và retry -4130."""
        global _last_sl_tp_update
        from core.execution.simulator import PaperExecutionSimulator
        key = (position.symbol, position.side)
        now_ts = time.time()
        if key in _last_sl_tp_update and (now_ts - _last_sl_tp_update[key]) < _LAST_SL_TP_UPDATE_INTERVAL:
            return  # Throttle: không cập nhật quá thường (tránh spam, -4130, whipsaw)
        symbol_b = _binance_symbol(position.symbol)
        if self._hedge_mode is None:
            self._hedge_mode = _is_hedge_mode(self)
        position_side = ("LONG" if position.side == "long" else "SHORT") if self._hedge_mode else "BOTH"
        self._cancel_all_algo_for_position(position.symbol, position_side)
        time.sleep(2.0)  # Cho sàn kịp xử lý hủy, tránh -4130
        current_price = None
        try:
            # Public endpoint — không cần signed (document/request).
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{self.base_url}/fapi/v1/ticker/price", params={"symbol": symbol_b})
                r.raise_for_status()
                data = r.json()
                current_price = float(data.get("price", 0) or 0)
        except Exception:
            pass
        lot = _get_lot_size(self.base_url, symbol_b)
        tick_size = lot.get("tickSize", "0.00001")
        close_side = "SELL" if position.side == "long" else "BUY"
        tick_f = float(tick_size) or 0.00001
        buffer = max(tick_f * 2, current_price * 0.0005) if current_price else 0

        def _would_trigger_immediately(otype: str, trigger_price: float) -> bool:
            """Tránh -2021 Order would immediately trigger: SL/TP phải chưa in the money."""
            if current_price is None or current_price <= 0:
                return False
            t = float(trigger_price)
            if position.side == "long":
                if otype == "STOP_MARKET":
                    return t >= current_price - buffer
                if otype == "TAKE_PROFIT_MARKET":
                    return t <= current_price + buffer
            else:
                if otype == "STOP_MARKET":
                    return t <= current_price + buffer
                if otype == "TAKE_PROFIT_MARKET":
                    return t >= current_price - buffer
            return False

        def _place_algo(otype: str, trigger: str) -> bool:
            p = {"algoType": "CONDITIONAL", "symbol": symbol_b, "side": close_side, "type": otype, "triggerPrice": trigger, "closePosition": "true"}
            if self._hedge_mode:
                p["positionSide"] = position_side
            for attempt in range(3):
                try:
                    self._signed_request("POST", "/fapi/v1/algoOrder", p)
                    return True
                except Exception as e:
                    err_str = str(e)
                    if "-4130" in err_str or "4130" in err_str:
                        self._cancel_all_algo_for_position(position.symbol, position_side)
                        time.sleep(2.0)
                        continue
                    print(f"[BinanceFutures] Update {otype} failed for {position.symbol}: {e}")
                    return False
            print(f"[BinanceFutures] Update {otype} failed for {position.symbol} after retries (-4130)")
            return False

        if new_sl is not None:
            stop_sl = _round_stop_price(new_sl, tick_size)
            if _would_trigger_immediately("STOP_MARKET", stop_sl):
                print(f"[BinanceFutures] Skip STOP_MARKET for {position.symbol} triggerPrice={stop_sl} (would trigger at price {current_price}, -2021)")
            elif _place_algo("STOP_MARKET", stop_sl):
                print(f"[BinanceFutures] Updated STOP_MARKET for {position.symbol} triggerPrice={stop_sl}")
        if new_tp is not None:
            stop_tp = _round_stop_price(new_tp, tick_size)
            if _would_trigger_immediately("TAKE_PROFIT_MARKET", stop_tp):
                print(f"[BinanceFutures] Skip TAKE_PROFIT_MARKET for {position.symbol} triggerPrice={stop_tp} (would trigger at price {current_price}, -2021)")
            elif _place_algo("TAKE_PROFIT_MARKET", stop_tp):
                print(f"[BinanceFutures] Updated TAKE_PROFIT_MARKET for {position.symbol} triggerPrice={stop_tp}")
        for k in list(_ALGO_ORDERS_CACHE.keys()):
            if k[0] == self.base_url and k[1] == symbol_b and k[2] == position_side:
                _ALGO_ORDERS_CACHE.pop(k, None)
                break
        _last_sl_tp_update[key] = time.time()
        PaperExecutionSimulator().update_position_sl_tp(db, position, new_sl, new_tp, note)
