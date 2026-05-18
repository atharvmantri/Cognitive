"""
Cognitive Server - Personalization API
Endpoints: GET /api/v1/personalization/baseline, GET /api/v1/personalization/circadian
Provides baseline collection status and circadian rhythm profile.
"""

from fastapi import APIRouter, HTTPException

from cognitive_server.ml.personalizer import Personalizer

router = APIRouter(tags=["personalization"])

_personalizer = Personalizer()


@router.get("/personalization/baseline",
            summary="Get baseline collection status",
            description="Returns baseline progress, sample count, and completion status.")
async def get_baseline_status():
    try:
        stats = _personalizer.get_stats()
        baseline = stats.get("baseline", {})
        return {
            "complete": baseline.get("complete", False),
            "progress": baseline.get("progress", 0),
            "samples": baseline.get("samples", 0),
            "target": baseline.get("target", 100),
            "started_at": baseline.get("started_at"),
            "eta": baseline.get("eta"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/personalization/circadian",
            summary="Get circadian rhythm profile",
            description="Returns classified circadian rhythm (night_owl/early_bird/intermediate).")
async def get_circadian_profile():
    try:
        stats = _personalizer.get_stats()
        rhythm = stats.get("circadian_rhythm", {})
        return {
            "profile": rhythm.get("profile", "unknown"),
            "peak_hour": rhythm.get("peak_hour"),
            "low_hour": rhythm.get("low_hour"),
            "confidence": rhythm.get("confidence", 0),
            "samples": rhythm.get("samples", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
