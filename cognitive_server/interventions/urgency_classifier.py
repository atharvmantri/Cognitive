"""
Cognitive Server - Urgency Classifier
Determines whether a held notification should bypass the hold
based on sender whitelist, keyword detection, and repeated-contact escalation.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


class UrgencyClassifier:
    """
    Classifies whether a notification is urgent enough to bypass
    the cognitive load hold.

    Three signal paths:
    1. Sender whitelist (manager, family, critical domains)
    2. Keyword detection ("URGENT", "PRODUCTION DOWN", etc.)
    3. Escalation pattern (repeated contact within N minutes)
    """

    DEFAULT_KEYWORDS = [
        "URGENT", "URGENT!", "CRITICAL", "PRODUCTION DOWN",
        "911", "EMERGENCY", "ASAP", "OUTAGE", "INCIDENT",
        "BLOCKED", "BLOCKING", "NOW", "IMMEDIATELY",
        "P0", "P1", "HOTFIX", "ROLLBACK",
    ]

    def __init__(self, config: Optional[Dict] = None):
        if config is None:
            config = {}
        interventions = config.get("interventions", {})

        self.whitelist_senders = set(
            s.strip().lower()
            for s in interventions.get("whitelist", {}).get("senders", [])
        )
        self.whitelist_domains = set(
            d.strip().lower()
            for d in interventions.get("whitelist", {}).get("domains", [])
        )
        self.keyword_triggers = [
            kw.upper().strip()
            for kw in interventions.get("keyword_triggers", self.DEFAULT_KEYWORDS)
        ]
        self.escalation_window_minutes = interventions.get("escalation_window_minutes", 10)
        self.escalation_repeat_count = interventions.get("escalation_repeat_count", 2)

    # ------- Public API -------

    def classify(self, sender: str, preview: str,
                 held_notifications_history: Optional[List[Dict]] = None) -> Dict:
        """
        Classify urgency of a notification.

        Args:
            sender: Who sent the notification
            preview: Text preview of the notification
            held_notifications_history: Recent held notifs for escalation detection

        Returns:
            {
                "is_urgent": bool,
                "urgency_score": float (0.0-1.0),
                "reasons": [str],
                "bypass": bool,
            }
        """
        reasons = []
        signals = []

        # 1. Whitelist sender check
        sender_lower = sender.strip().lower()
        if sender_lower in self.whitelist_senders:
            reasons.append(f"sender '{sender}' is whitelisted")
            signals.append(1.0)

        # Extract domain from sender email
        if "@" in sender_lower:
            domain = sender_lower.split("@")[-1]
            if domain in self.whitelist_domains:
                reasons.append(f"sender domain '{domain}' is whitelisted")
                signals.append(0.9)

        # 2. Keyword detection
        preview_upper = preview.upper()
        matched_keywords = [
            kw for kw in self.keyword_triggers if kw in preview_upper
        ]
        if matched_keywords:
            reasons.append(f"keyword match: {', '.join(matched_keywords)}")
            # Stronger signal for more keywords matched
            signals.append(min(1.0, 0.5 + 0.2 * len(matched_keywords)))

        # 3. Escalation pattern
        if held_notifications_history is not None:
            repeat_count = self._count_recent_from_sender(
                sender_lower, held_notifications_history
            )
            if repeat_count >= self.escalation_repeat_count:
                reasons.append(
                    f"repeated contact: {repeat_count} notifications in "
                    f"{self.escalation_window_minutes} min window"
                )
                signals.append(min(1.0, 0.4 + 0.2 * repeat_count))

        # Compute final score
        urgency_score = max(signals) if signals else 0.0
        is_urgent = urgency_score >= 0.5

        return {
            "is_urgent": is_urgent,
            "urgency_score": round(urgency_score, 3),
            "reasons": reasons,
            "bypass": is_urgent,
        }

    # ------- Internal Helpers -------

    def _count_recent_from_sender(self, sender_lower: str,
                                   history: List[Dict]) -> int:
        """Count notifications from same sender within escalation window."""
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=self.escalation_window_minutes)
        )
        count = 0
        for n in history:
            if n.get("sender", "").lower() != sender_lower:
                continue
            held_at_str = n.get("held_at", "")
            if not held_at_str:
                continue
            try:
                held_at = datetime.fromisoformat(held_at_str.replace("Z", "+00:00"))
                if held_at >= cutoff:
                    count += 1
            except Exception:
                continue
        return count