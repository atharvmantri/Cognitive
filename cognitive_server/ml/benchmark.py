"""
Cognitive Server - Inference Benchmark
Standalone script to verify NFR-P-001: CLS inference latency < 50 ms.

Usage:
    python cognitive_server/ml/benchmark.py
"""

import os
import sys
import time

# Resolve paths
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(ROOT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cognitive_server.ml.inference import compute_cls_model, get_model_info

ITERATIONS = 10000

FEATURE_SETS = [
    {"kpm": 0.1, "switch_rate": 0.05, "scroll_entropy": 0.01, "mouse_entropy": 0.1, "idle_ratio": 0.02, "tab_count": 0.1, "domain_switches": 0.05, "time_of_day": 0.0},
    {"kpm": 0.5, "switch_rate": 0.5, "scroll_entropy": 0.3, "mouse_entropy": 0.4, "idle_ratio": 0.2, "tab_count": 0.5, "domain_switches": 0.2, "time_of_day": 0.0},
    {"kpm": 0.9, "switch_rate": 0.9, "scroll_entropy": 0.8, "mouse_entropy": 0.9, "idle_ratio": 0.8, "tab_count": 0.9, "domain_switches": 0.9, "time_of_day": 0.5},
]

def benchmark():
    info = get_model_info()
    print(f"Model: {info['type']} ({info['size_kb']:.1f} KB)")
    print(f"Path: {info['path']}")
    print(f"Iterations: {ITERATIONS}")
    print()

    all_times = []
    for i, features in enumerate(FEATURE_SETS):
        # Warmup
        compute_cls_model(features)

        t0 = time.perf_counter()
        for _ in range(ITERATIONS):
            cls, conf = compute_cls_model(features)
        elapsed = time.perf_counter() - t0
        avg_ms = (elapsed / ITERATIONS) * 1000
        all_times.append(avg_ms)

        print(f"  Profile {i+1}: CLS={cls:.2f}, conf={conf:.3f}, avg={avg_ms:.3f} ms")

    overall_avg = sum(all_times) / len(all_times)
    p99 = max(all_times)

    print()
    print(f"Overall avg: {overall_avg:.3f} ms")
    print(f"Worst case:  {p99:.3f} ms")
    print(f"Target:      < 50 ms")
    print(f"Result:      {'PASS' if overall_avg < 50 else 'FAIL'}")

    return overall_avg < 50

if __name__ == "__main__":
    success = benchmark()
    sys.exit(0 if success else 1)
