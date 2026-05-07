"""
pipeline/nlu.py
───────────────────────────────────────────────────────────────────────────────
THE CORE BRAIN — Natural Language Understanding via LLM.

Given citizen text (translated to English), extracts intent, entities,
confidence, and generates a restatement in the citizen's language.
All in a single LLM call (Chat Completion API).

Backends: Groq (Llama-3, free) | Gemini Flash (free) | OpenRouter (free)
Fallback: keyword-based intent matching from intent_patterns.json

BUG-09 FIX: Added 'property_tax' and 'escalate_complaint' to _map_keyword_intent.
BUG-19 FIX: _keyword_fallback loads department_routing.json for location overrides.
BUG-20 FIX: _keyword_fallback checks native language keywords (kn/hi), not only en.
BUG-36 FIX: escalate_complaint is now reachable from keyword fallback.
BUG-38 FIX: Added 'ml' (Malayalam) and 'mr' (Marathi) to LANG_CONFIG.

B4  FIX: System prompt now provides the canonical department code list so the
         LLM cannot invent free-form names. _normalize_department() validates
         and aliases all routing_suggestion values from LLM + keyword paths.

B5  FIX: System prompt now has explicit fire→FIRE_DEPT(101) and
         safety/stranger→KSRP(100) rules. seek_safety and fire_emergency
         added to keyword intent map.

B7  FIX: _apply_confidence_penalties() reduces confidence when:
         (a) detected_language is Latin-script Indic (transliteration ran),
         (b) no entity extracted,
         (c) routing_suggestion not in canonical department list.

B8  FIX: System prompt now explicitly prohibits inventing context or changing
         who is speaking ("do NOT rephrase 'please come' as if directed at the
         agent — it is the citizen speaking to the authority").

B10 FIX: System prompt now includes LANGUAGE INSTRUCTION block that requires
         the restatement to be written in the citizen's own language, not EN.

GEMINI FIX (_call_gemini):
  1. Model updated: gemini-1.5-flash (deprecated/404 for many keys) ->
     gemini-2.0-flash via new GEMINI_MODEL config key.
  2. Endpoint updated: /v1beta/ -> /v1/ (stable). URL now built dynamically
     from model name so config changes propagate automatically.
  3. Defensive response extraction: replaced bare
     resp.json()["candidates"][0]["content"]["parts"][0]["text"] (crashes
     on safety-blocked responses) with guarded extraction that checks
     candidates list, finishReason, and parts before indexing.
  4. HTTP 404 logged with actionable message (model name + enablement hint).
"""

import json
import os
import re
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

# ── Canonical department list (B4 FIX) ───────────────────────────────────────
# ALL routing_suggestion values must resolve to one of these codes.
CANONICAL_DEPARTMENTS = frozenset({
    'BWSSB', 'BESCOM', 'BBMP_ROADS', 'BBMP_HEALTH', 'BBMP_REVENUE',
    'FOOD_CIVIL', 'REVENUE_DEPT', 'SOCIAL_WELFARE', 'KSRP', 'DMER',
    'DPAR', 'FIRE_DEPT', 'RDPR', 'PWD', 'CESC_MYSURU', 'KUWSDB',
    'HESCOM', 'MESCOM', 'MCLM_WATER', 'KIADB',
})

# Alias map — LLM free-form names → canonical codes (B4 FIX)
_DEPT_ALIASES = {
    'police': 'KSRP',
    'police helpline': 'KSRP',
    'local police': 'KSRP',
    'ksp': 'KSRP',
    'fire department': 'FIRE_DEPT',
    'fire dept': 'FIRE_DEPT',
    'fire service': 'FIRE_DEPT',
    'fire brigade': 'FIRE_DEPT',
    'fire station': 'FIRE_DEPT',
    'bbmp': 'BBMP_HEALTH',
    'bbmp garbage': 'BBMP_HEALTH',
    'bbmp garbage department': 'BBMP_HEALTH',
    'bbmp_garbage': 'BBMP_HEALTH',
    'waste management': 'BBMP_HEALTH',
    'health dept': 'BBMP_HEALTH',
    'health department': 'BBMP_HEALTH',
    'bbmp_health': 'BBMP_HEALTH',
    'bescom': 'BESCOM',
    'bangalore electricity supply company': 'BESCOM',
    'bwssb': 'BWSSB',
    'bwssb customer care': 'BWSSB',
    'water board': 'BWSSB',
    'food_civil': 'FOOD_CIVIL',
    'public distribution system': 'FOOD_CIVIL',
    'pds': 'FOOD_CIVIL',
    'social welfare': 'SOCIAL_WELFARE',
    'dmer': 'DMER',
    'bbmp roads': 'BBMP_ROADS',
    'bbmp_roads': 'BBMP_ROADS',
    'revenue dept': 'REVENUE_DEPT',
    'revenue department': 'REVENUE_DEPT',
    'ksrp': 'KSRP',
}


