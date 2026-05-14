"""
Cognitive Server - SQLite Store
Thread-safe async database operations with 48-hour rolling window.
"""

import aiosqlite
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

DB_PATH: str = "cognitive.db"
ROLLING_WINDOW_SECONDS: int = 48 * 60 * 60


def _find_schema():
    """Locate schema.sql file relative to this module."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(this_dir, "schema.sql"),
        os.path.join(this_dir, "db", "schema.sql"),
        os.path.join(os.path.dirname(this_dir), "db", "schema.sql"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"Could not find schema.sql. Searched: {candidates}")


async def initialize(db_path: str = "cognitive.db"):
    """Initialize database connection and run schema."""
    global DB_PATH
    DB_PATH = db_path

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        schema_path = _find_schema()
        with open(schema_path, "r") as f:
            schema = f.read()

        await db.executescript(schema)
        await db.commit()


async def close():
    """Close database connection."""
    pass


async def _get_connection() -> aiosqlite.Connection:
    """Get a new async connection with row factory."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# ----- Signals -----

async def insert_signal(signal: dict) -> int:
    """Insert a single signal record. Returns row id."""
    db = await _get_connection()
    try:
        cursor = await db.execute(
            """
            INSERT INTO signals
                (session_id, timestamp, kpm, inter_key_avg, switch_rate,
                 scroll_velocity, scroll_delta, mouse_entropy, idle_ratio,
                 tab_count, domain_switches, time_of_day,
                 active_url, active_title, idle_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.get("session_id", "unknown"),
                signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
                signal.get("kpm", 0),
                signal.get("inter_key_avg", 0),
                signal.get("switch_rate", 0),
                signal.get("scroll_velocity", 0),
                signal.get("scroll_delta", 0),
                signal.get("mouse_entropy", 0),
                signal.get("idle_ratio", 0),
                signal.get("tab_count", 1),
                signal.get("domain_switches", 0),
                signal.get("time_of_day", 0),
                signal.get("active_url", ""),
                signal.get("active_title", ""),
                signal.get("idle_seconds", 0),
            ),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def insert_signals_batch(signals_list: list) -> int:
    """Insert a batch of signal records."""
    for sig in signals_list:
        await insert_signal(sig)
    return len(signals_list)


async def get_signals_recent(hours: float = 0.5) -> List[Dict[str, Any]]:
    """Get signals from the last N hours."""
    db = await _get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await db.execute(
            "SELECT * FROM signals WHERE timestamp > ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def purge_old_signals():
    """Remove signals older than 48 hours."""
    db = await _get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ROLLING_WINDOW_SECONDS)).isoformat()
        await db.execute("DELETE FROM signals WHERE timestamp < ?", (cutoff,))
        await db.commit()
    finally:
        await db.close()


# ----- Load History -----

async def insert_load_record(cls_score: float, confidence: float,
                             load_state: str, source: str = "model",
                             features: Optional[dict] = None):
    """Insert a CLS inference record."""
    db = await _get_connection()
    try:
        await db.execute(
            """
            INSERT INTO load_history
                (timestamp, cls_score, confidence, load_state, source, features_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                cls_score,
                confidence,
                load_state,
                source,
                json.dumps(features or {}),
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def get_current_load() -> Optional[Dict[str, Any]]:
    """Get the most recent load record."""
    db = await _get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM load_history ORDER BY timestamp DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_load_history(hours: float = 2.0) -> List[Dict[str, Any]]:
    """Get load history for the last N hours."""
    db = await _get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await db.execute(
            "SELECT * FROM load_history WHERE timestamp > ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_load_stats(hours: float = 24.0) -> Dict[str, Any]:
    """Get aggregate stats for personalization."""
    db = await _get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor = await db.execute(
            """
            SELECT
                COALESCE(AVG(cls_score), 0) as mean_cls,
                COALESCE(MIN(cls_score), 0) as min_cls,
                COALESCE(MAX(cls_score), 0) as max_cls,
                COUNT(*) as sample_count
            FROM load_history
            WHERE timestamp > ?
            """,
            (cutoff,),
        )
        row = await cursor.fetchone()
        result = dict(row) if row else {}
        # Ensure numeric defaults
        for key in ("mean_cls", "min_cls", "max_cls"):
            result[key] = result.get(key) or 0.0
        result["sample_count"] = result.get("sample_count") or 0
        return result
    finally:
        await db.close()


# ----- Held Notifications -----

async def insert_held_notification(source: str, sender: str, preview: str,
                                    urgency_score: float = 0.0) -> int:
    db = await _get_connection()
    try:
        cursor = await db.execute(
            """
            INSERT INTO held_notifications (source, sender, preview, held_at, urgency_score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, sender, preview, datetime.now(timezone.utc).isoformat(), urgency_score),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_held_notifications() -> List[Dict[str, Any]]:
    """Get all currently held notifications."""
    db = await _get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM held_notifications WHERE released_at IS NULL ORDER BY held_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def release_notification(notification_id: int, reason: str = "smart_release"):
    """Mark a held notification as released."""
    db = await _get_connection()
    try:
        await db.execute(
            "UPDATE held_notifications SET released_at = ?, release_reason = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), reason, notification_id),
        )
        await db.commit()
    finally:
        await db.close()


async def release_all_notifications(reason: str = "manual"):
    """Release all held notifications."""
    db = await _get_connection()
    try:
        await db.execute(
            "UPDATE held_notifications SET released_at = ?, release_reason = ? WHERE released_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), reason),
        )
        await db.commit()
    finally:
        await db.close()


# ----- Intervention Log -----

async def log_intervention(intervention_type: str, details: dict, cls_at_time: float = 0.0):
    """Log an intervention action."""
    db = await _get_connection()
    try:
        await db.execute(
            """
            INSERT INTO interventions_log (timestamp, type, details, cls_at_time)
            VALUES (?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                intervention_type,
                json.dumps(details),
                cls_at_time,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def get_intervention_log(limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent intervention log entries."""
    db = await _get_connection()
    try:
        cursor = await db.execute(
            "SELECT * FROM interventions_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ----- Housekeeping -----

async def run_maintenance():
    """Purge old data outside the rolling window."""
    await purge_old_signals()