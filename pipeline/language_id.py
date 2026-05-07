"""
pipeline/language_id.py
───────────────────────────────────────────────────────────────────────────────
Detects the language and script of a text string.

Primary : langdetect (Python library — free, no API key)
Bonus   : Unicode range analysis for script detection (Kannada vs Devanagari
          vs Latin) — handles code-mixed text better than langdetect alone.

BUG-08 FIX: In _detect_script, trust Indic script if >15% of chars are Indic
            even when Latin is dominant (handles code-mixed text).
BUG-16 FIX: Pre-check for ALL-CAPS acronym input → return 'en' not 'de'.
BUG-17 FIX: Return confidence=0.0 for junk input instead of 0.3.

B1 FIX: Added _detect_transliterated_indic() — a transliteration-aware
        pre-pass that fires BEFORE langdetect for Latin-script text.
        langdetect mis-classifies romanised Kannada ("namma maneyalli current
        illa") as English, Italian, Somali, etc.  We tokenise the text and
        count hits against two curated marker-word sets (Manglish-KN and
        Manglish-HI). If ≥2 markers are found, we return 'kn'/'hi' with
        script='Latin' so the pipeline knows to run transliteration before
        translation — fixing B1 and partially B2.

Used after STT to confirm/correct the language hint before translation.
"""

import re
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# ── Unicode script ranges ─────────────────────────────────────────────────────
SCRIPT_RANGES = {
    'Kannada':    (0x0C80, 0x0CFF),
    'Devanagari': (0x0900, 0x097F),
    'Tamil':      (0x0B80, 0x0BFF),
    'Telugu':     (0x0C00, 0x0C7F),
    'Malayalam':  (0x0D00, 0x0D7F),
    'Latin':      (0x0000, 0x024F),
}

SCRIPT_TO_LANG = {
    'Kannada':    'kn',
    'Devanagari': 'hi',
    'Tamil':      'ta',
    'Telugu':     'te',
    'Malayalam':  'ml',
    'Latin':      'en',
}

# BUG-16: Pattern for all-caps acronym input (e.g., "BWSSB 1916")
_ACRONYM_PATTERN = re.compile(r'^[A-Z0-9\s\-]+$')

# ── B1 FIX: Manglish marker-word sets ────────────────────────────────────────
# High-frequency Kannada function words / complaint lemmas in Roman script.
# Hitting ≥2 of these → text is romanised Kannada, not English.
_MANGLISH_KN_MARKERS = frozenset({
    # postpositions / function words
    'namma', 'maneyalli', 'mane', 'alli', 'ge', 'ige', 'llige', 'inda',
    'andu', 'adhu', 'avru', 'avaga', 'barli', 'barthilla', 'bartha',
    # verb stems used in complaints
    'madidru', 'madalla', 'madbeku', 'maadi', 'madilla', 'maado',
    'madkolilla', 'madkolbeku', 'madtha',
    # common complaint words
    'neeru', 'kasa', 'thagedilla', 'thagedira', 'thagede',
    'beka', 'beku', 'illa', 'aagilla', 'aagthilla', 'aagtilla',
    'thumba', 'tumba', 'saari', 'hogthilla', 'hogi',
    'bandilla', 'houdu', 'beda', 'banni', 'helilla', 'helidru',
    # numerals / determiners
    'ondhu', 'eradu', 'mooru', 'naaku', 'aidu',
    # interrogatives
    'yaav', 'yaaru', 'yen', 'yenu', 'yaake', 'yaavaga', 'yelli',
    # known Kannada-local proper nouns often mixed in
    'bescom', 'bwssb', 'bbmp',
})

# High-frequency Hindi function words / complaint lemmas in Roman script.
_MANGLISH_HI_MARKERS = frozenset({
    'hai', 'nahi', 'nahin', 'hain', 'mein', 'mujhe', 'humara', 'humko',
    'karo', 'karna', 'karein', 'aao', 'aana', 'aaye', 'gaye', 'gaya',
    'paani', 'bijli', 'sadak', 'ghar', 'makan', 'rasta', 'safai',
    'kab', 'kaise', 'kyun', 'kyunki', 'phir', 'abhi', 'bilkul',
    'darj', 'sun', 'suno', 'baar', 'dafa',
    'theek', 'hoga', 'hoti', 'hota', 'chuka', 'chuke',
})


