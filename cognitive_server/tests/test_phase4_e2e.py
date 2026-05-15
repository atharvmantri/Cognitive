"""End-to-end test of Phase 4: Decision Proxy engine."""
import os, sys, tempfile, asyncio, json
sys.path.insert(0, 'C:/projects/Cognitive')

from starlette.testclient import TestClient
from datetime import datetime, timezone, timedelta

# Initialize DB
db_path = os.path.join(tempfile.mkdtemp(), "test_phase4.db")
from cognitive_server.db import sqlite_store
asyncio.run(sqlite_store.initialize(db_path))
os.environ["COGNITIVE_DB_PATH"] = db_path

from cognitive_server.main import app
client = TestClient(app, raise_server_exceptions=True)

print("=" * 60)
print("PHASE 4 DECISION PROXY - END-TO-END VALIDATION")
print("=" * 60)

# Setup: seed some load data
async def setup():
    now = datetime.now(timezone.utc)
    for i in range(100):
        ts = (now - timedelta(minutes=i*10)).isoformat()
        await sqlite_store.insert_load_record(
            cls_score=50 + (i % 30),
            confidence=0.8,
            load_state="focused",
            source="model",
            features='{"kpm":0.5,"switch_rate":0.3,"scroll_entropy":0.4,"mouse_entropy":0.5,"idle_ratio":0.2,"tab_count":0.5,"domain_switches":0.2,"time_of_day":0.0}'
        )
asyncio.run(setup())

# Test 1: Basic scheduling works
future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
r1 = client.post('/api/v1/decisions/schedule', json={
    'proposed_slots': [
        future,
        (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
    ],
    'duration_minutes': 30,
    'context': 'Sprint planning'
})
print('\nTest 1 - Basic scheduling:')
resp = r1.json()
print(f'  Ranked options: {len(resp["ranked_options"])}')
print(f'  Top slot: {resp["ranked_options"][0]["slot"]}')
print(f'  Top score: {resp["ranked_options"][0]["score"]}')
print(f'  Rationale: {resp["ranked_options"][0]["rationale"]}')
print(f'  Suggested response preview: {resp["suggested_response"][:80]}...')
assert r1.status_code == 200
assert len(resp["ranked_options"]) == 3
assert resp["ranked_options"][0]["score"] >= 0
print('  PASSED')

# Test 2: Factor breakdown is present
print('\nTest 2 - Factor breakdown:')
for opt in resp["ranked_options"]:
    f = opt["factors"]
    print(f'  Slot {opt["rank"]}: energy={f["energy"]:.2f}, conflict={f["conflict"]:.2f}, '
          f'deadline={f["deadline"]:.2f}, circadian={f["circadian"]:.2f}, focus={f["focus_preservation"]:.2f}')
assert all(attr in r1.json()["ranked_options"][0]["factors"]
           for attr in ["energy", "conflict", "deadline", "circadian", "focus_preservation"])
print('  PASSED')

# Test 3: Feedback endpoint
r3 = client.post('/api/v1/decisions/feedback',
                 params={
                     "chosen_slot": resp["ranked_options"][0]["slot"],
                     "reason": "user_chose_best_slot"
                 })
print('\nTest 3 - Decision feedback:')
print(f'  Status: {r3.status_code}')
assert r3.status_code == 200
print('  PASSED')

# Test 4: Decision stats
r4 = client.get('/api/v1/decisions/stats')
print('\nTest 4 - Decision stats:')
print(f'  Stats: {r4.json()}')
assert r4.status_code == 200
assert r4.json()["total_decisions"] >= 1
print('  PASSED')

# Cleanup
try:
    os.unlink(db_path)
except:
    pass

print('\n' + '=' * 60)
print('PHASE 4 DECISION PROXY - ALL TESTS PASSED!')
print('=' * 60)