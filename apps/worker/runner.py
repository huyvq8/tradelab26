import logging
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from datetime import date
import uuid

from apscheduler.schedulers.blocking import BlockingScheduler

from core.logging_config import setup_app_logging
from core.log_monitor import scan_log_and_report

setup_app_logging()
logger = logging.getLogger(__name__)

from sqlalchemy import select
from core.db import (
    SessionLocal,
    Base,
    engine,
    ensure_brain_v4_p1_trace_columns,
    ensure_learning_artifact_governance_columns,
    ensure_positions_thesis_columns,
    ensure_trades_brain_cycle_id_column,
    ensure_trades_decision_trace_id_column,
    ensure_trades_risk_metadata_columns,
)
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot

try:
    import core.brain.models  # noqa: F401 — register Brain V4 tables before create_all
    import core.brain.p2_models  # noqa: F401
except ImportError:
    pass
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport
from core.config import settings
from core.execution import get_execution_backend
from core.orchestration.cycle import SimulationCycle
from core.observability.reject_reason_codes import normalize_entry_reject_reason_code_for_summary
from core.observability.reject_classification import classify_entry_reject
from core.reporting.service import DailyReportService
from core.watchlist import get_effective_execution_watchlist, get_watchlist
from core.signals.analysis import build_entry_analysis_from_dict, format_telegram_alert

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
    ensure_trades_risk_metadata_columns()
    ensure_brain_v4_p1_trace_columns()
    ensure_positions_thesis_columns()
    ensure_learning_artifact_governance_columns()
except Exception:
    pass

scheduler = BlockingScheduler()


def _send_telegram(text: str) -> None:
    from core.config import settings
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        from integrations.telegram.report import send_report
        send_report(settings.telegram_bot_token, settings.telegram_chat_id, text[:4000])
    except Exception as e:
        print(f"[Telegram] Loi gui tin: {e}")


def _log_backend_once():
    """In một lần backend đang dùng để kiểm tra cấu hình (Binance thật hay Paper)."""
    from core.execution import get_execution_backend, get_effective_enable_live_binance_futures
    from core.config import settings
    enable = get_effective_enable_live_binance_futures()
    has_key = bool((getattr(settings, "binance_api_key", None) or "").strip())
    has_secret = bool((getattr(settings, "binance_api_secret", None) or "").strip())
    backend = get_execution_backend()
    is_binance = type(backend).__name__ == "BinanceFuturesExecutor"
    if is_binance:
        testnet = getattr(backend, "base_url", "").find("testnet") >= 0
        print(f"[Cycle] Backend: Binance Futures ({'Testnet' if testnet else 'Mainnet'}) -> lenh se tao tren san.")
    else:
        reason = []
        if not enable:
            reason.append("chua bat danh that (dashboard hoac .env ENABLE_LIVE_BINANCE_FUTURES)")
        if not has_key:
            reason.append("thieu BINANCE_API_KEY trong .env")
        if not has_secret:
            reason.append("thieu BINANCE_API_SECRET trong .env")
        print(f"[Cycle] Backend: Paper (khong tao lenh tren Binance). Ly do: {'; '.join(reason) or 'unknown'}")


_run_cycle_backend_logged = False