def _detect_transliterated_indic(text: str) -> Dict | None:
    """
    B1 FIX — Transliteration-aware pre-pass for romanised Indic text.

    Tokenises the text and counts how many tokens hit the Manglish-KN or
    Manglish-HI marker sets.  Returns a language dict if confident, or None
    to let the standard langdetect path continue.

    Threshold: ≥2 absolute hits (short text ≤25 tokens) or ≥12% hit ratio
    (longer text).  Kannada wins a tie with Hindi.
    """
    if not text or not text.strip():
        return None

    # Only run on Latin-script text — bail out if any Indic codepoints found
    for char in text:
        code = ord(char)
        if 0x0900 <= code <= 0x0DFF:
            return None

    tokens = re.findall(r"[a-zA-Z']+", text.lower())
    if not tokens:
        return None

    n = len(tokens)
    kn_hits = sum(1 for t in tokens if t in _MANGLISH_KN_MARKERS)
    hi_hits = sum(1 for t in tokens if t in _MANGLISH_HI_MARKERS)

    min_hits = 2 if n <= 25 else max(2, int(n * 0.12))

    if kn_hits >= min_hits and kn_hits >= hi_hits:
        confidence = min(round(kn_hits / max(n, 1) * 3, 3), 0.85)
        logger.debug(f'Manglish-KN detected: {kn_hits}/{n} tokens matched')
        return {'language': 'kn', 'confidence': confidence, 'script': 'Latin'}

    if hi_hits >= min_hits:
        confidence = min(round(hi_hits / max(n, 1) * 3, 3), 0.85)
        logger.debug(f'Manglish-HI detected: {hi_hits}/{n} tokens matched')
        return {'language': 'hi', 'confidence': confidence, 'script': 'Latin'}

    return None


# ── CRIT-5 FIX: Dialect detection marker sets ─────────────────────────────────
# Coastal Karnataka (Tulu-influenced, Dakshina Kannada / Udupi districts)
_DIALECT_KN_COASTAL = frozenset({
    'udupi', 'mangalore', 'mangaluru', 'kasaragod', 'ullal', 'sullia',
    'puttur', 'bantwal', 'belthangady', 'kundapura', 'karkala',
    'tulu', 'tulunadu', 'tuluva',
})
# Northern Karnataka (Dharwad / Kittur Kannada dialect region)
_DIALECT_KN_NORTH = frozenset({
    'dharwad', 'hubli', 'hubballi', 'haveri', 'gadag', 'belgaum',
    'belagavi', 'koppal', 'raichur', 'bidar', 'kalaburagi', 'gulbarga',
    'bijapur', 'vijayapura', 'bagalkot',
})
# Old Mysuru (standard literary Kannada region)
_DIALECT_KN_OLD_MYSURU = frozenset({
    'mysuru', 'mysore', 'chamarajanagar', 'mandya', 'hassan',
    'kodagu', 'coorg', 'tumkur', 'tumakuru', 'ramanagara',
})
# Hindi regional markers
_DIALECT_HI_NORTH = frozenset({
    'dehradun', 'lucknow', 'allahabad', 'prayagraj', 'kanpur',
    'varanasi', 'banaras', 'agra', 'meerut', 'bareilly',
})
_DIALECT_HI_WEST = frozenset({
    'mumbai', 'pune', 'nagpur', 'nashik', 'aurangabad',
    'bhopal', 'indore', 'gwalior', 'jabalpur',
})
_DIALECT_TOKEN_RE = re.compile(r'[\u0C80-\u0CFF]+|[a-zA-Z]+')


def detect_dialect(text: str, language: str):
    """
    CRIT-5 FIX: Infer regional dialect variant from text content.

    Uses location names and dialect-specific lexical markers.
    Returns a dialect tag string or None when insufficient evidence.

    Dialect tags:
        kn -> 'coastal_kn' | 'north_kn' | 'old_mysuru_kn' | 'manglish_kn'
        hi -> 'north_hi' | 'west_hi' | 'manglish_hi'
        en -> None
    """
    if not text or language not in ('kn', 'hi'):
        return None

    tokens = set(_DIALECT_TOKEN_RE.findall(text.lower()))

    if language == 'kn':
        if tokens & _DIALECT_KN_COASTAL:
            return 'coastal_kn'
        if tokens & _DIALECT_KN_NORTH:
            return 'north_kn'
        if tokens & _DIALECT_KN_OLD_MYSURU:
            return 'old_mysuru_kn'
        # Romanised Kannada (Manglish) is itself a dialect signal
        if sum(1 for t in tokens if t in _MANGLISH_KN_MARKERS) >= 2:
            return 'manglish_kn'

    elif language == 'hi':
        if tokens & _DIALECT_HI_NORTH:
            return 'north_hi'
        if tokens & _DIALECT_HI_WEST:
            return 'west_hi'
        if sum(1 for t in tokens if t in _MANGLISH_HI_MARKERS) >= 2:
            return 'manglish_hi'

    return None



