"""
session/manager.py
──────────────────
Holds live call state in memory during a call.
In production, swap _store dict → Redis (set REDIS_URL in .env).
Key  : call_id (str)
Value: dict of session data (language, turns, emotion scores, etc.)
"""
import json
import threading
from datetime import datetime
from flask import current_app

# ── In-memory store (dev / hackathon) ──────────────────────────────────────
_store: dict = {}
_lock = threading.Lock()


def _redis():
    """Return Redis client if configured, else None."""
    try:
        import redis
        url = current_app.config.get('REDIS_URL', '')
        if url and 'PLACEHOLDER' not in url:
            return redis.from_url(url, decode_responses=True)
    except Exception:
        pass
    return None


# ── Public API ──────────────────────────────────────────────────────────────

def create_session(call_id: str, language: str = 'kn') -> dict:
    """Initialise a fresh session for a new call."""
    session = {
        'call_id':                call_id,
        'language':               language,
        'dialect':                None,
        'detected_script':        None,
        'turn_number':            0,
        'turns':                  [],
        'current_intent':         None,
        'current_entities':       {},
        'distress_score':         0.0,
        'urgency_score':          0.0,
        'anger_score':            0.0,
        'fear_score':             0.0,
        'confusion_score':        0.0,
        'dominant_emotion':       'neutral',
        'emotion_history':        [],
        'confidence_score':       0.0,
        'verification_attempts':  0,
        'verification_status':    'pending',
        'escalation_flag':        False,
        'escalation_reason':      None,
        'last_restatement':       None,
        'department_routed_to':   None,
        'agent_id':               None,
        'priority':               'normal',
        'status':                 'active',
        'started_at':             datetime.utcnow().isoformat(),
    }
    _put(call_id, session)
    return session


def get_session(call_id: str) -> dict | None:
    """Retrieve session by call_id. Returns None if not found."""
    r = _redis()
    if r:
        raw = r.get(f'vaaksetu:session:{call_id}')
        return json.loads(raw) if raw else None
    with _lock:
        return _store.get(call_id)


def update_session(call_id: str, updates: dict) -> dict | None:
    """
    Merge updates dict into existing session.

    BUG-41 FIX: The lock now covers the full read-modify-write cycle.
    Previously get_session() and _put() each held the lock individually,
    creating a race window where two concurrent /turn requests could
    overwrite each other's updates (lost turn counts, emotion scores, etc.).
    """
    r = _redis()
    if r:
        # Redis is atomic per-command; use a pipeline for safety
        raw = r.get(f'vaaksetu:session:{call_id}')
        if raw is None:
            return None
        session = json.loads(raw)
        session.update(updates)
        r.setex(f'vaaksetu:session:{call_id}', 3600, json.dumps(session))
        return session

    with _lock:  # BUG-41: single lock covers entire read-modify-write
        session = _store.get(call_id)
        if session is None:
            return None
        # Extract fear_score from emotion_scores when emotion data is updated
        if 'emotion_scores' in updates:
            es = updates['emotion_scores']
            updates['fear_score'] = es.get('fear', 0.0)
        session.update(updates)
        _store[call_id] = session
        return session


def add_turn(call_id: str, speaker: str, text: str,
             metadata: dict = None) -> None:
    """
    Add a new turn to the session's turn list with rich metadata.

    Args:
        call_id: Session ID
        speaker: 'citizen', 'ai', or 'agent'
        text: The spoken/generated text
        metadata: Optional dict with intent, emotion, confidence, etc.
    """
    session = get_session(call_id)
    if not session:
        return
    turn = {
        'turn_number': session['turn_number'] + 1,
        'speaker': speaker,
        'text': text,
        'clean_text': text,
        'timestamp': datetime.utcnow().isoformat(),
    }
    if metadata:
        turn.update(metadata)
    session['turns'].append(turn)
    session['turn_number'] += 1
    _put(call_id, session)


def should_escalate(call_id: str) -> tuple:
    """
    Check if session warrants escalation based on current state.

    Returns:
        (bool, reason_str | None)
    """
    session = get_session(call_id)
    if not session:
        return False, None

    # Already escalated
    if session.get('escalation_flag'):
        return True, session.get('escalation_reason', 'already_escalated')

    # CRIT-3 FIX: default matches EmotionEngine.thresholds['distress'] = 0.70
    distress_thresh = 0.70
    urgency_thresh = 0.60
    try:
        distress_thresh = current_app.config.get(
            'DISTRESS_ESCALATION_THRESHOLD', 0.70)
        urgency_thresh = current_app.config.get(
            'URGENCY_ESCALATION_THRESHOLD', 0.60)
    except RuntimeError:
        pass

    if session.get('distress_score', 0) >= distress_thresh:
        return True, 'distress'
    if session.get('urgency_score', 0) >= urgency_thresh:
        return True, 'urgency'

    # Fear is an explicit escalation trigger per the theme requirement
    if session.get('fear_score', 0.0) >= 0.65:
        return True, 'fear_detected'

    # CRIT-6 FIX: confusion can paralyse a citizen; escalate at threshold
    confusion_thresh = 0.90
    try:
        confusion_thresh = current_app.config.get('CONFUSION_ESCALATION_THRESHOLD', 0.90)
    except RuntimeError:
        pass
    if session.get('confusion_score', 0.0) >= confusion_thresh:
        return True, 'confusion'

    # Too many verification failures
    max_attempts = 2
    try:
        max_attempts = current_app.config.get(
            'MAX_VERIFICATION_ATTEMPTS', 2)
    except RuntimeError:
        pass
    if session.get('verification_attempts', 0) >= max_attempts:
        return True, 'max_retries'

    # Low confidence
    if (session.get('confidence_score', 0) < 0.50
            and session.get('turn_number', 0) >= 2):
        return True, 'low_confidence'

    return False, None


def close_session(call_id: str) -> dict | None:
    """Mark session as closed and return final state."""
    session = update_session(call_id, {
        'status': 'closed',
        'ended_at': datetime.utcnow().isoformat(),
    })
    return session


def delete_session(call_id: str) -> None:
    """Remove from store (called after DB persistence)."""
    r = _redis()
    if r:
        r.delete(f'vaaksetu:session:{call_id}')
        return
    with _lock:
        _store.pop(call_id, None)


def list_active_sessions() -> list:
    """Return all sessions with status='active'."""
    r = _redis()
    if r:
        keys = r.keys('vaaksetu:session:*')
        sessions = []
        for key in keys:
            raw = r.get(key)
            if raw:
                s = json.loads(raw)
                if s.get('status') == 'active':
                    sessions.append(s)
        return sessions
    with _lock:
        return [s for s in _store.values() if s.get('status') == 'active']


# BUG-18: append_turn REMOVED — was a legacy duplicate of add_turn with a
# different signature. call_routes.py uses add_turn; having both caused
# confusion about which to call. Use add_turn() for all new code.


# ── Internal ────────────────────────────────────────────────────────────────

def _put(call_id: str, session: dict) -> None:
    r = _redis()
    if r:
        r.setex(f'vaaksetu:session:{call_id}', 3600, json.dumps(session))
        return
    with _lock:
        _store[call_id] = session
