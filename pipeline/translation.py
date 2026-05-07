"""
pipeline/translation.py
───────────────────────────────────────────────────────────────────────────────
Translation layer for VaakSetu.

BUG-02 FIX: Replaced MyMemory (returns 403 for Indian languages) with
deep-translator's GoogleTranslator backend (free, no key, works offline-ish).

Install: pip install deep-translator

FREE backend : deep-translator GoogleTranslator — no API key required.
PAID upgrade : BHASHINI — India's national language API, free for
               non-commercial/govt use.

Translates citizen complaint text ↔ English for NLU processing,
and AI restatement from English → citizen's language.
"""

import logging
from flask import current_app

logger = logging.getLogger(__name__)

# deep-translator language codes (ISO 639-1 codes work directly)
LANG_CODES = {
    'kn': 'kn',   # Kannada
    'hi': 'hi',   # Hindi
    'en': 'en',
    'te': 'te',   # Telugu
    'ta': 'ta',   # Tamil
    'ml': 'ml',   # Malayalam
    'mr': 'mr',   # Marathi
}


def translate_text(text: str, source_lang: str = 'kn',
                   target_lang: str = 'en') -> dict:
    """
    Translate text between languages.

    Args:
        text: Text to translate
        source_lang: Source language ISO code ('kn', 'hi', 'en', etc.)
        target_lang: Target language ISO code

    Returns:
        {
            'translated': str,
            'source': str,
            'target': str,
            'confidence': float,
            'backend': str,
            'error': str | None
        }
    """
    if not text or not text.strip():
        return {'translated': '', 'source': source_lang,
                'target': target_lang, 'backend': 'empty', 'error': None}

    if source_lang == target_lang:
        return {'translated': text, 'source': source_lang,
                'target': target_lang, 'backend': 'passthrough',
                'confidence': 1.0, 'error': None}

    backend = current_app.config.get('TRANSLATION_BACKEND', 'deep_translator')

    # Try BHASHINI first if configured
    if backend == 'bhashini':
        result = _translate_bhashini(text, source_lang, target_lang)
        if result and not result.get('error'):
            return result

    # deep-translator (primary free backend — BUG-02 fix)
    return _translate_deep(text, source_lang, target_lang)


def batch_translate(texts: list, source_lang: str = 'kn',
                    target_lang: str = 'en') -> list:
    """
    Translate multiple texts at once.

    Args:
        texts: List of strings to translate
        source_lang: Source language code
        target_lang: Target language code

    Returns:
        List of translation result dicts (same format as translate_text)
    """
    results = []
    for text in texts:
        result = translate_text(text, source_lang, target_lang)
        results.append(result)
    return results


# ── Backend implementations ───────────────────────────────────────────────────

def _translate_deep(text: str, src: str, tgt: str) -> dict:
    """
    deep-translator GoogleTranslator — BUG-02 fix.
    Free, no API key, supports all Indian languages including Kannada.
    Install: pip install deep-translator
    """
    try:
        from deep_translator import GoogleTranslator
        src_code = LANG_CODES.get(src, src)
        tgt_code = LANG_CODES.get(tgt, tgt)
        translated = GoogleTranslator(source=src_code, target=tgt_code).translate(text)
        if not translated:
            raise ValueError("Empty translation result")
        return {
            'translated': translated,
            'source': src,
            'target': tgt,
            'confidence': 0.85,
            'backend': 'deep_translator',
            'error': None,
        }
    except ImportError:
        logger.warning('deep-translator not installed — pip install deep-translator')
        return {'translated': text, 'source': src, 'target': tgt,
                'confidence': 0.0, 'backend': 'deep_translator',
                'error': 'deep-translator not installed'}
    except Exception as e:
        logger.error(f'deep-translator error: {e}')
        return {'translated': text, 'source': src, 'target': tgt,
                'confidence': 0.0, 'backend': 'deep_translator', 'error': str(e)}


def _translate_bhashini(text: str, src: str, tgt: str) -> dict | None:
    """
    BHASHINI Dhruva translation API.
    Best quality for Indian languages. Free for non-commercial use.
    Apply at: https://bhashini.gov.in
    Set BHASHINI_API_KEY in .env and TRANSLATION_BACKEND=bhashini.
    """
    try:
        import requests
        api_key = current_app.config.get('BHASHINI_API_KEY', '')
        if 'PLACEHOLDER' in api_key:
            return None

        payload = {
            'pipelineTasks': [{
                'taskType': 'translation',
                'config': {
                    'language': {
                        'sourceLanguage': src,
                        'targetLanguage': tgt,
                    }
                }
            }],
            'inputData': {
                'input': [{'source': text}]
            }
        }
        resp = requests.post(
            current_app.config['BHASHINI_URL'],
            headers={'Authorization': api_key,
                     'Content-Type': 'application/json'},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        output = resp.json()
        translated = (output.get('pipelineResponse', [{}])[0]
                            .get('output', [{}])[0]
                            .get('target', text))
        return {
            'translated': translated,
            'source': src,
            'target': tgt,
            'confidence': 0.85,
            'backend': 'bhashini',
            'error': None,
        }
    except Exception as e:
        logger.error(f'BHASHINI error: {e}')
        return None
