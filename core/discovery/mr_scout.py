from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.market_data.client import get_top_symbols_quotes_by_volume, get_quotes_with_fallback, get_klines_1h, MarketQuote
from core.profit.volatility_guard import check_volatility_guard
from core.regime.detector import derive_regime
from core.risk.quick_sizing import estimate_max_size_usd_from_risk
from core.risk.trade_r_metrics import planned_r_multiple
from core.strategies.implementations import build_strategy_set

_ROOT = Path(__file__).resolve().parents[2]
_CFG = _ROOT / "config" / "mr_opportunity.v1.json"


@dataclass
class MRCandidate:
    symbol: str
    planned_r_multiple: float
    confidence: float
    regime: str
    change_24h: float
    volume_24h: float
    executable_after_sizing: bool
    blocked_by_volatility: bool


def load_mr_opportunity_config() -> dict:
    if not _CFG.exists():
        return {
            "enabled": True,
            "top_universe": 120,
            "min_volume_usd": 1_000_000,
            "result_top_n": 12,
            "oversold_change_24h_max": -6.0,
            "min_planned_r_multiple": 0.8,
            "min_notional_usd": 25.0,
            "volatility_block_reduce_pct": 0.5,
            "dynamic_watchlist_when_mr_only": True,
            "dynamic_watchlist_top_n": 6,
        }
    try:
        return json.loads(_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True}


def _mr_strategy():
    for s in build_strategy_set():
        if getattr(s, "name", "") == "mean_reversion":
            return s
    return None


def evaluate_symbol_mr_fitness(symbol: str, quote: MarketQuote, cfg: dict, *, available_cash: float = 1000.0, capital_usd_for_risk: float = 1000.0, risk_pct: float = 0.01) -> dict:
    chg = float(quote.percent_change_24h or 0)
    vol = float(quote.volume_24h or 0)
    regime = derive_regime(chg, vol)
    oversold_max = float(cfg.get("oversold_change_24h_max", -6.0))
    min_r = float(cfg.get("min_planned_r_multiple", 0.8))
    min_notional = float(cfg.get("min_notional_usd", 25.0))
    vol_reduce_block = float(cfg.get("volatility_block_reduce_pct", 0.5))

    strat = _mr_strategy()
    if strat is None:
        return {"symbol": symbol, "mr_eligible": False, "why_not": "mean_reversion strategy unavailable"}

    if chg > oversold_max:
        return {
            "symbol": symbol,
            "mr_eligible": False,
            "why_not": f"not oversold enough ({chg:.2f}% > {oversold_max:.2f}%)",
            "regime": regime,
            "change_24h": chg,
            "volume_24h": vol,
        }

    sig = strat.evaluate(symbol, quote.price, chg, vol, regime)
    if not sig:
        return {
            "symbol": symbol,
            "mr_eligible": False,
            "why_not": "regime/setup mismatch for MR evaluate",
            "regime": regime,
            "change_24h": chg,
            "volume_24h": vol,
        }

    pr = planned_r_multiple(sig)
    if pr is None or pr < min_r:
        return {
            "symbol": symbol,
            "mr_eligible": False,
            "why_not": f"planned R too low ({(pr if pr is not None else 0):.2f} < {min_r:.2f})",
            "regime": regime,
            "planned_r_multiple": pr,
            "change_24h": chg,
            "volume_24h": vol,
        }

    est = estimate_max_size_usd_from_risk(
        sig,
        available_cash=float(available_cash),
        capital_usd_for_risk=float(capital_usd_for_risk),
        risk_pct=float(risk_pct),
    )
    if float(est) < min_notional:
        return {
            "symbol": symbol,
            "mr_eligible": False,
            "why_not": f"exchange min issue (est {est:.2f} < min_notional {min_notional:.2f})",
            "regime": regime,
            "planned_r_multiple": pr,
            "estimate_max_from_risk_usd": round(float(est), 4),
            "change_24h": chg,
            "volume_24h": vol,
        }

    try:
        kl = get_klines_1h(symbol, limit=20)
    except Exception:
        kl = []
    vg = check_volatility_guard(symbol, quote, kl, config=None)
    blocked_vol = (not vg.allow_trade) or (float(vg.reduce_size_pct or 0) >= vol_reduce_block)
    if blocked_vol:
        return {
            "symbol": symbol,
            "mr_eligible": False,
            "why_not": f"volatility block/compression ({vg.block_reason or 'reduce'})",
            "regime": regime,
            "planned_r_multiple": pr,
            "volatility_reduce_size_pct": float(vg.reduce_size_pct or 0),
            "change_24h": chg,
            "volume_24h": vol,
        }

    return {
        "symbol": symbol,
        "mr_eligible": True,
        "why_not": "",
        "regime": regime,
        "planned_r_multiple": round(float(pr), 4),
        "confidence": float(sig.confidence or 0),
        "change_24h": chg,
        "volume_24h": vol,
        "estimate_max_from_risk_usd": round(float(est), 4),
    }


def scan_mr_candidates(*, cfg: dict | None = None, available_cash: float = 1000.0, capital_usd_for_risk: float = 1000.0, risk_pct: float = 0.01) -> list[dict]:
    c = cfg or load_mr_opportunity_config()
    quotes = get_top_symbols_quotes_by_volume(
        top_n=int(c.get("top_universe", 120)),
        min_volume_usd=float(c.get("min_volume_usd", 1_000_000)),
    )
    out: list[dict] = []
    for symbol, q in quotes.items():
        fit = evaluate_symbol_mr_fitness(
            symbol,
            q,
            c,
            available_cash=available_cash,
            capital_usd_for_risk=capital_usd_for_risk,
            risk_pct=risk_pct,
        )
        if fit.get("mr_eligible"):
            out.append(fit)
    out.sort(key=lambda x: (float(x.get("planned_r_multiple") or 0), float(x.get("confidence") or 0)), reverse=True)
    return out[: int(c.get("result_top_n", 12))]


def watchlist_mr_fitness(symbols: list[str], cfg: dict | None = None) -> list[dict]:
    c = cfg or load_mr_opportunity_config()
    if not symbols:
        return []
    quotes = get_quotes_with_fallback(symbols)
    out: list[dict] = []
    for s in symbols:
        q = quotes.get(s)
        if not q:
            out.append({"symbol": s, "mr_eligible": False, "why_not": "quote unavailable"})
            continue
        out.append(evaluate_symbol_mr_fitness(s, q, c))
    return out
