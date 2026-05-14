"""
Cognitive Server - Intervention API
Endpoints: GET /api/v1/interventions/active, GET /api/v1/interventions/log
Manages held notifications and intervention state.
"""

from fastapi import APIRouter, HTTPException

from cognitive_server.db import sqlite_store
from cognitive_server.interventions.engine import evaluate_interventions
from cognitive_server.interventions.urgency_classifier import UrgencyClassifier

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


@router.post("/interventions/page-notifications",
             summary="Ingest page-level notifications",
             description="Accept browser-captured notifications; hold if CLS is high and not urgent.")
async def ingest_page_notifications(payload: dict):
    """
    Receive notifications captured from web page DOM.
    Holds them if CLS > 60 and not urgent.
    """
    try:
        notifications = payload.get("notifications", [])
        source_url = payload.get("source_url", "")

        if not notifications:
            return {"status": "ignored", "message": "No notifications to process"}

        # Get current CLS state
        current = await sqlite_store.get_current_load()
        cls_score = current["cls_score"] if current else None
        state = current["load_state"] if current else "learning"

        held_threshold = 60
        should_hold = cls_score is not None and cls_score > held_threshold

        urg_classifier = UrgencyClassifier()
        results = []

        for notif in notifications:
            sender = notif.get("sender", "Unknown")
            preview = notif.get("preview", "")

            # Check urgency
            urgency_result = urg_classifier.classify(sender, preview)
            is_urgent = urgency_result["is_urgent"]

            if should_hold and not is_urgent:
                # Hold the notification
                notif_id = await sqlite_store.insert_held_notification(
                    source="browser",
                    sender=sender,
                    preview=preview,
                    urgency_score=urgency_result["urgency_score"],
                )
                await sqlite_store.log_intervention(
                    "page_notification_held",
                    {
                        "sender": sender,
                        "preview": preview[:100],
                        "source_url": source_url,
                        "cls": cls_score,
                    },
                    cls_score,
                )
                results.append({
                    "sender": sender,
                    "action": "held",
                    "id": notif_id,
                })
            else:
                # Pass through (urgent or CLS below threshold)
                await sqlite_store.log_intervention(
                    "page_notification_passed",
                    {
                        "sender": sender,
                        "preview": preview[:100],
                        "reason": "urgent" if is_urgent else "cls_low",
                    },
                    cls_score or 0,
                )
                results.append({
                    "sender": sender,
                    "action": "passed",
                    "reason": "urgent" if is_urgent else "cls_low",
                })

        return {
            "status": "processed",
            "cls_score": cls_score,
            "state": state,
            "results": results,
            "held_count": sum(1 for r in results if r["action"] == "held"),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process page notifications: {str(e)}",
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