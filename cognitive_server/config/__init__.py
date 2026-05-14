"""
Cognitive Server - Config Loader
Simple JSON config reader with defaults.
"""

import json
import os
from typing import Dict


_config_cache: Dict = {}


def load_config(config_path: str = None) -> Dict:
    """
    Load the user profile configuration from JSON.
    Caches the result for performance.
    """
    global _config_cache

    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "user_profile.json",
        )

    # Return cached config if available
    if _config_cache:
        return _config_cache

    try:
        with open(config_path, "r") as f:
            _config_cache = json.load(f)
        return _config_cache
    except FileNotFoundError:
        # Return sane defaults if config missing
        return {
            "cognitive_load": {
                "thresholds": {
                    "restorative": 20,
                    "light": 40,
                    "focused": 60,
                    "heavy": 75,
                    "overloaded": 100,
                }
            },
            "interventions": {
                "hold_threshold": 60,
                "release_threshold": 40,
                "release_sustained_seconds": 300,
                "scheduled_release_interval_minutes": 90,
                "urgency_bypass_enabled": True,
                "keyword_triggers": [
                    "URGENT",
                    "PRODUCTION DOWN",
                    "CRITICAL",
                    "911",
                    "ASAP",
                    "EMERGENCY",
                ],
                "escalation_window_minutes": 10,
                "escalation_repeat_count": 2,
            },
            "decision_proxy": {
                "max_options": 3,
                "default_meeting_duration_minutes": 30,
            },
            "whitelist": {
                "senders": [],
                "domains": [],
            },
            "privacy": {
                "log_keystroke_content": False,
                "log_screenshot": False,
                "store_notification_content_hours": 48,
            },
        }


def reload_config():
    """Force reload config from disk."""
    global _config_cache
    _config_cache = {}
    return load_config()