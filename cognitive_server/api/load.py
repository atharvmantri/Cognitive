"""
Cognitive Server - Load Score API
Endpoints: GET /api/v1/load/current, GET /api/v1/load/history
Returns CLS (Cognitive Load Score) and load state.
Uses adaptive personalizer for self-improving predictions.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from cognitive_server.db import sqlite_store
from cognitive_server.ml.features import engineer_features
from cognitive_server.ml.inference import compute_cls_heuristic, compute_cls_model
from cognitive_server.ml.personalizer import get_personalizer

router = APIRouter(tags=["load"])

USE_MODEL = os.environ.get("COGNITIVE_USE_MODEL", "false").lower() == "true"


def _classify_state(cls_score: float) -> str:
    """Map CLS score to a named load state using personalizer thresholds."""
    return get_personalizer().get_load_state(cls_score)


def _compute_estimated_recovery(cls_score: float) -> Optional[str]:
    """Estimate when CLS will drop below 40 based on simple decay."""
    if cls_score <= 40:
        return datetime.now(timezone.utc).isoformat()
    minutes_to_recovery = (cls_score - 40) / 5.0 * 10.0
    recovery_time = datetime.now(timezone.utc) + timedelta(minutes=minutes_to_recovery)
    return recovery_time.isoformat()


@router.get("/load/current", summary="Get current cognitive load",
             description="Returns the latest CLS score, load state, 2-hour trend, and estimated recovery time.")
async def get_current_load():
    """Return current cognitive load score and classification."""
    try:
        personalizer = get_personalizer()
        record = await sqlite_store.get_current_load()

        if record and USE_MODEL:
            # Use persisted model score + adaptive blending
            cls_score = record["cls_score"]
            confidence = record.get("confidence", 1.0)
            features = record.get("features_json", {})
            if isinstance(features, str):
                import json
                try:
                    features = json.loads(features)
                except:
                    features = {}

            heuristic_score = compute_cls_heuristic(features) if features else cls_score
            cls_score, confidence = personalizer.compute_adaptive_score(
                features,
                base_heuristic_score=heuristic_score,
                ml_score=cls_score,
            )
            source = "adaptive"
        elif record:
            # Record exists but model mode off - use record score with personalizer classification
            cls_score = record["cls_score"]
            confidence = record.get("confidence", 1.0)
            source = record.get("source", "heuristic")
        else:
            # No record yet - compute from recent signals
            recent_signals = await sqlite_store.get_signals_recent(hours=0.5)
            if not recent_signals:
                return {
                    "cognitive_load_score": None,
                    "confidence": None,
                    "state": "learning",
                    "trend": [],
                    "estimated_recovery": None,
                    "message": "Collecting baseline signals. Waiting for data..."
                }

            features = engineer_features(recent_signals)
            cls_score = compute_cls_heuristic(features)
            confidence = round(1.0 - (abs(cls_score - 50) / 100.0), 2)
            source = "heuristic"

            await sqlite_store.insert_load_record(
                cls_score=cls_score,
                confidence=confidence,
                load_state=personalizer.get_load_state(cls_score),
                source=source,
                features=features,
            )

        state = personalizer.get_load_state(cls_score)

        # Get 2-hour trend
        trend_records = await sqlite_store.get_load_history(hours=2.0)
        trend = [r["cls_score"] for r in trend_records]

        recovery = _compute_estimated_recovery(cls_score)
        
        # Get personalization stats
        p_stats = personalizer.get_stats()

        return {
            "cognitive_load_score": round(cls_score, 2),
            "confidence": round(confidence, 3),
            "state": state,
            "source": source,
            "trend": trend,
            "estimated_recovery": recovery,
            "load_state_label": state.upper(),
            "personalization": p_stats,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute load: {str(e)}",
        )


@router.get("/load/history", summary="Get load history",
            description="Retrieve CLS history for trend visualization over the last N hours.")
async def get_load_history(hours: float = 2.0):
    """Get CLS trend data for sparkline visualization."""
    try:
        records = await sqlite_store.get_load_history(hours=hours)
        trend = [
            {
                "timestamp": r["timestamp"],
                "cls_score": r["cls_score"],
                "state": r["load_state"],
            }
            for r in records
        ]
        return {
            "window_hours": hours,
            "data_points": len(trend),
            "trend": trend,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve history: {str(e)}",
        )


@router.get("/load/stats", summary="Get load statistics",
            description="Aggregate CLS statistics for personalization and baseline computation.")
async def get_load_stats(hours: float = 24.0):
    """Get aggregate CLS stats for personalization engine."""
    try:
        stats = await sqlite_store.get_load_stats(hours=hours)
        return {
            "window_hours": hours,
            "mean_cls": round(stats.get("mean_cls", 0), 2),
            "min_cls": round(stats.get("min_cls", 0), 2),
            "max_cls": round(stats.get("max_cls", 0), 2),
            "sample_count": stats.get("sample_count", 0),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute stats: {str(e)}",
        )