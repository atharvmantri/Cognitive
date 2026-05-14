"""
Cognitive Server - Unit Tests
Tests for signal ingestion, CLS computation, feature engineering,
intervention logic, and decision proxy.
"""

import pytest
import json
import os
import sys
import tempfile
import math
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient

from cognitive_server.ml.features import engineer_features, feature_vector_to_array, _normalize
from cognitive_server.ml.inference import compute_cls_heuristic, compute_confidence, compute_cls_model
from cognitive_server.api.load import _classify_state, _compute_estimated_recovery


@pytest.fixture(scope="module")
def test_db():
    """Initialize and yield a test database path."""
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    import asyncio
    from cognitive_server.db.sqlite_store import initialize
    asyncio.run(initialize(db_path))
    yield db_path
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture(scope="module")
def test_app(test_db):
    """Create a test FastAPI application instance with a pre-initialized DB."""
    os.environ["COGNITIVE_DB_PATH"] = test_db
    from cognitive_server.main import app
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    del os.environ["COGNITIVE_DB_PATH"]


class TestFeatureEngineering:
    def test_empty_signals_returns_zero_features(self):
        result = engineer_features([])
        assert all(v == 0.0 for v in result.values())

    def test_single_signal_valid(self):
        signal = {
            "session_id": "test", "timestamp": "2026-05-14T10:30:00Z",
            "kpm": 60.0, "inter_key_avg": 200.0, "switch_rate": 2.5,
            "scroll_velocity": 500.0, "scroll_delta": 100.0,
            "mouse_entropy": 0.45, "idle_ratio": 0.1, "tab_count": 5,
            "domain_switches": 2, "time_of_day": 0.5,
            "active_url": "example.com", "active_title": "Test Page",
            "idle_seconds": 5,
        }
        result = engineer_features([signal])
        for v in result.values():
            assert 0.0 <= v <= 1.0

    def test_feature_vector_to_array_order(self):
        features = {
            "kpm": 0.5, "switch_rate": 0.3, "scroll_entropy": 0.7,
            "mouse_entropy": 0.4, "idle_ratio": 0.2, "tab_count": 0.6,
            "domain_switches": 0.1, "time_of_day": 0.8,
        }
        assert feature_vector_to_array(features) == [0.5, 0.3, 0.7, 0.4, 0.2, 0.6, 0.1, 0.8]

    def test_multiple_signals_averaged(self):
        signals = [
            {"kpm": 10.0, "switch_rate": 1.0, "scroll_velocity": 100.0,
             "scroll_delta": 50.0, "mouse_entropy": 0.2, "idle_ratio": 0.0,
             "tab_count": 3, "domain_switches": 1, "time_of_day": 0.5},
            {"kpm": 50.0, "switch_rate": 3.0, "scroll_velocity": 500.0,
             "scroll_delta": 200.0, "mouse_entropy": 0.6, "idle_ratio": 0.1,
             "tab_count": 5, "domain_switches": 2, "time_of_day": 0.5},
        ]
        result = engineer_features(signals)
        assert abs(result["kpm"] - _normalize(30.0, 0, 120)) < 0.01

    def test_extreme_values_clamped(self):
        signal = {"kpm": 9999.0, "switch_rate": 1000.0, "scroll_velocity": 99999.0,
                  "scroll_delta": 99999.0, "mouse_entropy": 1.0, "idle_ratio": 1.0,
                  "tab_count": 100, "domain_switches": 100, "time_of_day": 5.0}
        result = engineer_features([signal])
        for v in result.values():
            assert 0.0 <= v <= 1.0


