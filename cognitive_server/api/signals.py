"""
Cognitive Server - Signal Ingestion API
Endpoint: POST /api/v1/signals
Accepts batched behavioral signals from browser extension and desktop agent.
"""

import time
from typing import List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, validator

from cognitive_server.db import sqlite_store

router = APIRouter(tags=["signals"])


# --- Pydantic Models ---

class SignalPayload(BaseModel):
    session_id: str = Field(..., description="Unique browser session or device session ID")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp")
    kpm: float = Field(default=0.0, ge=0, description="Keystrokes per minute")
    inter_key_avg: float = Field(default=0.0, ge=0, description="Average inter-keystroke interval in ms")
    switch_rate: float = Field(default=0.0, ge=0, description="Window/tab switches per minute")
    scroll_velocity: float = Field(default=0.0, ge=0, description="Average scroll speed in px/sec")
    scroll_delta: float = Field(default=0.0, description="Net scroll direction magnitude")
    mouse_entropy: float = Field(default=0.0, ge=0, le=1.0, description="Mouse movement randomness 0-1")
    idle_ratio: float = Field(default=0.0, ge=0, le=1.0, description="Fraction of window with no input")
    tab_count: int = Field(default=1, ge=1, description="Number of open tabs")
    domain_switches: int = Field(default=0, ge=0, description="Unique domains visited in window")
    time_of_day: float = Field(default=0.0, ge=-1.0, le=1.0, description="Sin/cos encoded hour")
    active_url: str = Field(default="", description="Current page domain")
    active_title: str = Field(default="", description="Document title")
    idle_seconds: int = Field(default=0, ge=0, description="Seconds since last input event")

    @validator("timestamp")
    def validate_timestamp(cls, v):
        # Basic ISO format check
        if not v or len(v) < 10:
            raise ValueError("timestamp must be ISO-8601 format")
        return v


class BatchPayload(BaseModel):
    signals: List[SignalPayload] = Field(..., min_items=1, max_items=200,
                                          description="Array of signal readings (max 200 per batch)")


class SignalResponse(BaseModel):
    batch_id: str
    count: int
    status: str
    latency_ms: float


# --- Endpoints ---

@router.post("/signals", response_model=SignalResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="Ingest behavioral signals",
             description="Accept a batch of behavioral signal readings from the browser extension or desktop agent.")
async def post_signals(payload: BatchPayload):
    """Ingest a batch of behavioral signals into the database."""
    t0 = time.perf_counter()

    signals_list = [s.dict() for s in payload.signals]

    try:
        count = await sqlite_store.insert_signals_batch(signals_list)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store signals: {str(e)}",
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    # Trigger async purge of old data every 100th request (lightweight maintenance)
    # In production this would be a background scheduler task

    return SignalResponse(
        batch_id=f"batch_{int(time.time() * 1000)}",
        count=count,
        status="accepted",
        latency_ms=round(latency_ms, 2),
    )


@router.get("/signals/recent",
            summary="Get recent signals",
            description="Retrieve signals from the last N hours for debugging and monitoring.")
async def get_recent_signals(hours: float = 0.5):
    """Get recent signal data, useful for debugging and dashboard monitoring."""
    try:
        signals = await sqlite_store.get_signals_recent(hours=hours)
        return {
            "count": len(signals),
            "window_hours": hours,
            "signals": signals,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve signals: {str(e)}",
        )