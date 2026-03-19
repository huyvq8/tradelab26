import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.db import (
    SessionLocal,
    Base,
    engine,
    ensure_brain_v4_p1_trace_columns,
    ensure_trades_brain_cycle_id_column,
    ensure_trades_decision_trace_id_column,
)

try:
    import core.brain.models  # noqa: F401
except ImportError:
    pass
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot
from core.reporting.models import DailyReport
from core.journal.models import JournalEntry
from core.recommendation.engine import RecommendationEngine
from core.reflection.engine import ReflectionEngine
from core.analytics.metrics import compute_metrics
from core.watchlist import get_watchlist, set_watchlist
try:
    from core.market_data.client import get_market_client, get_quotes_with_fallback, get_klines_1h, get_klines_5m
except ImportError:
    from core.market_data.client import CoinMarketCapClient
    def get_market_client():
        return CoinMarketCapClient()
from core.regime.detector import derive_regime
from core.strategies.implementations import build_strategy_set
from core.risk.daily_r import MIN_RISK_USD_FOR_R_AGGREGATION, sum_daily_realized_r_from_trades
from core.config import (
    settings,
    get_effective_enable_live_binance_futures,
    get_effective_binance_futures_testnet,
    save_dashboard_overrides,
    get_effective_kill_switch_enabled,
    get_effective_kill_switch_r_threshold,
    save_kill_switch,
    get_effective_max_consecutive_loss_stop,
    save_max_consecutive_loss_stop,
    get_effective_single_strategy_mode,
    save_single_strategy_mode,
)
from core.signals.analysis import build_entry_analysis_from_dict
from core.observability.decision_log_tail import tail_decision_log_entries
from core.execution import get_execution_backend
from core.orchestration.cycle import SimulationCycle
from core.portfolio.capital_split import normalize_bucket
from datetime import date, datetime, time, timedelta, timezone

Base.metadata.create_all(bind=engine)
try:
    from core.db import (
        ensure_journal_tp_sl_explanation_column,
        ensure_positions_hedge_column,
        ensure_positions_entry_regime_column,
        ensure_positions_capital_bucket_column,
        ensure_positions_initial_stop_loss_column,
        ensure_trades_capital_bucket_column,
        ensure_journal_capital_bucket_column,
        ensure_journal_setup_hedge_columns,
    )
    ensure_journal_tp_sl_explanation_column()
    ensure_positions_hedge_column()
    ensure_positions_entry_regime_column()
    ensure_positions_capital_bucket_column()
    ensure_positions_initial_stop_loss_column()
    ensure_trades_capital_bucket_column()
    ensure_journal_capital_bucket_column()
    ensure_journal_setup_hedge_columns()
    ensure_trades_brain_cycle_id_column()
    ensure_trades_decision_trace_id_column()
    ensure_brain_v4_p1_trace_columns()
except Exception:
    pass

st.set_page_config(page_title="Trading Lab Pro", layout="wide")
st.title("Trading Lab Pro Dashboard")