def _normalize_department(raw: str | None) -> str | None:
    """
    B4 FIX: Normalise LLM-generated department name to a canonical code.
    Returns None if the value cannot be resolved.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    # Direct canonical match (case-insensitive)
    upper = raw.strip().upper()
    if upper in CANONICAL_DEPARTMENTS:
        return upper
    # Alias lookup
    if key in _DEPT_ALIASES:
        return _DEPT_ALIASES[key]
    # Partial match fallback
    for alias, code in _DEPT_ALIASES.items():
        if alias in key:
            return code
    logger.warning(f'Unknown department name from LLM: {raw!r} — setting None')
    return None


# ── NLU Prompt Template ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are VaakSetu, an AI assistant for Karnataka's 1092 helpline.
A citizen just spoke. You MUST respond ONLY with valid JSON — no markdown fences.

CANONICAL DEPARTMENT CODES (use ONLY these exact strings for routing_suggestion):
  BWSSB        — water supply / sewage complaints
  BESCOM       — electricity / power outage / street lights
  BBMP_ROADS   — potholes, road damage
  BBMP_HEALTH  — garbage, sanitation, waste collection
  BBMP_REVENUE — property tax
  FOOD_CIVIL   — ration card, PDS
  REVENUE_DEPT — land records, khata
  SOCIAL_WELFARE — pension, disability, welfare schemes
  KSRP         — police, safety threats, strangers, crime (helpline: 100)
  DMER         — medical emergency, ambulance (helpline: 108)
  FIRE_DEPT    — fire, building fire, smoke, fire brigade (helpline: 101)
  DPAR         — general / unclear inquiry

CRITICAL ROUTING RULES (override everything else):
  • Any mention of FIRE, house on fire, smoke, burning → FIRE_DEPT, emergency intent
  • Strangers near house / being followed / scared of person → KSRP, seek_safety or emergency
  • Street light not working → BESCOM
  • Garbage not collected → BBMP_HEALTH (NOT BWSSB)

LANGUAGE INSTRUCTION (B10):
  The field restatement_{lang} MUST be written entirely in {lang_name}.
  Do NOT write the restatement in English if the citizen's language is Kannada or Hindi.
  Use the script of {lang_name} (Kannada = ಕನ್ನಡ script, Hindi = Devanagari script).

RESTATEMENT RULES (B8):
  • Rephrase ONLY what the citizen said — do NOT invent context.
  • Do NOT change who is speaking to whom.
    ("please come" in the citizen's message is directed AT the authority, not at you.)
  • Do NOT add locations, dates, or names that are not in the citizen's words.
  • Start with the prefix: '{restatement_prefix}'

Analyze the citizen's message and return:
{{
  "intent": one of ["report_issue", "seek_information", "escalate_complaint",
                    "emergency", "seek_safety", "unclear"],
  "entities": {{
    "location":   "extracted location or null",
    "department": "canonical code from list above or null",
    "issue_type": "brief description or null",
    "duration":   "how long the problem has existed or null"
  }},
  "confidence": 0.0 to 1.0,
  "restatement_{lang}": "restatement in {lang_name} — see rules above",
  "routing_suggestion": "canonical department code from list above or null",
  "requires_escalation": true or false
}}"""

# BUG-38 FIX: Added 'ml' (Malayalam) and 'mr' (Marathi)
LANG_CONFIG = {
    'kn': {'name': 'Kannada',   'prefix': 'ನೀವು ಹೇಳಿದ್ದು...'},
    'hi': {'name': 'Hindi',     'prefix': 'आपने बताया कि...'},
    'en': {'name': 'English',   'prefix': 'You said...'},
    'te': {'name': 'Telugu',    'prefix': 'మీరు చెప్పింది...'},
    'ta': {'name': 'Tamil',     'prefix': 'நீங்கள் சொன்னது...'},
    'ml': {'name': 'Malayalam', 'prefix': 'നിങ്ങൾ പറഞ്ഞത്...'},
    'mr': {'name': 'Marathi',   'prefix': 'तुम्ही सांगितले...'},
}


