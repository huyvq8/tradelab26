"""Proposal + runtime override (SQLite memory)."""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.db import Base
from core.brain.p2_models import LearningArtifact
from core.brain.proposal_service import (
    activate_proposal_runtime_override,
    approve_proposal,
    create_proposal_from_learning_artifact,
    rollback_proposal,
)
from core.profit.thesis_profiles import _load_thesis_management_file, load_thesis_management_config


def _sess() -> Session:
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, future=True)()


def test_proposal_pipeline_merges_thesis_config():
    db = _sess()
    before_w = float(
        (_load_thesis_management_file().get("profiles") or {}).get("default", {}).get(
            "warning_zone_shift", 0.45
        )
    )
    art = LearningArtifact(
        symbol="BTC",
        payload_json=json.dumps(
            {"pnl_usd": -10, "thesis_state": "INVALID", "thesis_type": "generic"}
        ),
        sample_size=5,
        artifact_type="trade_close_summary",
    )
    db.add(art)
    db.commit()

    prop = create_proposal_from_learning_artifact(db, art.id, force=True)
    assert prop is not None
    assert prop.status == "proposed"

    assert approve_proposal(db, prop.public_id, reviewer_label="tester", notes="ok")
    db.commit()

    ov = activate_proposal_runtime_override(
        db, prop.public_id, rollout_mode="full", reviewer_label="tester"
    )
    assert ov is not None
    db.commit()

    cfg = load_thesis_management_config(db)
    after_w = float(cfg["profiles"]["default"]["warning_zone_shift"])
    assert after_w < before_w

    assert rollback_proposal(db, prop.public_id, "unit_test")
    db.commit()
    cfg2 = load_thesis_management_config(db)
    assert (
        float(cfg2["profiles"]["default"]["warning_zone_shift"]) == before_w
    )  # override rolled back
    db.close()
