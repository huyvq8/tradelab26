from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def ensure_journal_tp_sl_explanation_column():
    """Add journal_entries.tp_sl_explanation if missing (SQLite). Safe to call on every startup."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE journal_entries ADD COLUMN tp_sl_explanation TEXT"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                pass
            else:
                raise


def ensure_positions_hedge_column():
    """Add positions.hedge_of_position_id if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN hedge_of_position_id INTEGER REFERENCES positions(id)"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_positions_entry_regime_column():
    """Add positions.entry_regime if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN entry_regime VARCHAR(40)"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_positions_capital_bucket_column():
    """Add positions.capital_bucket if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN capital_bucket VARCHAR(16) DEFAULT 'core'"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_trades_capital_bucket_column():
    """Add trades.capital_bucket if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE trades ADD COLUMN capital_bucket VARCHAR(16) DEFAULT 'core'"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_journal_capital_bucket_column():
    """Add journal_entries.capital_bucket if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE journal_entries ADD COLUMN capital_bucket VARCHAR(16)"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_journal_setup_hedge_columns():
    """Add journal_entries setup_type, hedge, token_intelligence columns if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    cols = [
        ("setup_type", "VARCHAR(50)"),
        ("hedge_reason", "TEXT"),
        ("hedge_ratio", "REAL"),
        ("token_type", "VARCHAR(30)"),
        ("liquidity_tier", "VARCHAR(20)"),
        ("volatility_tier", "VARCHAR(20)"),
        ("manipulation_risk", "VARCHAR(20)"),
        ("was_strategy_allowed", "INTEGER"),
        ("short_allowed_flag", "INTEGER"),
        ("hedge_allowed_flag", "INTEGER"),
    ]
    with engine.connect() as conn:
        for name, typ in cols:
            try:
                conn.execute(text(f"ALTER TABLE journal_entries ADD COLUMN {name} {typ}"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                    raise


def ensure_trades_brain_cycle_id_column():
    """Add trades.brain_cycle_id if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE trades ADD COLUMN brain_cycle_id VARCHAR(36)"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def ensure_positions_initial_stop_loss_column():
    """Add positions.initial_stop_loss if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    _sqlite_try_add_column("positions", "initial_stop_loss", "REAL")


def ensure_trades_decision_trace_id_column():
    """Add trades.decision_trace_id if missing (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE trades ADD COLUMN decision_trace_id VARCHAR(36)"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                pass
            else:
                raise


def ensure_trades_risk_metadata_columns():
    """Add trades columns for normalized R / entry snapshot / close_source (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    cols = [
        ("planned_r_multiple", "REAL"),
        ("initial_risk_usd", "REAL"),
        ("risk_per_unit", "REAL"),
        ("notional_usd", "REAL"),
        ("intended_entry_price", "REAL"),
        ("intended_stop_loss", "REAL"),
        ("intended_take_profit", "REAL"),
        ("close_source", "VARCHAR(40)"),
        ("realized_r_multiple", "REAL"),
    ]
    for name, typ in cols:
        _sqlite_try_add_column("trades", name, typ)


def _sqlite_try_add_column(table: str, column: str, col_type: str) -> None:
    with engine.connect() as conn:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                pass
            else:
                raise


def ensure_learning_artifact_governance_columns():
    """Add governance columns on learning_artifacts (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    cols = [
        ("confidence", "REAL"),
        ("sample_size", "INTEGER DEFAULT 1"),
        ("evidence_json", "TEXT DEFAULT '{}'"),
        ("promotion_status", "VARCHAR(20) DEFAULT 'none'"),
        ("promoted_proposal_public_id", "VARCHAR(36)"),
    ]
    for name, typ in cols:
        _sqlite_try_add_column("learning_artifacts", name, typ)


def ensure_positions_thesis_columns():
    """Add thesis / zone-shift columns on positions (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    cols = [
        ("thesis_type", "VARCHAR(64)"),
        ("thesis_version", "VARCHAR(16) DEFAULT '1'"),
        ("thesis_metadata_json", "TEXT DEFAULT '{}'"),
        ("thesis_state", "VARCHAR(16) DEFAULT 'NORMAL'"),
        ("thesis_last_score", "REAL"),
        ("thesis_last_reason", "VARCHAR(512)"),
        ("thesis_warning_count", "INTEGER DEFAULT 0"),
        ("thesis_danger_count", "INTEGER DEFAULT 0"),
        ("zone_shift_risk_score", "REAL"),
        ("zone_shift_risk_level", "VARCHAR(16)"),
    ]
    for name, typ in cols:
        _sqlite_try_add_column("positions", name, typ)


def ensure_brain_v4_p1_trace_columns():
    """Add decision_trace_id / market_decision_trace_id on brain event tables (SQLite)."""
    if "sqlite" not in (settings.database_url or "").lower():
        return
    pairs = [
        ("brain_cycles", "market_decision_trace_id", "VARCHAR(36)"),
        ("state_inference_events", "decision_trace_id", "VARCHAR(36)"),
        ("state_inference_events", "market_decision_trace_id", "VARCHAR(36)"),
        ("change_point_events", "decision_trace_id", "VARCHAR(36)"),
        ("change_point_events", "market_decision_trace_id", "VARCHAR(36)"),
        ("policy_mode_events", "decision_trace_id", "VARCHAR(36)"),
        ("reflex_action_events", "decision_trace_id", "VARCHAR(36)"),
        ("reflex_action_events", "market_decision_trace_id", "VARCHAR(36)"),
    ]
    for table, col, typ in pairs:
        _sqlite_try_add_column(table, col, typ)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