# ---- Sidebar: Watchlist & Auto-refresh ----
with st.sidebar:
    st.subheader("Watchlist (token theo dõi)")
    watchlist_str = st.text_area(
        "Symbols (cách nhau bởi dấu phẩy)",
        value=", ".join(get_watchlist()),
        height=100,
        key="watchlist_input",
    )
    if st.button("Lưu watchlist"):
        syms = [s.strip().upper() for s in watchlist_str.replace(",", " ").split() if s.strip()]
        if syms:
            set_watchlist(syms)
            st.success("Đã lưu. Worker sẽ dùng danh sách này.")
        else:
            st.warning("Nhập ít nhất 1 symbol.")
    st.caption("Ví dụ: BTC, ETH, SOL, BNB")
    st.divider()
    auto_refresh = st.number_input("Tự động refresh (giây). 10 = lấy dữ liệu 10s/lần, 0 = tắt.", min_value=0, value=10, step=5)
    st.divider()
    st.subheader("Cấu hình đánh thật Binance")
    saved_enable = get_effective_enable_live_binance_futures()
    saved_testnet = get_effective_binance_futures_testnet()
    enable_live = st.checkbox(
        "Bật đánh lệnh thật Binance Futures (USD-M)",
        value=saved_enable,
        key="binance_live_enable",
        help="Khi bật, Worker sẽ đặt lệnh thật kèm TP/SL trên Binance. Cần BINANCE_API_KEY và BINANCE_API_SECRET trong .env.",
    )
    use_testnet = st.checkbox(
        "Dùng Testnet (khuyến nghị thử trước)",
        value=saved_testnet,
        key="binance_testnet",
        help="Testnet = không dùng tiền thật. Tắt = sàn thật (mainnet).",
    )
    has_unsaved = enable_live != saved_enable or use_testnet != saved_testnet
    if has_unsaved:
        st.warning("⚠️ **Thay đổi chưa lưu.** Worker vẫn đang dùng cấu hình cũ. Bấm **Lưu cấu hình đánh thật** bên dưới để áp dụng (tắt/bật đánh thật mới có hiệu lực).")
    if st.button("Lưu cấu hình đánh thật", key="save_binance_config"):
        save_dashboard_overrides(enable_live, use_testnet)
        st.success("Đã lưu. Worker lần chạy tiếp theo sẽ dùng cấu hình mới (Paper nếu tắt, Binance nếu bật).")
        st.rerun()
    st.divider()
    st.subheader("Dừng trade khi lỗ trong ngày (Kill switch v5)")
    kill_switch_saved = get_effective_kill_switch_enabled()
    kill_switch_r_saved = get_effective_kill_switch_r_threshold()
    kill_switch_enable = st.checkbox(
        "Bật dừng trade khi lỗ trong ngày đạt ngưỡng -R",
        value=kill_switch_saved,
        key="kill_switch_enable",
        help="Khi bật: nếu tổng R thua trong ngày (từ các lệnh đã đóng) <= -R, Worker sẽ không mở lệnh mới đến hết ngày.",
    )
    kill_switch_r = st.number_input(
        "Ngưỡng R (vd 3 = dừng khi lỗ -3R/ngày)",
        min_value=0.5,
        max_value=10_000.0,
        value=float(kill_switch_r_saved),
        step=0.5,
        key="kill_switch_r",
        help="Trần trên form 10 000 R (trước đây 20 chỉ là giới hạn UI). Lưu xong Worker dùng đúng số đã lưu.",
    )
    kill_switch_unsaved = kill_switch_enable != kill_switch_saved or abs(kill_switch_r - kill_switch_r_saved) > 0.01
    if kill_switch_unsaved:
        st.caption("Thay đổi Kill switch chưa lưu. Bấm **Lưu Kill switch** để áp dụng.")
    if st.button("Lưu Kill switch", key="save_kill_switch"):
        save_kill_switch(kill_switch_enable, kill_switch_r)
        st.success("Đã lưu. Worker sẽ dùng ngưỡng này khi đánh giá mở lệnh mới.")
        st.rerun()
    if kill_switch_saved:
        st.caption(f"Hiện tại: Kill switch **{'bật' if kill_switch_saved else 'tắt'}**, ngưỡng **-{kill_switch_r_saved} R/ngày**.")
    # Trạng thái: đang lỗ bao nhiêu R, còn bao nhiêu đến giới hạn chặn
    daily_r = 0.0
    daily_close_pnl_usd = 0.0
    try:
        with SessionLocal() as _db:
            portfolio = _db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
            if portfolio:
                today_start = datetime.combine(date.today(), time.min)
                today_end = today_start + timedelta(days=1)
                closed_today = list(_db.scalars(select(Trade).where(
                    Trade.portfolio_id == portfolio.id,
                    Trade.action == "close",
                    Trade.created_at >= today_start,
                    Trade.created_at < today_end,
                )))
                daily_r = sum_daily_realized_r_from_trades(closed_today)
                daily_close_pnl_usd = round(sum(float(t.pnl_usd or 0) for t in closed_today), 2)
    except Exception:
        daily_r = 0.0
        daily_close_pnl_usd = 0.0
    threshold = float(kill_switch_r_saved)
    limit_r = -threshold
    # Số R lỗ thêm (dương) thì chạm ngưỡng chặn
    remaining_to_limit = abs(limit_r - daily_r) if daily_r > limit_r else 0.0
    st.markdown("**Trạng thái hôm nay:**")
    c_r, c_usd = st.columns(2)
    with c_r:
        st.metric(
            "Tổng R đã thực hiện (lệnh đóng)",
            f"{daily_r:+.2f} R",
            help=(
                "Cộng dồn **PnL USD ÷ risk USD** từng lệnh **đóng hết** (action=close) trong ngày theo calendar server. "
                f"Risk trên lệnh đóng dùng **SL ban đầu** (`initial_stop_loss`) nếu có — tránh R phình khi đã trailing SL về sát giá. "
                f"Bỏ qua lệnh có risk < {MIN_RISK_USD_FOR_R_AGGREGATION} USD."
            ),
        )
    with c_usd:
        st.metric(
            "Tổng PnL đã chốt hôm nay (USD)",
            f"{daily_close_pnl_usd:+,.2f} USD",
            help="Cộng `pnl_usd` của các lệnh đóng hết trong ngày — đây mới là ‘lỗ/lãi bao nhiêu tiền’ đã chốt.",
        )
    if abs(daily_r) > 100:
        st.caption("⚠️ Giá trị R rất lớn — có thể do vài lệnh có risk_usd quá nhỏ trong DB hoặc dữ liệu cũ trước khi có initial_stop_loss. Nên kiểm tra bảng trades đóng.")
    if kill_switch_saved and threshold > 0:
        if daily_r <= limit_r:
            st.warning(f"Đã chạm ngưỡng chặn (**{daily_r:+.2f} R** ≤ **{limit_r:.1f} R**). Worker **sẽ không mở lệnh mới** đến hết ngày.")
        else:
            st.info(f"Còn **{remaining_to_limit:.2f} R** nữa là đến ngưỡng chặn (**{limit_r:.1f} R**). Lỗ thêm {remaining_to_limit:.2f} R thì bị chặn.")
    st.caption("v5: Giảm rủi ro — dừng mở lệnh khi lỗ trong ngày đạt -R.")
    st.divider()
    st.subheader("Consecutive loss stop (v5)")
    consec_saved = get_effective_max_consecutive_loss_stop()
    consec_n = st.number_input("Dừng mở lệnh sau N lệnh thua liên tiếp (0 = tắt)", min_value=0, max_value=10, value=consec_saved, key="consec_loss_n")
    if consec_n != consec_saved and st.button("Lưu Consecutive loss", key="save_consec"):
        save_max_consecutive_loss_stop(int(consec_n))
        st.rerun()
    if consec_saved > 0:
        st.caption(f"Hiện tại: dừng sau **{consec_saved}** lệnh thua liên tiếp.")
    st.divider()
    st.subheader("Chỉ 1 strategy (v5)")
    single_saved = get_effective_single_strategy_mode()
    strategy_names = ["trend_following", "breakout_momentum", "mean_reversion", "liquidity_sweep_reversal"]
    single_choice = st.selectbox(
        "Chỉ chạy 1 strategy (để thu 50–100 lệnh rồi phân tích)",
        options=[""] + strategy_names,
        index=(strategy_names.index(single_saved) + 1) if single_saved in strategy_names else 0,
        format_func=lambda x: "(Tất cả)" if x == "" else x,
        key="single_strategy_select",
    )
    if single_choice != single_saved and st.button("Lưu Single strategy", key="save_single"):
        save_single_strategy_mode(single_choice or "")
        st.rerun()
    if single_saved:
        st.caption(f"Hiện tại: chỉ chạy **{single_saved}**.")
    if st.button("Kiểm tra kết nối Binance", key="test_binance_connection"):
        from core.execution.binance_futures import BinanceFuturesExecutor
        ok, msg = BinanceFuturesExecutor.test_connection(use_testnet=get_effective_binance_futures_testnet())
        if ok:
            st.success(msg)
        else:
            st.error(msg)
    st.caption("API Key / Secret vẫn cấu hình trong file .env. **Cấu hình chỉ có hiệu lực sau khi bấm «Lưu cấu hình đánh thật»** — Worker đọc mỗi cycle, không cần khởi động lại.")
    # Hiển thị backend thực sự đang dùng (có thể khác checkbox nếu thiếu key/secret)
    _has_binance_creds = bool((getattr(settings, "binance_api_key", None) or "").strip() and (getattr(settings, "binance_api_secret", None) or "").strip())
    _backend_binance = get_effective_enable_live_binance_futures() and _has_binance_creds
    if _backend_binance:
        st.success("**Backend hiện tại: Binance Futures (lệnh thật).** Worker tự chạy cycle mỗi vài giây → tạo lệnh khi có tín hiệu và risk cho phép.")
    elif enable_live and not _has_binance_creds:
        st.warning("Đã bật đánh thật nhưng chưa cấu hình **BINANCE_API_KEY** / **BINANCE_API_SECRET** trong .env → đang dùng **Paper**, không tạo lệnh trên sàn.")
    if enable_live and _has_binance_creds:
        st.markdown("**Xem lệnh:** Testnet ✓ → [testnet.binancefuture.com](https://testnet.binancefuture.com) | Testnet ✗ → [binance.com/futures](https://www.binance.com/vi/futures).")
    with st.expander("Kiểm tra điều kiện lệnh Binance"):
        _enable = get_effective_enable_live_binance_futures()
        _key = bool((getattr(settings, "binance_api_key", None) or "").strip())
        _secret = bool((getattr(settings, "binance_api_secret", None) or "").strip())
        _be = get_execution_backend()
        _is_binance = type(_be).__name__ == "BinanceFuturesExecutor"
        st.markdown("| Điều kiện | Trạng thái |")
        st.markdown("|-----------|------------|")
        st.markdown(f"| Bật đánh thật (enable_live) | {'Có' if _enable else 'Không'} |")
        st.markdown(f"| BINANCE_API_KEY | {'Đã cấu hình' if _key else 'Chưa cấu hình'} |")
        st.markdown(f"| BINANCE_API_SECRET | {'Đã cấu hình' if _secret else 'Chưa cấu hình'} |")
        if _is_binance:
            _testnet = getattr(_be, "base_url", "") and "testnet" in getattr(_be, "base_url", "")
            st.markdown(f"| Backend thực tế | Binance Futures ({'Testnet' if _testnet else 'Mainnet'}) |")
        else:
            st.markdown("| Backend thực tế | **Paper** (không tạo lệnh trên sàn) |")
        try:
            with SessionLocal() as _db:
                from core.portfolio.models import Position, Trade
                _open_count = _db.query(Position).filter(Position.is_open == True).count()
                _today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                _daily = _db.query(Trade).filter(Trade.action == "close", Trade.created_at >= _today_start).all()
                _daily_pnl = sum(t.pnl_usd for t in _daily)
        except Exception:
            _open_count = 0
            _daily_pnl = 0.0
        st.markdown(f"| Lệnh đang mở / Slot tối đa | {_open_count} / {settings.max_concurrent_trades} |")
        st.markdown(f"| Lỗ đã chốt trong ngày (USD) | {_daily_pnl:,.2f} |")
        st.caption("Worker tạo lệnh khi: Backend = Binance, còn slot, chưa vượt giới hạn lỗ ngày, có tín hiệu; cùng symbol chỉ vào thêm nếu MAX_POSITIONS_PER_SYMBOL≥2 và giá còn trong vùng đẹp.")
    leverage = max(1, min(125, getattr(settings, "binance_futures_leverage", 20)))
    st.markdown(f"**Đòn bẩy:** {leverage}x (chỉnh trong .env: `BINANCE_FUTURES_LEVERAGE={leverage}`)")
    use_ts = getattr(settings, "binance_use_trailing_stop", False)
    st.caption(f"**Trailing Stop:** {'Bật' if use_ts else 'Tắt'} (BINANCE_USE_TRAILING_STOP, BINANCE_TRAILING_CALLBACK_PCT, BINANCE_TRAILING_ONLY_MOMENTUM trong .env). Khi bật + regime high_momentum → dùng Trailing thay TP cố định.")
    st.divider()
    st.subheader("Giới hạn lệnh / Giới hạn thua")
    st.markdown(f"**Số lệnh tối đa:** {settings.max_concurrent_trades}")
    max_per_sym = getattr(settings, "max_positions_per_symbol", 1)
    st.markdown(f"**Số lệnh tối đa / 1 symbol:** {max_per_sym}" + (" (có thể vào thêm khi giá còn trong vùng đẹp)" if max_per_sym >= 2 else " (không vào thêm cùng symbol)"))
    st.markdown(f"**Giới hạn lỗ trong ngày:** {settings.max_daily_loss_pct*100:.0f}% (≈ {settings.default_capital_usd * settings.max_daily_loss_pct:.0f} USD)")
    max_hold = getattr(settings, "max_hold_hours", 0) or 0
    risk_off_close = getattr(settings, "proactive_close_if_risk_off", False)
    st.caption(f"**Đóng chủ động:** Giữ tối đa {max_hold}h (MAX_HOLD_HOURS); đóng long khi risk_off: {'Có' if risk_off_close else 'Không'} (PROACTIVE_CLOSE_IF_RISK_OFF).")
    st.markdown(f"**Risk mỗi lệnh:** {settings.default_risk_pct*100:.0f}%")
    st.markdown(f"**Vốn mặc định:** {settings.default_capital_usd:.0f} USD")
    with st.expander("Chỉnh ở đâu?"):
        st.markdown("""
Các giới hạn này đọc từ **file `.env`** trong thư mục dự án. Để thay đổi:

1. Mở file **`.env`** (cùng thư mục với `run_all.bat`).
2. Sửa các dòng:
   - `MAX_CONCURRENT_TRADES=3` → số lệnh tối đa mở cùng lúc
   - `MAX_POSITIONS_PER_SYMBOL=1` → tối đa 1 lệnh/symbol (đặt 2 để cho phép vào thêm lệnh khi giá còn trong vùng đẹp)
   - `ENTRY_ZONE_PCT=0.005` → vùng giá đẹp = entry ± 0.5% (khi vào thêm cùng symbol)
   - `ADD_ONLY_DIFFERENT_STRATEGY=true` → chỉ thêm lệnh nếu tín hiệu từ chiến lược khác (tránh trùng thesis)
   - `MIN_ADD_DISTANCE_PCT=0.003` → khoảng cách tối thiểu 0.3% với entry lệnh cũ (tránh hai lệnh cùng mức)
   - `MAX_HOLD_HOURS=0` → 0 = tắt. >0 = đóng chủ động mọi vị thế giữ quá N giờ (trước thời hạn TP/SL).
   - `PROACTIVE_CLOSE_IF_RISK_OFF=false` → bật true để đóng long khi regime risk_off (giảm rủi ro).
   - `MAX_DAILY_LOSS_PCT=0.03` → 3% = giới hạn lỗ trong ngày (0.03 = 3%)
   - `DEFAULT_RISK_PCT=0.01` → 1% risk mỗi lệnh
   - `DEFAULT_CAPITAL_USD=1000` → vốn giả lập
3. **Lưu file** rồi **khởi động lại** Worker và API (đóng cửa sổ và chạy lại `run_all.bat` hoặc worker/API).

Dashboard không cho sửa trực tiếp để tránh nhầm lẫn; mọi thay đổi qua `.env` để dễ kiểm soát và backup.
        """)

