"""
Cognitive Server - Adaptive Personalizer
Online learning system that continuously improves based on user behavior.
Tracks implicit feedback, adapts normalization, and learns optimal weights.

Phase 5 features:
- 24h baseline collection before enabling full intervention
- Circadian rhythm detection (night owl vs early bird)
- Weekly threshold recalibration (±5% adjustment)
- Baseline progress tracking
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from collections import deque
import numpy as np

from cognitive_server.db import sqlite_store


DEFAULT_THRESHOLDS = {
    "restorative": 20,
    "light": 40,
    "focused": 60,
    "heavy": 75,
    "overloaded": 100,
}

DEFAULT_FEATURE_WEIGHTS = {
    "kpm": 0.20,
    "switch_rate": 0.20,
    "scroll_entropy": 0.10,
    "mouse_entropy": 0.10,
    "idle_ratio": 0.15,
    "tab_count": 0.10,
    "domain_switches": 0.10,
    "time_of_day": 0.05,
}


class AdaptivePersonalizer:
    """
    Self-improving personalizer that learns from user behavior.
    
    Key improvements over basic personalizer:
    1. Online weight calibration via gradient descent
    2. Rolling percentile normalization (adapts to user's actual range)
    3. Implicit feedback tracking (user actions inform accuracy)
    4. Time-of-day specific calibration
    5. Confidence-weighted predictions
    6. Baseline collection mode (24h before full intervention)
    7. Circadian rhythm classification (night owl / early bird / neutral)
    8. Weekly threshold recalibration (±5% adjustment)
    """

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "..", "config", "user_profile.json"
        )
        self.config = self._load_config()
        
        # Load or initialize state
        cognitiva = self.config.get("cognitive_load", {})
        self.thresholds = cognitiva.get("thresholds", dict(DEFAULT_THRESHOLDS))
        self.feature_weights = cognitiva.get("feature_weights", dict(DEFAULT_FEATURE_WEIGHTS))
        self.circadian_profile = cognitiva.get("circadian_profile", None)
        
        # Baseline collection state (Phase 5)
        baseline_config = cognitiva.get("baseline", {})
        self.baseline_start = baseline_config.get("start_time", None)
        self.baseline_duration_hours = baseline_config.get("duration_hours", 24)
        self.baseline_active = baseline_config.get("active", True)
        
        # Weekly recalibration state (Phase 5)
        recal_config = cognitiva.get("recalibration", {})
        self.last_recalibration = recal_config.get("last_time", None)
        self.recalibration_interval_hours = recal_config.get("interval_hours", 168)  # 7 days
        self.recalibration_adjustment_pct = recal_config.get("adjustment_pct", 5)
        
        # Rolling statistics for adaptive normalization (percentiles)
        self.feature_percentiles = cognitiva.get("feature_percentiles", {
            k: {"p10": 0, "p50": 0.5, "p90": 1} for k in DEFAULT_FEATURE_WEIGHTS.keys()
        })
        
        # Feedback history for implicit learning
        self.feedback_history = deque(maxlen=200)
        
        # Time-of-day weight adjustments (learned per time period)
        self.time_weights = cognitiva.get("time_weights", {
            "morning": dict(DEFAULT_FEATURE_WEIGHTS),   # 6-12
            "afternoon": dict(DEFAULT_FEATURE_WEIGHTS), # 12-18
            "evening": dict(DEFAULT_FEATURE_WEIGHTS),   # 18-24
            "night": dict(DEFAULT_FEATURE_WEIGHTS),     # 0-6
        })
        
        # Track accuracy over time for confidence scoring
        self.prediction_accuracy = deque(maxlen=100)
        
        # Feature history for percentile calculation
        self.feature_history = {
            k: deque(maxlen=500) for k in DEFAULT_FEATURE_WEIGHTS.keys()
        }
        
        # Learning rate for weight updates
        self.learning_rate = 0.02
        
        # Minimum samples before adaptation starts
        self.min_samples_for_adaptation = 100

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_config(self):
        try:
            if "cognitive_load" not in self.config:
                self.config["cognitive_load"] = {}
            self.config["cognitive_load"]["thresholds"] = self.thresholds
            self.config["cognitive_load"]["feature_weights"] = self.feature_weights
            self.config["cognitive_load"]["circadian_profile"] = self.circadian_profile
            self.config["cognitive_load"]["feature_percentiles"] = self.feature_percentiles
            self.config["cognitive_load"]["time_weights"] = self.time_weights
            self.config["cognitive_load"]["baseline"] = {
                "start_time": self.baseline_start,
                "duration_hours": self.baseline_duration_hours,
                "active": self.baseline_active,
            }
            self.config["cognitive_load"]["recalibration"] = {
                "last_time": self.last_recalibration,
                "interval_hours": self.recalibration_interval_hours,
                "adjustment_pct": self.recalibration_adjustment_pct,
            }
            
            with open(self.config_path, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    # ----- Baseline Collection (Phase 5) -----

    def start_baseline_collection(self, duration_hours: int = 24):
        """Start the 24h baseline collection period before enabling full intervention."""
        self.baseline_start = datetime.now(timezone.utc).isoformat()
        self.baseline_duration_hours = duration_hours
        self.baseline_active = True
        self._save_config()

    def get_baseline_progress(self) -> Dict:
        """Get baseline collection progress (0-1) and estimated completion time."""
        if not self.baseline_active or not self.baseline_start:
            return {"active": False, "progress": 1.0, "complete": True}

        start = datetime.fromisoformat(self.baseline_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        elapsed = (now - start).total_seconds() / 3600
        progress = min(1.0, elapsed / self.baseline_duration_hours)

        if progress >= 1.0:
            self.baseline_active = False
            self._save_config()
            return {"active": False, "progress": 1.0, "complete": True}

        remaining_hours = self.baseline_duration_hours - elapsed
        return {
            "active": True,
            "progress": round(progress, 3),
            "complete": False,
            "remaining_hours": round(remaining_hours, 1),
            "started_at": self.baseline_start,
        }

    def is_baseline_complete(self) -> bool:
        """Check if baseline collection is complete."""
        progress = self.get_baseline_progress()
        return progress["complete"]

    # ----- Circadian Rhythm Classification (Phase 5) -----

    def classify_circadian_rhythm(self) -> str:
        """
        Classify user's circadian rhythm pattern.
        
        Returns: "early_bird", "night_owl", "neutral"
        """
        if not self.circadian_profile or "hourly_avg" not in self.circadian_profile:
            return "neutral"

        hourly_avg = self.circadian_profile["hourly_avg"]

        # Calculate average CLS for morning (6-10) vs evening (18-22)
        morning_scores = []
        evening_scores = []

        for hour_str, score in hourly_avg.items():
            try:
                hour = int(hour_str)
            except (ValueError, TypeError):
                continue

            if 6 <= hour <= 10:
                morning_scores.append(score)
            elif 18 <= hour <= 22:
                evening_scores.append(score)

        if not morning_scores or not evening_scores:
            return "neutral"

        morning_avg = sum(morning_scores) / len(morning_scores)
        evening_avg = sum(evening_scores) / len(evening_scores)

        # Lower CLS = better performance
        # If morning CLS is significantly lower than evening → early bird
        # If evening CLS is significantly lower than morning → night owl
        diff = morning_avg - evening_avg

        if diff < -5:
            return "early_bird"
        elif diff > 5:
            return "night_owl"
        else:
            return "neutral"

    # ----- Weekly Threshold Recalibration (Phase 5) -----

    async def check_weekly_recalibration(self) -> Dict:
        """
        Check if weekly recalibration is due and apply ±5% adjustment.
        
        Analyzes the past week's CLS distribution and adjusts thresholds
        to better match the user's actual patterns.
        """
        now = datetime.now(timezone.utc)

        # Check if recalibration is due
        if self.last_recalibration:
            last = datetime.fromisoformat(self.last_recalibration)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds() / 3600
            if elapsed < self.recalibration_interval_hours:
                return {"recalibrated": False, "next_check_hours": round(self.recalibration_interval_hours - elapsed, 1)}

        # Get past week's data
        records = await sqlite_store.get_load_history(hours=168)
        if len(records) < 100:
            return {"recalibrated": False, "reason": "insufficient_data"}

        # Compute percentile distribution
        scores = [r["cls_score"] for r in records]
        p25 = np.percentile(scores, 25)
        p50 = np.percentile(scores, 50)
        p75 = np.percentile(scores, 75)

        # Adjust thresholds based on actual distribution
        adjustment = self.recalibration_adjustment_pct / 100.0

        # If median is higher than expected, user tends toward higher load → raise thresholds
        # If median is lower, user tends toward lower load → lower thresholds
        if p50 > 60:
            # User is often in heavy/overloaded state → raise thresholds slightly
            direction = 1
        elif p50 < 30:
            # User is often in restorative/light state → lower thresholds slightly
            direction = -1
        else:
            direction = 0

        if direction != 0:
            for key in ["restorative", "light", "focused", "heavy"]:
                current = self.thresholds[key]
                delta = current * adjustment * direction
                self.thresholds[key] = max(5, min(100, round(current + delta)))

        self.last_recalibration = now.isoformat()
        self._save_config()

        return {
            "recalibrated": True,
            "direction": "up" if direction > 0 else "down" if direction < 0 else "none",
            "new_thresholds": dict(self.thresholds),
            "percentiles": {"p25": round(p25, 1), "p50": round(p50, 1), "p75": round(p75, 1)},
        }

    # ----- Feature Normalization -----

    def _update_feature_percentiles(self, features: Dict[str, float]):
        """Update rolling percentiles for each feature."""
        for feature, value in features.items():
            if feature in self.feature_history:
                self.feature_history[feature].append(value)
                
                if len(self.feature_history[feature]) >= 20:
                    values = list(self.feature_history[feature])
                    sorted_vals = sorted(values)
                    n = len(sorted_vals)
                    
                    self.feature_percentiles[feature] = {
                        "p10": sorted_vals[int(n * 0.1)],
                        "p50": sorted_vals[int(n * 0.5)],
                        "p90": sorted_vals[int(n * 0.9)],
                    }

    def _normalize_feature(self, feature: str, value: float) -> float:
        """Normalize feature using user's learned percentiles."""
        if feature not in self.feature_percentiles:
            return value
            
        p = self.feature_percentiles[feature]
        p10, p90 = p["p10"], p["p90"]
        
        if p90 <= p10:
            return 0.5
            
        normalized = (value - p10) / (p90 - p10)
        return max(0, min(1, normalized))

    # ----- Time-of-Day Adaptation -----

    def _get_time_period(self) -> str:
        """Get current time period for weight adjustment."""
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 24:
            return "evening"
        else:
            return "night"

    def get_adaptive_weights(self, features: Dict[str, float]) -> Dict[str, float]:
        """Get weights adjusted for time of day and current features."""
        time_period = self._get_time_period()
        time_weights = self.time_weights.get(time_period, DEFAULT_FEATURE_WEIGHTS)
        
        # If we don't have enough history, return time-adjusted defaults
        total_samples = sum(len(h) for h in self.feature_history.values())
        if total_samples < self.min_samples_for_adaptation:
            return dict(time_weights)
        
        # Otherwise, blend time-specific weights with base weights
        blended = {}
        for feature, base_weight in DEFAULT_FEATURE_WEIGHTS.items():
            time_weight = time_weights.get(feature, base_weight)
            # Blend: 70% time-specific, 30% baseline
            blended[feature] = 0.7 * time_weight + 0.3 * base_weight
        
        return blended

    # ----- Implicit Feedback Learning -----

    def record_feedback(self, feedback_type: str, context: Dict):
        """
        Record implicit user feedback for learning.
        
        feedback_type:
        - "manual_release": User manually released held notifications
        - "auto_release_ok": Auto-release after CLS<40 was appropriate
        - "ignored_intervention": User ignored the intervention
        - "correct_state": Predicted state matched observed behavior
        """
        self.feedback_history.append({
            "type": feedback_type,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        # Trigger learning if we have enough feedback
        if len(self.feedback_history) >= 10:
            self._learn_from_feedback()

    def _learn_from_feedback(self):
        """Update weights based on accumulated feedback."""
        if len(self.feedback_history) < 10:
            return
            
        # Count feedback types
        manual_releases = sum(1 for f in self.feedback_history if f["type"] == "manual_release")
        auto_releases = sum(1 for f in self.feedback_history if f["type"] == "auto_release_ok")
        
        # If manual releases are high, the model is overestimating load
        # If auto releases are high, the model is accurate
        
        if manual_releases > auto_releases * 1.5:
            # System is too aggressive - shift weights toward less sensitive features
            adjustment = -self.learning_rate
        elif auto_releases > manual_releases * 1.5:
            # System is accurate - slight boost to confidence
            adjustment = self.learning_rate
        else:
            adjustment = 0
            
        if adjustment != 0:
            # Increase weight on idle_ratio and time_of_day (less sensitive to spikes)
            self.feature_weights["idle_ratio"] = min(0.3, self.feature_weights.get("idle_ratio", 0.15) + adjustment)
            self.feature_weights["time_of_day"] = min(0.15, self.feature_weights.get("time_of_day", 0.05) + adjustment)
            
            # Decrease weight on kpm and switch_rate (more volatile)
            self.feature_weights["kpm"] = max(0.10, self.feature_weights.get("kpm", 0.20) - adjustment * 0.5)
            self.feature_weights["switch_rate"] = max(0.10, self.feature_weights.get("switch_rate", 0.20) - adjustment * 0.5)
            
            self._save_config()

    # ----- Adaptive Prediction -----

    def compute_adaptive_score(self, features: Dict[str, float], 
                                 base_heuristic_score: float = None,
                                 ml_score: float = None) -> Tuple[float, float]:
        """
        Compute CLS score with learned adaptation.
        
        Returns: (adapted_score, confidence)
        """
        # Update percentiles with new features
        self._update_feature_percentiles(features)
        
        # Get time-adaptive weights
        weights = self.get_adaptive_weights(features)
        
        # Calculate weighted normalized score
        adapted_score = 0.0
        total_weight = 0.0
        
        for feature, value in features.items():
            if feature in weights:
                normalized = self._normalize_feature(feature, value)
                weight = weights[feature]
                adapted_score += normalized * weight
                total_weight += weight
        
        if total_weight > 0:
            adapted_score = (adapted_score / total_weight) * 100
        
        # Blend with ML model if available
        if ml_score is not None:
            ml_confidence = self._get_ml_confidence()
            # Weighted blend: more weight to ML as confidence increases
            adapted_score = adapted_score * (1 - ml_confidence) + ml_score * ml_confidence
        
        # Blend with base heuristic if provided
        if base_heuristic_score is not None:
            conf = self._get_confidence()
            adapted_score = adapted_score * (1 - conf) + base_heuristic_score * conf
        
        # Calculate confidence based on data coverage
        confidence = self._get_confidence()
        
        return round(adapted_score, 2), round(confidence, 3)

    def _get_confidence(self) -> float:
        """Calculate confidence based on amount of real user data."""
        total_samples = sum(len(h) for h in self.feature_history.values())
        
        if total_samples < 50:
            return 0.1  # Very low confidence
        elif total_samples < 100:
            return 0.3
        elif total_samples < 200:
            return 0.5
        elif total_samples < 500:
            return 0.7
        else:
            return 0.9

    def _get_ml_confidence(self) -> float:
        """Calculate confidence in ML model based on synthetic vs real gap."""
        # If we have real feedback, we're more confident in model
        recent_feedback = list(self.feedback_history)[-20:]
        correct_count = sum(1 for f in recent_feedback if f["type"] == "correct_state")
        
        if len(recent_feedback) < 5:
            return 0.2  # Low confidence without feedback
        return min(0.8, correct_count / len(recent_feedback))

    # ----- Circadian Pattern Detection -----

    async def update_circadian_profile(self):
        """Detect and update circadian patterns from recent data."""
        records = await sqlite_store.get_load_history(hours=72)
        
        if len(records) < 50:
            return
            
        hourly_cls = {h: [] for h in range(24)}
        for r in records:
            try:
                ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                hourly_cls[ts.hour].append(r["cls_score"])
            except:
                continue
        
        # Calculate average CLS per hour
        hourly_avg = {}
        for hour, scores in hourly_cls.items():
            if scores:
                hourly_avg[hour] = sum(scores) / len(scores)
        
        if not hourly_avg:
            return
            
        # Update time-specific weights based on patterns
        for period, hours in {
            "morning": list(range(6, 12)),
            "afternoon": list(range(12, 18)),
            "evening": list(range(18, 24)),
            "night": list(range(0, 6)),
        }.items():
            period_avg = sum(hourly_avg.get(h, 50) for h in hours) / len(hours)
            
            # Adjust weights based on typical load at this time
            if period_avg > 60:  # High load period
                # Be more sensitive - boost idle_ratio weight
                self.time_weights[period]["idle_ratio"] = min(0.25, 
                    self.time_weights[period].get("idle_ratio", 0.15) + 0.02)
            elif period_avg < 40:  # Low load period
                # Less sensitive - reduce weights
                self.time_weights[period]["idle_ratio"] = max(0.05,
                    self.time_weights[period].get("idle_ratio", 0.15) - 0.02)
        
        self.circadian_profile = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "hourly_avg": {str(h): round(v, 2) for h, v in hourly_avg.items()},
        }
        
        self._save_config()

    # ----- Public API -----

    def get_thresholds(self) -> Dict[str, int]:
        return dict(self.thresholds)

    def get_load_state(self, cls_score: float) -> str:
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

    def is_learning(self) -> bool:
        return sum(len(h) for h in self.feature_history.values()) < self.min_samples_for_adaptation

    def get_stats(self) -> Dict:
        """Get personalization statistics."""
        baseline = self.get_baseline_progress()
        return {
            "total_samples": sum(len(h) for h in self.feature_history.values()),
            "feedback_count": len(self.feedback_history),
            "time_period": self._get_time_period(),
            "confidence": self._get_confidence(),
            "is_learning": self.is_learning(),
            "circadian_profile": self.circadian_profile,
            "circadian_rhythm": self.classify_circadian_rhythm(),
            "baseline": baseline,
            "thresholds": dict(self.thresholds),
            "last_recalibration": self.last_recalibration,
        }


# Singleton instance
_personalizer = None


def get_personalizer() -> AdaptivePersonalizer:
    global _personalizer
    if _personalizer is None:
        _personalizer = AdaptivePersonalizer()
    return _personalizer


# Backward compatibility alias
Personalizer = AdaptivePersonalizer