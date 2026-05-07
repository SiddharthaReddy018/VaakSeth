import json
import random
import uuid
import os
import sys
from datetime import datetime, timedelta
from app import create_app
from extensions import db
from models import CallSession, CallTurn

app = create_app()

INTENTS = ['report_issue', 'seek_information', 'escalate_complaint', 'emergency']
DEPARTMENTS = ['BWSSB', 'BESCOM', 'BBMP', 'Police', 'DMER', 'FOOD_CIVIL', 'REVENUE_DEPT', 'SOCIAL_WELFARE']
LANGUAGES = ['kn', 'hi', 'en', 'te', 'ta']

# Seed data templates
TEMPLATES = {
    'report_issue': [
        ("No water in our area for {duration}", "ನಮ್ಮ ಏರಿಯಾದಲ್ಲಿ {duration}ಯಿಂದ ನೀರಿಲ್ಲ", "BWSSB", "distress"),
        ("Power cut since {duration}", "ನಮ್ಮ ಮನೆಯಲ್ಲಿ {duration}ಯಿಂದ ಕರೆಂಟ್ ಇಲ್ಲ", "BESCOM", "urgency"),
        ("Garbage not collected for {duration}", "{duration}ಯಿಂದ ಕಸ ತೆಗೆದಿಲ್ಲ", "BBMP", "anger"),
        ("Pothole on the main road", "ರಸ್ತೆಯಲ್ಲಿ ದೊಡ್ಡ ಗುಂಡಿ ಇದೆ", "BBMP", "anger"),
        ("Streetlight is broken", "ಸ್ಟ್ರೀಟ್ ಲೈಟ್ ಹಾಳಾಗಿದೆ", "BBMP", "neutral"),
        ("Sewage pipe is leaking", "ಚರಂಡಿ ನೀರು ರಸ್ತೆಗೆ ಬರ್ತಿದೆ", "BWSSB", "distress")
    ],
    'emergency': [
        ("Child needs an ambulance immediately", "ಮಗುವಿಗೆ ಆ್ಯಂಬುಲೆನ್ಸ್ ಬೇಕು ಬೇಗ ಬನ್ನಿ", "DMER", "distress"),
        ("Fire in the neighborhood", "ಪಕ್ಕದ ಮನೆಯಲ್ಲಿ ಬೆಂಕಿ ಬಿದ್ದಿದೆ", "DMER", "distress"),
        ("Someone is breaking into the house", "ಮನೆಗೆ ಕಳ್ಳರು ನುಗ್ಗಿದ್ದಾರೆ", "Police", "distress"),
        ("Severe accident on the highway", "ಹೈವೇನಲ್ಲಿ ದೊಡ್ಡ ಆಕ್ಸಿಡೆಂಟ್ ಆಗಿದೆ", "Police", "distress")
    ],
    'seek_information': [
        ("How to apply for a ration card?", "ರೇಷನ್ ಕಾರ್ಡ್ಗೆ ಹೇಗೆ ಅಪ್ಲೈ ಮಾಡಬೇಕು?", "FOOD_CIVIL", "neutral"),
        ("What documents for birth certificate?", "ಜನನ ಪ್ರಮಾಣ ಪತ್ರಕ್ಕೆ ಯಾವ ದಾಖಲೆ ಬೇಕು?", "BBMP", "neutral"),
        ("How to check property tax?", "ಆಸ್ತಿ ತೆರಿಗೆ ಹೇಗೆ ಚೆಕ್ ಮಾಡೋದು?", "BBMP", "confusion"),
        ("Details about farmer pension", "ರೈತರ ಪಿಂಚಣಿ ಬಗ್ಗೆ ಮಾಹಿತಿ ಬೇಕು", "SOCIAL_WELFARE", "neutral")
    ],
    'escalate_complaint': [
        ("Complained about water 3 times, no action", "ಮೂರು ಸಲ ಕಂಪ್ಲೇಂಟ್ ಕೊಟ್ಟರೂ ನೀರು ಬಂದಿಲ್ಲ", "BWSSB", "anger"),
        ("Pension not received for 6 months", "ಆರು ತಿಂಗಳಿಂದ ಪಿಂಚಣಿ ಬಂದಿಲ್ಲ", "SOCIAL_WELFARE", "distress"),
        ("Ration shop is overcharging", "ರೇಷನ್ ಅಂಗಡಿಯಲ್ಲಿ ಹೆಚ್ಚು ಹಣ ಕೇಳ್ತಿದ್ದಾರೆ", "FOOD_CIVIL", "anger"),
        ("Police not filing FIR", "ಪೊಲೀಸರು FIR ತಗೊಳ್ತಾ ಇಲ್ಲ", "Police", "anger")
    ]
}

