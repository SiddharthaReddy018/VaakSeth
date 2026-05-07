"""
routes/auth_routes.py
───────────────────────────────────────────────────────────────────────────────
Agent authentication for the VaakSetu dashboard.
Simple username/password login using Flask sessions.

POST /api/auth/login    — { username, password } → session cookie
POST /api/auth/logout   — clear session
GET  /api/auth/me       — current agent info
POST /api/auth/register — create agent account (admin only, for setup)
"""

from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from models import AgentUser
from extensions import db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    body     = request.get_json(force=True)
    username = body.get('username', '').strip()
    password = body.get('password', '').strip()

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    user = AgentUser.query.filter_by(username=username, is_active=True).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    # Store agent in Flask session
    session['agent_id']   = user.id
    session['agent_name'] = user.name or user.username
    session['languages']  = user.languages

    return jsonify({
        'status':    'logged_in',
        'agent_id':  user.id,
        'name':      user.name or user.username,
        'languages': user.languages,
    })


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'logged_out'})


@auth_bp.route('/me', methods=['GET'])
def me():
    agent_id = session.get('agent_id')
    if not agent_id:
        return jsonify({'error': 'Not authenticated'}), 401
    user = AgentUser.query.get(agent_id)
    if not user:
        return jsonify({'error': 'Agent not found'}), 404
    return jsonify({'agent_id': user.id, 'name': user.name,
                    'languages': user.languages})


@auth_bp.route('/register', methods=['POST'])
def register():
    """
    Create a new agent account.

    BUG-30 FIX: Requires ADMIN_SECRET to prevent public account creation.
    Any internet user was previously able to create agent accounts and
    gain full access to all citizen call data.
    Set ADMIN_SECRET in .env to enable registration.
    """
    body     = request.get_json(force=True)
    username = body.get('username', '').strip()
    password = body.get('password', '').strip()
    name     = body.get('name', username)
    languages = body.get('languages', 'kn,hi,en')

    # BUG-30: Require admin secret
    from flask import current_app
    admin_secret = current_app.config.get('ADMIN_SECRET', '').strip()
    if not admin_secret or body.get('admin_secret', '').strip() != admin_secret:
        return jsonify({'error': 'Forbidden — valid admin_secret required'}), 403

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    if AgentUser.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 409

    user = AgentUser(
        username      = username,
        password_hash = generate_password_hash(password),
        name          = name,
        languages     = languages,
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'status': 'created', 'agent_id': user.id}), 201