# APScheduler max_instances=1: nếu cycle trước chưa xong (review + HTTP + AI) thì lần sau bị skip. Mặc định interval 15s (config); tăng thêm nếu vẫn skip thường xuyên.
@scheduler.scheduled_job("interval", seconds=max(5, getattr(settings, "cycle_interval_seconds", 10)))
def run_cycle_job():
    global _run_cycle_backend_logged
    if not _run_cycle_backend_logged:
        _log_backend_once()
        _run_cycle_backend_logged = True
    watchlist_ctx = get_effective_execution_watchlist()
    symbols = list(watchlist_ctx.get("effective_watchlist") or [])
    if not symbols:
        print("[Cycle] Watchlist rong -> khong co symbol de quet. Them symbol trong Sidebar.")
    try:
        exec_backend = get_execution_backend()
        # Bước 2 (document/request): WebSocket giá + User Data Stream khi Binance Futures
        if type(exec_backend).__name__ == "BinanceFuturesExecutor":
            try:
                from core.market_data.binance_futures_ws import get_binance_futures_ws_manager
                get_binance_futures_ws_manager().start(symbols)
            except Exception:
                pass
            try:
                exec_backend.start_user_stream_if_enabled()
            except Exception:
                pass
        with SessionLocal() as db:
            cycle = SimulationCycle()
            brain_cycle_id = str(uuid.uuid4())
            cycle.check_sl_tp_and_close(db, "Paper Portfolio")
            # Khi đánh thật Binance: đồng bộ DB với sàn (vị thế đã đóng bởi TP/SL/Trailing trên sàn)
            if type(exec_backend).__name__ == "BinanceFuturesExecutor":
                sync_res = cycle.sync_positions_from_binance(db, "Paper Portfolio")
                if sync_res.get("closed", 0) > 0:
                    print(f"[Cycle] Dong bo Binance: {sync_res['closed']} vi the da dong tren san (TP/SL/Trailing).")
                if sync_res.get("merged", 0) > 0:
                    print(f"[Cycle] Dong bo Binance: {sync_res['merged']} vi the da gop (san gop cung symbol+side, DB cap nhat 1 ban ghi).")
                # Số dư thật ở trên sàn: cập nhật portfolio.cash_usd = available balance để dashboard và equity không bị cộng dồn sai.
                bal = exec_backend.get_available_balance_usd()
                if bal is not None and bal >= 0:
                    port = db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
                    if port:
                        port.cash_usd = round(bal, 2)
                        # v6: đồng bộ capital_usd với balance khả dụng — RiskEngine + scale-in dùng cùng nguồn vốn thật
                        port.capital_usd = round(bal, 2)
            brain_ctx = None
            try:
                from core.brain.context import build_brain_v4_tick_context

                brain_ctx = build_brain_v4_tick_context(
                    db,
                    portfolio_name="Paper Portfolio",
                    symbols=symbols,
                    brain_cycle_id=brain_cycle_id,
                )
            except Exception:
                brain_ctx = None
            # Đánh giá từng vị thế hiện tại: cần đóng? cần update TP/SL? hay giữ? — rồi thực hiện và log
            position_actions = cycle.review_positions_and_act(
                db,
                "Paper Portfolio",
                brain_v4_ctx=brain_ctx,
                brain_cycle_id=brain_cycle_id,
            )
            for pa in position_actions:
                print(f"[Cycle] Vi the {pa['symbol']} ({pa['side']}): {pa['action']} — {pa['reason']}")
            result = cycle.run(db, "Paper Portfolio", symbols, brain_v4_ctx=brain_ctx)
            db.commit()
        opened = {p["symbol"] for p in result.get("opened_positions") or []}
        signals_fired = result.get("signals_fired") or []
        rejected = result.get("rejected_signals") or []
        skipped_already_open = result.get("skipped_already_open") or []
        n_signals = len(signals_fired)
        n_opened = len(opened)
        n_rejected = len(rejected)
        # Luôn in tóm tắt mỗi cycle (console + file log) để debug khi "báo đẹp nhưng không đánh"
        dr = result.get("daily_realized_r")
        du = result.get("daily_realized_usd")
        rc = result.get("risk_capital_usd")
        extra = ""
        if dr is not None and du is not None:
            extra = f" | daily_R={dr} daily_PnL_usd={du}"
        if rc is not None:
            extra += f" risk_capital={rc}"
        effective_symbols = list(watchlist_ctx.get("effective_watchlist") or [])
        reasons_by_symbol: dict[str, set[str]] = {}
        for r in rejected:
            sym = str(r.get("symbol") or "").strip().upper() or "UNKNOWN"
            code = normalize_entry_reject_reason_code_for_summary(r)
            reasons_by_symbol.setdefault(sym, set()).add(code)
        reject_reason_summary = ", ".join(
            f"{sym}:{'|'.join(sorted(codes))}"
            for sym, codes in sorted(reasons_by_symbol.items())
        )
        reject_suffix = f" [{reject_reason_summary}]" if reject_reason_summary else ""
        _interval_s = max(5, int(getattr(settings, "cycle_interval_seconds", 10) or 10))
        _cdur = result.get("cycle_duration_sec")
        _dur_part = ""
        if _cdur is not None:
            _dur_part = f" | cycle_duration_sec={_cdur}"
            try:
                _cdur_f = float(_cdur)
                if _cdur_f >= _interval_s * 0.85:
                    _ow = (
                        f"[Cycle] WARN cycle_duration_sec={_cdur_f} is >=85% of cycle_interval_seconds={_interval_s} "
                        "(APScheduler max_instances=1 may skip the next tick)."
                    )
                    logger.warning(_ow)
                    print(_ow)
            except (TypeError, ValueError):
                pass
        cycle_summary = (
            f"[Cycle] symbols={result.get('symbols', 0)}"
            f" | manual_watchlist_count={watchlist_ctx.get('manual_watchlist_count', 0)}"
            f" | dynamic_shortlist_count={watchlist_ctx.get('dynamic_shortlist_count', 0)}"
            f" | effective_execution_watchlist_count={watchlist_ctx.get('effective_execution_watchlist_count', 0)}"
            f" | effective_execution_symbols={','.join(effective_symbols)}"
            f" | tin_hieu={n_signals} | mo_lenh={n_opened} | tu_choi={n_rejected}{reject_suffix}{extra}{_dur_part}"
        )
        logger.info(cycle_summary)
        print(cycle_summary)
        _scope = result.get("strategy_scope_in_cycle") or []
        _eval_cand = result.get("evaluated_candidate_symbols") or []
        _rows_after_filter = result.get("candidate_rows_after_strategy_filter")
        _sig_syms = result.get("signals_fired_symbols") or []
        _opened_syms = result.get("opened_symbols") or []
        _opened_positions = result.get("opened_positions") or []
        _eff_rows = []
        for _op in _opened_positions:
            _ratio = _op.get("risk_efficiency_ratio")
            if isinstance(_ratio, (int, float)):
                _eff_rows.append((str(_op.get("symbol") or ""), float(_ratio)))
        _eff_avg = (sum(v for _, v in _eff_rows) / len(_eff_rows)) if _eff_rows else None
        _eff_sample = ",".join(f"{sym}:{round(val * 100, 1)}%" for sym, val in _eff_rows[:5]) if _eff_rows else "none"
        _rej_bucket_count = 0
        _rej_sizing_count = 0
        for _r in rejected:
            _rc = normalize_entry_reject_reason_code_for_summary(_r)
            _b = classify_entry_reject(_rc)
            if _b == "policy_reject":
                _rej_bucket_count += 1
            elif _b == "sizing_reject":
                _rej_sizing_count += 1
        exec_diag = (
            f"[Cycle][exec_diag] strategy_scope_in_cycle={_scope}"
            f" | evaluated_candidate_symbols={_eval_cand}"
            f" | candidate_rows_after_strategy_filter={_rows_after_filter}"
            f" | signals_fired_symbols={_sig_syms}"
            f" | rejected_symbols_with_reasons={reject_reason_summary or 'none'}"
            f" | opened_symbols={_opened_syms}"
            f" | opened_risk_efficiency_avg={round(_eff_avg * 100, 1) if _eff_avg is not None else 'n/a'}%"
            f" | opened_risk_efficiency_sample={_eff_sample}"
            f" | rejected_bucket_count={_rej_bucket_count}"
            f" | rejected_sizing_count={_rej_sizing_count}"
        )
        logger.info(exec_diag)
        print(exec_diag)
        if skipped_already_open:
            # Gộp theo symbol để tránh spam: 1 dòng/symbol/cycle (vd. "SIREN: da co 1/1 vi the, bo qua 2 tin hieu (trend_following, breakout_momentum)")
            by_symbol = {}
            for s in skipped_already_open:
                # Format: "SYMBOL (strategy_name) — reason" hoặc "SYMBOL (strategy_name) — ..."
                part = s.split(" — ", 1)
                if part:
                    left = part[0].strip()
                    reason = part[1].strip() if len(part) > 1 else ""
                    if " (" in left and ")" in left:
                        sym = left.split(" (")[0].strip()
                        strat = left.split(" (")[1].rstrip(")").strip()
                    else:
                        sym = left
                        strat = ""
                    if sym not in by_symbol:
                        by_symbol[sym] = {"strategies": [], "reason": reason}
                    if strat and strat not in by_symbol[sym]["strategies"]:
                        by_symbol[sym]["strategies"].append(strat)
            for sym, data in by_symbol.items():
                strats = ", ".join(data["strategies"]) if data["strategies"] else "?"
                reason_short = data["reason"].split("[")[0].strip() if data["reason"] else "da dat so lenh/symbol"
                print(f"[Cycle] Bo qua {sym}: {reason_short} | tin hieu bi skip: {strats}")
        if signals_fired and len(opened) == 0 and rejected:
            try:
                from core.rejected_signals_log import log_rejected
                for r in rejected:
                    log_rejected(
                        r.get("symbol", ""),
                        r.get("strategy_name", ""),
                        r.get("reason", ""),
                        reason_code=r.get("reason_code"),
                        meta=r.get("meta"),
                    )
            except Exception:
                pass
            for r in rejected:
                msg = f"[Cycle] Tin hieu {r.get('symbol')} ({r.get('strategy_name')}) nhung risk tu choi: {r.get('reason', '')}"
                logger.info(msg)
        elif signals_fired and len(opened) == 0:
            print(f"[Cycle] Co {len(signals_fired)} tin hieu nhung khong mo lenh (co the do loi khi dat lenh - xem log tren).")
        for sig in signals_fired:
            try:
                from core.observability.telegram_signal_dedupe import should_send_signal_telegram

                if not should_send_signal_telegram(sig):
                    continue
            except Exception:
                pass
            analysis = build_entry_analysis_from_dict(sig)
            msg = format_telegram_alert(analysis)
            if sig["symbol"] in opened:
                msg += "\n\n✅ Da tu dong mo lenh paper."
            _send_telegram(msg)
    except Exception as e:
        print(f"[Cycle] Loi khi chay cycle: {e}")
        logger.exception("Cycle job error")


