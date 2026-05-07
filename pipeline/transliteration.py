"""
pipeline/transliteration.py
───────────────────────────────────────────────────────────────────────────────
Converts romanized text back to native script.
Preserves English proper nouns and acronyms (BWSSB, BESCOM, etc.)

BUG-05 FIX: Placeholder tokens now use 'PRESERVEwordX' format (all alpha,
no digits, no underscores) — digits and underscores were being transliterated
into Kannada numerals/chars (e.g., __P0__ → __P೦__) which broke replacement.

B2 FIX: Added _normalize_manglish_to_itrans() pre-processor that converts
informal Manglish Kannada romanization into proper ITRANS notation before
passing to indic-transliteration.  Without this, words like "madbeku",
"thagedilla", "kasa" were rendered as nonsense (e.g., "Ratione Chard Gay")
because ITRANS interprets 'th' as tha-halant, 'a' endings differently, etc.
The normalizer handles the most common informal spellings used in Bengaluru
complaint calls.  Unknown words are passed through as-is so the pipeline
degrades gracefully.
"""

import re
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

PRESERVE_WORDS = {
    'BWSSB', 'BESCOM', 'BBMP', 'KSRTC', 'BMTC', 'BDA', 'KIADB',
    'FIR', 'ATM', 'OTP', 'SIM', 'PIN', 'PAN', 'UID', 'PUC',
    'COVID', 'ICU', 'OPD', 'OK', 'SMS', 'EMI', 'GST',
}

SCRIPT_MAP = {
    'kn': 'KANNADA', 'hi': 'DEVANAGARI', 'te': 'TELUGU',
    'ta': 'TAMIL', 'ml': 'MALAYALAM', 'mr': 'DEVANAGARI',
}

# BUG-05: ASCII-safe placeholder names (all letters, no digits, no underscores)
_PLACEHOLDER_NAMES = [
    'TOKENALPHA', 'TOKENBETA', 'TOKENGAMMA', 'TOKENDELTA', 'TOKENEPSILON',
    'TOKENETA', 'TOKENZETA', 'TOKENTHETA', 'TOKENIOTA', 'TOKENKAPPA',
    'TOKENLAMBDA', 'TOKENMU', 'TOKENNU', 'TOKENXI', 'TOKENOMEGA',
    'TOKENPI', 'TOKENRHO', 'TOKENSIGMA', 'TOKENTAU', 'TOKENUPSILON',
]

# ── B2 FIX: Manglish → ITRANS normalisation map ──────────────────────────────
# Maps informal Manglish Kannada spellings → ITRANS equivalents.
# ITRANS reference: a=ಅ, aa/A=ಆ, i=ಇ, ii/I=ಈ, u=ಉ, uu/U=ಊ,
#   e=ಎ, ee/E=ಏ, ai=ಐ, o=ಒ, oo/O=ಓ, au=ಔ
#   k=ಕ, g=ಗ, ch=ಚ, j=ಜ, T=ಟ, D=ಡ, N=ಣ, t=ತ, d=ದ, n=ನ,
#   p=ಪ, b=ಬ, m=ಮ, y=ಯ, r=ರ, l=ಲ, v/w=ವ, sh=ಶ, S=ಷ, s=ಸ,
#   h=ಹ, L=ಳ, R=ಱ
# The map is applied word-by-word before ITRANS transliteration.
_MANGLISH_TO_ITRANS = {
    # === Common verb endings ===
    'madbeku':      'maaDbEku',     # ಮಾಡಬೇಕು
    'maadibeku':    'maaDbEku',
    'madabeku':     'maaDbEku',
    'madidru':      'maaDidru',     # ಮಾಡಿದ್ರು
    'madidara':     'maaDidara',
    'madilla':      'maaDigilla',   # ಮಾಡಿಲ್ಲ
    'madkolilla':   'maaDigoLilla', # ಮಾಡಿಕೊಳ್ಳಿಲ್ಲ
    'madkolbeku':   'maaDigoLbEku',
    'madtha':       'maadutha',     # ಮಾಡುತ್ತ
    'maado':        'maaDu',        # ಮಾಡು
    'maadi':        'maaD',         # ಮಾಡಿ
    'helilla':      'hELilla',      # ಹೇಳಿಲ್ಲ
    'helidru':      'hELidru',      # ಹೇಳಿದ್ರು
    'hogthilla':    'hooguvadhilla', # ಹೋಗುವುದಿಲ್ಲ
    'hogi':         'hOgi',         # ಹೋಗಿ
    'bandilla':     'bandilla',     # ಬಂದಿಲ್ಲ
    'banni':        'banni',        # ಬನ್ನಿ
    'barthilla':    'baruvudhilla',  # ಬರುವುದಿಲ್ಲ
    # === Nouns / complaint words ===
    'kasa':         'kachara',      # ಕಚರ/ಕಸ
    'thagedilla':   'tageDilla',    # ತೆಗೆದಿಲ್ಲ
    'thagedira':    'tageDira',
    'thagede':      'tageDe',
    'neeru':        'nIru',         # ನೀರು
    'neeruilla':    'nIru illa',
    # === Postpositions / function words ===
    'namma':        'namma',        # ನಮ್ಮ
    'maneyalli':    'maneyalli',    # ಮನೆಯಲ್ಲಿ
    'mane':         'mane',         # ಮನೆ
    'alli':         'alli',         # ಅಲ್ಲಿ
    'ige':          'ige',          # ಇಗೆ
    # === Common particles ===
    'illa':         'illa',         # ಇಲ್ಲ
    'beku':         'bEku',         # ಬೇಕು
    'beka':         'bEka',
    'beda':         'bEDa',         # ಬೇಡ
    'houdu':        'haudu',        # ಹೌದು
    'saari':        'saari',        # ಸಾರಿ
    'thumba':       'tumba',        # ತುಂಬ
    'tumba':        'tumba',
    # === Interrogatives ===
    'yenu':         'yEnu',         # ಏನು
    'yaake':        'yAke',         # ಯಾಕೆ
    'yelli':        'yelli',        # ಎಲ್ಲಿ
    'yaaru':        'yAru',         # ಯಾರು
}


