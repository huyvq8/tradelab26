"""Streamlit sections for Brain P2 / governance dashboard."""
from __future__ import annotations

from typing import Any, Callable

from core.brain.p2_dashboard_queries import (
    fetch_learning_artifacts_board,
    fetch_portfolio_brain_board,
    fetch_proposal_governance_board,
    fetch_strategy_thesis_fit_stats,
    fetch_thesis_open_health,
)


def render_brain_p2_sections(session_factory: Callable[..., Any], portfolio_name: str = "Paper Portfolio") -> None:
    import pandas as pd
    import streamlit as st

    st.subheader("Brain P2 — Thesis, học có giám sát & governance (§8.2)")
    st.caption(
        "Không ghi đè file config trong repo; override chạy qua bảng runtime + TTL. "
        "Đề xuất từ learning artifact cần duyệt (class B/C)."
    )
    try:
        with session_factory() as db:
            th = fetch_thesis_open_health(db, portfolio_name)
            fit = fetch_strategy_thesis_fit_stats(db)
            learn = fetch_learning_artifacts_board(db)
            gov = fetch_proposal_governance_board(db)
            port = fetch_portfolio_brain_board(db, portfolio_name)
    except Exception as e:
        st.warning(f"Không đọc P2: {e}")
        return

    with st.expander("1) Thesis health — lệnh đang mở", expanded=True):
        if th.get("error"):
            st.caption("Chưa có portfolio.")
        else:
            st.metric("Open positions", th.get("open_count", 0))
            st.metric("INVALID thesis (open)", th.get("invalid_count", 0))
            if th.get("positions"):
                st.dataframe(pd.DataFrame(th["positions"]), width="stretch")
            else:
                st.caption("Không có vị thế mở.")

    with st.expander("2) Strategy — thesis fit (từ learning artifacts gần đây)", expanded=False):
        st.json(fit.get("by_thesis_type") or {})
        st.caption(f"Mẫu artifact trong cửa sổ: {fit.get('sample_artifacts', 0)}")

    with st.expander("3) Learning artifacts", expanded=False):
        st.json(
            {
                "promoted_in_window": learn.get("promoted_in_window"),
                "not_promoted_in_window": learn.get("not_promoted_in_window"),
            }
        )
        if learn.get("recent"):
            st.dataframe(pd.DataFrame(learn["recent"]), width="stretch")

    with st.expander("4) Proposal governance", expanded=False):
        st.metric("Active runtime overrides", gov.get("active_overrides", 0))
        st.caption(f"Targets: {', '.join(gov.get('override_targets') or []) or '—'}")
        if gov.get("pending_detail"):
            st.dataframe(pd.DataFrame(gov["pending_detail"]), width="stretch")
        else:
            st.caption("Không có proposal pending.")

    with st.expander("5) Portfolio state", expanded=False):
        if port.get("error"):
            st.caption("Chưa có portfolio.")
        else:
            st.markdown(
                f"**Latest:** `{port.get('latest_state')}` — stress `{port.get('latest_stress')}` @ `{port.get('latest_at')}`"
            )
            st.metric("Thesis divergence (WARN/DANGER/INVALID open)", port.get("thesis_divergence_count", 0))
            st.metric("Concentration (max symbol share)", port.get("concentration_max_symbol_share", 0))
            st.json({"cluster_exposure": port.get("cluster_exposure") or {}})
