"""
Cognitive Server - Notification Holder
Manages the lifecycle of held notifications: hold, track, release, expire.
"""

import datetime
from typing import List, Optional, Dict, Any

from cognitive_server.db import sqlite_store


class NotificationHolder:
    """Manages held notifications with TTL and state tracking."""

    def __init__(self):
        pass

    async def hold(self, source: str, sender: str, preview: str,
                   urgency_score: float = 0.0) -> int:
        """Hold a notification. Returns notification ID."""
        return await sqlite_store.insert_held_notification(
            source=source,
            sender=sender,
            preview=preview,
            urgency_score=urgency_score,
        )

    async def get_all_held(self) -> List[Dict[str, Any]]:
        """Get all currently held notifications."""
        return await sqlite_store.get_held_notifications()

    async def release(self, notification_id: int, reason: str = "smart_release"):
        """Release a specific held notification."""
        await sqlite_store.release_notification(notification_id, reason=reason)

    async def release_all(self, reason: str = "manual"):
        """Release all held notifications."""
        await sqlite_store.release_all_notifications(reason=reason)

    async def get_by_source(self, source: str) -> List[Dict[str, Any]]:
        """Get held notifications filtered by source (e.g., 'slack', 'gmail')."""
        all_held = await self.get_all_held()
        return [n for n in all_held if n["source"] == source]

    async def get_count_by_source(self) -> Dict[str, int]:
        """Get count of held notifications grouped by source."""
        all_held = await self.get_all_held()
        counts = {}
        for n in all_held:
            src = n["source"]
            counts[src] = counts.get(src, 0) + 1
        return counts

    async def get_urgent_held(self) -> List[Dict[str, Any]]:
        """Get held notifications that have been flagged as urgent."""
        all_held = await self.get_all_held()
        return [n for n in all_held if n.get("urgency_score", 0) > 0.7]