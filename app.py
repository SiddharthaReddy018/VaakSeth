"""
app.py — VaakSetu Flask Application Factory
───────────────────────────────────────────────────────────────────────────────
Creates Flask app, registers blueprints, initializes SocketIO + DB.

Blueprints:
    /api/call/*   — call_routes  (citizen audio processing)
    /api/agent/*  — agent_routes (dashboard API)
    /api/auth/*   — auth_routes  (agent login/register)
"""

from flask import Flask, send_from_directory
from flask_cors import CORS
from extensions import db, socketio
from config import Config
import os


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app, resources={r"/*": {"origins": "*"}})
    db.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*", async_mode='threading')

    # ── Register Blueprints ──────────────────────────────────────
    from routes.call_routes  import call_bp
    from routes.agent_routes import agent_bp
    from routes.auth_routes  import auth_bp

    app.register_blueprint(call_bp,  url_prefix='/api/call')
    app.register_blueprint(agent_bp, url_prefix='/api/agent')
    app.register_blueprint(auth_bp,  url_prefix='/api/auth')

    # ── WebSocket events (live call updates) ─────────────────────
    from socket_events import register_socket_events
    register_socket_events(socketio)

    # ── Serve frontend (hackathon demo) ──────────────────────────
    frontend_dir = os.path.join(os.path.dirname(__file__), 'frontend')

    @app.route('/')
    def serve_frontend():
        return send_from_directory(frontend_dir, 'index.html')

    @app.route('/frontend/<path:filename>')
    def serve_frontend_file(filename):
        return send_from_directory(frontend_dir, filename)

    # ── Health check ─────────────────────────────────────────────
    @app.route('/api/health')
    def health():
        return {'status': 'ok', 'service': 'VaakSetu'}

    with app.app_context():
        db.create_all()

    return app


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True)
