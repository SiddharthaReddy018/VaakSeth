"""
socket_events.py
─────────────────────────────────────────────────────────────────────────────
Socket.IO event handlers for real-time agent dashboard updates.

Events emitted TO the dashboard (server → client):
  session_update   — new intent/emotion/verification state for a call
  escalation_alert — a call needs immediate human intervention
  session_closed   — call ended

Events received FROM the dashboard (client → server):
  join_room        — agent subscribes to a specific call_id room
  leave_room       — agent unsubscribes
"""

from extensions import socketio
from session.manager import get_session
from flask_socketio import join_room, leave_room, emit


def register_socket_events(sio):
    """Register all WebSocket event handlers. Called from app.py."""

    @sio.on('join_room')
    def on_join(data):
        """Agent joins a specific call room to receive live updates."""
        call_id = data.get('call_id')
        if call_id:
            join_room(call_id)
            session = get_session(call_id)
            if session:
                emit('session_update', session, room=call_id)

    @sio.on('leave_room')
    def on_leave(data):
        call_id = data.get('call_id')
        if call_id:
            leave_room(call_id)

    @sio.on('connect')
    def on_connect():
        # BUG-11: All agents join the shared 'agents' room so they receive
        # escalation alerts — but only escalation alerts, not call-specific updates.
        join_room('agents')
        emit('connected', {'message': 'VaakSetu agent socket connected'})

    @sio.on('disconnect')
    def on_disconnect():
        pass   # cleanup handled by Flask-SocketIO


# ── Helper functions called by other modules ─────────────────────────────────

def push_session_update(call_id: str, data: dict):
    """
    Push live session state to all agents watching this call_id.
    Call this after any significant state change (intent update,
    emotion spike, verification confirmed, escalation triggered).
    """
    socketio.emit('session_update', {**data, 'call_id': call_id}, room=call_id)


def push_escalation_alert(call_id: str, reason: str, summary: dict):
    """
    Push an escalation alert to the 'agents' room.

    BUG-11 FIX: Added room='agents' so alerts only go to agents who have
    connected to the dashboard, not broadcast to every socket globally.
    In a real helpline with 20+ agents, global broadcast creates noise for
    calls the agent isn't monitoring.
    """
    socketio.emit('escalation_alert', {
        'call_id': call_id,
        'reason':  reason,
        'summary': summary,
    }, room='agents')


def push_session_closed(call_id: str):
    """Notify dashboard that a call has ended."""
    socketio.emit('session_closed', {'call_id': call_id}, room=call_id)