def _apply_confidence_penalties(result: dict, detected_language: str,
                                  is_transliterated: bool) -> dict:
    """
    B7 FIX: Reduce confidence to reflect genuine ambiguity:
      (a) transliterated input: -0.10 (language detection was uncertain)
      (b) no entity extracted: -0.10
      (c) routing_suggestion not in canonical list: -0.20
      (d) 'unclear' intent always: max 0.30
    """
    conf = result.get('confidence', 0.5)

    if is_transliterated:
        conf -= 0.10

    entities = result.get('entities', {})
    has_entity = any(v for v in entities.values() if v)
    if not has_entity:
        conf -= 0.10

    dept = result.get('routing_suggestion')
    if dept and dept not in CANONICAL_DEPARTMENTS:
        conf -= 0.20

    if result.get('intent') == 'unclear':
        conf = min(conf, 0.30)

    result['confidence'] = round(max(conf, 0.0), 3)
    return result


# Sentinel returned by _call_* functions on HTTP 429.
# Distinct from None (which means "error/skip") so analyze_turn can
# differentiate "this backend is rate-limited" from "this backend errored".
_THROTTLED = object()


def analyze_turn(text_en: str, turn_history: list = None,
                 citizen_language: str = 'kn',
                 is_transliterated: bool = False) -> dict:
    """
    Analyze a citizen's utterance using LLM.

    Args:
        text_en: English translation of citizen's message
        turn_history: List of previous turn dicts (last 3)
        citizen_language: Citizen's language code for restatement
        is_transliterated: True if input went through transliteration (B7)

    Returns dict with intent, entities, confidence, restatement, routing, escalation.

    429/fallback fix: The old code called the primary backend, then re-called
    ALL three backends unconditionally — meaning Groq could be called twice and
    a 429 storm would exhaust all three backends in one request.
    Fixed by:
      • Building a deduplicated, priority-ordered backend list that excludes
        the already-tried primary backend from the fallback sequence.
      • Each _call_* function now returns a sentinel _THROTTLED instead of None
        on 429, so throttled backends are skipped rather than retried.
      • A 1 s sleep is inserted before retrying a throttled backend as last
        resort (gives the rate-limit window a chance to reset).
    """
    if not text_en or not text_en.strip():
        return _fallback_result('unclear', 0.0, citizen_language)

    backend = current_app.config.get('LLM_BACKEND', 'groq')

    _CALLERS = {
        'groq':       _call_groq,
        'gemini':     _call_gemini,
        'openrouter': _call_openrouter,
    }

    # Ordered fallback list: preferred backend first, then the others.
    # Each name appears EXACTLY ONCE — no double-calling on 429.
    _FALLBACK_ORDER = ['groq', 'gemini', 'openrouter']
    ordered = [backend] + [b for b in _FALLBACK_ORDER if b != backend]

    llm_result = None
    throttled  = []   # backends that returned 429 this request

    for name in ordered:
        caller = _CALLERS.get(name)
        if caller is None:
            continue
        result = caller(text_en, turn_history, citizen_language)
        if result is _THROTTLED:
            throttled.append(name)
            logger.warning(f'{name} rate-limited (429) — skipping in this request')
            continue
        if result is not None:
            llm_result = result
            break

    # Last-resort: if all non-throttled backends failed and some were throttled,
    # wait 1 s and try the throttled ones once in order.
    if llm_result is None and throttled:
        import time
        logger.warning(f'All backends failed or throttled — sleeping 1s then retrying: {throttled}')
        time.sleep(1)
        for name in throttled:
            result = _CALLERS[name](text_en, turn_history, citizen_language)
            if result is not None and result is not _THROTTLED:
                llm_result = result
                break

    if llm_result:
        # B4 FIX: Normalize department to canonical code
        llm_result['routing_suggestion'] = _normalize_department(
            llm_result.get('routing_suggestion'))
        if llm_result.get('entities', {}).get('department'):
            llm_result['entities']['department'] = _normalize_department(
                llm_result['entities']['department'])
        # B7 FIX: Apply confidence penalties
        llm_result = _apply_confidence_penalties(
            llm_result, citizen_language, is_transliterated)
        return llm_result

    logger.warning('All LLM backends failed — keyword fallback')
    result = _keyword_fallback(text_en, citizen_language)
    result = _apply_confidence_penalties(result, citizen_language, is_transliterated)
    return result


