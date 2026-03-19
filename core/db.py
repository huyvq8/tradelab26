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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
