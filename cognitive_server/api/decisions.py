"""
Cognitive Server - Decision Proxy API
Endpoint: POST /api/v1/decisions/schedule
Handles meeting scheduling requests, predicts optimal time slots, and auto-drafts responses.
"""

import datetime
import math
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from cognitive_server.db import sqlite_store
from cognitive_server.interventions.draft_generator import generate_scheduling_response

router = APIRouter(tags=["decisions"])


# --- Pydantic Models ---

class SlotProposal(BaseModel):
    slot: str = Field(..., description="ISO-8601 UTC datetime for proposed meeting")
    duration_minutes: int = Field(default=30, ge=1, le=480, description="Meeting duration in minutes")


class DecisionRequest(BaseModel):
    proposed_slots: List[str] = Field(..., min_items=1, max_items=10,
                                       description="List of proposed meeting time slots (ISO-8601 UTC)")
    duration_minutes: int = Field(default=30, ge=1, le=480,
                                   description="Duration of the meeting in minutes")
    attendees: List[str] = Field(default_factory=list,
                                  description="List of attendee emails")
    context: str = Field(default="", description="Meeting context (e.g. 'Sprint planning')")


class RankedOption(BaseModel):
    slot: str
    rank: int
    rationale: str
    predicted_cls_at_slot: float


class DecisionResponse(BaseModel):
    ranked_options: List[RankedOption]
    suggested_response: str


# --- Helper: Convert CLS to energy level ---

def _cls_to_energy(cls_score: float) -> float:
    """Higher CLS = lower energy. Returns 0.0 (exhausted) to 1.0 (fully energized)."""
    if cls_score < 0:
        return 1.0
    if cls_score > 100:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - (cls_score / 120.0))), 3)


def _parse_iso_datetime(dt_str: str) -> datetime.datetime:
    """Parse ISO-8601 string to datetime (UTC)."""
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(dt_str)


def _format_datetime(dt: datetime.datetime) -> str:
    """Format datetime to a human-readable string."""
    return dt.strftime("%a %b %d, %I:%M %p UTC")


# --- Score a time slot ---

async def _score_slot(slot_str: str, duration_minutes: int, context: str) -> dict:
    """
    Score a proposed time slot based on:
    1. Calendar conflicts (future placeholder for Google Calendar API)
    2. Historical energy patterns from CLS load history
    3. Time-of-day heuristics
    """
    proposed_dt = _parse_iso_datetime(slot_str)
    proposed_hour = proposed_dt.hour + proposed_dt.minute / 60.0

    # --- 1. Historical Energy Prediction ---
    # Look at historical CLS for similar time-of-day
    hour_sin = math.sin(2 * math.pi * proposed_hour / 24.0)

    # Get recent load stats for baseline
    stats = await sqlite_store.get_load_stats(hours=48.0)
    mean_cls = float(stats.get("mean_cls") or 0)

    # Time-of-day energy curve (typical knowledge worker pattern)
    # Peak focus: 9-11am, 2-4pm | Trough: 12-1pm, after 5pm
    if 9 <= proposed_hour < 11:
        time_modifier = -15.0  # lower CLS expected (better focus)
    elif 14 <= proposed_hour < 16:
        time_modifier = -10.0
    elif 12 <= proposed_hour < 13:
        time_modifier = +15.0  # post-lunch dip
    elif proposed_hour >= 17 or proposed_hour < 8:
        time_modifier = +10.0
    else:
        time_modifier = 0.0

    predicted_cls = max(0.0, min(100.0, mean_cls + time_modifier))

    # --- 2. Calendar Conflict Check (placeholder) ---
    # In production: query Google Calendar API for overlapping events
    has_conflict = False
    conflict_note = ""

    # --- 3. Score Calculation ---
    energy = _cls_to_energy(predicted_cls)

    # Penalize slots with conflicts heavily
    if has_conflict:
        energy *= 0.3

    return {
        "slot": slot_str,
        "predicted_cls": round(predicted_cls, 2),
        "energy": round(energy, 3),
        "has_conflict": has_conflict,
        "conflict_note": conflict_note,
        "time_formatted": _format_datetime(proposed_dt),
    }


# --- Endpoint ---

@router.post("/decisions/schedule",
             response_model=DecisionResponse,
             summary="Get scheduling recommendations",
             description="Submit proposed meeting time slots; returns ranked options based on predicted cognitive energy.")
async def schedule_decision(request: DecisionRequest):
    """
    Evaluate proposed meeting time slots and return ranked options
    with predicted cognitive load and auto-drafted response.
    """
    try:
        scored_slots = []
        for slot_str in request.proposed_slots:
            result = await _score_slot(slot_str, request.duration_minutes, request.context)
            scored_slots.append(result)

        # Sort by energy (descending) = best slot first
        scored_slots.sort(key=lambda x: x["energy"], reverse=True)

        # Take top N (max 3 per FR-DP-002)
        top_slots = scored_slots[:3]

        ranked_options = []
        for i, slot_info in enumerate(top_slots, start=1):
            rationale_parts = []

            if slot_info["has_conflict"]:
                rationale_parts.append("conflicts with existing event")
            else:
                rationale_parts.append(
                    f"predicted cognitive load: {slot_info['predicted_cls']:.0f}"
                    f" ({_cls_to_energy(slot_info['predicted_cls']) * 100:.0f}% energy)"
                )

            if "morning" in slot_info["time_formatted"].lower() or 8 <= _parse_iso_datetime(slot_info["slot"]).hour < 12:
                rationale_parts.append("morning focus window")
            elif 14 <= _parse_iso_datetime(slot_info["slot"]).hour < 16:
                rationale_parts.append("afternoon energy window")

            rationale = "; ".join(rationale_parts) if rationale_parts else "reasonable time slot"

            ranked_options.append(RankedOption(
                slot=slot_info["slot"],
                rank=i,
                rationale=rationale,
                predicted_cls_at_slot=slot_info["predicted_cls"],
            ))

        # Auto-draft response using the best slot
        best_slot = top_slots[0] if top_slots else None
        if best_slot and not best_slot["has_conflict"]:
            suggested_response = generate_scheduling_response(
                context=request.context,
                proposed_time=_format_datetime(_parse_iso_datetime(best_slot["slot"])),
                energy_level=_cls_to_energy(best_slot["predicted_cls"]),
                load_state="focused" if best_slot["predicted_cls"] <= 60 else "heavy",
            )
        elif best_slot and best_slot["has_conflict"]:
            suggested_response = (
                f"I have a conflict during {best_slot['time_formatted']}. "
                f"Could we explore an alternative time? "
                f"If you have flexibility, I'll suggest an open slot when my focus is at its best."
            )
        else:
            suggested_response = (
                "I'm having difficulty finding an optimal slot right now. "
                "Please propose a few times and I'll respond with my availability shortly."
            )

        return DecisionResponse(
            ranked_options=ranked_options,
            suggested_response=suggested_response,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Decision proxy error: {str(e)}",
        )