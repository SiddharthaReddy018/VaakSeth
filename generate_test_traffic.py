import requests
import json
import time
import os
from dotenv import load_dotenv

load_dotenv()
BASE_URL = "http://127.0.0.1:5000/api"
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

import uuid

# Use a session to persist the login cookie
s = requests.Session()

test_agent_user = f"test_bot_{uuid.uuid4().hex[:8]}"

print(f"Authenticating agent {test_agent_user}...")
reg_resp = s.post(f"{BASE_URL}/auth/register", json={
    "username": test_agent_user,
    "password": "testpassword",
    "admin_secret": ADMIN_SECRET
})
print(f"Register status: {reg_resp.status_code} - {reg_resp.text}")

login_resp = s.post(f"{BASE_URL}/auth/login", json={
    "username": test_agent_user,
    "password": "testpassword"
})
if login_resp.status_code == 200:
    print("Agent authenticated successfully.\n")
else:
    print(f"Failed to authenticate agent: {login_resp.status_code} - {login_resp.text}\n")

test_queries = [
    {
        "name": "Medical Emergency — English",
        "language_hint": "en",
        "text": "My child is not breathing please send ambulance immediately",
        "expected_intent": "emergency",
        "expected_routing": "DMER",
    },
    {
        "name": "Fear scenario — Kannada Manglish",
        "language_hint": "kn",
        "text": "yaro nanna mane ge bandu bedreesthidhare nanu bahala hedritidini police bega banni",
        "expected_intent": "emergency",
        "expected_emotion": "fear",
    },
    {
        "name": "Power Outage — Manglish",
        "language_hint": "kn",
        "text": "namma maneyalli current illa 3 dina aytu BESCOM ge call maadle yenu answer illa",
        "expected_intent": "report_issue",
        "expected_routing": "BESCOM",
    },
    {
        "name": "Water Supply — Kannada script",
        "language_hint": "kn",
        "text": "\u0ca8\u0cae\u0ccd\u0cae \u0c8f\u0cb0\u0cbf\u0caf\u0cbe\u0ca6\u0cb2\u0ccd\u0cb2\u0cbf \u0ce9 \u0ca6\u0cbf\u0ca8\u0ca6\u0cbf\u0c82\u0ca6 \u0ca8\u0cc0\u0cb0\u0cc1 \u0cac\u0cb0\u0ccd\u0ca4\u0cbe \u0c87\u0cb2\u0ccd\u0cb2",
        "expected_intent": "report_issue",
        "expected_routing": "BWSSB",
    },
    {
        "name": "Ration Card — Hindi",
        "language_hint": "hi",
        "text": "Mera ration card nahi ban raha hai kaise apply karu",
        "expected_intent": "seek_information",
        "expected_routing": "FOOD_CIVIL",
    },
    {
        "name": "Angry Escalation — Kannada script",
        "language_hint": "kn",
        "text": "\u0ca8\u0cbe\u0cb2\u0ccd\u0c95\u0cc1 \u0cac\u0cbe\u0cb0\u0cbf \u0c95\u0c82\u0caa\u0ccd\u0cb2\u0cc7\u0c82\u0c9f\u0ccd \u0c95\u0cca\u0c9f\u0ccd\u0c9f\u0cb0\u0cc2 \u0c95\u0cb8 \u0ca4\u0cc6\u0c97\u0cc6\u0ca6\u0cbf\u0cb2\u0ccd\u0cb2 \u0c87\u0ca6\u0cc1 \u0c8e\u0c82\u0ca5\u0cbe \u0c95\u0cc6\u0cb2\u0cb8 \u0caf\u0cbe\u0cb0\u0cc2 \u0c95\u0cc7\u0cb3\u0ccd\u0ca4\u0cbf\u0cb2\u0ccd\u0cb2",
        "expected_intent": "escalate_complaint",
        "expected_emotion": "anger",
    },
    {
        "name": "Angry Escalation — Manglish",
        "language_hint": "kn",
        "text": "4 sari complaint kottidini kasa tege illaaa yaru kelalla",
        "expected_intent": "escalate_complaint",
    },
    {
        "name": "Vague Unclear",
        "language_hint": "en",
        "text": "I don't know what to do anymore",
        "expected_intent": "unclear",
    },
]