def _normalize_manglish_to_itrans(text: str) -> str:
    """
    B2 FIX: Pre-process informal Manglish Kannada into ITRANS-compatible
    romanization before passing to indic-transliteration.

    Strategy:
    1. Tokenise word-by-word (preserving spaces and punctuation).
    2. Look up each lowercased token in _MANGLISH_TO_ITRANS.
    3. If found, replace; otherwise, pass through unchanged.

    This is intentionally conservative — only known bad spellings are mapped.
    Unknown words are left as-is so they either transliterate acceptably or
    pass through as Latin text (better than nonsense).
    """
    tokens = re.split(r'(\s+|[^\w]+)', text)
    out = []
    for tok in tokens:
        low = tok.lower().strip()
        if low in _MANGLISH_TO_ITRANS:
            # Preserve leading capital if original had one
            replacement = _MANGLISH_TO_ITRANS[low]
            out.append(replacement)
        else:
            out.append(tok)
    return ''.join(out)


def transliterate(text: str, source_script: str = 'en',
                  target_script: str = 'kn') -> dict:
    """Convert romanized text to native script."""
    if not text or not text.strip():
        return {'transliterated': '', 'original': text, 'backend': 'empty'}

    if '-' in source_script:
        target_script = source_script.split('-')[1]

    result = _transliterate_platform(text, target_script)
    if result:
        return result

    result = _transliterate_local(text, target_script)
    if result:
        return result

    return {'transliterated': text, 'original': text, 'backend': 'passthrough'}


def _transliterate_local(text: str, target_lang: str) -> dict | None:
    """
    Use indic-transliteration library (free, offline).

    B2 FIX: Run _normalize_manglish_to_itrans() before ITRANS transliteration
    so informal spellings ("madbeku", "thagedilla") produce correct Kannada
    output instead of nonsense.
    """
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate as itrans

        target = SCRIPT_MAP.get(target_lang)
        if not target:
            return None
        target_scheme = getattr(sanscript, target, None)
        if not target_scheme:
            return None

        # BUG-05: Use ASCII-safe alphabetic placeholder tokens
        preserved = {}
        processed = text
        idx = 0
        for word in re.findall(r'\b[A-Z][A-Z0-9]{1,}\b', text):
            if idx >= len(_PLACEHOLDER_NAMES):
                break
            ph = _PLACEHOLDER_NAMES[idx]
            idx += 1
            preserved[ph] = word
            processed = processed.replace(word, ph, 1)

        # B2 FIX: Normalise informal Manglish spellings to ITRANS before
        # transliterating — prevents "Ratione Chard Gay" style nonsense
        if target_lang == 'kn':
            processed = _normalize_manglish_to_itrans(processed)

        result = itrans(processed, sanscript.ITRANS, target_scheme)

        # Restore preserved words
        for ph, orig in preserved.items():
            result = result.replace(ph, orig)

        return {'transliterated': result, 'original': text,
                'backend': 'indic-transliteration'}
    except ImportError:
        logger.warning('indic-transliteration not installed')
        return None
    except Exception as e:
        logger.error(f'Transliteration error: {e}')
        return None


def _transliterate_platform(text: str, target_lang: str) -> dict | None:
    """Platform API — placeholder for production."""
    try:
        url = current_app.config.get('TRANSLITERATION_URL', '')
        api_key = current_app.config.get('PLATFORM_API_KEY', '')
        if not url or 'PLACEHOLDER' in url or 'PLACEHOLDER' in api_key:
            return None

        resp = requests.post(url,
            headers={'Authorization': f'Bearer {api_key}',
                     'Content-Type': 'application/json'},
            json={'text': text, 'source_language': 'en',
                  'target_language': target_lang},
            timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return {'transliterated': data.get('transliterated_text', text),
                'original': text, 'backend': 'platform'}
    except Exception as e:
        logger.error(f'Platform transliteration error: {e}')
        return None
