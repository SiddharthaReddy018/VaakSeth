"""
pipeline/asr.py
───────────────────────────────────────────────────────────────────────────────
Speech-to-Text module for VaakSetu.

Primary backend : Groq Whisper (large-v3, free tier — fast, accurate)
Fallback        : Return None → escalate to human agent

Handles raw audio bytes (WAV/WebM from browser mic or uploaded file)
and returns a text transcript with detected language and confidence.
"""

import io
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)


def transcribe(audio_bytes: bytes, language_hint: str = 'kn') -> dict | None:
    """
    Convert raw audio bytes to text transcript.

    Args:
        audio_bytes: Raw WAV/WebM audio data
        language_hint: ISO 639-1 language code hint ('kn', 'hi', 'en')

    Returns:
        {
            'transcript': str,
            'detected_language': str,
            'confidence': float
        }
        or None if all backends fail.
    """
    backend = current_app.config.get('ASR_BACKEND', 'webspeech')

    if backend == 'sarvam':
        result = _transcribe_sarvam(audio_bytes, language_hint)
        if result:
            return result

    # Groq Whisper (primary free backend)
    result = _transcribe_groq(audio_bytes, language_hint)
    if result:
        return result

    logger.error('All ASR backends failed — escalate to human agent')
    return None


def transcribe_and_translate(audio_bytes: bytes, target_lang: str = 'en') -> dict | None:
    """
    Speech-to-text + translation in sequence.
    Calls STT first, then translates if source != target.

    Args:
        audio_bytes: Raw audio data
        target_lang: Target language for translation (default 'en' for NLU)

    Returns:
        {
            'transcript_original': str,
            'transcript_translated': str,
            'detected_language': str
        }
        or None if STT fails.
    """
    # Step 1: Transcribe
    stt_result = transcribe(audio_bytes)
    if not stt_result:
        return None

    original = stt_result['transcript']
    detected = stt_result.get('detected_language', 'kn')

    # Step 2: Translate if needed
    if detected == target_lang:
        return {
            'transcript_original': original,
            'transcript_translated': original,
            'detected_language': detected,
        }

    # Import translation module (avoid circular imports)
    from pipeline.translation import translate_text
    trans_result = translate_text(original, source_lang=detected, target_lang=target_lang)

    return {
        'transcript_original': original,
        'transcript_translated': trans_result.get('translated', original),
        'detected_language': detected,
    }


# ── Backend implementations ───────────────────────────────────────────────────

def _transcribe_groq(audio_bytes: bytes, language_hint: str) -> dict | None:
    """
    Groq Whisper large-v3 — free tier, very fast.
    Endpoint: https://api.groq.com/openai/v1/audio/transcriptions
    """
    api_key = current_app.config.get('GROQ_API_KEY', '')
    if 'PLACEHOLDER' in api_key:
        logger.warning('Groq API key is placeholder — skipping Groq ASR')
        return None

    url = current_app.config.get('GROQ_WHISPER_URL',
                                  'https://api.groq.com/openai/v1/audio/transcriptions')

    # Language hint mapping for Whisper
    whisper_lang = {
        'kn': 'kn', 'hi': 'hi', 'en': 'en',
        'te': 'te', 'ta': 'ta', 'ml': 'ml', 'mr': 'mr',
    }.get(language_hint, language_hint)

    try:
        # Prepare multipart form data
        files = {
            'file': ('audio.wav', io.BytesIO(audio_bytes), 'audio/wav'),
        }
        data = {
            'model': 'whisper-large-v3',
            'language': whisper_lang,
            'response_format': 'verbose_json',
        }
        headers = {
            'Authorization': f'Bearer {api_key}',
        }

        resp = requests.post(url, files=files, data=data, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        # BUG-28: Clamp confidence to [0.0, 1.0] — noisy audio gives
        # avg_logprob < -1.0 which produces negative confidence values
        raw_conf = 1.0 - (result.get('avg_logprob', -0.5) / -1.0)
        return {
            'transcript': result.get('text', '').strip(),
            'detected_language': result.get('language', language_hint),
            'confidence': max(0.0, min(1.0, raw_conf)),
        }
    except Exception as e:
        logger.error(f'Groq Whisper ASR error: {e}')
        return None


def _transcribe_sarvam(audio_bytes: bytes, language_hint: str) -> dict | None:
    """
    Sarvam AI Speech-to-Text — placeholder for production use.
    Best quality for Indian languages.
    """
    api_key = current_app.config.get('SARVAM_API_KEY', '')
    if 'PLACEHOLDER' in api_key:
        return None

    url = current_app.config.get('SARVAM_ASR_URL',
                                  'https://api.sarvam.ai/speech-to-text')

    try:
        files = {
            'file': ('audio.wav', io.BytesIO(audio_bytes), 'audio/wav'),
        }
        data = {
            'language_code': language_hint,
            'model': 'saarika:v2',
        }
        headers = {
            'API-Subscription-Key': api_key,
        }

        resp = requests.post(url, files=files, data=data, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        return {
            'transcript': result.get('transcript', '').strip(),
            'detected_language': result.get('language_code', language_hint),
            'confidence': result.get('confidence', 0.8),
        }
    except Exception as e:
        logger.error(f'Sarvam ASR error: {e}')
        return None