print("Starting exhaustive API backend testing...\n")

results_report = []

for idx, tq in enumerate(test_queries):
    print(f"--- TEST {idx+1}: {tq['name']} ---")

    # 1. Start Call
    try:
        start_resp = s.post(f"{BASE_URL}/call/start", json={"language_hint": tq["language_hint"]})
        if start_resp.status_code not in [200, 201]:
            print(f"FAILED to start call: {start_resp.status_code} - {start_resp.text}")
            continue
    except Exception as e:
        print(f"FAILED exception: {e}")
        continue
    call_id = start_resp.json()["call_id"]
    print(f"Started Call: {call_id}")

    # 2. Process Turn
    turn_resp = s.post(f"{BASE_URL}/call/turn", json={
        "call_id": call_id,
        "text": tq["text"],
        "language_hint": tq["language_hint"]
    })

    if turn_resp.status_code == 200:
        data = turn_resp.json()
        actual_intent   = data.get('intent')
        actual_routing  = data.get('routing_suggestion')
        actual_emotion  = data.get('emotion_scores', {}).get('dominant_emotion')
        actual_fear     = data.get('fear_score', 0)

        expected_intent  = tq.get('expected_intent')
        expected_routing = tq.get('expected_routing')
        expected_emotion = tq.get('expected_emotion')

        fails = []
        if expected_intent and actual_intent != expected_intent:
            fails.append(f'intent got={actual_intent} want={expected_intent}')
        if expected_routing and actual_routing != expected_routing:
            fails.append(f'routing got={actual_routing} want={expected_routing}')
        if expected_emotion and actual_emotion != expected_emotion:
            fails.append(f'emotion got={actual_emotion} want={expected_emotion}')

        status = 'PASS' if not fails else 'FAIL'

        print(f"Detected Intent: {actual_intent} (Expected: {expected_intent})")
        print(f"Confidence: {data.get('confidence')}")
        print(f"Routing Suggestion: {actual_routing}")
        print(f"Emotion: {actual_emotion}")
        print(f"Fear Score: {actual_fear}")
        print(f"Status: {status}")
        if fails:
            for fr in fails:
                print(f"  FAIL: {fr}")

        results_report.append({
            'test_name': tq['name'],
            'call_id':   call_id,
            'status':    status,
            'fails':     fails,
            'intent':    actual_intent,
            'routing':   actual_routing,
            'emotion':   actual_emotion,
            'fear_score': actual_fear,
            'confidence': data.get('confidence'),
        })
    else:
        print(f"TURN ERROR: {turn_resp.status_code} - {turn_resp.text}")
        results_report.append({"test_name": tq["name"], "status": "FAIL", "fails": ["HTTP error"]})

    # 3. Close the test session
    try:
        end_resp = s.post(f"{BASE_URL}/call/end", json={"call_id": call_id})
        if end_resp.status_code == 200:
            print(f"Session {call_id} closed cleanly.")
        else:
            print(f"Warning: could not close session {call_id}: {end_resp.status_code}")
    except Exception as e:
        print(f"Warning: error closing session: {e}")

    print("\n")
    time.sleep(1)  # Breathe between API calls

print("Testing Complete!\n")

# Summary
passed = sum(1 for r in results_report if r['status'] == 'PASS')
total = len(results_report)
print(f"Results: {passed}/{total} PASS\n")

# Write report to a markdown file
with open("data/test_report.md", "w", encoding="utf-8") as f:
    f.write("# Backend API Exhaustive Test Report\n\n")
    f.write(f"**Results: {passed}/{total} PASS**\n\n")
    for r in results_report:
        f.write(f"**Test**: {r['test_name']}\n")
        f.write(f"- Status: {r['status']}\n")
        if r.get('fails'):
            for f_reason in r['fails']:
                f.write(f"- FAIL: {f_reason}\n")
        f.write(f"- Emotion: {r.get('emotion')} | Fear score: {r.get('fear_score')}\n")
        f.write(f"- Confidence: {r.get('confidence')}\n")
        f.write(f"- Call ID: {r.get('call_id')}\n")
        f.write(f"- Intent: {r.get('intent')}\n")
        f.write(f"- Routing: {r.get('routing')}\n")
        f.write("\n")
