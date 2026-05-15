"""
Cognitive Server - Decision Proxy Engine
Intelligent scheduling recommendations based on:
  - Historical CLS patterns (from personalizer)
  - Calendar conflict detection (Google Calendar API or fallback)
  - Deadline optimization
  - Multi-factor slot scoring
"""

import asyncio
import datetime
import math
import heapq
from typing import Dict, List, Optional, Tuple

from cognitive_server.db import sqlite_store
from cognitive_server.ml.personalizer import get_personalizer
from cognitive_server.ml.features import feature_vector_to_array
from cognitive_server.interventions.calendar_api import get_calendar_client


class SchedulingConflict:
    """Represents a detected calendar conflict."""
    def __init__(self, start: str, end: str, title: str = "Unknown"):
        self.start = start
        self.end = end
        self.title = title


class SlotScore:
    """Represents a scored time slot with full rationale."""
    def __init__(self, slot: str, score: float, factors: Dict):
        self.slot = slot
        self.score = score
        self.factors = factors  # {"energy": 0.8, "conflict": 1.0, "deadline": 0.6, ...}

    def __lt__(self, other):
        return self.score < other.score


class DecisionProxy:
    """
    Intelligent scheduling decision engine.
    
    Scores proposed time slots using:
    1. Historical energy patterns (from CLS load history)
    2. Calendar conflict detection (Google Calendar API or fallback)
    3. Deadline urgency optimization
    4. Personal circadian rhythm patterns
    5. Focus block preservation
    """

    # Scoring weights (configurable)
    WEIGHTS = {
        "energy": 0.35,       # Historical CLS-based energy prediction
        "conflict": 0.25,     # Calendar conflict penalty
        "deadline": 0.15,     # Deadline proximity bonus
        "circadian": 0.15,    # Personal circadian alignment
        "focus_preservation": 0.10,  # Don't break deep focus blocks
    }

    def __init__(self):
        self.calendar_events: List[SchedulingConflict] = []
        self.scheduling_history: List[Dict] = []
        self._calendar_client = None

    async def initialize(self):
        """Initialize with calendar data from Google Calendar API or fallback."""
        if self._calendar_client is None:
            self._calendar_client = get_calendar_client()
            await self._calendar_client.initialize()

        # Load events for next 24 hours
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=24)

        events = await self._calendar_client.get_events(now, end)
        self.calendar_events = [
            SchedulingConflict(
                start=evt["start"],
                end=evt["end"],
                title=evt["title"],
            )
            for evt in events
        ]

    def _parse_iso(self, dt_str: str) -> datetime.datetime:
        """Parse ISO-8601 string to UTC datetime."""
        return datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

    def _format_dt(self, dt: datetime.datetime) -> str:
        """Format datetime for display."""
        return dt.strftime("%a %b %d, %I:%M %p UTC")

    def _is_in_conflict(self, slot_dt: datetime.datetime, 
                        duration_min: int) -> Tuple[bool, Optional[str]]:
        """Check if a slot conflicts with known calendar events."""
        slot_end = slot_dt + datetime.timedelta(minutes=duration_min)
        
        for event in self.calendar_events:
            event_start = self._parse_iso(event.start)
            event_end = self._parse_iso(event.end)
            
            # Check for overlap
            if slot_dt < event_end and slot_end > event_start:
                return True, event.title
        
        return False, None

    def _calculate_energy_score(self, slot_dt: datetime.datetime) -> float:
        """
        Predict energy at a given time using:
        1. Historical CLS patterns from load_history
        2. Personalizer circadian profile
        3. Time-of-day heuristics
        """
        hour = slot_dt.hour + slot_dt.minute / 60.0
        
        # Start with baseline score (1.0 = full energy)
        energy = 1.0
        
        # Factor 1: Known circadian patterns from personalizer
        personalizer = get_personalizer()
        circadian = personalizer.circadian_profile
        
        if circadian:
            avg_cls = float(circadian.get("hourly_avg", {}).get(str(hour), 50))
            # Convert CLS to energy (lower CLS = higher energy)
            circadian_energy = max(0.1, 1.0 - (avg_cls / 120.0))
            energy *= (0.3 + 0.5 * circadian_energy)  # Weight circadian data
        
        # Factor 2: Time-of-day energy curve (knowledge worker defaults)
        # Peak focus: 9-11am, 2-4pm | Trough: 12-1pm, post-5pm
        if 9 <= hour < 11:
            energy *= 1.1
        elif 14 <= hour < 16:
            energy *= 1.05
        elif 12 <= hour < 13:
            energy *= 0.75  # Post-lunch dip
        elif 17 <= hour < 19:
            energy *= 0.8   # Evening wind-down
        elif hour >= 21 or hour < 6:
            energy *= 0.5   # Late night / early morning
        
        # Factor 3: Historical load for this time window
        # Look at last 2 weeks for similar day/time patterns
        weekday = slot_dt.weekday()
        historical_boost = 1.0
        
        # If user is in learning mode, use conservative estimates
        if personalizer.is_learning():
            energy *= 0.8  # Be more conservative
        
        return max(0.0, min(1.0, energy))

    def _calculate_deadline_score(self, slot_dt: datetime.datetime,
                                   context: str) -> float:
        """
        Score based on deadline urgency.
        Meetings earlier in the day get a slight bonus for urgency.
        """
        hour = slot_dt.hour + slot_dt.minute / 60.0
        
        # Morning slots get urgency bonus if context mentions deadline
        deadline_keywords = ["deadline", "urgent", "due", "by", "before", "end of"]
        context_lower = context.lower()
        
        urgency_bonus = 0.0
        for keyword in deadline_keywords:
            if keyword in context_lower:
                urgency_bonus = 0.2
                break
        
        # Earlier slots get more urgency weight
        if hour < 12:
            return min(1.0, 0.7 + urgency_bonus)
        elif hour < 17:
            return min(1.0, 0.5 + urgency_bonus)
        else:
            return min(1.0, 0.3 + urgency_bonus)

    def _calculate_focus_preservation(self, slot_dt: datetime.datetime,
                                       duration_min: int) -> float:
        """
        Score based on whether meeting preserves or breaks focus blocks.
        Avoids breaking deep focus periods.
        """
        # Check if slot falls in a known deep focus period
        start_hour = slot_dt.hour + slot_dt.minute / 60.0
        end_hour = start_hour + duration_min / 60.0
        
        # Typical deep focus hours (configurable per user eventually)
        focus_blocks = [(9, 12), (14, 17)]
        
        for block_start, block_end in focus_blocks:
            # If meeting starts in a focus block and is short, it's disruptive
            if block_start <= start_hour < block_end:
                if duration_min > 45:
                    return 0.4  # Long meeting in focus block = bad
                else:
                    return 0.7  # Short meeting tolerable
            # If meeting is right before focus block (buffer time)
            elif block_start - 0.5 <= start_hour < block_start:
                return 0.9  # Good: before focus block
        
        return 0.8  # Neutral

    def score_slot(self, slot_str: str, duration_min: int, 
                   context: str = "") -> SlotScore:
        """
        Score a proposed time slot using all factors.
        
        Returns SlotScore with individual factor scores.
        """
        slot_dt = self._parse_iso(slot_str)
        
        # Check calendar conflicts
        has_conflict, conflict_title = self._is_in_conflict(slot_dt, duration_min)
        
        if has_conflict:
            # Severely penalize conflicting slots
            return SlotScore(slot_str, 0.1, {
                "energy": 0.0,
                "conflict": 0.0,
                "deadline": self._calculate_deadline_score(slot_dt, context),
                "circadian": self._calculate_energy_score(slot_dt),
                "focus_preservation": 0.0,
                "conflict_title": conflict_title,
            })
        
        # Calculate individual factor scores
        factors = {
            "energy": self._calculate_energy_score(slot_dt),
            "conflict": 1.0,  # No conflict
            "deadline": self._calculate_deadline_score(slot_dt, context),
            "circadian": self._calculate_energy_score(slot_dt),
            "focus_preservation": self._calculate_focus_preservation(
                slot_dt, duration_min
            ),
        }
        
        # Weighted composite score
        score = sum(
            factors[key] * self.WEIGHTS.get(key, 0)
            for key in factors
        )
        
        return SlotScore(slot_str, round(score, 4), factors)

    async def recommend_slots(self, proposed_slots: List[str],
                              duration_min: int,
                              context: str = "",
                              max_results: int = 3) -> Dict:
        """
        Score and rank proposed time slots.
        
        Returns ranked list with scores and rationale.
        """
        await self.initialize()
        
        scored_slots: List[SlotScore] = []
        
        for slot in proposed_slots:
            try:
                score_result = self.score_slot(slot, duration_min, context)
                scored_slots.append(score_result)
            except Exception as e:
                # If parsing fails, give minimal score
                scored_slots.append(SlotScore(slot, 0.01, {"error": str(e)}))
        
        # Sort by score descending
        scored_slots.sort(key=lambda x: x.score, reverse=True)
        
        # Take top N
        top_slots = scored_slots[:max_results]
        
        return {
            "ranked_options": [
                {
                    "slot": s.slot,
                    "score": s.score,
                    "factors": s.factors,
                    "time_formatted": self._format_dt(self._parse_iso(s.slot)),
                }
                for s in top_slots
            ],
            "all_scores": [
                {
                    "slot": s.slot,
                    "score": s.score,
                }
                for s in scored_slots
            ],
        }

    def record_scheduling_decision(self, chosen_slot: str, 
                                    rejected_slots: List[str],
                                    reason: str = ""):
        """Record user's scheduling choice for learning."""
        self.scheduling_history.append({
            "chosen_slot": chosen_slot,
            "rejected": rejected_slots,
            "reason": reason,
            "timestamp": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
        })
        
        # Keep only recent history
        self.scheduling_history = self.scheduling_history[-50:]

    def get_scheduling_stats(self) -> Dict:
        """Get statistics about scheduling decisions."""
        total = len(self.scheduling_history)
        if total == 0:
            return {"total_decisions": 0}
        
        return {
            "total_decisions": total,
            "avg_rejected": sum(
                len(d["rejected"]) for d in self.scheduling_history
            ) / total,
        }


# Global singleton
_decision_proxy = None


def get_decision_proxy() -> DecisionProxy:
    global _decision_proxy
    if _decision_proxy is None:
        _decision_proxy = DecisionProxy()
    return _decision_proxy


async def get_decision_proxy_async() -> DecisionProxy:
    """Async version that initializes the proxy."""
    proxy = get_decision_proxy()
    await proxy.initialize()
    return proxy