def identify_language(text: str) -> Dict:
    """
    Detect the language of a text string.

    Returns:
        {
            'language': str,     # ISO 639-1 code ('kn', 'hi', 'en', …)
            'confidence': float, # 0.0–1.0
            'script': str,       # 'Kannada', 'Devanagari', 'Latin', …
        }
    """
    if not text or not text.strip():
        return {'language': 'en', 'confidence': 0.0, 'script': 'Latin'}

    # BUG-16: All-caps acronym / number → English
    stripped = text.strip()
    if _ACRONYM_PATTERN.match(stripped) and len(stripped) <= 30:
        return {'language': 'en', 'confidence': 0.9, 'script': 'Latin'}

    # Step 1: Unicode script analysis
    script_result = _detect_script(text)

    # Step 2: langdetect (cross-check for non-Indic or mixed)
    langdetect_result = _detect_langdetect(text)

    # Step 3: Merge
    if script_result['script'] != 'Latin' and script_result['confidence'] > 0.3:
        # Indic script — trust Unicode analysis
        return {
            'language': script_result['language'],
            'confidence': max(script_result['confidence'],
                              langdetect_result.get('confidence', 0.0)),
            'script': script_result['script'],
        }
    else:
        # B1 FIX: For Latin text, run Manglish pre-pass BEFORE langdetect.
        # langdetect returns 'en', 'it', 'so' etc. for romanised Kannada.
        translit_result = _detect_transliterated_indic(text)
        if translit_result:
            return translit_result

        # Pure Latin / English
        return {
            'language': langdetect_result.get('language', 'en'),
            'confidence': langdetect_result.get('confidence', 0.5),
            'script': 'Latin',
        }


def detect_script_mismatch(text: str, detected_language: str) -> bool:
    """
    Return True if text is in a different script than the detected language
    implies (e.g. language='kn' but script='Latin' → romanised Kannada).
    """
    script = _detect_script(text)
    expected_scripts = {
        'kn': 'Kannada', 'hi': 'Devanagari', 'ta': 'Tamil',
        'te': 'Telugu',  'ml': 'Malayalam',  'en': 'Latin',
    }
    expected = expected_scripts.get(detected_language, 'Latin')
    return script['script'] != expected and detected_language != 'en'


# ── Internal helpers ──────────────────────────────────────────────────────────

def _detect_script(text: str) -> Dict:
    """
    BUG-08 FIX: Trust Indic script if >15% of chars are Indic even when
    Latin is the majority (handles code-mixed text like "ಕನ್ನಡ hello mixed").
    """
    script_counts = {name: 0 for name in SCRIPT_RANGES}
    total_alpha = 0

    for char in text:
        code = ord(char)
        if char.isspace() or char in '.,!?;:-\u2013\u2014()[]{}"\'/\\@#$%^&*+=<>~`|':
            continue
        total_alpha += 1
        for script_name, (start, end) in SCRIPT_RANGES.items():
            if start <= code <= end:
                script_counts[script_name] += 1
                break

    if total_alpha == 0:
        return {'language': 'en', 'confidence': 0.0, 'script': 'Latin'}

    indic_scripts = ['Kannada', 'Devanagari', 'Tamil', 'Telugu', 'Malayalam']
    for script_name in indic_scripts:
        ratio = script_counts[script_name] / total_alpha
        if ratio > 0.15:
            return {
                'language': SCRIPT_TO_LANG[script_name],
                'confidence': round(ratio, 3),
                'script': script_name,
            }

    dominant = max(script_counts.items(), key=lambda x: x[1])
    confidence = dominant[1] / total_alpha if total_alpha > 0 else 0.0
    return {
        'language': SCRIPT_TO_LANG.get(dominant[0], 'en'),
        'confidence': round(confidence, 3),
        'script': dominant[0],
    }


def _detect_langdetect(text: str) -> Dict:
    """
    BUG-17 FIX: Return confidence=0.0 instead of 0.3 for junk/no-feature input.
    """
    try:
        from langdetect import detect_langs
        results = detect_langs(text)
        if results:
            top = results[0]
            lang_map = {
                'kn': 'kn', 'hi': 'hi', 'en': 'en',
                'te': 'te', 'ta': 'ta', 'ml': 'ml', 'mr': 'mr',
            }
            lang = lang_map.get(top.lang, top.lang)
            return {'language': lang, 'confidence': round(top.prob, 3)}
    except Exception as e:
        logger.warning(f'langdetect failed: {e}')
    return {'language': 'en', 'confidence': 0.0}