class TestHeuristicCLS:
    def test_zero_features_low_score(self):
        features = {k: 0.0 for k in ["kpm", "switch_rate", "scroll_entropy",
                                       "mouse_entropy", "idle_ratio", "tab_count",
                                       "domain_switches", "time_of_day"]}
        assert 0.0 <= compute_cls_heuristic(features) <= 30.0

    def test_high_activity_high_score(self):
        features = {k: 1.0 for k in ["kpm", "switch_rate", "scroll_entropy",
                                       "mouse_entropy", "idle_ratio", "tab_count",
                                       "domain_switches", "time_of_day"]}
        assert compute_cls_heuristic(features) > 50.0

    def test_score_in_range(self):
        for val in [0.0, 0.5, 1.0]:
            features = {k: val for k in ["kpm", "switch_rate", "scroll_entropy",
                                           "mouse_entropy", "idle_ratio", "tab_count",
                                           "domain_switches", "time_of_day"]}
            score = compute_cls_heuristic(features)
            assert 0.0 <= score <= 100.0

    def test_confidence_good_signals(self):
        sparse = {"kpm": 0.5}
        full = {k: 0.5 for k in ["kpm", "switch_rate", "scroll_entropy",
                                   "mouse_entropy", "idle_ratio", "tab_count",
                                   "domain_switches", "time_of_day"]}
        assert compute_confidence(full, 50.0) >= compute_confidence(sparse, 50.0)

    def test_confidence_contradictory(self):
        c = {"kpm": 0.9, "switch_rate": 0.1, "scroll_entropy": 0.1,
             "mouse_entropy": 0.1, "idle_ratio": 0.9, "tab_count": 0.5,
             "domain_switches": 0.1, "time_of_day": 0.0}
        assert compute_confidence(c, 50.0) < 0.9

    def test_normalize(self):
        assert _normalize(50, 0, 100) == 0.5
        assert _normalize(0, 0, 100) == 0.0
        assert _normalize(100, 0, 100) == 1.0
        assert _normalize(200, 0, 100) == 1.0
        assert _normalize(-10, 0, 100) == 0.0

    def test_variance(self):
        from cognitive_server.ml.features import _variance
        assert _variance([5.0, 5.0, 5.0]) == 0.0
        assert _variance([1.0, 5.0, 9.0]) > 0.0
        assert _variance([]) == 0.0


class TestModelInference:
    def test_model_missing_fallback(self):
        features = {"kpm": 0.5, "switch_rate": 0.5, "scroll_entropy": 0.3,
                     "mouse_entropy": 0.4, "idle_ratio": 0.2, "tab_count": 0.5,
                     "domain_switches": 0.2, "time_of_day": 0.0}
        cls_score, confidence = compute_cls_model(features)
        assert 0.0 <= cls_score <= 100.0
        assert 0.0 < confidence <= 1.0


class TestLoadState:
    def test_ranges(self):
        for val, expected in [(10, "restorative"), (30, "light"),
                              (50, "focused"), (70, "heavy"), (90, "overloaded")]:
            assert _classify_state(val) == expected

    def test_recovery_estimate(self):
        assert _compute_estimated_recovery(30.0) is not None
        assert _compute_estimated_recovery(85.0) is not None


class TestPersonalizer:
    def test_default_thresholds(self):
        from cognitive_server.ml.personalizer import Personalizer
        p = Personalizer()
        assert all(k in p.get_thresholds() for k in
                   ["restorative", "light", "focused", "heavy", "overloaded"])

    def test_custom_thresholds(self):
        from cognitive_server.ml.personalizer import Personalizer
        p = Personalizer()
        p.thresholds = {"restorative": 15, "light": 35, "focused": 55, "heavy": 70, "overloaded": 100}
        assert p.get_load_state(10) == "restorative"
        assert p.get_load_state(30) == "light"
        assert p.get_load_state(50) == "focused"
        assert p.get_load_state(65) == "heavy"
        assert p.get_load_state(90) == "overloaded"

    def test_learning_default(self):
        from cognitive_server.ml.personalizer import Personalizer
        p = Personalizer()
        assert p.is_learning()
        assert p.circadian_profile is None


class TestUrgencyClassifier:
    def test_detect_urgent_keyword(self):
        from cognitive_server.interventions.urgency_classifier import UrgencyClassifier
        r = UrgencyClassifier().classify("a@b.com", "URGENT: fix now", [])
        assert r["is_urgent"] is True

    def test_whitelist_sender(self):
        from cognitive_server.interventions.urgency_classifier import UrgencyClassifier
        c = UrgencyClassifier({"interventions": {"whitelist": {"senders": ["boss@co.com"], "domains": []}}})
        assert c.classify("boss@co.com", "hello", [])["is_urgent"] is True

    def test_no_urgency_normal(self):
        from cognitive_server.interventions.urgency_classifier import UrgencyClassifier
        r = UrgencyClassifier().classify("news@x.com", "Weekly tips", [])
        assert r["is_urgent"] is False


