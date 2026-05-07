from datetime import datetime
from extensions import db
from flask_login import UserMixin


class CallSession(db.Model):
    """One row per incoming citizen call."""
    __tablename__ = 'call_sessions'

    id                  = db.Column(db.Integer, primary_key=True)
    call_id             = db.Column(db.String(64), unique=True, nullable=False)
    language            = db.Column(db.String(8), default='kn')
    dialect             = db.Column(db.String(32), nullable=True)
    status              = db.Column(db.String(20), default='active')
    # active | verified | escalated | closed

    # Emotion state
    distress_score      = db.Column(db.Float, default=0.0)
    urgency_score       = db.Column(db.Float, default=0.0)
    anger_score         = db.Column(db.Float, default=0.0)
    fear_score          = db.Column(db.Float, default=0.0)
    dominant_emotion    = db.Column(db.String(20), default='neutral')

    # Verification loop
    verification_attempts  = db.Column(db.Integer, default=0)
    verification_status    = db.Column(db.String(20), default='pending')
    # pending | confirmed | rejected | escalated
    confidence_score       = db.Column(db.Float, default=0.0)

    # Resolved summary
    verified_intent        = db.Column(db.String(64), nullable=True)
    verified_entities      = db.Column(db.Text, nullable=True)   # JSON string
    department_routed_to   = db.Column(db.String(64), nullable=True)
    priority               = db.Column(db.String(10), default='normal')  # normal | urgent | critical

    # Timing
    started_at          = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at            = db.Column(db.DateTime, nullable=True)

    # Transcript & Summary (secondary scope)
    raw_transcript      = db.Column(db.Text, nullable=True)
    call_summary        = db.Column(db.Text, nullable=True)

    # Relationships
    turns    = db.relationship('CallTurn', backref='session', lazy=True)
    feedback = db.relationship('Feedback', backref='session', lazy=True)


class CallTurn(db.Model):
    """One row per speech turn within a call."""
    __tablename__ = 'call_turns'

    id              = db.Column(db.Integer, primary_key=True)
    session_id      = db.Column(db.Integer, db.ForeignKey('call_sessions.id'), nullable=False)
    turn_number     = db.Column(db.Integer, nullable=False)
    speaker         = db.Column(db.String(10), nullable=False)   # citizen | ai | agent

    raw_transcript  = db.Column(db.Text, nullable=True)          # raw STT output
    clean_text      = db.Column(db.Text, nullable=True)          # after dialect normalisation
    language        = db.Column(db.String(8), nullable=True)

    intent          = db.Column(db.String(64), nullable=True)
    entities        = db.Column(db.Text, nullable=True)          # JSON
    emotion         = db.Column(db.String(20), nullable=True)
    confidence      = db.Column(db.Float, nullable=True)

    ai_restatement  = db.Column(db.Text, nullable=True)          # what AI said back
    citizen_confirmed = db.Column(db.Boolean, nullable=True)     # True/False/None

    timestamp       = db.Column(db.DateTime, default=datetime.utcnow)


class AgentUser(UserMixin, db.Model):
    """1092 helpline agents — login to the dashboard."""
    __tablename__ = 'agent_users'

    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    name         = db.Column(db.String(128), nullable=True)
    languages    = db.Column(db.String(64), default='kn,hi,en')  # comma-separated
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class Feedback(db.Model):
    """Agent corrections and citizen confirmations used for RLHF-style loop."""
    __tablename__ = 'feedback'

    id              = db.Column(db.Integer, primary_key=True)
    session_id      = db.Column(db.Integer, db.ForeignKey('call_sessions.id'), nullable=False)
    turn_id         = db.Column(db.Integer, db.ForeignKey('call_turns.id'), nullable=True)
    source          = db.Column(db.String(10), nullable=False)  # 'citizen' | 'agent'
    feedback_type   = db.Column(db.String(20), nullable=False)
    # intent_wrong | entity_wrong | emotion_wrong | routing_wrong | confirmed
    original_value  = db.Column(db.Text, nullable=True)
    corrected_value = db.Column(db.Text, nullable=True)
    timestamp       = db.Column(db.DateTime, default=datetime.utcnow)
