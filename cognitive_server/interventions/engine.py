"""
Cognitive Server - Intervention Engine
Core decision logic: evaluates CLS and triggers notification holds, releases,
and draft generation based on configurable rules and ML predictions.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from cognitive_server.config import load_config
from cognitive_server.db import sqlite_store

logger = logging.getLogger("cognitive.interventions")

# Reload interval for checking if held notifications should be released
CHECK_INTERVAL_SECONDS = 30


async def evaluate_interventions():
    """
    Main intervention evaluation loop iteration.
    Called periodically by the background scheduler.

    Checks:
    1. Should new notifications be held? (CLS > hold_threshold)
    2. Should held notifications be released? (CLS < release_threshold sustained)
    3. Urgency bypass checks on held items
    """
    try:
        config = load_config()

        # Get current CLS
        current = await sqlite_store.get_current_load()

        # Phase 1: If no CLS data yet, skip intervention evaluation
        if current is None or current.get("cognitive_load_score") is None:
            return {"action": "no_data", "message": "Waiting for baseline signals"}

        cls_score = current["cognitive_load_score"]
        state = current["state"]

        hold_threshold = config.get("interventions", {}).get("hold_threshold", 60)
        release_threshold = config.get("interventions", {}).get("release_threshold", 40)
        sustained_seconds = config.get("interventions", {}).get("release_sustained_seconds", 300)

        # --- Check release conditions for held notifications ---
        held = await sqlite_store.get_held_notifications()
        released_count = 0

        if held and cls_score < release_threshold:
            # Check if CLS has been below threshold long enough
            if _sustained_below_threshold(release_threshold, sustained_seconds):
                for n in held:
                    await sqlite_store.release_notification(
                        n["id"], reason="smart_release_cls_below_threshold"
                    )
                    released_count += 1
                await sqlite_store.log_intervention(
                    "batch_release",
                    {"count": released_count, "cls": cls_score, "reason": "smart_release"},
                    cls_score,
                )
                logger.info(f"Released {released_count} notifications (CLS={cls_score})")

        # --- Urgency bypass check on each held notification ---
        urg_classifier = UrgencyClassifier(config)
        for n in held:
            if n["released_at"] is not None:
                continue

            should_bypass = await urg_classifier.should_bypass(n)
            if should_bypass:
                await sqlite_store.release_notification(
                    n["id"], reason="urgency_bypass"
                )
                await sqlite_store.log_intervention(
                    "urgency_bypass",
                    {"notification_id": n["id"], "sender": n["sender"], "source": n["source"]},
                    cls_score,
                )
                logger.info(f"Urgency bypass for notification {n['id']} from {n['sender']}")

        # --- Check scheduled release timer ---
        scheduled_interval = config.get("interventions", {}).get(
            "scheduled_release_interval_minutes", 90
        )
        for n in held:
            if n["released_at"] is not None:
                continue
            held_at = datetime.fromisoformat(n["held_at"])
            if datetime.now(timezone.utc) - held_at > timedelta(minutes=scheduled_interval):
                await sqlite_store.release_notification(
                    n["id"], reason="scheduled_interval_release"
                )
                logger.info(f"Scheduled release for notification {n['id']}")

        return {
            "action": "evaluated",
            "cls_score": cls_score,
            "state": state,
            "held_count": len(held),
            "released_this_cycle": released_count,
        }

    except Exception as e:
        logger.error(f"Intervention evaluation error: {e}")
        return {"action": "error", "message": str(e)}


def _sustained_below_threshold(threshold: float, sustained_seconds: int) -> bool:
    """
    Check if CLS has been below threshold for the sustained duration.
    Looks at recent load_history entries.
    """
    # This is a simplified check; in full implementation we'd query
    # load_history for the last N minutes and verify all values are below threshold
    import asyncio
    return True  # Simplified for Phase 1; expanded in Phase 2


class UrgencyClassifier:
    """Determines if a held notification should bypass the hold based on urgency rules."""

    def __init__(self, config: dict):
        self.config = config
        interventions_config = config.get("interventions", {})
        self.keyword_triggers = [
            kw.upper() for kw in interventions_config.get("keyword_triggers", [])
        ]
        self.whitelist_senders = set(
            s.lower() for s in interventions_config.get("whitelist", {}).get("senders", [])
        )
        self.whitelist_domains = set(
            d.lower() for d in interventions_config.get("whitelist", {}).get("domains", [])
        )
        self.escalation_window_minutes = interventions_config.get("escalation_window_minutes", 10)
        self.escalation_repeat_count = interventions_config.get("escalation_repeat_count", 2)

    async def should_bypass(self, notification: dict) -> bool:
        """Check if a notification should bypass the hold."""
        # 1. Whitelist sender check
        sender = notification.get("sender", "").lower()
        if sender in self.whitelist_senders:
            return True

        # 2. Keyword urgency detection
        preview = notification.get("preview", "").upper()
        for keyword in self.keyword_triggers:
            if keyword in preview:
                return True

        # 3. Repeated contact escalation
        if await self._is_repeated_contact(notification):
            return True

        return False

    async def _is_repeated_contact(self, notification: dict) -> bool:
        """Check if the same sender contacted multiple times within escalation window."""
        sender = notification.get("sender", "")
        if not sender:
            return False

        cutoff = (
            datetime.fromisoformat(notification["held_at"])
            - timedelta(minutes=self.escalation_window_minutes)
        ).isoformat()

        recent_notifications = await sqlite_store.get_held_notifications()
        count = sum(
            1
            for n in recent_notifications
            if n["sender"] == sender
            and n["held_at"] >= cutoff
            and n["id"] != notification["id"]
        )

        return count >= self.escalation_repeat_count


async def record_feedback(feedback_type: str, details: dict):
    """
    Record user feedback for adaptive learning.

    Feedback types:
    - "manual_release": User manually released a held notification
    - "auto_release_ok": Auto-release was appropriate (user didn't react)
    - "catch_up": User released all notifications at once
    - "state_correct": Predicted state matched observed behavior
    """
    from cognitive_server.ml.personalizer import get_personalizer

    personalizer = get_personalizer()
    personalizer.record_feedback(feedback_type, details)

    # Also log to intervention log for audit trail
    from cognitive_server.db import sqlite_store
    await sqlite_store.log_intervention(
        f"feedback_{feedback_type}",
        details,
        details.get("cls_at_time", 0),
    )