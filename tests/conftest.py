"""SQLite migrations for tests hitting the dev DB schema."""
from __future__ import annotations

import pytest

from core.db import ensure_trades_risk_metadata_columns


@pytest.fixture(scope="session", autouse=True)
def _ensure_trades_risk_columns() -> None:
    ensure_trades_risk_metadata_columns()