# ---- Fetch current prices for watchlist (unrealized PnL & signals) ----
watchlist = get_watchlist()
quotes_now = {}
signals_now = []  # Ứng viên strategy (mỗi dòng = 1 strategy fire; giống bước evaluate đầu của Worker)
system_insights = []  # Mô tả "hệ thống đang nghĩ gì" cho từng symbol
try:
    quotes_now = get_quotes_with_fallback(watchlist)
    _price_src = (getattr(settings, "price_source", "cmc") or "cmc").strip().lower()
    _src_label = "Binance (Spot + Futures)" if _price_src == "binance" else "CoinMarketCap"
    st.sidebar.caption(f"Nguồn giá: **{_src_label}**. Muốn SIREN từ Binance Futures → đặt PRICE_SOURCE=binance trong .env")
    strategies = build_strategy_set()
    for symbol, q in quotes_now.items():
        chg, vol = q.percent_change_24h, q.volume_24h
        regime = derive_regime(chg, vol)
        # Tại sao regime này
        if chg > 5 and vol > 5_000_000:
            regime_reason = f"change_24h={chg:.1f}% > 5 và volume={vol/1e6:.1f}M > 5M → high_momentum"
        elif chg < -5:
            regime_reason = f"change_24h={chg:.1f}% < -5 → risk_off"
        else:
            regime_reason = f"change_24h={chg:.1f}%, volume={vol/1e6:.1f}M → balanced (không đủ điều kiện high_momentum/risk_off)"
        strategy_reasons = []
        first_signal_for_symbol = None
        klines_1h_full: list = []
        try:
            klines_1h_full = list(get_klines_1h(symbol, 25) or [])
        except Exception:
            klines_1h_full = []
        klb = len(klines_1h_full)
        klines_for_eval = klines_1h_full if klb else None
        for strat in strategies:
            sig = strat.evaluate(symbol, q.price, chg, vol, regime, klines_1h=klines_for_eval)
            if sig:
                strategy_reasons.append(f"**{strat.name}**: CÓ ỨNG VIÊN (strategy) — {sig.rationale}")
                if first_signal_for_symbol is None:
                    first_signal_for_symbol = sig
                signals_now.append({
                    "symbol": symbol,
                    "regime": regime,
                    "strategy": sig.strategy_name,
                    "side": sig.side,
                    "entry": sig.entry_price,
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "rationale": sig.rationale[:80] + "..." if len(sig.rationale) > 80 else sig.rationale,
                    "confidence": sig.confidence,
                    "pipeline_stage": "strategy_candidate",
                    "klines_1h_bars": klb,
                    "levels_from_structure": bool(getattr(sig, "levels_from_structure", False)),
                })
            else:
                if strat.name == "trend_following":
                    r = "CÓ" if (regime == "high_momentum" and chg > 3) else f"KHÔNG (regime={regime}, change_24h={chg:.1f})"
                elif strat.name == "breakout_momentum":
                    r = "CÓ" if (chg > 6 and vol > 10_000_000) else f"KHÔNG (change_24h={chg:.1f}, volume={vol/1e6:.0f}M)"
                elif strat.name == "mean_reversion":
                    r = "CÓ" if chg < -6 else f"KHÔNG (change_24h={chg:.1f} > -6)"
                elif strat.name == "liquidity_sweep_reversal":
                    r = "CÓ" if (4 <= chg <= 10 and regime == "high_momentum") else f"KHÔNG (change_24h={chg:.1f}, regime={regime})"
                else:
                    r = "KHÔNG"
                strategy_reasons.append(f"**{strat.name}**: {r}")
        # Trạng thái đường giá (nến 1h nếu có)
        price_state = "tăng mạnh" if chg > 3 else ("giảm mạnh" if chg < -3 else "sideway")
        candle_state = ""
        trend_development = ""
        try:
            klines = get_klines_1h(symbol, 2)
            if klines:
                last = klines[-1]
                o = getattr(last, "open", None)
                c = getattr(last, "close", None)
                if o and c:
                    body_pct = abs(c - o) / o * 100 if o else 0
                    candle_state = f"Nến 1h: {'xanh (tăng)' if c >= o else 'đỏ (giảm)'}, body {body_pct:.1f}%"
                if regime == "high_momentum":
                    trend_development = "Xu hướng tăng mạnh; tỷ lệ tiếp tục cao nếu volume giữ. Rủi ro: đuổi đỉnh."
                elif regime == "risk_off":
                    trend_development = "Xu hướng giảm; có thể bounce (mean reversion) hoặc giảm tiếp. Theo dõi volume."
                else:
                    trend_development = "Cân bằng; chờ breakout (momentum) hoặc vùng oversold (mean reversion)."
        except Exception:
            if regime == "high_momentum":
                trend_development = "Xu hướng tăng mạnh; tỷ lệ tiếp tục cao nếu volume giữ."
            elif regime == "risk_off":
                trend_development = "Xu hướng giảm; có thể bounce hoặc giảm tiếp."
            else:
                trend_development = "Cân bằng; chờ breakout hoặc mean reversion."
        # Dấu hiệu tiếp theo (chỉ = ứng viên strategy — Worker còn context gates + risk + scale-in)
        if first_signal_for_symbol:
            sig = first_signal_for_symbol
            zone = sig.entry_price * 0.005
            entry_signals = (
                f"**Ứng viên** {sig.side.upper()} (chưa qua pipeline Worker): vùng {sig.entry_price - zone:.4f}–{sig.entry_price + zone:.4f} "
                f"({sig.strategy_name}). SL={sig.stop_loss:.4f}, TP={sig.take_profit:.4f}. "
                f"Nến 1h dùng evaluate: **{klb}** bar. {sig.rationale[:55]}..."
            )
        else:
            first_no = next((r for r in strategy_reasons if "KHÔNG" in r), "")
            entry_signals = "Chưa đủ điều kiện. " + (first_no.replace("**", "").replace("**:", ": ") if first_no else "Chờ regime/giá phù hợp.")
        system_insights.append({
            "symbol": symbol,
            "price": q.price,
            "change_24h": chg,
            "volume_24h": vol,
            "regime": regime,
            "regime_reason": regime_reason,
            "strategy_reasons": strategy_reasons,
            "price_state": price_state,
            "candle_state": candle_state,
            "trend_development": trend_development,
            "entry_signals": entry_signals,
        })
except Exception as e:
    st.sidebar.warning(f"Không lấy được giá: {e}")

# Symbol trong watchlist nhưng không có giá (vd. SIREN không có cặp trên Binance → cần CMC key để fallback)
if watchlist:
    _missing = [s for s in watchlist if s and s not in quotes_now]
    if _missing and quotes_now:
        st.sidebar.caption(f"Chưa có giá: {', '.join(_missing)}. Nếu dùng Binance, thử thêm CMC_API_KEY trong .env để lấy từ CMC.")