class TestDraftGenerator:
    def test_scheduling_high_energy(self):
        from cognitive_server.interventions.draft_generator import generate_scheduling_response
        r = generate_scheduling_response("Sprint", "Fri 2:00 PM", 0.85, "focused")
        assert len(r) > 0 and "Sprint" in r

    def test_scheduling_low_energy(self):
        from cognitive_server.interventions.draft_generator import generate_scheduling_response
        r = generate_scheduling_response("Team sync", "Fri 4:00 PM", 0.2, "heavy")
        assert len(r) > 0

    def test_deferral_gmail(self):
        from cognitive_server.interventions.draft_generator import generate_deferral_response
        r = generate_deferral_response("gmail", "deep_focus_deferral", "heavy", "3:00 PM")
        assert "focus block" in r.lower()

    def test_deferral_slack(self):
        from cognitive_server.interventions.draft_generator import generate_deferral_response
        r = generate_deferral_response("slack", "capacity_decline", "overloaded", "tomorrow", "Monday")
        assert "Monday" in r

    def test_all_templates_present(self):
        from cognitive_server.interventions.draft_generator import TEMPLATES
        for t in ["deep_focus_deferral", "calendar_reschedule", "capacity_decline",
                   "generic_deferral", "urgency_ack"]:
            assert t in TEMPLATES
            assert "gmail" in TEMPLATES[t]
            assert "slack" in TEMPLATES[t]


class TestSignalAPI:
    def test_valid(self, test_app):
        r = test_app.post("/api/v1/signals", json={
            "signals": [{"session_id": "s1", "timestamp": "2026-05-14T10:30:00Z",
                         "kpm": 45.0, "switch_rate": 2.5, "scroll_velocity": 300.0,
                         "scroll_delta": 50.0, "mouse_entropy": 0.4,
                         "idle_ratio": 0.1, "tab_count": 8, "domain_switches": 2}]
        })
        assert r.status_code == 202
        assert r.json()["status"] == "accepted"

    def test_empty_rejected(self, test_app):
        assert test_app.post("/api/v1/signals", json={"signals": []}).status_code == 422

    def test_large_rejected(self, test_app):
        r = test_app.post("/api/v1/signals", json={"signals": [{"session_id": "s", "timestamp": "2026-05-14"}] * 201})
        assert r.status_code == 422


class TestLoadAPI:
    def test_current_no_data(self, test_app):
        r = test_app.get("/api/v1/load/current")
        assert r.status_code == 200
        assert "state" in r.json()

    def test_history_empty(self, test_app):
        assert test_app.get("/api/v1/load/history?hours=2").status_code == 200

    def test_stats_empty(self, test_app):
        assert test_app.get("/api/v1/load/stats?hours=24").status_code == 200


class TestDecisionProxy:
    def test_returns_ranked(self, test_app):
        now = datetime.datetime.now(datetime.timezone.utc)
        s1 = (now + datetime.timedelta(hours=2)).isoformat()
        r = test_app.post("/api/v1/decisions/schedule", json={
            "proposed_slots": [s1], "duration_minutes": 30, "context": "test"
        })
        assert r.status_code == 200
        assert len(r.json()["ranked_options"]) >= 1

    def test_max_three(self, test_app):
        now = datetime.datetime.now(datetime.timezone.utc)
        slots = [(now + datetime.timedelta(hours=i)).isoformat() for i in range(1, 11)]
        r = test_app.post("/api/v1/decisions/schedule", json={
            "proposed_slots": slots, "duration_minutes": 30, "context": "test"
        })
        assert len(r.json()["ranked_options"]) <= 3


class TestInterventions:
    def test_empty_initially(self, test_app):
        r = test_app.get("/api/v1/interventions/active")
        assert r.status_code == 200

    def test_log_empty(self, test_app):
        assert test_app.get("/api/v1/interventions/log?limit=10").status_code == 200

    def test_release_all(self, test_app):
        assert test_app.post("/api/v1/interventions/release-all").status_code == 200


class TestHealth:
    def test_root(self, test_app):
        r = test_app.get("/")
        assert r.status_code == 200 and r.json()["status"] == "ok"

    def test_health(self, test_app):
        r = test_app.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "healthy"