def _build_messages(text_en, turn_history, citizen_language):
    """Build the chat messages array for the LLM."""
    lang_cfg = LANG_CONFIG.get(citizen_language, LANG_CONFIG['en'])
    system = SYSTEM_PROMPT.format(
        lang=citizen_language,
        lang_name=lang_cfg['name'],
        restatement_prefix=lang_cfg['prefix'],
    )

    messages = [{'role': 'system', 'content': system}]

    if turn_history:
        ctx = '\n'.join([
            f"Turn {t.get('turn_number', i)}: {t.get('clean_text', t.get('text', ''))}"
            for i, t in enumerate(turn_history[-3:])
        ])
        messages.append({
            'role': 'user',
            'content': f'CONTEXT (previous turns):\n{ctx}'
        })
        messages.append({
            'role': 'assistant',
            'content': 'Understood. I will consider this context.'
        })

    messages.append({
        'role': 'user',
        'content': f'CITIZEN SAYS (English translation):\n{text_en}'
    })

    return messages


def _parse_llm_response(raw: str, citizen_language: str) -> dict | None:
    """Parse and validate the LLM's JSON response."""
    try:
        cleaned = re.sub(r'```json\s*', '', raw)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        data = json.loads(cleaned)

        restatement = ''
        for key in data:
            if key.startswith('restatement'):
                restatement = data[key]
                break

        return {
            'intent': data.get('intent', 'unclear'),
            'entities': data.get('entities', {}),
            'confidence': float(data.get('confidence', 0.5)),
            'restatement': restatement,
            'routing_suggestion': data.get('routing_suggestion'),
            'requires_escalation': bool(data.get('requires_escalation', False)),
            'raw_llm_response': raw,
        }
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f'Failed to parse LLM response: {e}\nRaw: {raw[:300]}')
        return None


# ── LLM Backend: Groq ─────────────────────────────────────────────────────────

def _call_groq(text_en, turn_history, citizen_language):
    api_key = current_app.config.get('GROQ_API_KEY', '')
    if not api_key or 'PLACEHOLDER' in api_key:
        return None

    url   = current_app.config.get('GROQ_LLM_URL',
                'https://api.groq.com/openai/v1/chat/completions')
    model = current_app.config.get('GROQ_LLM_MODEL', 'llama3-8b-8192')
    messages = _build_messages(text_en, turn_history, citizen_language)

    try:
        resp = requests.post(url,
            headers={'Authorization': f'Bearer {api_key}',
                     'Content-Type': 'application/json'},
            json={'model': model, 'messages': messages,
                  'temperature': 0.3, 'max_tokens': 500},
            timeout=30)
        if resp.status_code == 429:
            logger.warning('Groq 429 — rate limited')
            return _THROTTLED
        resp.raise_for_status()
        raw = resp.json()['choices'][0]['message']['content']
        return _parse_llm_response(raw, citizen_language)
    except requests.exceptions.HTTPError as e:
        logger.error(f'Groq LLM error: {e}')
        return None
    except Exception as e:
        logger.error(f'Groq LLM error: {e}')
        return None


# ── LLM Backend: Gemini Flash ─────────────────────────────────────────────────

# Gemini model in config.py was pointing at gemini-1.5-flash on v1beta —
# that model is deprecated/unavailable for many API keys and projects.
# Fixes applied here:
#   1. Build the endpoint URL from GEMINI_MODEL config (defaults to
#      gemini-2.0-flash) so it never drifts out of sync with the model name.
#   2. Use the stable /v1/ endpoint path instead of /v1beta/.
#   3. Defensive response extraction: check candidates list length, check
#      finishReason, handle SAFETY / empty part lists instead of crashing
#      with an IndexError on resp.json()['candidates'][0]['content']['parts'][0].

_GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1/models'
_GEMINI_DEFAULT_MODEL = 'gemini-2.0-flash'   # replaces deprecated gemini-1.5-flash


