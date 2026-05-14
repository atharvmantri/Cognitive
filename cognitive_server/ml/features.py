"""
Cognitive Server - Feature Engineering
Transforms raw behavioral signals into the 8-dimensional feature vector
used by the CLS inference model.
"""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import math


def engineer_features(signals: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Compute the 8-dim feature vector from a window of raw signal readings.

    Input: list of signal dicts from the last ~30 seconds (6 batches at 5s intervals).
    Output: dict with 8 normalized features ready for model inference.
    """
    if not signals:
        return _zero_features()

    n = len(signals)

    # 1. kpm - average keystrokes per minute
    kpm = _safe_mean([s.get("kpm", 0) for s in signals])

    # 2. switch_rate - average window/tab switches per minute
    switch_rate = _safe_mean([s.get("switch_rate", 0) for s in signals])

    # 3. scroll_entropy - variance in scroll velocity (higher = more erratic)
    scroll_velocities = [s.get("scroll_velocity", 0) for s in signals]
    scroll_entropy = _variance(scroll_velocities)

    # 4. mouse_entropy - average mouse movement randomness
    mouse_entropy = _safe_mean([s.get("mouse_entropy", 0) for s in signals])

    # 5. idle_ratio - average fraction of idle time in the window
    idle_ratio = _safe_mean([s.get("idle_ratio", 0) for s in signals])

    # 6. tab_count - average number of open tabs
    tab_count = _safe_mean([s.get("tab_count", 1) for s in signals])

    # 7. domain_switches - unique domains visited in the window
    domains = set()
    for s in signals:
        url = s.get("active_url", "")
        if url:
            domains.add(url)
    domain_switches = len(domains)

    # 8. time_of_day - sin/cos encoding of current hour (use latest signal)
    latest = signals[-1]
    time_of_day = latest.get("time_of_day", 0.0)

    features = {
        "kpm": _normalize(kpm, 0, 120),
        "switch_rate": _normalize(switch_rate, 0, 30),
        "scroll_entropy": _normalize(scroll_entropy, 0, 5000),
        "mouse_entropy": _clamp(mouse_entropy, 0, 1),
        "idle_ratio": _clamp(idle_ratio, 0, 1),
        "tab_count": _normalize(tab_count, 0, 30),
        "domain_switches": _normalize(domain_switches, 0, 15),
        "time_of_day": _clamp(time_of_day, -1, 1),
    }

    return features


def feature_vector_to_array(features: Dict[str, float]) -> list:
    """Convert feature dict to ordered 8-dim list for TFLite model input."""
    return [
        features["kpm"],
        features["switch_rate"],
        features["scroll_entropy"],
        features["mouse_entropy"],
        features["idle_ratio"],
        features["tab_count"],
        features["domain_switches"],
        features["time_of_day"],
    ]


# --- Helpers ---

def _safe_mean(values: list) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _normalize(value: float, min_val: float, max_val: float) -> float:
    """Normalize value to 0-1 range."""
    if max_val <= min_val:
        return 0.0
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _zero_features() -> Dict[str, float]:
    return {
        "kpm": 0.0,
        "switch_rate": 0.0,
        "scroll_entropy": 0.0,
        "mouse_entropy": 0.0,
        "idle_ratio": 0.0,
        "tab_count": 0.0,
        "domain_switches": 0.0,
        "time_of_day": 0.0,
    }