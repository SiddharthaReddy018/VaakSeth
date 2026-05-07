"""
routes/agent_routes.py
───────────────────────────────────────────────────────────────────────────────
HTTP endpoints for the agent dashboard.

GET  /api/agent/sessions              — list active sessions
GET  /api/agent/session/<call_id>     — session detail
GET  /api/agent/session/<call_id>/history — full turn history
POST /api/agent/session/<call_id>/correct — submit agent correction
POST /api/agent/session/<call_id>/close   — agent closes a call

Bugs fixed in this file:
  BUG-12: Double-close returns 409 instead of 200
  BUG-22: Correction on non-existent session returns 404 instead of 200
  BUG-29: ?status=closed no longer leaks active in-memory sessions
  BUG-34: All routes protected by @agent_required decorator
"""

import json
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, session
from extensions import db
from models import CallSession, CallTurn, Feedback
from session.manager import (
    get_session, update_session, close_session,
    list_active_sessions,
)
from socket_events import push_session_update, push_session_closed

logger = logging.getLogger(__name__)

agent_bp = Blueprint('agent', __name__)


# ── BUG-34: Authentication decorator ──────────────────────────────────────────

def agent_required(f):
    """
    Decorator that requires a valid agent session cookie.

    BUG-34 FIX: All agent routes were fully unauthenticated — any HTTP request
    could list all citizen calls, view transcripts, poison the RLHF feedback,
    or close live calls. This decorator gates all routes behind login.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('agent_id'):
            return jsonify({'error': 'Authentication required — please login at /api/auth/login'}), 401
        return f(*args, **kwargs)
    return decorated


# ── Routes ────────────────────────────────────────────────────────────────────

@agent_bp.route('/sessions', methods=['GET'])
@agent_required  # BUG-34
def get_sessions():
    """List call sessions for the dashboard."""
    status_filter = request.args.get('status', 'active')

    # BUG-29 FIX: Only fetch in-memory active sessions when actually filtering
    # for active. Previously list_active_sessions() ran unconditionally, so
    # ?status=closed returned active sessions PLUS closed DB sessions.
    if status_filter == 'active':
        sessions = list_active_sessions()
        # Merge DB active sessions so seeded hackathon data shows up
        db_sessions = CallSession.query.filter_by(status='active').order_by(
            CallSession.started_at.desc()).limit(50).all()
        in_memory_ids = {s['call_id'] for s in sessions}
        for dbs in db_sessions:
            if dbs.call_id not in in_memory_ids:
                db_turns = CallTurn.query.filter_by(
                    session_id=dbs.id).order_by(CallTurn.turn_number).all()
                turns = _serialize_turns(db_turns)
                sessions.append(_serialize_db_session(dbs, turns))
    else:
        # BUG-29: Start with EMPTY list for non-active filters
        sessions = []
        db_sessions = CallSession.query.filter_by(
            status=status_filter).order_by(
            CallSession.started_at.desc()).limit(50).all()
        for dbs in db_sessions:
            db_turns = CallTurn.query.filter_by(
                session_id=dbs.id).order_by(CallTurn.turn_number).all()
            turns = _serialize_turns(db_turns)
            sessions.append(_serialize_db_session(dbs, turns))

    return jsonify(sessions)


@agent_bp.route('/session/<call_id>', methods=['GET'])
@agent_required  # BUG-34
def get_session_detail(call_id):
    """Get full session detail for a specific call."""
    # Try in-memory first
    session_data = get_session(call_id)
    if session_data:
        return jsonify(session_data)

    # Fallback to DB
    db_session = CallSession.query.filter_by(call_id=call_id).first()
    if not db_session:
        return jsonify({'error': 'Session not found'}), 404

    turns = CallTurn.query.filter_by(
        session_id=db_session.id).order_by(
        CallTurn.turn_number).all()

    return jsonify({
        'call_id': db_session.call_id,
        'language': db_session.language,
        'status': db_session.status,
        'distress_score': db_session.distress_score,
        'urgency_score': db_session.urgency_score,
        'anger_score': db_session.anger_score,
        'dominant_emotion': db_session.dominant_emotion,
        'current_intent': db_session.verified_intent,
        'current_entities': json.loads(db_session.verified_entities or '{}'),
        'confidence_score': db_session.confidence_score,
        'verification_status': db_session.verification_status,
        'department_routed_to': db_session.department_routed_to,
        'priority': db_session.priority,
        'turns': [_serialize_turn(t) for t in turns],
    })


@agent_bp.route('/session/<call_id>/history', methods=['GET'])
@agent_required  # BUG-34
def get_session_history(call_id):
    """Full turn history with emotion scores per turn."""
    session_data = get_session(call_id)
    if session_data:
        turns = session_data.get('turns', [])
        emotion_history = session_data.get('emotion_history', [])
        return jsonify({
            'call_id': call_id,
            'turns': turns,
            'emotion_history': emotion_history,
        })

    # Fallback to DB
    db_session = CallSession.query.filter_by(call_id=call_id).first()
    if not db_session:
        return jsonify({'error': 'Session not found'}), 404

    turns = CallTurn.query.filter_by(
        session_id=db_session.id).order_by(
        CallTurn.turn_number).all()

    return jsonify({
        'call_id': call_id,
        'turns': [{
            'turn_number': t.turn_number,
            'speaker': t.speaker,
            'text': t.clean_text or t.raw_transcript,
            'emotion': t.emotion,
            'confidence': t.confidence,
            'intent': t.intent,
            'ai_restatement': t.ai_restatement,
            'timestamp': t.timestamp.isoformat() if t.timestamp else None,
        } for t in turns],
    })


@agent_bp.route('/session/<call_id>/correct', methods=['POST'])
@agent_required  # BUG-34
def submit_correction(call_id):
    """Agent submits a correction (wrong intent, entity, etc.)."""
    body = request.get_json(force=True) or {}
    feedback_type = body.get('feedback_type')
    corrected_value = body.get('corrected_value', '')
    agent_id = body.get('agent_id')

    if not feedback_type:
        return jsonify({'error': 'feedback_type required'}), 400

    # BUG-22: Validate that session exists before saving — prevents orphaned
    # feedback records with session_id=0 that pollute the RLHF training signal.
    session_data = get_session(call_id)
    db_session = CallSession.query.filter_by(call_id=call_id).first()
    if not session_data and not db_session:
        return jsonify({'error': 'Session not found'}), 404

    # Get original value from session
    original_value = ''
    if session_data:
        if feedback_type == 'intent_wrong':
            original_value = session_data.get('current_intent', '')
        elif feedback_type == 'routing_wrong':
            original_value = session_data.get('department_routed_to', '')
        elif feedback_type == 'emotion_wrong':
            original_value = session_data.get('dominant_emotion', '')

    # Save to DB
    try:
        # GAP-2 FIX: populate turn_id so corrections are tied to a specific turn.
        # Previously turn_id always defaulted to null, losing the fine-grained
        # mapping needed for targeted model improvement.
        turn_id = None
        if db_session:
            latest_turn = (CallTurn.query
                           .filter_by(session_id=db_session.id)
                           .order_by(CallTurn.turn_number.desc())
                           .first())
            if latest_turn:
                turn_id = latest_turn.id
        feedback = Feedback(
            session_id=db_session.id if db_session else 0,
            turn_id=turn_id,
            source='agent',
            feedback_type=feedback_type,
            original_value=original_value,
            corrected_value=corrected_value,
        )
        db.session.add(feedback)
        db.session.commit()
    except Exception as e:
        logger.error(f'Feedback save error: {e}')
        db.session.rollback()

    # Also save via feedback_store
    try:
        from feedback.feedback_store import save_correction
        save_correction(call_id, feedback_type, original_value,
                        corrected_value, agent_id)
    except Exception as e:
        logger.warning(f'feedback_store error: {e}')

    return jsonify({'status': 'saved', 'feedback_type': feedback_type})


@agent_bp.route('/session/<call_id>/close', methods=['POST'])
@agent_required  # BUG-34
def agent_close_call(call_id):
    """Agent manually closes a call."""
    # BUG-12: Check DB status to prevent double-close returning 200
    db_session = CallSession.query.filter_by(call_id=call_id).first()
    if db_session and db_session.status == 'closed':
        return jsonify({'error': 'Session already closed'}), 409

    session_data = get_session(call_id)
    if not session_data:
        # Check DB for a non-closed session
        if not db_session:
            return jsonify({'error': 'Session not found'}), 404

    if session_data:
        close_session(call_id)

    # Update DB
    try:
        from datetime import datetime
        if db_session:
            db_session.status = 'closed'
            db_session.ended_at = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        logger.error(f'DB close error: {e}')
        db.session.rollback()

    push_session_closed(call_id)
    return jsonify({'status': 'closed', 'call_id': call_id})




@agent_bp.route('/feedback/summary', methods=['GET'])
@agent_required
def feedback_summary():
    """
    GAP-1 FIX: Expose RLHF learning-loop metrics that were computed by
    feedback_store.get_corrections_summary() but never surfaced via HTTP.
    Supervisors and evaluators can now see total corrections, breakdown by
    type, and the most-common mis-predicted intents/emotions at a glance.

    Optional query param: ?since=2024-01-01T00:00:00
    """
    since = request.args.get('since')
    try:
        from feedback.feedback_store import get_corrections_summary
        summary = get_corrections_summary(since_date=since)
    except Exception as e:
        logger.error(f'feedback_summary error: {e}')
        return jsonify({'error': 'Failed to generate summary', 'detail': str(e)}), 500

    # Fold in DB row counts for completeness
    try:
        summary['db_total']                  = Feedback.query.count()
        summary['db_citizen_confirmations']  = Feedback.query.filter_by(source='citizen').count()
        summary['db_agent_corrections']      = Feedback.query.filter_by(source='agent').count()
    except Exception:
        pass  # DB stats are informational — don't fail the request

    return jsonify(summary)

# ── Serialization helpers ──────────────────────────────────────────────────────

def _serialize_turn(t) -> dict:
    return {
        'turn_number': t.turn_number,
        'speaker': t.speaker,
        'raw_transcript': t.raw_transcript,
        'clean_text': t.clean_text,
        'language': t.language,
        'intent': t.intent,
        'entities': json.loads(t.entities or '{}') if t.entities else {},
        'emotion': t.emotion,
        'confidence': t.confidence,
        'ai_restatement': t.ai_restatement,
        'timestamp': t.timestamp.isoformat() if t.timestamp else None,
    }


def _serialize_turns(db_turns) -> list:
    return [_serialize_turn(t) for t in db_turns]


def _serialize_db_session(dbs, turns) -> dict:
    return {
        'call_id': dbs.call_id,
        'language': dbs.language,
        'status': dbs.status,
        'distress_score': dbs.distress_score,
        'urgency_score': dbs.urgency_score,
        'anger_score': dbs.anger_score,
        'dominant_emotion': dbs.dominant_emotion,
        'current_intent': dbs.verified_intent,
        'confidence_score': dbs.confidence_score,
        'priority': dbs.priority,
        'department_routed_to': dbs.department_routed_to,
        'verification_status': dbs.verification_status,
        'turns': turns,
    }