with SessionLocal() as db:
    # Kiểm tra SL/TP ngay khi mở/refresh dashboard để đóng lệnh đã chạm SL/TP (không cần chờ Worker)
    try:
        closed = SimulationCycle().check_sl_tp_and_close(db, "Paper Portfolio")
        if closed.get("closed", 0) > 0:
            db.commit()
    except Exception:
        db.rollback()

    portfolios = list(db.scalars(select(Portfolio)))
    positions = list(db.scalars(select(Position)))
    trades = list(db.scalars(select(Trade)))
    journals = list(db.scalars(select(JournalEntry)))
    reports = list(db.scalars(select(DailyReport).order_by(DailyReport.report_date.desc())))
    snapshots = list(db.scalars(select(DailySnapshot).order_by(DailySnapshot.snapshot_date.desc())))

    total_cash = sum(p.cash_usd for p in portfolios)
    realized_pnl = round(sum(t.pnl_usd for t in trades if t.action == "close"), 2)
    open_positions = [p for p in positions if p.is_open]
    open_count = len(open_positions)

    # Luôn lấy giá cho symbol của lệnh đang mở (để hiển thị lời/lỗ và cho phép đóng lệnh)
    position_symbols = list({p.symbol for p in open_positions if p.symbol})
    missing_for_quotes = [s for s in position_symbols if s not in quotes_now]
    if missing_for_quotes:
        try:
            extra_quotes = get_quotes_with_fallback(missing_for_quotes)
            quotes_now = {**quotes_now, **extra_quotes}
        except Exception:
            pass

    # Unrealized PnL (current price from CMC)
    unrealized_pnl = 0.0
    equity_estimate = total_cash
    for p in open_positions:
        if p.symbol in quotes_now:
            price_now = quotes_now[p.symbol].price
            direction = 1 if p.side == "long" else -1
            unrealized_pnl += (price_now - p.entry_price) * p.quantity * direction
            if p.side == "long":
                equity_estimate += price_now * p.quantity
            else:
                equity_estimate += (p.entry_price - price_now) * p.quantity

    # ---- Row 1: Cash, Lời/lỗ, Positions, Equity ----
    _binance_info = None
    try:
        _exec = get_execution_backend()
        if hasattr(_exec, "get_balance_info"):
            _binance_info = _exec.get_balance_info()
    except Exception:
        pass

    # Khi có Binance: dùng lời/lỗ chưa chốt từ sàn (khớp số dư thực tế); không có thì dùng unrealized_pnl từ DB/giá
    unrealized_display = unrealized_pnl
    if _binance_info is not None and "cross_un_pnl" in _binance_info:
        unrealized_display = _binance_info["cross_un_pnl"]

    total_pnl = realized_pnl + unrealized_display  # Tổng lời/lỗ = đã chốt + chưa chốt → biết đang lời hay lỗ

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Cash (USD)", f"{total_cash:,.2f}")
    c2.metric("Lời/lỗ đã chốt (USD)", f"{realized_pnl:,.2f}")
    c3.metric("Lời/lỗ chưa chốt (USD)", f"{unrealized_display:,.2f}")
    c4.metric("Số lệnh đang mở", open_count)
    c5.metric("Tổng equity (ước tính, USD)", f"{equity_estimate:,.2f}")

    if _binance_info is not None:
        avail = _binance_info.get("available_balance")
        total_eq = _binance_info.get("total_equity")
        st.markdown("**Số dư thực tế trên Binance**")
        b1, b2, b3 = st.columns(3)
        b1.metric("Khả dụng (có thể mở lệnh)", f"{avail:,.2f}" if avail is not None else "—", "USDT")
        b2.metric("Tổng equity (ví + lời/lỗ chưa chốt)", f"{total_eq:,.2f}" if total_eq is not None else "—", "USDT")
        total_pnl_str = f"{total_pnl:+,.2f} USD"
        b3.metric("Tổng lời/lỗ (đã chốt + chưa chốt)", total_pnl_str, "Đang lời" if total_pnl >= 0 else "Đang lỗ")
        st.caption(
            "**Cách tính:** Số dư lấy trực tiếp từ Binance (Khả dụng = tiền có thể vào lệnh mới; Tổng equity = wallet + lời/lỗ chưa chốt trên sàn). "
            "**Lời/lỗ đã chốt** = tổng PnL các lệnh đã đóng (DB). **Lời/lỗ chưa chốt** = từ Binance (crossUnPnl). "
            "**Đang lời hay lỗ** = **Tổng lời/lỗ** = đã chốt + chưa chốt: **dương = lời**, **âm = lỗ**. Cash bên trên là DB (Worker cập nhật = available Binance khi chạy)."
        )
    else:
        st.markdown("**Tổng lời/lỗ (đã chốt + chưa chốt)**")
        st.metric("Kết quả", f"{total_pnl:+,.2f} USD", "Đang lời" if total_pnl >= 0 else "Đang lỗ")
        st.caption(
            "**Cash** = số tiền có thể cược (giảm khi mở lệnh, tăng khi đóng). "
            "**Lời/lỗ đã chốt** = tổng PnL các lệnh đã đóng. **Lời/lỗ chưa chốt** = theo giá hiện tại. "
            "**Đang lời hay lỗ** = **Tổng lời/lỗ** = đã chốt + chưa chốt: **dương = lời**, **âm = lỗ**. "
            "Tổng equity = Cash + giá trị vị thế đang mở. Nếu Cash âm, dùng **Đồng bộ từ Binance** hoặc **Reset Cash** trong Thao tác thủ công."
        )

    # ---- Sự thông minh của hệ thống ----
    st.subheader("Sự thông minh của hệ thống")
    st.caption(
        "Regime + **ứng viên strategy** (evaluate + klines 1h). "
        "Mục **Luồng quyết định** bên dưới đối chiếu với Worker (blocked + decision_log). "
        "AI tình huống: Worker mỗi 15 phút cho symbol có vị thế."
    )
    for ins in system_insights:
        with st.expander(f"**{ins['symbol']}** — giá {ins['price']:.4f} | 24h {ins['change_24h']:+.1f}% | {ins['regime']} | {ins.get('price_state', '')}", expanded=False):
            st.markdown("**Trạng thái hiện tại**")
            st.markdown(f"- Regime: **{ins['regime']}** — {ins['regime_reason']}")
            st.markdown(f"- Đường giá 24h: **{ins.get('price_state', '—')}** (volume 24h: {ins.get('volume_24h', 0)/1e6:.1f}M)")
            if ins.get("candle_state"):
                st.markdown(f"- Nến 1h: {ins['candle_state']}")
            st.markdown("**Xu hướng & tỷ lệ phát triển hình thái**")
            st.markdown(ins.get("trend_development", "—"))
            st.markdown("**Ứng viên (chưa qua risk/gates Worker)**")
            st.markdown(ins.get("entry_signals", "—"))
            st.markdown("**Từng chiến lược:**")
            for line in ins["strategy_reasons"]:
                st.markdown("- " + line)
    wl_set = {s.strip().upper() for s in watchlist if s.strip()}
    blocked_watchlist = []
    try:
        from core.rejected_signals_log import get_rejected_signals

        for row in get_rejected_signals(limit=80):
            sym = (row.get("symbol") or "").strip().upper()
            if sym and sym in wl_set:
                blocked_watchlist.append(row)
    except Exception:
        pass
    recent_worker = []
    try:
        recent_worker = tail_decision_log_entries(
            limit=60,
            symbols=wl_set if wl_set else None,
            events={"entry_rejected", "entry_opened"},
        )
    except Exception:
        recent_worker = []

    st.subheader("Luồng quyết định: Dashboard vs Worker")
    st.caption(
        "**Dashboard** = bước `strategy.evaluate` + cùng nguồn klines 1h (25 bar) như Worker — **chưa** gồm combo, entry context gates, entry timing, volatility guard, risk sizing, scale-in. "
        "**Worker** mới quyết định mở lệnh thật. Số dòng ứng viên ở đây **không** bằng số lệnh có thể vào."
    )
    c_a, c_b, c_c = st.columns(3)
    with c_a:
        st.metric("Ứng viên strategy (Dashboard)", len(signals_now), help="Mỗi strategy fire = 1 dòng; có thể nhiều dòng/symbol.")
    with c_b:
        st.metric("Worker: entry_rejected gần đây (watchlist)", sum(1 for r in recent_worker if r.get("event") == "entry_rejected"))
    with c_c:
        st.metric("Worker: entry_opened gần đây (watchlist)", sum(1 for r in recent_worker if r.get("event") == "entry_opened"))
    if blocked_watchlist:
        with st.expander("Tín hiệu bị Worker từ chối (blocked_signals.json, watchlist)", expanded=False):
            st.caption("Gồm risk reject (vd. volatility_guard) và **scale-in từ chối** (`SCALE_IN_REJECTED`) từ Worker.")
            st.dataframe(pd.DataFrame(blocked_watchlist), width="stretch")
    else:
        st.caption("Chưa có mục blocked_signals.json cho symbol watchlist (hoặc file trống).")
    if recent_worker:
        wr = []
        for r in recent_worker:
            wr.append(
                {
                    "ts": r.get("ts"),
                    "event": r.get("event"),
                    "symbol": r.get("symbol"),
                    "strategy": r.get("strategy_name"),
                    "reason_code": r.get("reason_code"),
                }
            )
        with st.expander("Worker: decision_log gần đây (entry_opened / entry_rejected, watchlist)", expanded=True):
            st.dataframe(pd.DataFrame(wr), width="stretch")
    else:
        st.caption("Không đọc được decision_log hoặc chưa có dòng cho watchlist.")

    st.subheader("Brain V4 (P1 trace)")
    try:
        with SessionLocal() as _db_br:
            from core.brain.persistence import fetch_latest_cycle_summary

            _bundle = fetch_latest_cycle_summary(_db_br)
        if _bundle and "error" not in _bundle:
            _cyc = _bundle.get("cycle") or {}
            _cid = str(_cyc.get("id") or "")
            _mkt = str(_cyc.get("market_decision_trace_id") or "")
            st.caption(
                f"Cycle `{_cid[:8]}…` — started {_cyc.get('started_at')} — "
                f"market_trace `{_mkt[:8]}…` — hash `{str(_cyc.get('config_hash_v4') or '')[:16]}…`"
            )
            _pols = _bundle.get("policy_mode_events") or []
            if _pols:
                st.markdown("**Policy (tick gần nhất)**")
                st.json(_pols[-1])
            st.caption(
                f"Inference: {len(_bundle.get('state_inference_events') or [])} | "
                f"CP: {len(_bundle.get('change_point_events') or [])} | "
                f"Reflex: {len(_bundle.get('reflex_action_events') or [])}"
            )
            with st.expander("Full cycle bundle", expanded=False):
                st.json(_bundle)
        else:
            st.caption("Chưa có bản ghi brain cycle (Worker + `p1.persistence.enabled` sẽ ghi DB).")
    except Exception as _e:
        st.caption(f"Không đọc brain trace: {_e}")

    if signals_now:
        st.info(
            f"Có **{len(signals_now)}** dòng **ứng viên strategy** (không phải “lệnh đã phê duyệt”). "
            "Xem bảng **Ứng viên / pipeline** bên dưới."
        )
    else:
        st.info("Không có ứng viên strategy nào (regime + điều kiện chiến lược). Worker cũng sẽ không có candidate ở bước đầu.")
    # Trang thai risk engine
    daily_realized = round(sum(t.pnl_usd for t in trades if t.action == "close" and t.created_at and t.created_at.date() == date.today()), 2) if trades else 0
    daily_limit = -settings.default_capital_usd * settings.max_daily_loss_pct
    slots_left = max(0, settings.max_concurrent_trades - open_count)
    st.markdown("**Risk engine:** " + (
        f"Còn {slots_left} slot được mở lệnh ({open_count}/{settings.max_concurrent_trades}). "
        f"Daily PnL = {daily_realized} USD (nếu ≤ {daily_limit:.0f} sẽ TỪ CHỐI mở thêm). "
        + ("Đang an toàn, có thể mở thêm." if slots_left > 0 and daily_realized > daily_limit else "Gần hoặc đã chạm ngưỡng.")
    ))
    with st.expander("Giải thích dòng Risk engine"):
        st.markdown("""
**Còn X slot được mở lệnh (Y/Z)**  
- **Z** = số lệnh tối đa được phép mở cùng lúc (cấu hình `MAX_CONCURRENT_TRADES`, mặc định 3).  
- **Y** = số lệnh đang mở hiện tại.  
- **X** = Z − Y = số “chỗ trống” còn lại. Nếu **X = 0** thì đã đủ 3/3 lệnh, hệ thống **không cho mở lệnh mới** cho đến khi đóng ít nhất một lệnh.

**Daily PnL và ngưỡng −30**  
- **Daily PnL** = tổng lãi/lỗ đã thực hiện (đóng lệnh) trong ngày.  
- **Ngưỡng** = −30 USD (từ vốn 1000 × 3% = 30). Nếu Daily PnL **≤ −30** thì hệ thống **từ chối mở thêm lệnh** đến hết ngày để tránh gồng lỗ.

**“Gần hoặc đã chạm ngưỡng”**  
Hiển thị khi: không còn slot (X = 0) **hoặc** Daily PnL đã ≤ ngưỡng. Lúc đó risk engine đang **chặn** không cho mở lệnh mới.  
**“Đang an toàn, có thể mở thêm”** khi vẫn còn slot và Daily PnL chưa chạm ngưỡng.
        """)

    # ---- Hệ thống tự động ----
    _cycle_sec = max(5, getattr(settings, "cycle_interval_seconds", 10))
    st.subheader("Hệ thống tự động")
    st.markdown(f"""
Hệ thống **tự quyết định** khi nào làm việc gì — chỉ cần **chạy Worker** (vd. `run_all.bat` hoặc `python apps/worker/runner.py`). Không cần bấm nút trên Dashboard.

| Công việc | Chu kỳ | Mô tả |
|-----------|--------|--------|
| **Cycle (quét tín hiệu, mở/đóng lệnh)** | Mỗi **{_cycle_sec}s** | Lấy watchlist, regime + chiến lược, risk → mở lệnh nếu có tín hiệu và còn slot; kiểm tra SL/TP và đóng lệnh. |
| **Phân tích tình huống (AI)** | Mỗi **15 phút** | Phân tích AI cho symbol đang có vị thế (hoặc 1 symbol watchlist); gửi Telegram nếu cấu hình. |
| **Phân tích nến 1h đặc biệt (AI)** | Mỗi **giờ** (phút 5) | Khi nến 1h có body lớn hoặc volume spike → gọi AI và gửi Telegram. |
| **Báo cáo ngày** | **23:55** mỗi ngày | Tổng kết PnL, gợi ý; gửi Telegram. |

Dashboard chỉ **xem** tín hiệu và lệnh theo dữ liệu hiện tại; việc **mở/đóng lệnh** và **phân tích AI** do Worker thực hiện tự động.
    """)
    st.caption("Thao tác thủ công (Chạy cycle ngay, Phân tích tình huống theo symbol) nằm trong mục **Thao tác thủ công (nâng cao)** bên dưới — dùng khi cần can thiệp nhanh.")

    # ---- Gợi ý token có dấu hiệu vào lệnh (scout top 10) ----
    st.subheader("Gợi ý token có dấu hiệu vào lệnh (top 10)")
    st.caption(
        "Khi watchlist (vd. BTC, SIREN) không có tín hiệu tốt, quét top token theo volume 24h từ Binance, "
        "chạy regime + chiến lược, chấm điểm và lấy top 10 gợi ý. Có thể thêm vào watchlist để theo dõi."
    )
    if "scout_results" not in st.session_state:
        st.session_state["scout_results"] = None
    if st.button("Quét token có dấu hiệu (top 10)", key="run_scout"):
        with st.spinner("Đang lấy top symbol theo volume và chạy regime + chiến lược..."):
            try:
                from core.discovery.signal_scout import scan_candidates
                st.session_state["scout_results"] = scan_candidates(
                    top_universe=100,
                    min_volume_usd=500_000,
                    result_top_n=10,
                )
            except Exception as e:
                st.error(f"Lỗi khi quét: {e}")
                st.session_state["scout_results"] = None
    scout_results = st.session_state.get("scout_results")
    if scout_results:
        current_wl = set(get_watchlist())
        for r in scout_results:
            sig = r.best_signal
            side_label = "LONG" if sig.side == "long" else "SHORT"
            in_wl = r.symbol in current_wl
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.markdown(
                    f"**{r.symbol}** — {side_label} · {sig.strategy_name} · regime **{r.regime}** · "
                    f"confidence {sig.confidence:.2f} · điểm **{r.score:.1f}** · 24h {r.quote.percent_change_24h:+.1f}% · vol {r.quote.volume_24h/1e6:.1f}M"
                )
            with col2:
                if st.button("Thêm vào watchlist", key=f"add_scout_{r.symbol}", disabled=in_wl):
                    new_wl = get_watchlist() + [r.symbol] if r.symbol not in current_wl else get_watchlist()
                    set_watchlist(new_wl)
                    st.success(f"Đã thêm {r.symbol}.")
                    st.rerun()
            with col3:
                if in_wl:
                    st.caption("Đã trong watchlist")
        if scout_results and st.button("Thêm tất cả top 10 vào watchlist", key="add_all_scout"):
            new_wl = list(current_wl)
            for r in scout_results:
                if r.symbol not in new_wl:
                    new_wl.append(r.symbol)
            set_watchlist(new_wl)
            st.success("Đã thêm tất cả vào watchlist.")
            st.rerun()
    elif scout_results is not None and len(scout_results) == 0:
        st.info("Không tìm thấy token nào có tín hiệu trong top 100 theo volume. Thử lại sau hoặc giảm ngưỡng.")

    # ---- Performance metrics ----
    portfolio_id = portfolios[0].id if portfolios else None
    metrics = compute_metrics(db, portfolio_id)
    st.subheader("Performance metrics")
    m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
    m1.metric("Win rate", f"{metrics['win_rate']*100:.1f}%")
    m2.metric("Profit factor", f"{metrics['profit_factor']:.2f}")
    m3.metric("Expectancy (USD)", f"{metrics['expectancy_usd']:.2f}")
    m4.metric("Max drawdown %", f"{metrics['max_drawdown_pct']:.1f}%")
    m5.metric("Total trades", metrics["total_trades"])
    m6.metric("Tổng lời/lỗ (USD)", f"{metrics['total_pnl_usd']:,.2f}")
    m7.metric("Avg R", f"{metrics.get('avg_r_multiple', 0):.2f}")
    m8.metric("Sharpe (sim)", f"{metrics.get('sharpe_simulated', 0):.2f}")

    # ---- Chiến lược đang đánh (from open positions) ----
    st.subheader("Chiến lược đang đánh")
    if open_positions:
        by_strategy = {}
        for p in open_positions:
            by_strategy[p.strategy_name] = by_strategy.get(p.strategy_name, 0) + 1
        st.write(", ".join(f"{k}: {v} lệnh" for k, v in by_strategy.items()))
    else:
        st.caption("Không có lệnh mở.")
        if get_effective_enable_live_binance_futures() and bool((getattr(settings, "binance_api_key", None) or "").strip() and (getattr(settings, "binance_api_secret", None) or "").strip()):
            st.caption("💡 **Binance thật đang bật:** Lệnh được tạo tự động khi **Worker đang chạy** (run_all.bat / python apps/worker/runner.py). Chỉ cần chạy Worker, hệ thống tự quyết định mở/đóng theo tín hiệu và risk.")

    # ---- Phân tích điểm vào lệnh (vùng vào, xác suất, tỷ lệ R) — từ ứng viên strategy ----
    st.subheader("Phân tích ứng viên (chưa = lệnh Worker đã duyệt)")
    st.caption(
        "Heuristic từ **ứng viên strategy** (evaluate + klines 1h). **Không** thay thế volatility guard / context gates / risk. "
        "Telegram từ Worker vẫn phụ thuộc cycle thật."
    )
    with st.expander("💡 Hệ thống (rule) vs AI: tại sao có thể khác nhau? Khi nào nên nghe theo?"):
        st.markdown("""
- **Bảng ứng viên** (long/short, vùng vào, R): **chỉ** regime + `strategy.evaluate` với nến 1h (25 bar) giống Worker ở bước sinh signal. Xác suất % là heuristic từ confidence. **Worker** sau đó còn nhiều lớp (gates, risk) mà Dashboard không mô phỏng ở đây.
- **Phân tích AI** (tự động mỗi 15 phút hoặc thủ công trong Thao tác nâng cao): AI đọc **nến 1h/5m, giá, volume** và đưa ra góc nhìn **cấu trúc** — support/resistance, tích lũy, "vùng dễ fake", entry đẹp hay không. Có thể **trái với rule** khi AI thấy "không phải entry đẹp" dù rule bật tín hiệu.
- **Không có "một lệnh đúng" tuyệt đối.** Rule cho tính nhất quán và backtest; AI cho bối cảnh và lọc entry kém. **Khi hai bên trái ngược:** nên thận trọng — giảm size, chờ xác nhận thêm, hoặc bỏ qua; ưu tiên cảnh báo "vùng dễ fake" thường an toàn hơn.
        """)
    if signals_now:
        for s in signals_now:
            try:
                a = build_entry_analysis_from_dict(s)
                side_label = "LONG" if a.side == "long" else "SHORT"
                with st.expander(f"Ứng viên {side_label} — {a.symbol} ({a.strategy}) [chưa duyệt Worker]", expanded=True):
                    st.markdown(f"**Nếu giá lên/xuống:**")
                    st.code(f"{a.entry_zone_low} – {a.entry_zone_high}\n→ {a.side} tốt")
                    st.markdown("**Xác suất hiện tại:**")
                    st.markdown(f"- {a.prob_tp_pct}% hướng TP ({a.take_profit})")
                    st.markdown(f"- {a.prob_sideway_pct}% sideway")
                    st.markdown(f"- {a.prob_sl_pct}% chạm SL ({a.stop_loss})")
                    st.markdown(f"**Tỷ lệ R:** {a.r_multiple}R | **Lý do:** {a.rationale}")
            except Exception as e:
                st.warning(f"Không tạo được phân tích cho {s.get('symbol')}: {e}")
    else:
        st.info("Không có ứng viên strategy. Khi có, đây hiển thị vùng/R heuristic; Worker vẫn có thể từ chối.")

    # ---- Bảng ứng viên (raw) ----
    st.subheader("Bảng ứng viên strategy (theo thời gian thực)")
    _live_on_effective = get_effective_enable_live_binance_futures() and bool((getattr(settings, "binance_api_key", None) or "").strip() and (getattr(settings, "binance_api_secret", None) or "").strip())
    st.info(
        "Cột **klines_1h_bars**: số nến 1h đưa vào `evaluate` (25 nếu API OK). Nếu **0**, rationale có thể là fallback giống Worker khi thiếu dữ liệu. "
        "Mở lệnh thật chỉ khi **Worker** pass risk/volatility/gates. "
        + ("Binance thật: Worker đặt lệnh khi duyệt xong." if _live_on_effective else "")
    )
    if signals_now:
        sig_df = pd.DataFrame(signals_now)
        st.dataframe(sig_df, width="stretch")
        st.caption("Mỗi hàng = một (symbol, strategy) fire. Cùng symbol có thể nhiều hàng (trend + breakout, …).")
    else:
        st.caption("Không có ứng viên. Kiểm tra regime / điều kiện chiến lược.")

    # ---- Thao tác thủ công (nâng cao): Reset Cash, Chạy cycle ngay + Phân tích tình huống AI ----
    with st.expander("Thao tác thủ công (nâng cao)", expanded=False):
        st.markdown("**Reset số dư có thể đánh (Cash)** — đặt lại Cash của portfolio về số USD bạn nhập (dùng khi Cash âm hoặc muốn bắt đầu lại).")
        reset_cash_val = st.number_input("Số USD đặt làm Cash (số dư có thể cược)", min_value=0.0, value=300.0, step=50.0, key="reset_cash_input")
        if st.button("Reset Cash về số trên", key="reset_cash_btn"):
            try:
                portfolio = db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
                if portfolio:
                    portfolio.cash_usd = float(reset_cash_val)
                    db.commit()
                    st.success(f"Đã đặt Cash = {reset_cash_val} USD. Trang sẽ tải lại.")
                else:
                    st.warning("Không tìm thấy portfolio 'Paper Portfolio'.")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi: {e}")
                db.rollback()
        st.divider()
        st.markdown("**Chạy cycle ngay** — ép chạy một lần cycle (mở lệnh nếu có tín hiệu). Bình thường Worker tự chạy mỗi vài giây.")
        if st.button("Chạy cycle ngay", type="secondary", key="run_cycle_now"):
            try:
                cycle = SimulationCycle()
                result = cycle.run(db, "Paper Portfolio", get_watchlist())
                db.commit()
                opened = result.get("opened", 0)
                if opened:
                    st.success(f"Đã mở {opened} lệnh. Trang sẽ tải lại.")
                else:
                    st.warning(
                        "Cycle chạy xong nhưng không mở lệnh mới (có thể do risk từ chối, đã đủ slot, hoặc điều kiện thay đổi khi Worker lấy giá)."
                    )
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi khi chạy cycle: {e}")
                db.rollback()
        st.divider()
        st.markdown("**Phân tích tình huống (AI)** — gọi AI cho một symbol (nến 1h/5m, vị thế). Bình thường Worker tự chạy mỗi 15 phút cho symbol có vị thế.")
        situation_symbol = st.selectbox(
            "Symbol cần phân tích",
            options=get_watchlist() or ["SIREN", "BTC", "ETH"],
            key="situation_symbol",
        )
        situation_notes = st.text_area(
            "Ghi chú thêm (tùy chọn)",
            value="",
            height=60,
            key="situation_notes",
            placeholder="Ví dụ: Cấu trúc LH/LL. Support 0.50, 0.48.",
        )
        if st.button("Phân tích tình huống", key="run_situation"):
            with st.spinner("Đang lấy nến 1h và gọi AI..."):
                try:
                    klines = get_klines_1h(situation_symbol, limit=5)
                    try:
                        klines_5m = get_klines_5m(situation_symbol, limit=12)
                    except Exception:
                        klines_5m = []
                    q = get_quotes_with_fallback([situation_symbol])
                    quote = q.get(situation_symbol)
                    if not quote:
                        st.warning(f"Không lấy được giá cho {situation_symbol}. Thử lại hoặc kiểm tra nguồn giá.")
                    else:
                        pos_for_symbol = [
                            {"side": p.side, "entry_price": p.entry_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit, "strategy_name": p.strategy_name}
                            for p in open_positions if p.symbol == situation_symbol
                        ]
                        ins_for_symbol = next((i for i in system_insights if i["symbol"] == situation_symbol), None)
                        extra_notes_for_ai = situation_notes.strip()
                        if ins_for_symbol:
                            sys_context = (
                                f"Regime hiện tại: {ins_for_symbol.get('regime', '')} — {ins_for_symbol.get('regime_reason', '')}. "
                                f"Đường giá: {ins_for_symbol.get('price_state', '')}. "
                                f"Dấu hiệu vào lệnh (rule): {ins_for_symbol.get('entry_signals', '')}"
                            )
                            extra_notes_for_ai = f"{sys_context}\n\n{extra_notes_for_ai}" if extra_notes_for_ai else sys_context
                        signal_for_symbol = next((s for s in signals_now if s.get("symbol") == situation_symbol), None)
                        if signal_for_symbol:
                            try:
                                a = build_entry_analysis_from_dict(signal_for_symbol)
                                side_lbl = "LONG" if a.side == "long" else "SHORT"
                                signal_context = (
                                    f"Hệ thống rule đang báo: **{side_lbl} tốt** — vùng vào {a.entry_zone_low} – {a.entry_zone_high}, "
                                    f"TP={a.take_profit}, SL={a.stop_loss}, tỷ lệ R={a.r_multiple}R, lý do: {a.rationale}. "
                                    f"Bạn hãy đánh giá: trong bối cảnh cấu trúc/nến hiện tại, entry này có phải 'entry đẹp' hay dễ fake? Trả lời rõ đồng ý hoặc không đồng ý và lý do."
                                )
                                extra_notes_for_ai = f"{extra_notes_for_ai}\n\n{signal_context}" if extra_notes_for_ai else signal_context
                            except Exception:
                                pass
                        from core.reflection.ai_situation import analyze_market_situation
                        result = analyze_market_situation(
                            symbol=situation_symbol,
                            klines_1h=klines,
                            quote_price=quote.price,
                            quote_pct_24h=quote.percent_change_24h,
                            quote_volume_24h=quote.volume_24h,
                            positions_for_symbol=pos_for_symbol,
                            extra_notes=extra_notes_for_ai,
                            klines_5m=klines_5m,
                            reason="manual_dashboard",
                        )
                        if result:
                            if result.startswith("ERROR: "):
                                st.error("Lỗi gọi API OpenAI: " + result[7:])
                            else:
                                st.markdown("---")
                                st.markdown("### Kết quả phân tích")
                                st.markdown(result)
                        else:
                            st.info("Chưa cấu hình OPENAI_API_KEY trong .env hoặc key rỗng.")
                except Exception as e:
                    st.error(f"Lỗi: {e}")

    # ---- Open Positions (giá hiện tại, lời/lỗ tại thời điểm hiện tại, nút đóng lệnh) ----
    st.subheader("Lệnh đang mở")
    _live_on = get_effective_enable_live_binance_futures()
    _has_binance_creds = bool((getattr(settings, "binance_api_key", None) or "").strip() and (getattr(settings, "binance_api_secret", None) or "").strip())
    _lev = max(1, min(125, getattr(settings, "binance_futures_leverage", 20)))
    st.caption(
        "Lời/lỗ theo giá từ nguồn đang dùng (CMC hoặc Binance). Dùng **Đóng lệnh ngay** để chốt theo giá hiện tại. "
        "SL/TP được kiểm tra mỗi lần mở/refresh Dashboard và mỗi chu kỳ Worker — lệnh chạm SL hoặc TP sẽ tự đóng."
        + (f" Khi bật đánh thật Binance Futures: đòn bẩy **{_lev}x** (chỉnh `BINANCE_FUTURES_LEVERAGE` trong .env)." if _live_on else "")
    )
    if _live_on and _has_binance_creds:
        st.caption("Danh sách lấy từ **DB**. **Đồng bộ từ Binance**: đóng trong DB những vị thế không còn trên sàn; thêm vào DB những vị thế đang có trên sàn; cập nhật TP/SL từ sàn vào DB.")
        if st.button("Đồng bộ từ Binance", key="sync_binance_positions"):
            try:
                exec_backend = get_execution_backend()
                if not hasattr(exec_backend, "get_binance_open_positions"):
                    st.info("Chỉ áp dụng khi backend là Binance Futures.")
                else:
                    binance_list = exec_backend.get_binance_open_positions()
                    binance_set = {(b["symbol"], b["position_side"]) for b in binance_list}
                    hedge = getattr(exec_backend, "_hedge_mode", None)
                    if hedge is None and hasattr(exec_backend, "_signed_request"):
                        from core.execution.binance_futures import _is_hedge_mode
                        hedge = _is_hedge_mode(exec_backend)
                    portfolio = db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
                    closed_count = 0
                    for p in open_positions:
                        ps = ("LONG" if p.side == "long" else "SHORT") if hedge else "BOTH"
                        if (p.symbol, ps) not in binance_set:
                            p.is_open = False
                            if getattr(p, "closed_at", None) is None:
                                p.closed_at = datetime.utcnow()
                            db.add(Trade(
                                portfolio_id=p.portfolio_id,
                                position_id=p.id,
                                symbol=p.symbol,
                                side=p.side,
                                strategy_name=p.strategy_name or "",
                                action="close",
                                price=p.entry_price,
                                quantity=p.quantity,
                                fee_usd=0.0,
                                pnl_usd=0.0,
                                note="Đồng bộ từ Binance: không còn vị thế trên sàn",
                                capital_bucket=normalize_bucket(getattr(p, "capital_bucket", None)),
                            ))
                            closed_count += 1
                    db.flush()
                    added_count = 0
                    still_open_set = {(p.symbol, ("LONG" if p.side == "long" else "SHORT") if hedge else "BOTH") for p in open_positions if p.is_open}
                    added_positions = []
                    for b in binance_list:
                        if (b["symbol"], b["position_side"]) in still_open_set:
                            continue
                        if not portfolio:
                            break
                        side = b.get("side", "long" if b["position_side"] == "LONG" else "short")
                        pos = Position(
                            portfolio_id=portfolio.id,
                            symbol=b["symbol"],
                            side=side,
                            strategy_name="Đồng bộ Binance",
                            entry_price=b["entry_price"],
                            quantity=b["quantity"],
                            stop_loss=None,
                            take_profit=None,
                            confidence=0.0,
                            opened_at=datetime.utcnow(),
                            is_open=True,
                            capital_bucket="core",
                        )
                        db.add(pos)
                        db.flush()
                        db.add(Trade(
                            portfolio_id=portfolio.id,
                            position_id=pos.id,
                            symbol=pos.symbol,
                            side=pos.side,
                            strategy_name=pos.strategy_name or "",
                            action="open",
                            price=pos.entry_price,
                            quantity=pos.quantity,
                            fee_usd=0.0,
                            pnl_usd=0.0,
                            note="Đồng bộ từ Binance: thêm vị thế từ sàn",
                            capital_bucket="core",
                        ))
                        added_count += 1
                        added_positions.append(pos)
                        still_open_set.add((b["symbol"], b["position_side"]))
                    all_open_now = [p for p in open_positions if p.is_open] + added_positions
                    updated_sl_tp = 0
                    if hasattr(exec_backend, "get_current_sl_tp_from_binance"):
                        from core.execution.simulator import PaperExecutionSimulator
                        sim = PaperExecutionSimulator()
                        for p in all_open_now:
                            ps = ("LONG" if p.side == "long" else "SHORT") if hedge else "BOTH"
                            if (p.symbol, ps) not in binance_set:
                                continue
                            sl_b, tp_b = exec_backend.get_current_sl_tp_from_binance(p.symbol, ps)
                            if (sl_b is not None and sl_b != p.stop_loss) or (tp_b is not None and tp_b != p.take_profit):
                                sim.update_position_sl_tp(db, p, sl_b, tp_b, "Cập nhật TP/SL từ Binance")
                                updated_sl_tp += 1
                    db.commit()
                    msg = f"Đã đồng bộ: {closed_count} lệnh đóng (không còn trên sàn), {added_count} vị thế thêm từ sàn, {updated_sl_tp} cập nhật TP/SL từ sàn."
                    st.success(msg)
            except Exception as e:
                st.error(f"Lỗi đồng bộ: {e}")
                db.rollback()
                import traceback
                st.code(traceback.format_exc())
            st.rerun()
    if open_positions:
        executor = get_execution_backend()
        for p in open_positions:
            price_now = quotes_now.get(p.symbol)
            direction = 1 if p.side == "long" else -1
            unrealized_usd = (price_now.price - p.entry_price) * p.quantity * direction if price_now else None
            with st.container():
                c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1.5, 1])
                with c1:
                    st.markdown(f"**{p.symbol}** · {p.side} · {p.strategy_name}")
                    sl_str = f"{p.stop_loss:.4f}" if p.stop_loss is not None else "—"
                    tp_str = f"{p.take_profit:.4f}" if p.take_profit is not None else "—"
                    st.caption(f"Vào {p.entry_price:.4f} · SL {sl_str} · TP {tp_str}")
                with c2:
                    st.metric("Khối lượng", f"{p.quantity:.6f}")
                with c3:
                    st.metric("Giá hiện tại", f"{price_now.price:.4f}" if price_now else "—")
                with c4:
                    if unrealized_usd is not None:
                        st.metric("Lời/lỗ hiện tại (USD)", f"{unrealized_usd:,.2f}")
                    else:
                        st.metric("Lời/lỗ hiện tại (USD)", "—")
                with c5:
                    can_close = price_now is not None
                    if st.button("Đóng lệnh ngay", key=f"close_{p.id}", type="primary", disabled=not can_close):
                        try:
                            exit_price = price_now.price
                            executor.close_position(db, p, exit_price, note="Đóng thủ công từ dashboard")
                            db.commit()
                            st.success(f"Đã đóng lệnh {p.symbol} tại giá {exit_price:.4f}.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi khi đóng lệnh: {e}")
                            db.rollback()
                    if not can_close:
                        st.caption("Chưa có giá")
                st.divider()
        # Bảng tổng hợp (giữ để xem nhanh)
        rows = []
        for p in open_positions:
            r = {
                "symbol": p.symbol,
                "side": p.side,
                "entry": p.entry_price,
                "qty": p.quantity,
                "current_price": None,
                "Lời/lỗ chưa chốt (USD)": None,
            }
            if p.symbol in quotes_now:
                price_now = quotes_now[p.symbol].price
                direction = 1 if p.side == "long" else -1
                u = (price_now - p.entry_price) * p.quantity * direction
                r["current_price"] = round(price_now, 4)
                r["Lời/lỗ chưa chốt (USD)"] = round(u, 2)
            rows.append(r)
        with st.expander("Bảng tổng hợp lệnh đang mở"):
            st.dataframe(pd.DataFrame(rows), width="stretch")
    else:
        st.info("Không có lệnh đang mở.")

    # ---- Recent Trades ----
    st.subheader("Recent Trades")
    pos_entry = {p.id: p.entry_price for p in positions}
    trade_rows = []
    for t in trades[-50:]:
        if t.action == "open":
            gia_vao_str = f"{t.price:.4f}"
            gia_dong_str = "—"
        else:
            entry = pos_entry.get(t.position_id) if t.position_id else None
            gia_vao_str = f"{entry:.4f}" if entry is not None else "—"
            gia_dong_str = f"{t.price:.4f}"
        trade_rows.append({
            "symbol": t.symbol,
            "action": t.action,
            "side": t.side,
            "strategy": t.strategy_name,
            "Giá vào": gia_vao_str,
            "Giá đóng": gia_dong_str,
            "Lời/lỗ (USD)": t.pnl_usd,
            "time": t.created_at,
        })
    trade_df = pd.DataFrame(trade_rows)
    st.dataframe(trade_df, width="stretch")
    st.caption("Giá vào = giá lúc mở lệnh, Giá đóng = giá lúc đóng. Lời/lỗ (USD) chỉ có khi đóng lệnh (sau phí).")

    # ---- Post-Trade Review (v4: vì sao vào / vì sao thua) ----
    closed_journals = sorted(
        [j for j in journals if j.result_summary],
        key=lambda x: x.id,
        reverse=True,
    )[:10]
    if closed_journals:
        try:
            from core.journal.context_builder import deserialize_reasons, deserialize_mistake_tags
        except ImportError:
            deserialize_reasons = lambda x: []
            deserialize_mistake_tags = lambda x: []
        st.subheader("Post-Trade Review (10 lệnh gần nhất)")
        st.caption("Vì sao vào lệnh (reasons, regime, confidence) và vì sao thua (exit_reason, mistake_tags).")
        review_rows = []
        for j in closed_journals:
            reasons = deserialize_reasons(j.reasons)
            tags = deserialize_mistake_tags(j.mistake_tags)
            review_rows.append({
                "symbol": j.symbol,
                "side": j.side or "—",
                "strategy": j.strategy_name,
                "vì sao vào": ((reasons[0][:50] + "…") if len(reasons[0]) > 50 else reasons[0]) if reasons else ((j.entry_reason[:50] + "…") if (j.entry_reason and len(j.entry_reason) > 50) else (j.entry_reason or "—")),
                "TP/SL lý do": (j.tp_sl_explanation[:80] + "…") if getattr(j, "tp_sl_explanation", None) and len(j.tp_sl_explanation) > 80 else (getattr(j, "tp_sl_explanation", None) or "—"),
                "regime": j.regime,
                "confidence": f"{(j.setup_score or 0)/100:.0%}" if j.setup_score else "—",
                "exit": j.exit_reason or "—",
                "mistake_tags": ", ".join(tags) if tags else "—",
                "R": f"{j.result_r:.2f}" if j.result_r is not None else "—",
                "PnL (summary)": (j.result_summary or "")[:60] + ("…" if (j.result_summary and len(j.result_summary) > 60) else ""),
            })
        st.dataframe(pd.DataFrame(review_rows), width="stretch")
    else:
        st.caption("Chưa có lệnh đóng có journal (Post-Trade Review sẽ có sau khi có lệnh đóng).")

    # ---- Trạng thái kinh nghiệm & học tập của AI ----
    st.subheader("Trạng thái kinh nghiệm & học tập")
    st.caption("Số liệu học được từ lệnh đã đóng, hiệu quả từng chiến lược, lỗi lặp lại, phản tư AI và khuyến nghị.")
    today = pd.Timestamp.utcnow().date()
    portfolio = next((p for p in portfolios if p.name == "Paper Portfolio"), None)
    portfolio_id = portfolio.id if portfolio else None
    reflection = ReflectionEngine().build_daily_reflection(db, today, portfolio_id=portfolio_id)
    recs = RecommendationEngine().next_steps(reflection, open_count)
    m = reflection.get("metrics") or {}

    with st.expander("Số liệu kinh nghiệm (từ lệnh đã đóng)", expanded=True):
        st.markdown(f"**Tổng lệnh đã đóng:** {m.get('total_trades', 0)} | **Win rate:** {m.get('win_rate', 0)*100:.1f}% | **Profit factor:** {m.get('profit_factor', 0):.2f} | **Avg R:** {m.get('avg_r_multiple', 0):.2f} | **Sharpe (sim):** {m.get('sharpe_simulated', 0):.2f}")
        acc = m.get("strategy_accuracy") or {}
        if acc:
            st.markdown("**Hiệu quả từng chiến lược (học từ dữ liệu):**")
            for strat, v in acc.items():
                if isinstance(v, dict):
                    wr = v.get("win_rate", 0) * 100
                    n = v.get("trades", 0)
                    st.markdown(f"- **{strat}**: {wr:.0f}% thắng ({v.get('wins', 0)}/{n} lệnh)")
    tp_reach = reflection.get("tp_reach_analysis") or {}
    with st.expander("TP không đạt — phân tích nguyên nhân (long đóng lãi nhưng không chạm TP)", expanded=bool(tp_reach.get("count"))):
        st.caption("Lệnh long đóng có lãi chủ yếu do SL được kéo lên (bảo vệ lời), không có lệnh đạt TP. Nguyên nhân: TP quá cao hay thiếu thời gian?")
        if tp_reach.get("count", 0) > 0:
            st.markdown(f"**Số lệnh long đóng có lãi (không phải TP):** {tp_reach['count']} | **Thời gian giữ TB:** {tp_reach.get('avg_hold_min')} phút | **TP tại entry TB:** {tp_reach.get('avg_tp_pct')}% | **Lời thực tế TB:** {tp_reach.get('avg_actual_pct')}%")
            if tp_reach.get("diagnosis"):
                st.markdown("**Chẩn đoán (rule-based):** " + tp_reach["diagnosis"])
            if tp_reach.get("suggestion"):
                st.markdown("**Gợi ý:** " + tp_reach["suggestion"])
            if tp_reach.get("ai_diagnosis"):
                st.markdown("**Phân tích chuyên gia (AI):**")
                st.markdown(tp_reach["ai_diagnosis"])
        else:
            st.caption("Chưa có đủ dữ liệu (hoặc chưa có long đóng có lãi mà không phải TP trong 30 ngày).")

    profit_source = reflection.get("profit_by_exit_reason") or {}
    with st.expander("Nguồn lợi nhuận (cắt lãi / ăn non / chiến thuật đúng)", expanded=bool(profit_source.get("by_exit_reason"))):
        st.caption("Lợi nhuận 30 ngày gần: từ Chốt lãi (TP), Cắt lỗ (SL), Đóng chủ động, Đồng bộ Binance, Thủ công.")
        if profit_source.get("summary_text"):
            st.markdown(profit_source["summary_text"])
        by_reason = profit_source.get("by_exit_reason") or {}
        if by_reason:
            total = profit_source.get("total_pnl_usd") or 0
            labels = {"tp_hit": "Chốt lãi (TP)", "sl_hit": "Cắt lỗ (SL)", "proactive": "Đóng chủ động", "sync_binance": "Đồng bộ Binance", "manual": "Thủ công", "unknown": "Không rõ"}
            for reason in ["tp_hit", "proactive", "sync_binance", "manual", "sl_hit", "unknown"]:
                if reason not in by_reason:
                    continue
                d = by_reason[reason]
                pct = (d["pnl_usd"] / total * 100) if total != 0 else 0
                st.caption(f"**{labels.get(reason, reason)}**: {d['pnl_usd']:+.2f} USD — {pct:.0f}% tổng PnL | {d['count']} lệnh, {d['count_win']} thắng")
        elif not profit_source.get("summary_text"):
            st.caption("Chưa có lệnh đóng trong 30 ngày.")
    with st.expander("v5: Số lệnh theo strategy & regime (mục tiêu 50–100)", expanded=False):
        closed_j = [j for j in journals if j.result_summary]
        by_strategy = {}
        by_regime_total = {}
        by_regime_loss = {}
        for j in closed_j:
            by_strategy[j.strategy_name] = by_strategy.get(j.strategy_name, 0) + 1
            reg = j.regime or "unknown"
            by_regime_total[reg] = by_regime_total.get(reg, 0) + 1
            if j.result_r is not None and j.result_r < 0:
                by_regime_loss[reg] = by_regime_loss.get(reg, 0) + 1
        st.markdown("**Số lệnh đã đóng theo strategy (v5: nên đạt 50–100 trước khi thêm strategy):**")
        for strat, count in sorted(by_strategy.items(), key=lambda x: -x[1]):
            need = "OK" if count >= 50 else f"can {50 - count} nua"
            st.markdown(f"- **{strat}**: {count} lenh — {need}")
        st.markdown("**Regime (số lệnh / số lỗ — biết regime nào lỗ):**")
        for reg in sorted(by_regime_total.keys(), key=lambda r: -by_regime_total[r]):
            total = by_regime_total[reg]
            loss = by_regime_loss.get(reg, 0)
            wr = ((total - loss) / total * 100) if total else 0
            st.markdown(f"- **{reg}**: {total} lenh, {loss} thua (winrate {wr:.0f}%)")
        if not closed_j:
            st.caption("Chua co lenh dong co journal.")

    with st.expander("Lỗi lặp lại & pattern (học từ journal)", expanded=True):
        if reflection.get("repeated_mistakes"):
            st.markdown("**Lỗi lặp lại (cần tránh):**")
            for x in reflection["repeated_mistakes"]:
                st.write(f"- {x['count']}x {x['text']}")
        else:
            st.caption("Chưa có lỗi lặp được ghi nhận.")
        top = reflection.get("top_pattern", "—")
        st.markdown(f"**Chiến lược xuất hiện nhiều nhất trong journal:** {top}")

    learned = reflection.get("learned_from_history") or {}
    warnings = learned.get("warnings") or []
    with st.expander("Hệ thống tự nhận ra (đọc lịch sử 30 ngày)", expanded=bool(warnings)):
        st.caption("Từ dữ liệu lệnh đóng + journal mistakes: combo strategy+symbol thua nhiều hoặc SL kích hoạt quá nhanh.")
        if warnings:
            for w in warnings:
                st.markdown(f"- **{w.get('type', '')}:** {w.get('message', '')}")
        else:
            st.caption("Chưa đủ dữ liệu hoặc chưa có cảnh báo (cần ≥2 lệnh đóng theo từng nhóm strategy+symbol).")

    with st.expander("Phản tư AI (khi có OPENAI_API_KEY)", expanded=bool(reflection.get("ai_summary"))):
        if reflection.get("ai_summary"):
            st.markdown("**Phản tư ngày:**")
            st.markdown(reflection["ai_summary"])
        if reflection.get("ai_next_day_plan"):
            st.markdown("**Kế hoạch ngày mai:**")
            st.markdown(reflection["ai_next_day_plan"])
        suggested = reflection.get("suggested_actions") or []
        if suggested:
            st.markdown("**Hành động đề xuất (v4):**")
            for a in suggested:
                st.write(f"- `{a.get('type', '')}`: {a.get('strategy', '')} {a.get('regime', '')} {a.get('value', '')}".strip())
        if not reflection.get("ai_summary") and not reflection.get("ai_next_day_plan") and not suggested:
            key_status = (settings.openai_api_key or "").strip()
            if key_status:
                st.caption("Key đã cấu hình nhưng AI chưa phản hồi. Kiểm tra key hợp lệ (một dòng, không dán hai lần). Nếu key bị dán trùng, app sẽ tự lấy key đầu.")
            else:
                st.caption("Chưa có phản tư AI. Thêm OPENAI_API_KEY trong .env (một key, không dán trùng).")

    with st.expander("Strategy Health (v4)", expanded=False):
        acc = m.get("strategy_accuracy") or {}
        if acc:
            for strat, v in acc.items():
                if isinstance(v, dict):
                    wr = v.get("win_rate", 0) * 100
                    n = v.get("trades", 0)
                    st.markdown(f"- **{strat}**: winrate {wr:.0f}% ({v.get('wins', 0)}/{n} lệnh)")
        else:
            st.caption("Chưa đủ dữ liệu theo strategy.")

    with st.expander("Candidate Config (v4)", expanded=False):
        try:
            from core.ai.optimizer_agent import get_candidate_strategy_config
            cand = get_candidate_strategy_config()
            if cand:
                st.json(cand)
                st.caption("Chạy: python scripts/run_backtest.py → python scripts/promote_candidate.py để so sánh và promote.")
            else:
                st.caption("Chưa có strategy.candidate.json. Chạy reflection rồi: python scripts/run_reflection.py --apply-candidate")
        except Exception as e:
            st.caption(f"Không đọc được candidate: {e}")

    with st.expander("Blocked Trades (lệnh bị risk chặn)", expanded=False):
        try:
            from core.rejected_signals_log import get_rejected_signals
            blocked = get_rejected_signals(limit=30)
            if blocked:
                st.dataframe(pd.DataFrame(blocked), width="stretch")
            else:
                st.caption("Chưa có lệnh nào bị chặn trong phiên gần đây.")
        except Exception as e:
            st.caption(f"Không đọc được log: {e}")

    # ---- Profit Layer v6 ----
    st.subheader("Profit Layer (v6)")
    st.caption("Sizing động (confidence × regime × strategy weight × allocation), volatility guard, expectancy.")
    try:
        from core.profit.volatility_guard import load_profit_config
        from core.profit.strategy_weight_engine import compute_strategy_weights
        from core.profit.expectancy_engine import compute_expectancy_map
        profit_cfg = load_profit_config()
        with st.expander("Profit Layer Decision (công thức & config)", expanded=False):
            st.markdown("**Công thức size:** `base_size × confidence_mult × regime_score × strategy_weight × portfolio_heat_mult`; volatility guard block/reduce trước risk.")
            if profit_cfg.get("sizing"):
                st.json(profit_cfg["sizing"])
            if profit_cfg.get("volatility_guard"):
                st.markdown("**Volatility guard:**")
                st.json(profit_cfg["volatility_guard"])
        with st.expander("Strategy Allocation Table (weight theo PF/win rate)", expanded=True):
            sw = compute_strategy_weights(db, portfolio_id=portfolio_id, lookback_days=30, min_sample=5)
            if sw:
                rows = [{"strategy": s, "weight": w} for s, w in sorted(sw.items(), key=lambda x: -x[1])]
                st.dataframe(pd.DataFrame(rows), width="stretch")
            else:
                st.caption("Chưa đủ lệnh đóng (cần ≥5 lệnh/strategy trong 30 ngày).")
        with st.expander("Volatility Guard", expanded=False):
            st.caption("Block/reduce khi ATR/price hoặc |change_24h| vượt ngưỡng. Lệnh bị chặn có reason chứa 'volatility_guard' trong Blocked Trades.")
            if profit_cfg.get("volatility_guard"):
                st.json(profit_cfg["volatility_guard"])
        with st.expander("Portfolio Heat & Allocation", expanded=False):
            open_pos = list(db.scalars(select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio_id))) if portfolio_id else []
            st.markdown(f"**Vị thế đang mở:** {len(open_pos)}. Heat ≈ tổng R (mỗi lệnh ~1R). Mult giảm khi heat > max_portfolio_heat_r hoặc nhiều lệnh cùng strategy.")
            if profit_cfg.get("allocation"):
                st.json(profit_cfg["allocation"])
        with st.expander("Expectancy Map (R theo strategy × regime × side)", expanded=False):
            exp_map = compute_expectancy_map(db, portfolio_id=portfolio_id, last_n_days=90, min_sample=2)
            if exp_map:
                rows = []
                for (strat, reg, side), v in sorted(exp_map.items(), key=lambda x: -x[1].get("expectancy_r", 0)):
                    rows.append({"strategy": strat, "regime": reg, "side": side, "expectancy_r": v.get("expectancy_r"), "sample": v.get("sample"), "win_rate": v.get("win_rate")})
                st.dataframe(pd.DataFrame(rows), width="stretch")
            else:
                st.caption("Chưa đủ mẫu (≥2 lệnh theo từng combo strategy/regime/side trong 90 ngày).")
    except Exception as e:
        st.caption(f"Profit layer: {e}")

    with st.expander("Regime Map (v4)", expanded=False):
        if quotes_now and watchlist:
            sym = watchlist[0] if watchlist else None
            if sym and sym in quotes_now:
                q = quotes_now[sym]
                reg = derive_regime(q.percent_change_24h, q.volume_24h)
                st.markdown(f"**Regime hiện tại ({sym}):** {reg}")
                if reg == "high_momentum":
                    st.caption("Strategy phù hợp: trend_following, breakout_momentum, liquidity_sweep_reversal (short).")
                elif reg == "risk_off":
                    st.caption("Strategy phù hợp: mean_reversion (long). Tránh momentum.")
                else:
                    st.caption("Regime balanced — chọn strategy theo điều kiện cụ thể.")
            else:
                st.caption("Chưa có giá cho symbol đầu watchlist.")
        else:
            st.caption("Chưa có watchlist hoặc giá.")

    st.markdown("**Khuyến nghị (từ rule + dữ liệu + AI):**")
    for item in recs:
        st.write(f"- {item}")

    # ---- Daily Reports ----
    st.subheader("Daily Reports")
    for report in reports[:5]:
        with st.expander(f"{report.report_date} - {report.headline}"):
            st.markdown(report.summary_markdown)
            st.markdown(report.recommendations_markdown)

    # ---- Equity chart ----
    st.subheader("Equity Snapshots")
    snap_df = pd.DataFrame([
        {"date": s.snapshot_date, "equity_usd": s.equity_usd, "realized_pnl_usd": s.realized_pnl_usd}
        for s in snapshots[:30]
    ])
    if not snap_df.empty:
        st.line_chart(snap_df.set_index("date")[["equity_usd", "realized_pnl_usd"]])
    else:
        st.caption("Chưa có snapshot.")

if auto_refresh > 0:
    import time
    st.caption(f"Tự động refresh sau {auto_refresh} giây...")
    time.sleep(auto_refresh)
    st.rerun()
