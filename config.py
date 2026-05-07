import os
import sys
from dotenv import load_dotenv

load_dotenv()

class Config:
    # ─────────────────────────────────────────────
    # Flask Core
    # ─────────────────────────────────────────────
    # BUG-33: Raise at startup if SECRET_KEY not explicitly set — prevents
    # forgeable session cookies via the known repo default.
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        # In development, use a strong random key but warn loudly.
        import secrets
        SECRET_KEY = secrets.token_hex(32)
        print(
            '[WARNING] SECRET_KEY not set in .env — generated a random key. '
            'Sessions will NOT persist across restarts. '
            'Set SECRET_KEY in .env for production.',
            file=sys.stderr
        )

    # BUG-30 / BUG-33 security: admin secret for registration endpoint
    ADMIN_SECRET = os.getenv('ADMIN_SECRET', 'test_admin_secret_123')
    DEBUG = os.getenv('DEBUG', 'True') == 'True'

    # ─────────────────────────────────────────────
    # Database  (SQLite — free, zero setup)
    # ─────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///vaaksetu.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ─────────────────────────────────────────────
    # ASR  — FREE: Web Speech API (browser-native)
    # Replace value below with Sarvam key for prod
    # ─────────────────────────────────────────────
    ASR_BACKEND = os.getenv('ASR_BACKEND', 'webspeech')   # 'webspeech' | 'sarvam' | 'whisper'
    SARVAM_API_KEY   = os.getenv('SARVAM_API_KEY',   'PLACEHOLDER_SARVAM_API_KEY')
    SARVAM_ASR_URL   = 'https://api.sarvam.ai/speech-to-text'
    SARVAM_TTS_URL   = 'https://api.sarvam.ai/text-to-speech'

    # FREE fallback ASR via Groq Whisper (large-v3, free tier)
    GROQ_API_KEY     = os.getenv('GROQ_API_KEY',     '')
    GROQ_WHISPER_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'

    # ─────────────────────────────────────────────
    # LLM  — FREE: Groq (Llama-3 8B, very fast)
    # Alternatives: ModelLake (already in repo), Gemini Flash
    # ─────────────────────────────────────────────
    LLM_BACKEND      = os.getenv('LLM_BACKEND', 'groq')   # 'groq' | 'gemini' | 'openrouter'
    GROQ_LLM_URL     = 'https://api.groq.com/openai/v1/chat/completions'
    GROQ_LLM_MODEL   = 'llama-3.1-8b-instant'                   # free tier

    # ModelLake (from SMILE_AGAIN — already have key)
    MODELLAKE_API_KEY = os.getenv('MODELLAKE_API_KEY', 'PLACEHOLDER_MODELLAKE_API_KEY')

    # Gemini Flash (free tier, generous limits)
    GEMINI_API_KEY   = os.getenv('GEMINI_API_KEY',   '')
    # GEMINI FIX: updated model (1.5-flash deprecated -> 2.0-flash) and
    # endpoint (v1beta -> v1 stable). nlu.py builds the URL dynamically
    # from GEMINI_MODEL, so changing this env var is all that's needed.
    GEMINI_MODEL     = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
    GEMINI_URL       = (
        'https://generativelanguage.googleapis.com/v1/models/'
        'gemini-2.0-flash:generateContent'
    )

    # OpenRouter (free tier — Llama 3.3 70B)
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
    OPENROUTER_URL    = 'https://openrouter.ai/api/v1/chat/completions'
    OPENROUTER_MODEL  = 'meta-llama/llama-3.3-70b-instruct:free'

    # ─────────────────────────────────────────────
    # TTS  — FREE: edge-tts (Microsoft, excellent Indian voices)
    # Replaces gTTS which returns 403 (BUG-03)
    # ─────────────────────────────────────────────
    TTS_BACKEND = os.getenv('TTS_BACKEND', 'edge')        # 'edge' | 'sarvam'

    # ─────────────────────────────────────────────
    # Translation — FREE: deep-translator (GoogleTranslator)
    # Replaces MyMemory which returns 403 for Indian languages (BUG-02)
    # ─────────────────────────────────────────────
    TRANSLATION_BACKEND = os.getenv('TRANSLATION_BACKEND', 'deep_translator')
    # Legacy MyMemory URL kept for reference
    MYMEMORY_URL         = 'https://api.mymemory.translated.net/get'
    BHASHINI_API_KEY     = os.getenv('BHASHINI_API_KEY', 'PLACEHOLDER_BHASHINI_API_KEY')
    BHASHINI_URL         = 'https://dhruva-api.bhashini.gov.in/services/inference/pipeline'

    # ─────────────────────────────────────────────
    # Transliteration — FREE: indic-transliteration (pip)
    # ─────────────────────────────────────────────
    TRANSLITERATION_URL  = os.getenv('TRANSLITERATION_URL', 'https://PLACEHOLDER/transliterate')
    PLATFORM_API_KEY     = os.getenv('PLATFORM_API_KEY', 'PLACEHOLDER_PLATFORM_KEY')

    # ─────────────────────────────────────────────
    # Session Store — FREE: in-memory dict (dev)
    # ─────────────────────────────────────────────
    SESSION_BACKEND = os.getenv('SESSION_BACKEND', 'memory')  # 'memory' | 'redis'
    REDIS_URL        = os.getenv('REDIS_URL', 'PLACEHOLDER_REDIS_URL')

    # ─────────────────────────────────────────────
    # VaakSetu Business Logic
    # ─────────────────────────────────────────────
    MAX_VERIFICATION_ATTEMPTS    = 2
    CONFIDENCE_HIGH              = 0.80
    CONFIDENCE_MEDIUM            = 0.50
    DISTRESS_ESCALATION_THRESHOLD = 0.70  # CRIT-3 FIX: aligned with EmotionEngine.thresholds['distress']
    URGENCY_ESCALATION_THRESHOLD  = 0.60
    SUPPORTED_LANGUAGES          = ['kn', 'hi', 'en', 'te', 'ta', 'ml', 'mr']
    DEFAULT_LANGUAGE             = 'kn'