@scheduler.scheduled_job("interval", seconds=600)  # Mỗi 10 phút: quét logic toàn hệ thống, đánh giá đa góc nhìn chuyên gia
def system_review_job():
    """Tự quét logic ra quyết định, đánh giá từ góc nhìn risk / edge / hành vi / vận hành, tìm điểm yếu và gợi ý cải thiện."""
    try:
        from core.reflection.system_review import run_system_review
        with SessionLocal() as db:
            report = run_system_review(db, portfolio_name="Paper Portfolio", use_ai_synthesis=bool(getattr(settings, "openai_api_key", None) and (settings.openai_api_key or "").strip()))
            db.commit()
        print(f"[SystemReview] {report.get('summary', '')[:300]}")
        for p in report.get("perspectives", []):
            for f in (p.get("findings") or [])[:2]:
                print(f"  [{p.get('name')}] {f[:120]}")
        if report.get("ai_synthesis"):
            print(f"[SystemReview] AI: {report['ai_synthesis'][:250]}...")
            if settings.telegram_bot_token and settings.telegram_chat_id:
                _send_telegram(f"🔍 System Review (10 phút)\n\n{report.get('summary', '')[:300]}\n\nAI: {report['ai_synthesis'][:500]}")
    except Exception as e:
        print(f"[SystemReview] Loi: {e}")
        logger.exception("SystemReview job error")


