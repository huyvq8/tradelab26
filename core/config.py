import json
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DASHBOARD_OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "dashboard_overrides.json"


def _load_dashboard_overrides() -> dict:
    """Đọc cấu hình override từ dashboard (Binance, kill switch v5, ...)."""
    if _DASHBOARD_OVERRIDES_PATH.exists():
        try:
            return json.loads(_DASHBOARD_OVERRIDES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_dashboard_overrides_merged(updates: dict) -> None:
    """Ghi đè một phần overrides (merge với hiện tại)."""
    _DASHBOARD_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _load_dashboard_overrides()
    data.update(updates)
    _DASHBOARD_OVERRIDES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_dashboard_overrides(enable_live_binance_futures: bool, binance_futures_testnet: bool) -> None:
    """Lưu cấu hình đánh thật Binance (gọi từ dashboard)."""
    _save_dashboard_overrides_merged({
        "enable_live_binance_futures": enable_live_binance_futures,
        "binance_futures_testnet": binance_futures_testnet,
    })


def get_effective_enable_live_binance_futures() -> bool:
    """Giá trị hiệu lực: ưu tiên dashboard_overrides.json rồi mới .env."""
    overrides = _load_dashboard_overrides()
    if "enable_live_binance_futures" in overrides:
        return bool(overrides["enable_live_binance_futures"])
    return getattr(settings, "enable_live_binance_futures", False)


def get_effective_binance_futures_testnet() -> bool:
    """Giá trị hiệu lực: ưu tiên dashboard_overrides.json rồi mới .env."""
    overrides = _load_dashboard_overrides()
    if "binance_futures_testnet" in overrides:
        return bool(overrides["binance_futures_testnet"])
    return getattr(settings, "binance_futures_testnet", True)


def get_effective_kill_switch_enabled() -> bool:
    """v5: Dừng trade khi lỗ trong ngày đạt -XR (checkbox Dashboard)."""
    overrides = _load_dashboard_overrides()
    return bool(overrides.get("kill_switch_enabled", False))


def get_effective_kill_switch_r_threshold() -> float:
    """v5: Ngưỡng R (số dương, vd 3 = dừng khi lỗ -3R/ngày)."""
    overrides = _load_dashboard_overrides()
    v = overrides.get("kill_switch_r_threshold")
    if v is not None:
        try:
            return max(0.5, float(v))
        except (TypeError, ValueError):
            pass
    return 3.0


def save_kill_switch(enabled: bool, r_threshold: float) -> None:
    """Lưu cấu hình Kill switch (Dashboard)."""
    _save_dashboard_overrides_merged({
        "kill_switch_enabled": enabled,
        "kill_switch_r_threshold": max(0.5, float(r_threshold)),
    })


def get_effective_max_consecutive_loss_stop() -> int:
    """v5: Sau N lệnh thua liên tiếp thì không mở lệnh mới (0 = tắt)."""
    overrides = _load_dashboard_overrides()
    v = overrides.get("max_consecutive_loss_stop")
    if v is not None:
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            pass
    return 0


def save_max_consecutive_loss_stop(n: int) -> None:
    """Lưu ngưỡng consecutive loss (0 = tắt)."""
    _save_dashboard_overrides_merged({"max_consecutive_loss_stop": max(0, int(n))})


def get_effective_single_strategy_mode() -> str:
    """v5: Chỉ chạy 1 strategy (tên strategy hoặc rỗng = tất cả)."""
    overrides = _load_dashboard_overrides()
    v = overrides.get("single_strategy")
    if v and isinstance(v, str) and v.strip():
        return v.strip()
    return ""


def save_single_strategy_mode(strategy_name: str) -> None:
    """Lưu single strategy (rỗng = tắt)."""
    _save_dashboard_overrides_merged({"single_strategy": (strategy_name or "").strip()})


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Trading Lab Pro"
    app_env: str = "development"
    database_url: str = "sqlite:///./trading_lab.db"
    redis_url: str = "redis://localhost:6379/0"
    cmc_api_key: str = ""
    price_source: str = "cmc"  # "cmc" | "binance"
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # Lệnh thật Binance Futures (USD-M): bật khi muốn đánh thật, kèm TP/SL
    enable_live_binance_futures: bool = False
    # Testnet: True = testnet.binancefuture.com, False = fapi.binance.com
    binance_futures_testnet: bool = True
    # Đòn bẩy Futures (1–125). Đặt trước khi mở lệnh.
    binance_futures_leverage: int = 20
    # recvWindow (ms) cho signed request — tăng nếu lỗi -1021 Timestamp outside recvWindow (đồng hồ lệch). Mặc định 60000 = 60s.
    binance_recv_window: int = 60000
    # Trailing Stop: khi bật, có thể dùng Trailing thay TP cố định. trailing_only_momentum=True → chỉ dùng Trailing khi regime=high_momentum.
    binance_use_trailing_stop: bool = False
    binance_trailing_callback_pct: float = 1.5  # callbackRate % (0.1–10). 1.5 = 1.5%
    binance_trailing_only_momentum: bool = True  # True = chỉ trailing khi high_momentum; False = trailing mọi lúc (nếu use_trailing_stop)
    openai_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @field_validator("openai_api_key", mode="after")
    @classmethod
    def normalize_openai_key(cls, v: str) -> str:
        """Nếu key bị dán hai lần (có hai chuỗi sk-proj-), chỉ giữ key đầu tiên."""
        if not v:
            return v
        k = (v or "").strip()
        if k.count("sk-proj-") >= 2:
            parts = k.split("sk-proj-", 2)
            return (parts[0] or "") + "sk-proj-" + (parts[1] or "")
        return k
    default_capital_usd: float = 1000.0
    default_risk_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_concurrent_trades: int = 3
    # Số lệnh tối đa cho cùng 1 symbol: 1 = không vào thêm; 2 = cho phép vào thêm 1 lệnh nếu giá còn trong vùng đẹp
    max_positions_per_symbol: int = 1
    # Vùng giá đẹp (entry ± %), dùng khi cho phép vào thêm lệnh cùng symbol. 0.005 = 0.5%
    entry_zone_pct: float = 0.005
    # Khi vào thêm cùng symbol: chỉ cho phép nếu tín hiệu mới từ chiến lược KHÁC (tránh trùng lệnh cùng thesis). True = bật.
    add_only_different_strategy: bool = True
    # Khoảng cách tối thiểu (%) giữa giá thêm và entry lệnh đã mở — tránh hai lệnh cùng mức. 0 = tắt. 0.003 = 0.3%
    min_add_distance_pct: float = 0.003
    sim_fee_bps: int = 10
    sim_slippage_bps: int = 5
    # Worker: chu kỳ chạy cycle (giây). 15 mặc định — giảm overlap "max_instances reached" khi cycle có review/HTTP/OpenAI.
    cycle_interval_seconds: int = 15
    # Khi True: chỉ vào long nếu nến 4h gần nhất tăng (close >= open); short nếu nến 4h giảm. Nến 4h cache 5 phút — tránh gọi API quá nhiều.
    use_4h_trend_filter: bool = False
    # Đóng chủ động trước thời hạn: 0 = tắt. >0 = đóng mọi vị thế đã giữ quá N giờ (theo opened_at).
    max_hold_hours: float = 0.0
    # Đóng long chủ động khi regime = risk_off (thị trường rủi ro); short khi regime = high_momentum mạnh tùy logic.
    proactive_close_if_risk_off: bool = False
    # Khung thời gian: "swing" = TP/SL theo % từ entry, có thể nới TP xa (giữ lệnh đến khi chạm). "day" = TP không cách giá hiện tại quá max_tp_pct_above_current (ví dụ 2%) — hợp cho đánh ngắn.
    trading_style: str = "swing"  # "swing" | "day"
    # Khi trading_style = "day" hoặc > 0: TP long không được cao hơn current_price * (1 + pct); TP short không thấp hơn current_price * (1 - pct). Ví dụ 0.02 = 2%. 0 = không giới hạn.
    max_tp_pct_above_current: float = 0.0
    # Khi True và có OpenAI key: ưu tiên gợi ý AI cho cập nhật TP/SL (gọi AI trước, dùng kết quả AI nếu có; không thì mới dùng rule). Tránh rule cứng ghi đè AI.
    prefer_ai_sl_tp: bool = True
    # Guard AI TP/SL: không gọi AI khi position còn quá mới và PnL thấp. Tuổi tối thiểu (phút) trước khi cho AI can thiệp TP/SL.
    ai_sl_tp_min_age_minutes: float = 5.0
    # PnL % tối thiểu (ví dụ 0.8 = 0.8%) để cho AI can thiệp TP/SL khi position còn mới; dưới ngưỡng này + dưới min_age thì skip AI.
    ai_sl_tp_min_pnl_pct: float = 0.8
    # Coin biến động mạnh (volatility_tier high/extreme): cho phép AI sớm hơn — tuổi tối thiểu (phút), ví dụ 3.
    ai_sl_tp_min_age_minutes_high_vol: float = 3.0
    # Coin biến động mạnh: PnL % tối thiểu để cho AI can thiệp khi position còn mới, ví dụ 0.5%.
    ai_sl_tp_min_pnl_pct_high_vol: float = 0.5
    # Chốt lãi an toàn (Lock profit): khi lãi chưa chốt >= số USD này thì gợi ý kéo SL lên để nếu đảo chiều vẫn có lãi. 0 = tắt.
    lock_profit_min_usd: float = 30.0
    # Khi chốt lãi: SL tối thiểu cách entry buffer % (tránh spread kích hoạt nhầm). 0.002 = 0.2%.
    lock_profit_buffer_pct: float = 0.002


settings = Settings()
