"""
Cognitive Server - Intervention API
Endpoints: GET /api/v1/interventions/active, GET /api/v1/interventions/log
Manages held notifications and intervention state.
"""

from fastapi import APIRouter, HTTPException

from cognitive_server.db import sqlite_store
from cognitive_server.interventions.engine import evaluate_interventions

router = APIRouter(tags=["interventions"])


@router.get("/interventions/active",
            summary="Get active interventions",
            description="Returns currently held notifications, active drafts, and intervention state.")
async def get_active_interventions():
    """Return all active (held) notifications and pending drafts."""
    try:
        held = await sqlite_store.get_held_notifications()

        # Run intervention evaluator to check if any should be released
        recommendations = await evaluate_interventions()

        held_notifications = []
        for n in held:
            held_notifications.append({
                "id": n["id"],
                "source": n["source"],
                "sender": n["sender"],
                "preview": n["preview"],
                "held_at": n["held_at"],
                "urgency_score": round(n["urgency_score"], 3),
            })

        return {
            "held_notifications": held_notifications,
            "held_count": len(held_notifications),
            "recommendations": recommendations,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve interventions: {str(e)}",
        )


@router.get("/interventions/release/{notification_id}",
            summary="Release a held notification",
            description="Manually release a specific held notification by ID.")
async def release_notification(notification_id: int):
    """Release a single held notification."""
    try:
        await sqlite_store.release_notification(notification_id, reason="manual")
        return {
            "status": "released",
            "notification_id": notification_id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to release notification: {str(e)}",
        )


@router.post("/interventions/release-all",
             summary="Release all held notifications",
             description="Release every held notification (e.g., 'catch up' shortcut).")
async def release_all():
    """Release all currently held notifications."""
    try:
        await sqlite_store.release_all_notifications(reason="manual_catchup")
        return {
            "status": "all_released",
            "released_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to release all notifications: {str(e)}",
        )


@router.get("/interventions/log",
            summary="Get intervention log",
            description="Retrieve the read-only intervention log for the last N entries.")
async def get_intervention_log(limit: int = 50):
    """Return recent intervention log entries for the popup UI."""
    try:
        log_entries = await sqlite_store.get_intervention_log(limit=limit)
        entries = []
        for e in log_entries:
            entries.append({
                "timestamp": e["timestamp"],
                "type": e["type"],
                "details": e.get("details", "{}"),
                "cls_at_time": round(e.get("cls_at_time", 0), 2),
            })
        return {
            "count": len(entries),
            "log": entries,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve intervention log: {str(e)}",
        )