@scheduler.scheduled_job("interval", seconds=600)  # Mỗi 10 phút: đọc log, nếu có lỗi gửi Telegram
def log_monitor_job():
    """Job chuyên đọc log: quét file logs/trading_lab.log tìm dòng lỗi, gửi Telegram để kiểm soát."""
    try:
        scan_log_and_report(last_n_lines=500, send_telegram=True)
    except Exception as e:
        logger.exception("Log monitor job error: %s", e)


@scheduler.scheduled_job("cron", minute=5)  # Mỗi giờ tại phút 5 (00:05, 01:05, ...)
def hourly_situation_job():
    """Nếu nến 1h vừa đóng có body lớn hoặc volume spike → gọi AI phân tích tình huống và gửi Telegram."""
    if not getattr(settings, "openai_api_key", None) or not settings.openai_api_key.strip():
        return
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        from core.market_data.client import get_klines_1h, get_klines_5m, get_quotes_with_fallback
        from core.reflection.ai_situation import analyze_market_situation
    except Exception:
        return
    symbols = get_watchlist()
    if not symbols:
        return
    quotes = get_quotes_with_fallback(symbols)
    from sqlalchemy import select
    with SessionLocal() as db:
        open_positions = list(db.scalars(select(Position).where(Position.is_open == True)))
    sent = 0
    for symbol in symbols[:5]:
        if sent >= 2:
            break
        try:
            klines = get_klines_1h(symbol, limit=3)
            if len(klines) < 2:
                continue
            last_ = klines[-1]
            prev_ = klines[-2]
            o, c = last_.open, last_.close
            body_pct = abs(c - o) / o * 100 if o and o > 0 else 0
            vol_ratio = (last_.volume / prev_.volume) if prev_.volume and prev_.volume > 0 else 0
            if body_pct < 4 and vol_ratio < 1.8:
                continue
            quote = quotes.get(symbol)
            if not quote:
                continue
            pos_for_symbol = [
                {"side": p.side, "entry_price": p.entry_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit, "strategy_name": p.strategy_name}
                for p in open_positions if p.symbol == symbol
            ]
            klines_5m = get_klines_5m(symbol, limit=12)
            result = analyze_market_situation(
                symbol=symbol,
                klines_1h=klines,
                quote_price=quote.price,
                quote_pct_24h=quote.percent_change_24h,
                quote_volume_24h=quote.volume_24h,
                positions_for_symbol=pos_for_symbol,
                extra_notes="",
                klines_5m=klines_5m,
                reason="hourly_situation",
            )
            if result:
                _send_telegram(f"📊 Tình huống {symbol} (nến 1h body {body_pct:.1f}% vol {vol_ratio:.1f}x)\n\n{result[:3500]}")
                sent += 1
        except Exception as e:
            print(f"[Hourly situation] {symbol}: {e}")
            logger.exception("Hourly situation error for %s", symbol)


