"""
pipeline/tts.py
───────────────────────────────────────────────────────────────────────────────
Text-to-Speech module for VaakSetu.

BUG-03 FIX: Replaced gTTS (returns 403) with edge-tts (Microsoft, free,
excellent Indian voices including kn-IN-SapnaNeural and hi-IN-SwaraNeural).

Primary : edge-tts (Microsoft — free, no API key, great Kannada/Hindi voices)
Upgrade : Sarvam Bulbul (natural Kannada voice)

Install: pip install edge-tts

Converts AI restatement text into spoken audio bytes.
"""

import io
import asyncio
import base64
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

# edge-tts voice mapping for Indian languages
EDGE_TTS_VOICES = {
    'kn': 'kn-IN-SapnaNeural',   # Kannada — female, clear
    'hi': 'hi-IN-SwaraNeural',   # Hindi — female, natural
    'en': 'en-IN-NeerjaNeural',  # English (India accent)
    'te': 'te-IN-ShrutiNeural',  # Telugu
    'ta': 'ta-IN-PallaviNeural', # Tamil
    'ml': 'ml-IN-SobhanaNeural', # Malayalam
    'mr': 'mr-IN-AarohiNeural',  # Marathi
}


def speak(text: str, language: str = 'kn') -> bytes | None:
    """
    Convert text to speech audio bytes.

    Args:
        text: Text to speak
        language: Language code ('kn', 'hi', 'en')

    Returns:
        Audio bytes (MP3) or None if all backends fail
    """
    if not text or not text.strip():
        return None

    backend = current_app.config.get('TTS_BACKEND', 'edge')

    if backend == 'sarvam':
        result = _speak_sarvam(text, language)
        if result:
            return result

    # edge-tts (free Microsoft TTS — BUG-03 fix)
    return _speak_edge(text, language)


def speak_to_base64(text: str, language: str = 'kn') -> str | None:
    """
    Convert text to speech and return as base64-encoded string.
    Ready for embedding in JSON responses.
    """
    audio_bytes = speak(text, language)
    if audio_bytes:
        return base64.b64encode(audio_bytes).decode('utf-8')
    return None


def speak_to_file(text: str, language: str, output_path: str) -> bool:
    """Save TTS audio to disk file."""
    audio_bytes = speak(text, language)
    if audio_bytes:
        try:
            with open(output_path, 'wb') as f:
                f.write(audio_bytes)
            return True
        except Exception as e:
            logger.error(f'Error saving TTS file: {e}')
    return False


# ── Backend: edge-tts (Microsoft, free, BUG-03 fix) ──────────────────────────

def _speak_edge(text: str, language: str) -> bytes | None:
    """
    edge-tts — Microsoft free TTS with natural Indian voices.
    Supports Kannada, Hindi, English, Telugu, Tamil, Malayalam, Marathi.
    No API key required.
    """
    try:
        import edge_tts

        voice = EDGE_TTS_VOICES.get(language, EDGE_TTS_VOICES['en'])

        async def _generate() -> bytes:
            communicate = edge_tts.Communicate(text, voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        # Run async in a new event loop (Flask is sync)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If there's already a running loop (rare in Flask threading mode),
                # create a new thread with its own loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _generate())
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(_generate())
        except RuntimeError:
            return asyncio.run(_generate())

    except ImportError:
        logger.warning('edge-tts not installed — pip install edge-tts')
        return None
    except Exception as e:
        logger.error(f'edge-tts error: {e}')
        return None


# ── Backend: Sarvam (production upgrade) ──────────────────────────────────────

def _speak_sarvam(text: str, language: str) -> bytes | None:
    """Sarvam Bulbul TTS — best quality for Indian languages."""
    api_key = current_app.config.get('SARVAM_API_KEY', '')
    if 'PLACEHOLDER' in api_key:
        return None

    url = current_app.config.get('SARVAM_TTS_URL',
                                  'https://api.sarvam.ai/text-to-speech')
    try:
        resp = requests.post(url,
            headers={'API-Subscription-Key': api_key,
                     'Content-Type': 'application/json'},
            json={'inputs': [text],
                  'target_language_code': language,
                  'speaker': 'meera',
                  'pitch': 0, 'pace': 0.9,
                  'loudness': 1.0,
                  'enable_preprocessing': True},
            timeout=30)
        resp.raise_for_status()
        data = resp.json()
        audio_b64 = data.get('audios', [None])[0]
        if audio_b64:
            return base64.b64decode(audio_b64)
        return None
    except Exception as e:
        logger.error(f'Sarvam TTS error: {e}')
        return None
