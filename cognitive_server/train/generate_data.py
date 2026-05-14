"""
Cognitive Server - Synthetic Data Generator
Generates realistic training data for the CLS model.
Usage: python generate_data.py
Output: train/synthetic_data.csv (10,000+ samples)
"""

import csv
import math
import os
import random
from datetime import datetime, timedelta, timezone

# Output path (relative to project root)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "train")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "synthetic_data.csv")

# --- Behavioral Profiles ---
# Each profile represents a "type" of user behavior at different load levels

PROFILES = {
    "light_browsing": {
        "kpm": (20, 10),
        "switch_rate": (0.5, 0.3),
        "scroll_velocity": (100, 50),
        "scroll_delta": (10, 20),
        "mouse_entropy": (0.1, 0.05),
        "idle_ratio": (0.05, 0.03),
        "tab_count": (2, 1),
        "domain_switches": (0.2, 0.3),
    },
    "focused_coding": {
        "kpm": (80, 20),
        "switch_rate": (0.3, 0.2),
        "scroll_velocity": (200, 80),
        "scroll_delta": (5, 10),
        "mouse_entropy": (0.15, 0.05),
        "idle_ratio": (0.03, 0.02),
        "tab_count": (4, 1),
        "domain_switches": (0.3, 0.2),
    },
    "frantic_multitasking": {
        "kpm": (120, 30),
        "switch_rate": (5.0, 2.0),
        "scroll_velocity": (800, 300),
        "scroll_delta": (100, 50),
        "mouse_entropy": (0.8, 0.1),
        "idle_ratio": (0.08, 0.05),
        "tab_count": (15, 5),
        "domain_switches": (5.0, 2.0),
    },
    "idle_overwhelmed": {
        "kpm": (5, 5),
        "switch_rate": (0.1, 0.1),
        "scroll_velocity": (10, 5),
        "scroll_delta": (2, 3),
        "mouse_entropy": (0.3, 0.1),
        "idle_ratio": (0.7, 0.2),
        "tab_count": (10, 3),
        "domain_switches": (1.0, 0.5),
    },
    "meeting_heavy": {
        "kpm": (30, 15),
        "switch_rate": (2.0, 1.0),
        "scroll_velocity": (300, 100),
        "scroll_delta": (30, 20),
        "mouse_entropy": (0.4, 0.15),
        "idle_ratio": (0.3, 0.15),
        "tab_count": (6, 2),
        "domain_switches": (2.0, 1.0),
    },
}


def generate_sample(profile_name: str, cls_target: float, hour: float) -> dict:
    """Generate one sample based on a behavioral profile with noise."""
    profile = PROFILES[profile_name]
    noise_level = 0.15

    sample = {}
    for key, (mean, std) in profile.items():
        noise = random.gauss(0, noise_level)
        value = mean + noise * std
        value = max(0, value)  # No negative values

        # Scale toward cls_target (higher target = more extreme values)
        scale = 0.5 + (cls_target / 100.0) * 0.5
        if key not in ("idle_ratio", "mouse_entropy"):
            value *= scale
        else:
            # idle_ratio and mouse_entropy increase with load
            value = value * (0.5 + cls_target / 200.0)

        sample[key] = round(value, 2)

    # Add time_of_day (sin/cos encoded)
    sample["time_of_day"] = round(math.sin(2 * math.pi * hour / 24), 4)

    # Cap values
    sample["kpm"] = min(sample["kpm"], 200)
    sample["switch_rate"] = min(sample["switch_rate"], 20)
    sample["tab_count"] = min(int(sample["tab_count"]), 30)
    sample["idle_ratio"] = min(sample["idle_ratio"], 0.95)
    sample["mouse_entropy"] = min(sample["mouse_entropy"], 1.0)
    sample["domain_switches"] = min(sample["domain_switches"], 15)

    return sample


def generate_dataset(num_samples: int = 10000) -> list:
    """Generate the full synthetic dataset."""
    samples = []
    base_time = datetime(2026, 5, 14, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(num_samples):
        # Vary the target CLS across the full range
        cls_target = (i / num_samples) * 100
        hour_offset = (i * 0.25) % 24  # Spread across 24 hours
        current_time = base_time + timedelta(minutes=i)

        # Choose a behavioral profile based on load level
        if cls_target < 20:
            profile = "light_browsing"
        elif cls_target < 40:
            profile = random.choice(["light_browsing", "focused_coding"])
        elif cls_target < 60:
            profile = "focused_coding"
        elif cls_target < 75:
            profile = random.choice(["focused_coding", "frantic_multitasking", "meeting_heavy"])
        else:
            profile = random.choice(["frantic_multitasking", "idle_overwhelmed", "meeting_heavy"])

        sample = generate_sample(profile, cls_target, hour_offset)

        sample["session_id"] = f"sess_{i // 100:04d}"
        sample["timestamp"] = current_time.isoformat()

        # Store the target CLS as ground truth (for training)
        sample["cls_target"] = round(cls_target, 2)

        samples.append(sample)

    return samples


def save_to_csv(samples: list, filepath: str):
    """Save samples to a CSV file."""
    if not samples:
        return

    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    fieldnames = [
        "session_id", "timestamp", "kpm", "inter_key_avg",
        "switch_rate", "scroll_velocity", "scroll_delta",
        "mouse_entropy", "idle_ratio", "tab_count",
        "domain_switches", "time_of_day", "cls_target",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)

    print(f"Saved {len(samples)} samples to {filepath}")


def main():
    print("Generating synthetic training data for Cognitive CLS model...")
    print("Profiles: light_browsing, focused_coding, frantic_multitasking,")
    print("          idle_overwhelmed, meeting_heavy")
    print()

    samples = generate_dataset(10000)
    save_to_csv(samples, OUTPUT_FILE)

    # Print statistics
    cls_values = [s["cls_target"] for s in samples]
    print(f"\nDataset Statistics:")
    print(f"  Total samples: {len(samples)}")
    print(f"  CLS range: {min(cls_values):.1f} - {max(cls_values):.1f}")
    print(f"  Mean CLS: {sum(cls_values)/len(cls_values):.1f}")
    print(f"  Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()