def _run_situation_for_symbol(symbol: str, open_positions: list, quotes: dict, send_telegram: bool = True) -> bool:
    """Chạy phân tích tình huống cho một symbol (1h + 5m), gửi Telegram nếu send_telegram. Trả về True nếu đã gửi."""
    try:
        from core.market_data.client import get_klines_1h, get_klines_5m, get_quotes_with_fallback
        from core.reflection.ai_situation import analyze_market_situation
    except Exception:
        return False
    quote = quotes.get(symbol) if quotes else None
    if not quote:
        q = get_quotes_with_fallback([symbol])
        quote = q.get(symbol)
    if not quote:
        return False
    klines_1h = get_klines_1h(symbol, limit=5)
    klines_5m = get_klines_5m(symbol, limit=12)
    pos_for_symbol = [
        {"side": p.side, "entry_price": p.entry_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit, "strategy_name": getattr(p, "strategy_name", "")}
        for p in open_positions if getattr(p, "symbol", None) == symbol
    ]
    result = analyze_market_situation(
        symbol=symbol,
        klines_1h=klines_1h,
        quote_price=quote.price,
        quote_pct_24h=quote.percent_change_24h,
        quote_volume_24h=quote.volume_24h,
        positions_for_symbol=pos_for_symbol,
        extra_notes="",
        klines_5m=klines_5m,
        reason="auto_situation",
    )
    if result and send_telegram:
        _send_telegram(f"📊 Tình huống (tự động) {symbol}\n\n{result[:3500]}")
        return True
    return False


@scheduler.scheduled_job("interval", minutes=15)
def auto_situation_job():
    """Tự chạy phân tích tình huống: mỗi 15 phút, với các symbol đang có vị thế mở (và tối đa 1 symbol trong watchlist nếu không có vị thế)."""
    if not getattr(settings, "openai_api_key", None) or not settings.openai_api_key.strip():
        return
    try:
        from core.market_data.client import get_quotes_with_fallback
    except Exception:
        return
    from sqlalchemy import select
    with SessionLocal() as db:
        open_positions = list(db.scalars(select(Position).where(Position.is_open == True)))
    symbols_with_positions = list({p.symbol for p in open_positions})
    watchlist = get_watchlist()
    # Ưu tiên symbol có vị thế; nếu không có thì lấy tối đa 1 symbol từ watchlist
    to_analyze = symbols_with_positions if symbols_with_positions else (watchlist[:1] if watchlist else [])
    if not to_analyze:
        return
    quotes = get_quotes_with_fallback(to_analyze)
    sent = 0
    for symbol in to_analyze[:3]:
        if sent >= 1:
            break
        if _run_situation_for_symbol(symbol, open_positions, quotes, send_telegram=bool(settings.telegram_bot_token and settings.telegram_chat_id)):
            sent += 1
    # Không gửi Telegram thì vẫn chạy phân tích (kết quả có thể lưu/ghi log sau nếu cần)


@scheduler.scheduled_job("cron", hour=23, minute=55)
def daily_report_job():
    from core.config import settings
    with SessionLocal() as db:
        report = DailyReportService().generate(db, date.today())
        db.commit()
    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            from integrations.telegram.report import send_report
            text = f"{report.headline}\n\n{report.summary_markdown[:1500]}\n\n{report.recommendations_markdown[:1000]}"
            send_report(settings.telegram_bot_token, settings.telegram_chat_id, text)
        except Exception as e:
            print(f"[Telegram] Loi gui bao cao ngay: {e}")


if __name__ == "__main__":
    scheduler.start()