def _call_gemini(text_en, turn_history, citizen_language):
    api_key = current_app.config.get('GEMINI_API_KEY', '')
    if not api_key or 'PLACEHOLDER' in api_key:
        return None

    # Build URL from model name so config changes propagate automatically.
    # Falls back to gemini-2.0-flash if GEMINI_MODEL is not set or still
    # points at the old v1beta URL (detected by presence of 'v1beta' in value).
    model = current_app.config.get('GEMINI_MODEL', _GEMINI_DEFAULT_MODEL)
    configured_url = current_app.config.get('GEMINI_URL', '')
    if configured_url and 'v1beta' not in configured_url and 'generateContent' in configured_url:
        # Caller already provides a clean, versioned URL — use it as-is.
        url = f'{configured_url}?key={api_key}'
    else:
        # Construct from scratch using stable v1 path.
        url = f'{_GEMINI_BASE}/{model}:generateContent?key={api_key}'

    messages = _build_messages(text_en, turn_history, citizen_language)
    prompt = '\n'.join([f"[{m['role'].upper()}]: {m['content']}" for m in messages])

    try:
        resp = requests.post(url,
            headers={'Content-Type': 'application/json'},
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 500},
            },
            timeout=30)

        # Surface a clear error for 404 (wrong model/endpoint) rather than
        # letting it silently fall through as a generic exception.
        if resp.status_code == 404:
            logger.error(
                f'Gemini 404 — model or endpoint not found. '
                f'URL used: {url.split("?")[0]!r}. '
                f'Check GEMINI_MODEL in config (current: {model!r}) and '
                f'that the Generative Language API is enabled for your project.'
            )
            return None

        resp.raise_for_status()
        body = resp.json()

        # Defensive extraction — Gemini can return:
        #   • an empty candidates list (prompt blocked by safety filters)
        #   • candidates with finishReason=SAFETY and no content/parts
        #   • a promptFeedback block with blockReason
        candidates = body.get('candidates') or []
        if not candidates:
            block_reason = (body.get('promptFeedback') or {}).get('blockReason', 'unknown')
            logger.warning(f'Gemini returned no candidates — blockReason: {block_reason}')
            return None

        candidate = candidates[0]
        finish_reason = candidate.get('finishReason', '')
        if finish_reason == 'SAFETY':
            logger.warning('Gemini candidate blocked by safety filter')
            return None

        parts = (candidate.get('content') or {}).get('parts') or []
        if not parts:
            logger.warning('Gemini candidate has no parts in content')
            return None

        raw = parts[0].get('text', '')
        if not raw:
            logger.warning('Gemini returned empty text part')
            return None

        return _parse_llm_response(raw, citizen_language)

    except requests.exceptions.HTTPError as e:
        logger.error(f'Gemini HTTP error {e.response.status_code}: {e}')
        return None
    except Exception as e:
        logger.error(f'Gemini LLM error: {e}')
        return None


# ── LLM Backend: OpenRouter ───────────────────────────────────────────────────

def _call_openrouter(text_en, turn_history, citizen_language):
    api_key = current_app.config.get('OPENROUTER_API_KEY', '')
    if not api_key or 'PLACEHOLDER' in api_key:
        return None

    url   = current_app.config.get('OPENROUTER_URL',
                'https://openrouter.ai/api/v1/chat/completions')
    model = current_app.config.get('OPENROUTER_MODEL',
                'meta-llama/llama-3.3-70b-instruct:free')
    messages = _build_messages(text_en, turn_history, citizen_language)

    try:
        resp = requests.post(url,
            headers={'Authorization': f'Bearer {api_key}',
                     'Content-Type': 'application/json',
                     'HTTP-Referer': 'https://vaaksetu.gov.in',
                     'X-Title': 'VaakSetu 1092 Helpline'},
            json={'model': model, 'messages': messages,
                  'temperature': 0.3, 'max_tokens': 500},
            timeout=30)
        if resp.status_code == 429:
            logger.warning('OpenRouter 429 — rate limited')
            return _THROTTLED
        resp.raise_for_status()
        raw = resp.json()['choices'][0]['message']['content']
        return _parse_llm_response(raw, citizen_language)
    except requests.exceptions.HTTPError as e:
        logger.error(f'OpenRouter error: {e}')
        return None
    except Exception as e:
        logger.error(f'OpenRouter error: {e}')
        return None


