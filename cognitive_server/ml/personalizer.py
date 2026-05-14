"""
Cognitive Server - Personalizer
Online threshold adaptation and circadian rhythm detection.
Adapts to individual user patterns over 24-48 hours of baseline data.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from cognitive_server.db import sqlite_store


# Default thresholds (from PRD §5.1.3)
DEFAULT_THRESHOLDS = {
    "restorative": 20,
    "light": 40,
    "focused": 60,
    "heavy": 75,
    "overloaded": 100,
}

# Adjustment step size (percentage of threshold)
ADJUSTMENT_STEP_PCT = 0.05  # 5%

# Hours of data needed before personalization activates
BASELINE_HOURS = 48


class Personalizer:
    """Adapts CLS thresholds to individual user patterns over time."""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "..", "config", "user_profile.json"
        )
        self.config = self._load_config()
        self.thresholds = self.config.get("cognitive_load", {}).get(
            "thresholds", dict(DEFAULT_THRESHOLDS)
        )
        self.circadian_profile = None
        self.baseline_complete = False

    def _load_config(self) -> dict:
        """Load user profile config from disk."""
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_config(self):
        """Persist updated thresholds back to config file."""
        try:
            self.config["cognitive_load"]["thresholds"] = self.thresholds
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    # ----- Baseline Collection -----

    async def check_baseline_complete(self) -> bool:
        """Check if we have enough data (48h) to exit learning mode."""
        stats = await sqlite_store.get_load_stats(hours=BASELINE_HOURS)
        sample_count = stats.get("sample_count", 0)

        # Need at least 500 data points (5-min intervals over ~42 hours)
        if sample_count >= 500:
            self.baseline_complete = True

            # Compute circadian profile once baseline is ready
            self.circadian_profile = await self._detect_circadian_pattern()

        return self.baseline_complete

    async def _detect_circadian_pattern(self) -> Dict[str, Any]:
        """
        Analyze 48h of load history to detect circadian rhythm.
        Returns: {
            "type": "early_bird" | "night_owl" | "standard",
            "peak_hours": [int, ...],
            "trough_hours": [int, ...],
            "hourly_avg_cls": {hour: avg_cls, ...}
        }
        """
        records = await sqlite_store.get_load_history(hours=BASELINE_HOURS)

        hourly_data: Dict[int, list] = {}
        for r in records:
            try:
                ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                hour = ts.hour
                hourly_data.setdefault(hour, []).append(r["cls_score"])
            except Exception:
                continue

        hourly_avg = {}
        for hour, scores in hourly_data.items():
            if scores:
                hourly_avg[hour] = sum(scores) / len(scores)

        if not hourly_avg:
            return {
                "type": "standard",
                "peak_hours": [11, 14],
                "trough_hours": [3, 22],
                "hourly_avg_cls": {},
            }

        # Find peak (highest avg CLS) and trough (lowest) hours
        peak_hours = sorted(hourly_avg, key=lambda h: hourly_avg[h], reverse=True)[:3]
        trough_hours = sorted(hourly_avg, key=lambda h: hourly_avg[h])[:3]

        # Classify circadian type
        morning_avg = sum(hourly_avg.get(h, 50) for h in range(6, 12)) / 6
        evening_avg = sum(hourly_avg.get(h, 50) for h in range(18, 24)) / 6
        standard_avg = sum(hourly_avg.get(h, 50) for h in range(9, 17)) / 8

        if morning_avg < evening_avg and morning_avg < standard_avg:
            ctype = "night_owl"
        elif morning_avg > evening_avg and morning_avg < standard_avg:
            ctype = "early_bird"
        else:
            ctype = "standard"

        return {
            "type": ctype,
            "peak_hours": sorted(peak_hours),
            "trough_hours": sorted(trough_hours),
            "hourly_avg_cls": {str(h): round(v, 2) for h, v in hourly_avg.items()},
        }

    # ----- Threshold Adaptation -----

    async def recalibrate(self):
        """
        Recalibrate thresholds based on accumulated baseline data.
        Adjusts each threshold up or down by up to ±5% based on user's
        observed load distribution.
        """
        if not self.baseline_complete:
            baseline_done = await self.check_baseline_complete()
            if not baseline_done:
                return {"status": "learning", "message": "Insufficient baseline data"}

        stats = await sqlite_store.get_load_stats(hours=BASELINE_HOURS)
        mean_cls = stats.get("mean_cls", 50.0)

        # If user's mean CLS is higher than 50, they're generally more loaded
        # Shift thresholds down (more sensitive to overload)
        if mean_cls > 55:
            shift = -ADJUSTMENT_STEP_PCT
        elif mean_cls < 40:
            shift = ADJUSTMENT_STEP_PCT
        else:
            shift = 0.0

        if shift != 0.0:
            self.thresholds["restorative"] = max(5, self.thresholds["restorative"] + int(20 * shift))
            self.thresholds["light"] = max(15, self.thresholds["light"] + int(20 * shift))
            self.thresholds["focused"] = max(25, min(75, self.thresholds["focused"] + int(20 * shift)))
            self.thresholds["heavy"] = max(40, min(90, self.thresholds["heavy"] + int(20 * shift)))

            self._save_config()

        return {
            "status": "calibrated",
            "mean_cls": mean_cls,
            "threshold_shift": shift,
            "updated_thresholds": self.thresholds,
            "circadian_profile": self.circadian_profile,
        }

    # ----- Query Methods -----

    def get_thresholds(self) -> Dict[str, int]:
        return dict(self.thresholds)

    def get_circadian_profile(self) -> Optional[Dict]:
        return self.circadian_profile

    def is_learning(self) -> bool:
        return not self.baseline_complete

    def get_load_state(self, cls_score: float) -> str:
        """Classify a CLS score into a named state using current thresholds."""
        t = self.thresholds
        if cls_score <= t["restorative"]:
            return "restorative"
        elif cls_score <= t["light"]:
            return "light"
        elif cls_score <= t["focused"]:
            return "focused"
        elif cls_score <= t["heavy"]:
            return "heavy"
        else:
            return "overloaded"