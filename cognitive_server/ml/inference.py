"""
Cognitive Server - ML Inference
TFLite model runtime + heuristic fallback for Cognitive Load Score prediction.
Phase 1 uses heuristic; Phase 2 adds trained TFLite model.
"""

import math
import os
from typing import Tuple, Optional


# ---------------------------------------------------------------------------
# Heuristic CLS Calculator (Phase 1 Primary)
# ---------------------------------------------------------------------------

# Weights for heuristic CLS calculation (sum to ~1.0)
_HEURISTIC_WEIGHTS = {
    "kpm":              0.20,   # high KPM = busy but possibly focused OR frantic
    "switch_rate":      0.20,   # rapid switching = fragmented attention
    "scroll_entropy":   0.10,   # erratic scrolling = restlessness
    "mouse_entropy":    0.10,   # jittery mouse = anxiety / distraction
    "idle_ratio":       0.15,   # high idle = overwhelmed / stuck
    "tab_count":        0.10,   # many tabs = context overload
    "domain_switches":  0.10,   # hopping domains = scattered focus
    "time_of_day":      0.05,   # circadian modulation (minor factor)
}

# Inverse features: higher raw value = LOWER load (for normalization inversion)
_INVERSE_FEATURES = {"idle_ratio"}


def compute_cls_heuristic(features: dict) -> float:
    """
    Compute CLS using a weighted heuristic formula.
    Returns score in range [0, 100].

    This is the Phase 1 primary path. Produces reasonable estimates
    before a trained model is available.
    """
    score = 0.0

    for feat_name, weight in _HEURISTIC_WEIGHTS.items():
        raw = features.get(feat_name, 0.0)

        if feat_name in _INVERSE_FEATURES:
            # Higher idle_ratio means more overloaded (inverse of "rested")
            contribution = raw * weight
        elif feat_name == "time_of_day":
            # time_of_day is -1..1; shift to 0..1 (morning/midday peak, night low)
            # We model cognitive load as higher during typical work hours
            # sin-encoded: peak at noon, low at midnight
            time_contribution = (raw + 1.0) / 2.0  # map to 0..1
            contribution = time_contribution * weight * 0.5  # dampen
        elif feat_name == "kpm":
            # KPM has an inverted-U: too low = idle, too high = frantic
            # Peak load around 80-120 KPM
            kpm_normalized = raw  # already 0..1
            if kpm_normalized < 0.3:
                contribution = kpm_normalized * weight * 0.5
            elif kpm_normalized < 0.7:
                # Rising load zone
                contribution = (0.3 + (kpm_normalized - 0.3) * 2.0) * weight
            else:
                # Frantic zone
                contribution = (0.7 + (kpm_normalized - 0.7) * 1.5) * weight
        elif feat_name == "scroll_entropy":
            # Zero scrolling = either idle or reading; moderate = engaged; high = frantic
            scroll = raw
            if scroll < 0.1:
                contribution = scroll * weight * 0.3
            elif scroll < 0.5:
                contribution = (0.1 + (scroll - 0.1) * 1.5) * weight
            else:
                contribution = (0.5 + (scroll - 0.5) * 2.0) * weight
        elif feat_name == "tab_count":
            # 1-3 tabs = fine; 5-10 = moderate; 15+ = overloaded
            tc = raw
            if tc < 0.2:
                contribution = tc * weight * 0.5
            elif tc < 0.5:
                contribution = (0.2 + (tc - 0.2) * 1.5) * weight
            else:
                contribution = (0.5 + (tc - 0.5) * 2.0) * weight
        elif feat_name == "switch_rate":
            # Similar to KPM: some switching is normal, rapid switching bad
            sr = raw
            if sr < 0.2:
                contribution = sr * weight * 0.5
            elif sr < 0.6:
                contribution = (0.2 + (sr - 0.2) * 1.5) * weight
            else:
                contribution = (0.6 + (sr - 0.6) * 1.5) * weight
        elif feat_name == "domain_switches":
            ds = raw
            if ds < 0.2:
                contribution = ds * weight * 0.5
            elif ds < 0.5:
                contribution = (0.2 + (ds - 0.2) * 2.0) * weight
            else:
                contribution = (0.5 + (ds - 0.5) * 1.5) * weight
        else:
            # Default: linear contribution
            contribution = raw * weight

        score += contribution

    # Normalize to 0-100 range
    cls_score = min(100.0, max(0.0, score * 100.0))
    return round(cls_score, 2)


def compute_confidence(features: dict, cls_score: float) -> float:
    """
    Estimate confidence in heuristic CLS based on signal quality.
    Higher when multiple signals agree, lower when sparse or contradictory.
    """
    # Signal completeness: how many features are non-zero?
    nonzero = sum(1 for v in features.values() if v > 0.01)
    completeness = nonzero / len(features)

    # Signal consistency: check for extreme contradictions
    # (e.g., very high KPM with zero mouse movement = keystroke-only, still valid)
    # (very high idle_ratio with high KPM = contradictory)
    kpm = features.get("kpm", 0)
    idle = features.get("idle_ratio", 0)
    consistency_penalty = 0.0
    if kpm > 0.6 and idle > 0.6:
        consistency_penalty = 0.3  # contradictory signals
    if kpm > 0.8 and idle > 0.8:
        consistency_penalty = 0.5

    confidence = completeness * (1.0 - consistency_penalty)
    return round(max(0.1, min(1.0, confidence)), 3)


# ---------------------------------------------------------------------------
# TFLite Model Inference (Phase 2+)
# ---------------------------------------------------------------------------

def compute_cls_model(features: dict) -> Tuple[float, float]:
    """
    Compute CLS using a quantized TFLite model.
    Returns (cls_score: float, confidence: float).

    Falls back to heuristic if model is unavailable.
    """
    model_path = os.path.join(os.path.dirname(__file__), "model.tflite")

    if not os.path.exists(model_path):
        # Model not yet available -- fall back to heuristic
        cls = compute_cls_heuristic(features)
        conf = compute_confidence(features, cls)
        return cls, conf

    import numpy as np

    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        # tflite-runtime not installed -- fall back
        cls = compute_cls_heuristic(features)
        conf = compute_confidence(features, cls)
        return cls, conf

    try:
        # Load model
        interpreter = Interpreter(model_path=model_path)
        interpreter.allocate_tensors()

        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()

        # Build input vector in expected order
        feature_vector = [
            features["kpm"],
            features["switch_rate"],
            features["scroll_entropy"],
            features["mouse_entropy"],
            features["idle_ratio"],
            features["tab_count"],
            features["domain_switches"],
            features["time_of_day"],
        ]

        input_data = np.array([feature_vector], dtype=np.float32)
        interpreter.set_tensor(input_details[0]["index"], input_data)

        # Run inference
        interpreter.invoke()

        # Get output
        output_data = interpreter.get_tensor(output_details[0]["index"])
        cls_score = float(output_data[0][0])
        cls_score = min(100.0, max(0.0, cls_score))

        # Confidence from model if second output available, else estimate
        if len(output_details) > 1:
            confidence = float(interpreter.get_tensor(output_details[1]["index"])[0][0])
        else:
            confidence = compute_confidence(features, cls_score)

        return round(cls_score, 2), round(confidence, 3)

    except Exception:
        # Any inference error -> fallback
        cls = compute_cls_heuristic(features)
        conf = compute_confidence(features, cls)
        return cls, conf