# ── Keyword Fallback ──────────────────────────────────────────────────────────

def _keyword_fallback(text_en: str, citizen_language: str) -> dict:
    """
    Match intent using keyword patterns from intent_patterns.json.
    BUG-20 FIX: Also checks native language keywords.
    BUG-19 FIX: Loads department_routing.json for location-aware overrides.
    B4  FIX: Normalizes department to canonical code.
    """
    try:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')

        patterns_path = os.path.join(data_dir, 'intent_patterns.json')
        with open(patterns_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        intents    = data.get('intents', {})
        dept_map   = data.get('intent_department_map', {})
        text_lower = text_en.lower()

        best_intent = 'unclear'
        best_score  = 0

        for intent_name, intent_data in intents.items():
            en_kw       = intent_data.get('keywords', {}).get('en', [])
            native_kw   = intent_data.get('keywords', {}).get(citizen_language, [])
            manglish_kw = intent_data.get('keywords', {}).get('manglish', [])
            all_kw      = en_kw + native_kw + manglish_kw
            hits = sum(1 for kw in all_kw if kw.lower() in text_lower)
            if hits > best_score:
                best_score  = hits
                best_intent = intent_name

        confidence = min(best_score * 0.2, 0.7) if best_score > 0 else 0.0
        dept = _normalize_department(dept_map.get(best_intent))   # B4 FIX

        # BUG-19: Location overrides
        try:
            routing_path = os.path.join(data_dir, 'department_routing.json')
            with open(routing_path, 'r', encoding='utf-8') as f:
                routing = json.load(f)

            for location, override_data in routing.get('location_overrides', {}).items():
                if location.lower() in text_lower:
                    intent_overrides = override_data.get('intents', {})
                    if best_intent in intent_overrides:
                        dept = _normalize_department(intent_overrides[best_intent])
                        logger.info(f'Location override: {location} → {dept}')
                        break
        except Exception as e:
            logger.warning(f'Could not load department_routing.json: {e}')

        nlu_intent = _map_keyword_intent(best_intent)

        # Generate template restatement so verification loop works even when
        # LLM backends are rate-limited.  Uses the language-specific prefix
        # from LANG_CONFIG with the English translation as content.
        lang_cfg = LANG_CONFIG.get(citizen_language, LANG_CONFIG['en'])
        restatement = f"{lang_cfg['prefix']} {text_en.strip()}" if text_en.strip() else ''

        return {
            'intent': nlu_intent,
            'entities': {'department': dept},
            'confidence': confidence,
            'restatement': restatement,
            'routing_suggestion': dept,
            'requires_escalation': confidence < 0.3 or nlu_intent in ('seek_safety', 'emergency'),
            'raw_llm_response': f'keyword_fallback:{best_intent}',
        }
    except Exception as e:
        logger.error(f'Keyword fallback error: {e}')
        return _fallback_result('unclear', 0.0, citizen_language)


def _map_keyword_intent(keyword_intent: str) -> str:
    """
    Map detailed keyword intents to NLU categories.
    BUG-09 FIX: Added property_tax.
    BUG-36 FIX: escalate_complaint is its own category.
    B5/B9 FIX: seek_safety and fire_emergency added.
    """
    emergency = {'medical_emergency', 'fire_emergency'}
    report    = {'water_supply_complaint', 'power_outage', 'road_damage',
                 'sanitation_issue', 'police_complaint', 'street_light',
                 'garbage_complaint'}
    info      = {'general_inquiry', 'pension_inquiry', 'ration_card',
                 'birth_death_certificate', 'land_records', 'property_tax'}
    escalate  = {'escalate_complaint'}
    safety    = {'seek_safety'}

    if keyword_intent in emergency:
        return 'emergency'
    if keyword_intent in safety:
        return 'seek_safety'
    if keyword_intent in report:
        return 'report_issue'
    if keyword_intent in info:
        return 'seek_information'
    if keyword_intent in escalate:
        return 'escalate_complaint'
    return 'unclear'


def _fallback_result(intent, confidence, lang):
    return {
        'intent': intent,
        'entities': {},
        'confidence': confidence,
        'restatement': '',
        'routing_suggestion': None,
        'requires_escalation': confidence < 0.3,
        'raw_llm_response': 'fallback',
    }
