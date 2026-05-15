"""
Cognitive Server - Decision Proxy API
Endpoint: POST /api/v1/decisions/schedule
Handles meeting scheduling requests, predicts optimal time slots, and auto-drafts responses.
Uses DecisionProxy for multi-factor slot scoring.
"""

import datetime
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from cognitive_server.db import sqlite_store
from cognitive_server.interventions.decision_proxy import (
    get_decision_proxy,
    get_decision_proxy_async,
    DecisionProxy,
)
from cognitive_server.interventions.draft_generator import (
    generate_scheduling_response,
    generate_deferral_response,
)

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


class FactorBreakdown(BaseModel):
    energy: float
    conflict: float
    deadline: float
    circadian: float
    focus_preservation: float


class RankedOption(BaseModel):
    slot: str
    rank: int
    rationale: str
    predicted_cls_at_slot: float
    score: float
    factors: FactorBreakdown


class DecisionResponse(BaseModel):
    ranked_options: List[RankedOption]
    suggested_response: str


def _parse_iso_datetime(dt_str: str) -> datetime.datetime:
    """Parse ISO-8601 string to datetime (UTC)."""
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(dt_str)


def _format_datetime(dt: datetime.datetime) -> str:
    """Format datetime to a human-readable string."""
    return dt.strftime("%a %b %d, %I:%M %p")


# --- Decision Logic ---

def _cls_to_energy(cls_score: float) -> float:
    """Higher CLS = lower energy. Returns 0.0 (exhausted) to 1.0 (fully energized)."""
    if cls_score < 0:
        return 1.0
    if cls_score > 100:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - (cls_score / 120.0))), 3)


def _build_rationale(slot_info: dict) -> str:
    """Generate a human-readable rationale for a slot ranking."""
    parts = []

    # Energy-based rationale
    energy = slot_info["factors"].get("energy", 0.5)
    if energy > 0.8:
        parts.append("high energy window")
    elif energy > 0.5:
        parts.append("moderate energy")
    else:
        parts.append("low energy window")

    # Conflict rationale
    conflict_title = slot_info["factors"].get("conflict_title")
    if conflict_title:
        parts.append(f"conflicts with '{conflict_title}'")

    # Focus preservation
    focus = slot_info["factors"].get("focus_preservation", 0.5)
    if focus > 0.85:
        parts.append("preserves focus block")
    elif focus < 0.5:
        parts.append("may interrupt focus time")

    # Deadline urgency
    deadline = slot_info["factors"].get("deadline", 0.5)
    if deadline > 0.7:
        parts.append("urgent timing")

    # Circadian alignment
    circadian = slot_info["factors"].get("circadian", 0.5)
    if circadian > 0.7:
        parts.append("aligned with your peak hours")
    elif circadian < 0.4:
        parts.append("outside your peak hours")

    return "; ".join(parts) if parts else "reasonable time slot"


async def _get_current_load_for_decisions():
    """Get current load state for decision-making."""
    record = await sqlite_store.get_current_load()
    if record:
        return {
            "cls_score": record.get("cls_score", 50.0),
            "state": record.get("load_state", "focused"),
        }
    return {"cls_score": 50.0, "state": "focused"}


# --- Endpoint ---

@router.post("/decisions/schedule",
             response_model=DecisionResponse,
             summary="Get scheduling recommendations",
             description=(
                 "Submit proposed meeting time slots; returns ranked options "
                 "based on predicted cognitive energy, calendar conflicts, and focus preservation."
             ))
async def schedule_decision(request: DecisionRequest):
    """
    Evaluate proposed meeting time slots and return ranked options
    with predicted cognitive load, factor breakdown, and auto-drafted response.
    """
    try:
        # Initialize the decision proxy
        proxy = await get_decision_proxy_async()

        # Get current cognitive state
        current_load = await _get_current_load_for_decisions()

        # Score all proposed slots
        result = await proxy.recommend_slots(
            proposed_slots=request.proposed_slots,
            duration_min=request.duration_minutes,
            context=request.context,
            max_results=min(3, len(request.proposed_slots)),
        )

        ranked_options = []
        for slot_info in result["ranked_options"]:
            rationale = _build_rationale(slot_info)
            ranked_options.append(RankedOption(
                slot=slot_info["slot"],
                rank=slot_info["rank"] if "rank" in slot_info else len(ranked_options) + 1,
                rationale=rationale,
                predicted_cls_at_slot=round(100 - (slot_info["factors"].get("energy", 0.5) * 100), 1),
                score=slot_info["score"],
                factors=FactorBreakdown(
                    energy=slot_info["factors"].get("energy", 0.0),
                    conflict=slot_info["factors"].get("conflict", 0.0),
                    deadline=slot_info["factors"].get("deadline", 0.0),
                    circadian=slot_info["factors"].get("circadian", 0.0),
                    focus_preservation=slot_info["factors"].get("focus_preservation", 0.0),
                ),
            ))

        # Rank options by score
        for i, opt in enumerate(ranked_options, start=1):
            opt.rank = i

        # Auto-draft response using the best slot
        best_slot = result["ranked_options"][0] if result["ranked_options"] else None
        load_state = current_load.get("state", "focused")

        if best_slot and not best_slot["factors"].get("conflict_title"):
            slot_dt = _parse_iso_datetime(best_slot["slot"])
            energy = best_slot["factors"].get("energy", 0.5)
            suggested_response = generate_scheduling_response(
                context=request.context,
                proposed_time=_format_datetime(slot_dt),
                energy_level=energy,
                load_state=load_state,
            )
        elif best_slot and best_slot["factors"].get("conflict_title"):
            conflict = best_slot["factors"]["conflict_title"]
            # Try next best slot
            alt_options = [s for s in result["ranked_options"]
                          if not s["factors"].get("conflict_title")]
            if alt_options:
                alt = alt_options[0]
                alt_dt = _parse_iso_datetime(alt["slot"])
                energy = alt["factors"].get("energy", 0.5)
                suggested_response = (
                    f"The best time conflicts with '{conflict}'. "
                    f"Here's the next best option:\n\n"
                )
                suggested_response += generate_scheduling_response(
                    context=request.context,
                    proposed_time=_format_datetime(alt_dt),
                    energy_level=energy,
                    load_state=load_state,
                )
            else:
                suggested_response = (
                    f"The top slot conflicts with '{conflict}'. "
                    f"All proposed times have conflicts — "
                    f"please suggest alternative times."
                )
        else:
            suggested_response = (
                f"Current cognitive load is {current_load['cls_score']:.0f} "
                f"({load_state}). I need a few more time slot options to find "
                f"the best window for this meeting."
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


@router.post("/decisions/feedback",
             summary="Submit scheduling feedback",
             description="Record user's scheduling decision to improve future recommendations.")
async def decision_feedback(chosen_slot: str, rejected_slots: List[str] = [],
                            reason: str = ""):
    """Record user feedback on scheduling decisions for self-improvement."""
    try:
        proxy = get_decision_proxy()
        proxy.record_scheduling_decision(
            chosen_slot=chosen_slot,
            rejected_slots=rejected_slots,
            reason=reason,
        )
        return {"status": "feedback_recorded"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to record feedback: {str(e)}",
        )


@router.get("/decisions/stats",
            summary="Get decision proxy statistics",
            description="Retrieve statistics about scheduling decisions made.")
async def decision_stats():
    """Get decision proxy stats for the popup UI."""
    try:
        proxy = get_decision_proxy()
        stats = proxy.get_scheduling_stats()
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve stats: {str(e)}",
        )