DURATIONS = ["2 days", "3 hours", "1 week", "10 days", "a month"]

def generate_complaints(count=200):
    complaints = []
    for i in range(count):
        intent = random.choices(INTENTS, weights=[0.5, 0.1, 0.3, 0.1])[0]
        template = random.choice(TEMPLATES[intent])
        
        duration = random.choice(DURATIONS)
        text_en = template[0].format(duration=duration)
        text_orig = template[1].format(duration=duration.replace("days", "ದಿನ").replace("hours", "ಗಂಟೆ").replace("week", "ವಾರ"))
        dept = template[2]
        emotion = template[3]
        
        language = random.choices(LANGUAGES, weights=[0.6, 0.2, 0.1, 0.05, 0.05])[0]
        
        score_map = {
            "distress": {"distress": random.uniform(0.6, 0.95), "urgency": random.uniform(0.3, 0.6), "anger": 0.1},
            "anger": {"distress": 0.2, "urgency": 0.4, "anger": random.uniform(0.6, 0.9)},
            "urgency": {"distress": 0.3, "urgency": random.uniform(0.6, 0.9), "anger": 0.2},
            "confusion": {"distress": 0.1, "urgency": 0.1, "anger": 0.1, "confusion": random.uniform(0.5, 0.8)},
            "neutral": {"distress": 0.1, "urgency": 0.1, "anger": 0.1, "confusion": 0.1}
        }
        
        scores = score_map.get(emotion, score_map["neutral"])
        
        complaint = {
            "id": f"syn_{str(i+1).zfill(3)}",
            "language": language,
            "text_original": text_orig if language == 'kn' else text_en, # Rough simulation
            "text_english": text_en,
            "intent": intent,
            "department": dept,
            "entities": {"issue_type": text_en.split()[0], "duration": duration if "duration" in template[0] else None},
            "emotion": emotion,
            "distress_score": round(scores.get("distress", 0.0), 2),
            "urgency_score": round(scores.get("urgency", 0.0), 2),
            "anger_score": round(scores.get("anger", 0.0), 2),
            "dialect_note": "synthetic"
        }
        complaints.append(complaint)
    return complaints

def seed_db(complaints):
    # BUG-43 FIX: Guard against running in production — this function
    # permanently deletes ALL citizen call records with no warning.
    if os.getenv('FLASK_ENV') == 'production':
        raise RuntimeError(
            'seed_db must not run in production! '
            'Set FLASK_ENV to development or unset it.'
        )

    if os.getenv('SKIP_SEED_CONFIRM') != '1':
        print('\n' + '='*60)
        print('WARNING: This will DELETE all CallTurn and CallSession data!')
        print('='*60)
        confirm = input('Type "yes" to continue, anything else to abort: ')
        if confirm.strip().lower() != 'yes':
            print('Aborted.')
            sys.exit(0)

    with app.app_context():
        # Clear existing
        db.session.query(CallTurn).delete()
        db.session.query(CallSession).delete()
        
        now = datetime.utcnow()
        for i, c in enumerate(complaints):
            call_id = f"CALL-{uuid.uuid4().hex[:8].upper()}"
            
            # Scatter timestamps over the last 7 days
            past_time = now - timedelta(days=random.uniform(0, 7))
            
            sess = CallSession(
                call_id=call_id,
                language=c['language'],
                status=random.choice(['active', 'closed', 'escalated']),
                distress_score=c['distress_score'],
                urgency_score=c['urgency_score'],
                anger_score=c['anger_score'],
                dominant_emotion=c['emotion'],
                verified_intent=c['intent'],
                department_routed_to=c['department'],
                priority='critical' if c['emotion'] == 'distress' or c['intent'] == 'emergency' else 'normal',
                started_at=past_time,
                ended_at=past_time + timedelta(minutes=random.uniform(1, 10))
            )
            db.session.add(sess)
            db.session.flush() # Get ID
            
            turn = CallTurn(
                session_id=sess.id,
                turn_number=1,
                speaker='citizen',
                raw_transcript=c['text_original'],
                clean_text=c['text_original'],
                language=c['language'],
                intent=c['intent'],
                entities=json.dumps(c['entities']),
                emotion=c['emotion'],
                confidence=random.uniform(0.6, 0.95),
                ai_restatement=c['text_english'],
                timestamp=past_time
            )
            db.session.add(turn)
            
        db.session.commit()
        print(f"Successfully seeded {len(complaints)} records into SQLite!")

if __name__ == '__main__':
    comps = generate_complaints(200)
    out_path = os.path.join(os.path.dirname(__file__), 'data', 'synthetic_complaints.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(comps, f, ensure_ascii=False, indent=2)
    print("Generated data/synthetic_complaints.json")
    seed_db(comps)
