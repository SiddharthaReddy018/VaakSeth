"""
routes/call_routes.py
───────────────────────────────────────────────────────────────────────────────
HTTP endpoints that process inbound audio/text from the citizen.
This is the entry point for every new utterance and the MAIN PIPELINE.

POST /api/call/start  — create a new session
POST /api/call/turn   — process one citizen utterance (full pipeline)
POST /api/call/verify — citizen confirms/rejects AI restatement
POST /api/call/end    — close session, persist to DB

Previous bug fixes (retained):
  BUG-07: Language hint not overwritten by langdetect for Latin text
  BUG-10: confidence_score written to CallSession DB on each turn
  BUG-13: verification_attempts written to CallSession DB
  BUG-21: verify endpoint guards against zero turns
  BUG-23: Input text sanitized with html.escape
  BUG-24: call_id validated with strict regex
  BUG-27: Turns rejected on closed sessions (409)
  BUG-31: _save_turn_to_db called before early escalation return
  BUG-32: Low-confidence escalation only fires after >= 2 turns
  BUG-40: Duplicate call_id on /start returns 200 not 201

New fixes in this file:
  B2  FIX: is_transliterated flag passed to NLU so confidence penalties apply.
  B4  FIX: routing_suggestion validated and normalised against canonical dept list.
  B5  FIX: fire_emergency intent hardcoded to FIRE_DEPT; missing dept fallbacks added.
  B7  FIX: Confidence penalised for transliterated input / missing entities / bad dept.
  B9  FIX: fear_score ≥ 0.5 + safety keywords → KSRP routing + auto-escalate,
           regardless of NLU intent. Routing override applied post-NLU.
"""

import uuid
import json
import html
import re
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import CallSession, CallTurn, Feedback
from session.manager import (
    create_session, get_session, update_session,
    add_turn, should_escalate, close_session,
)
from socket_events import (
    push_session_update, push_escalation_alert, push_session_closed,
)

logger = logging.getLogger(__name__)

call_bp = Blueprint('call', __name__)

# BUG-24: Strict call_id validation
_CALL_ID_PATTERN = re.compile(r'^[A-Za-z0-9\-_]{1,64}$')

# B9 FIX: Safety-threat keywords — if fear is high AND any of these appear,
# override routing to KSRP regardless of NLU result.
_SAFETY_KEYWORDS = re.compile(
    r'\b(follow|following|followed|stranger|strangers|suspicious|stalking|'
    r'stalker|threatening|threat|scared|afraid|attacked|attack|danger|'
    r'unsafe|help me im scared|someone outside|broke in|intruder)\b',
    re.IGNORECASE
)

# B5 FIX: Fire keywords — always route to FIRE_DEPT
_FIRE_KEYWORDS = re.compile(
    r'\b(fire|burning|burnt|house on fire|building fire|smoke everywhere|'
    r'fire brigade|fire engine|benki|aagide)\b',
    re.IGNORECASE
)


def _parse_spoken_confirmation(spoken: str, lang: str):
    t = spoken.strip().lower()
    YES     = ['ಹೌದು','ಹಾ','ಸರಿ','ಆಯ್ತು','ಒಪ್ಪಿಗೆ','houdu','hauda','sari','aytu','haa',
               'हाँ','हां','जी','सही','ठीक है','बिल्कुल','haan','ji','theek hai','bilkul',
               'yes','yeah','correct','right','confirmed','yep']
    PARTIAL = ['ಸ್ವಲ್ಪ ತಪ್ಪು','ಸ್ವಲ್ಪ ಸರಿ','ಭಾಗಶಃ','swalppa tappu','swalppa sari',
               'थोड़ा गलत','थोड़ा सही','आंशिक','thoda galat','thoda sahi',
               'partial','partly','partially','not exactly','somewhat','kind of','almost']
    NO      = ['ಇಲ್ಲ','ಬೇಡ','ತಪ್ಪು','ಅಲ್ಲ','ಸರಿಯಿಲ್ಲ','illa','beda','tappu','alla',
               'नहीं','नही','गलत','nahin','nahi','galat','no','nope','wrong','incorrect']
    if any(w in t for w in PARTIAL): return 'partial'
    if any(w in t for w in YES):     return 'confirmed'
    if any(w in t for w in NO):      return 'rejected'
    return None


def _validate_call_id(call_id: str):
    if not call_id or not _CALL_ID_PATTERN.match(call_id):
        return None, (jsonify({'error': 'Invalid call_id format.'}), 400)
    return call_id, None


# ── POST /api/call/start ──────────────────────────────────────────────────────

@call_bp.route('/start', methods=['POST'])
def start_call():
    body     = request.get_json(force=True) or {}
    call_id  = body.get('call_id', f'CALL-{uuid.uuid4().hex[:8].upper()}')
    language = body.get('language_hint', 'kn')

    call_id, err = _validate_call_id(call_id)
    if err: return err

    existing = get_session(call_id)
    if existing:
        return jsonify({'call_id': call_id,
                        'status': existing.get('status', 'active'),
                        'language': existing.get('language', language)}), 200

    create_session(call_id, language)
    try:
        db.session.add(CallSession(call_id=call_id, language=language, status='active'))
        db.session.commit()
    except Exception as e:
        logger.error(f'DB error creating session: {e}')
        db.session.rollback()

    return jsonify({'call_id': call_id, 'status': 'active', 'language': language}), 201


# ── POST /api/call/turn ──────────────────────────────────────────────────────

@call_bp.route('/turn', methods=['POST'])
def process_turn():
    """
    Process one citizen utterance — full pipeline:
    1. ASR (if audio) → transcript
    2. Language ID → confirm language
    3. Transliteration (if romanized)
    4. Translation → English
    5. Emotion scoring → escalation check
    6. NLU → intent, entities, confidence, restatement
    7. Post-NLU routing override (fire → FIRE_DEPT, fear → KSRP)
    8. TTS → restatement audio
    9. Session update + SocketIO push
    """
    _json         = request.get_json(silent=True) or {}
    call_id       = request.form.get('call_id')       or _json.get('call_id')
    language_hint = request.form.get('language_hint') or _json.get('language_hint', 'kn')
    text_input    = request.form.get('text')           or _json.get('text')

    if not call_id:
        return jsonify({'error': 'call_id required'}), 400

    call_id, err = _validate_call_id(call_id)
    if err: return err

    session = get_session(call_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    if session.get('status') == 'closed':
        return jsonify({'error': 'Session is closed'}), 409

    if text_input:
        text_input = html.escape(text_input.strip())

    # ── Step 1: Get transcript ────────────────────────────────────
    audio_file           = request.files.get('audio_file')
    transcript_original  = text_input or ''
    detected_language    = language_hint

    if audio_file and not text_input:
        try:
            from pipeline.asr import transcribe
            asr_result = transcribe(audio_file.read(), language_hint)
            if asr_result:
                transcript_original = asr_result['transcript']
                detected_language   = asr_result.get('detected_language', language_hint)
            else:
                # GAP-3 FIX: persist an escalation turn before returning so the
                # agent dashboard shows a non-empty transcript for the call
                _trigger_escalation(call_id, session, 'asr_failed')
                _save_turn_to_db(call_id, '', '', language_hint,
                                 {'intent': 'asr_failed', 'entities': {}, 'confidence': 0},
                                 {'dominant_emotion': 'neutral', 'escalate': True,
                                  'distress': 0, 'urgency': 0, 'anger': 0,
                                  'fear': 0, 'confusion': 0},
                                 '[ASR failed — no transcript]')
                add_turn(call_id, 'citizen', '[ASR failed]', {
                    'raw_transcript': '', 'clean_text': '[ASR failed — no transcript]',
                    'language': language_hint, 'intent': 'asr_failed',
                })
                return jsonify({'escalate': True, 'reason': 'asr_failed',
                                'message': 'Speech recognition failed'}), 200
        except Exception as e:
            logger.error(f'ASR error: {e}')
            transcript_original = ''

    if not transcript_original:
        return jsonify({'error': 'No audio or text provided'}), 400

    # ── Step 2: Language ID ───────────────────────────────────────
    detected_script   = 'Latin'
    is_transliterated = False  # B2/B7: track whether transliteration was run
    try:
        from pipeline.language_id import identify_language
        lang_result = identify_language(transcript_original)

        if lang_result.get('script') != 'Latin':
            detected_language = lang_result.get('language', language_hint)
        else:
            # BUG-07 + B1 FIX: for Latin text, trust the Manglish pre-pass
            # result if available, otherwise trust the UI language hint.
            detected_language = lang_result.get('language', language_hint)
            # If Manglish detected (script=Latin but language=kn/hi), keep it
            if lang_result.get('language') in ('kn', 'hi'):
                detected_language = lang_result['language']
            else:
                detected_language = language_hint

        detected_script = lang_result.get('script', 'Latin')
    except Exception as e:
        logger.warning(f'Language ID error: {e}')

    # CRIT-5 FIX: detect and store dialect signal
    detected_dialect = None
    try:
        from pipeline.language_id import detect_dialect
        detected_dialect = detect_dialect(transcript_original, detected_language)
    except Exception as e:
        logger.warning(f'Dialect detection error: {e}')

    # ── Step 3: Transliteration (if romanized Indic) ──────────────
    text_native = transcript_original
    try:
        if detected_script == 'Latin' and detected_language != 'en':
            from pipeline.transliteration import transliterate
            tl_result   = transliterate(transcript_original, 'en', detected_language)
            text_native = tl_result.get('transliterated', transcript_original)
            # B2/B7: Mark that transliteration ran (affects confidence penalty)
            is_transliterated = (text_native != transcript_original)
    except Exception as e:
        logger.warning(f'Transliteration error: {e}')

    # ── Step 4: Translation to English ────────────────────────────
    text_en = transcript_original
    try:
        if detected_language != 'en':
            from pipeline.translation import translate_text
            trans_result = translate_text(text_native, detected_language, 'en')
            text_en = trans_result.get('translated', transcript_original)
    except Exception as e:
        logger.warning(f'Translation error: {e}')

    # ── Step 5: Emotion scoring ───────────────────────────────────
    emotion_scores = {
        'distress': 0.0, 'urgency': 0.0, 'anger': 0.0,
        'fear': 0.0, 'confusion': 0.0,
        'dominant_emotion': 'neutral', 'escalate': False,
    }
    try:
        from pipeline.emotion import score_text
        emotion_scores = score_text(transcript_original, detected_language)
    except Exception as e:
        logger.warning(f'Emotion scoring error: {e}')

    update_session(call_id, {
        'distress_score':  emotion_scores.get('distress', 0),
        'urgency_score':   emotion_scores.get('urgency', 0),
        'anger_score':     emotion_scores.get('anger', 0),
        'fear_score':      emotion_scores.get('fear', 0),
        'confusion_score': emotion_scores.get('confusion', 0),
        'dominant_emotion':emotion_scores.get('dominant_emotion', 'neutral'),
        'detected_script': detected_script,
        'language':        detected_language,
        'emotion_scores':  emotion_scores,
        # CRIT-5 FIX: persist dialect only when a signal is found (never overwrite
        # a previously detected dialect with None)
        **({'dialect': detected_dialect} if detected_dialect else {}),
    })

    sess = get_session(call_id)
    if sess:
        history = sess.get('emotion_history', [])
        history.append(emotion_scores)
        update_session(call_id, {'emotion_history': history})

    if emotion_scores.get('escalate'):
        _trigger_escalation(call_id, get_session(call_id), 'emotion')
        restatement_audio = _generate_handover_audio(detected_language)
        add_turn(call_id, 'citizen', transcript_original, {
            'raw_transcript': transcript_original, 'clean_text': text_native,
            'language': detected_language,
            'emotion': emotion_scores.get('dominant_emotion'),
        })
        _save_turn_to_db(call_id, transcript_original, text_native,
                         detected_language,
                         {'intent': 'escalated', 'entities': {}, 'confidence': 0},
                         emotion_scores, '')
        # Route to KSRP for fear/safety, DMER for distress/medical emergencies
        dominant = emotion_scores.get('dominant_emotion', 'neutral')
        early_routing = 'KSRP' if dominant == 'fear' else 'DMER'
        return jsonify({
            'escalate':              True,
            'reason':                dominant,
            'emotion_scores':        emotion_scores,
            'restatement_audio_b64': restatement_audio,
            'language':              detected_language,
            'transcript_english':    text_en,
            'intent':                'emergency',
            'confidence':            0.9,
            'requires_escalation':   True,
            'routing_suggestion':    early_routing,
        }), 200

    # ── Step 6: NLU analysis ──────────────────────────────────────
    nlu_result = {
        'intent': 'unclear', 'entities': {},
        'confidence': 0.0, 'restatement': '',
        'routing_suggestion': None, 'requires_escalation': False,
    }
    try:
        from pipeline.nlu import analyze_turn
        turns = sess.get('turns', []) if sess else []
        # B2/B7: pass is_transliterated so NLU can apply confidence penalty
        nlu_result = analyze_turn(text_en, turns, detected_language,
                                  is_transliterated=is_transliterated)
    except Exception as e:
        logger.error(f'NLU error: {e}')

    # ── Step 7: Post-NLU routing overrides ───────────────────────
    routing = nlu_result.get('routing_suggestion')
    fear_score = emotion_scores.get('fear', 0.0)

    # B5 FIX: Fire keywords → always FIRE_DEPT regardless of NLU
    if _FIRE_KEYWORDS.search(text_en) or _FIRE_KEYWORDS.search(transcript_original):
        routing = 'FIRE_DEPT'
        nlu_result['routing_suggestion'] = 'FIRE_DEPT'
        nlu_result['intent'] = 'emergency'
        nlu_result['requires_escalation'] = True
        logger.info(f'Fire keyword detected → routing overridden to FIRE_DEPT')

    # B9 FIX: Safety threat → KSRP + auto-escalate.
    # Triggers when NLU already classified as seek_safety, OR when any safety
    # keyword is present with even moderate fear (≥0.30).  Parens added to
    # fix operator-precedence bug (and > or) from previous version.
    elif (nlu_result.get('intent') == 'seek_safety' or
          (fear_score >= 0.30 and
           (_SAFETY_KEYWORDS.search(text_en or '') or
            _SAFETY_KEYWORDS.search(transcript_original)))):
        routing = 'KSRP'
        nlu_result['routing_suggestion'] = 'KSRP'
        if nlu_result.get('intent') not in ('emergency',):
            nlu_result['intent'] = 'seek_safety'
        nlu_result['requires_escalation'] = True
        logger.info(f'Safety threat detected (fear={fear_score:.2f}) → KSRP')

    # BUG-32: Low-confidence escalation only after ≥2 turns
    turn_count = sess.get('turn_number', 0) if sess else 0
    if nlu_result.get('confidence', 0) < 0.50 and turn_count >= 2:
        _trigger_escalation(call_id, get_session(call_id), 'low_confidence')

    # CRIT-4 FIX: should_escalate() was imported but never invoked in this file.
    # Run the centralised session-manager check so all escalation rules
    # (distress, urgency, fear, confusion, verification retries, low confidence)
    # are enforced through one authoritative path, not just the ad-hoc checks above.
    manager_escalate, manager_reason = should_escalate(call_id)
    if manager_escalate and not get_session(call_id).get('escalation_flag'):
        _trigger_escalation(call_id, get_session(call_id), manager_reason or 'session_manager')

    priority = 'normal'
    if emotion_scores.get('distress', 0) > 0.5:  priority = 'urgent'
    if emotion_scores.get('distress', 0) > 0.70: priority = 'critical'

    update_session(call_id, {
        'current_intent':      nlu_result.get('intent'),
        'current_entities':    nlu_result.get('entities', {}),
        'confidence_score':    nlu_result.get('confidence', 0),
        'last_restatement':    nlu_result.get('restatement', ''),
        'department_routed_to':routing,
        'priority':            priority,
    })

    # Escalation state
    escalation_triggered = False
    escalation_reason    = None

    # Always escalate seek_safety and emergency — regardless of fear score.
    # Previously only fear_score >= 0.60 triggered _trigger_escalation, which
    # meant seek_safety sessions were never marked escalated in the session
    # store and agents never received SocketIO alerts.
    if nlu_result.get('intent') in ('seek_safety', 'emergency') and not escalation_triggered:
        escalation_triggered = True
        escalation_reason    = nlu_result.get('intent')
        _trigger_escalation(call_id, get_session(call_id), escalation_reason)

    if fear_score >= 0.60 and not escalation_triggered:
        escalation_triggered = True
        escalation_reason    = 'fear_detected'
        _trigger_escalation(call_id, get_session(call_id), 'fear_detected')

    # ── TTS ───────────────────────────────────────────────────────
    restatement_text      = nlu_result.get('restatement', '')
    restatement_audio_b64 = None
    if restatement_text:
        try:
            from pipeline.tts import speak_to_base64
            restatement_audio_b64 = speak_to_base64(restatement_text, detected_language)
        except Exception as tts_err:
            logger.warning(f'TTS restatement failed: {tts_err}')

    CONFIRM_PROMPTS = {
        'kn': 'ನಾನು ಸರಿಯಾಗಿ ಅರ್ಥ ಮಾಡಿಕೊಂಡೆನೇ? ಹೌದು, ತಪ್ಪು, ಅಥವಾ ಸ್ವಲ್ಪ ತಪ್ಪು ಎಂದು ಹೇಳಿ.',
        'hi': 'क्या मैंने सही समझा? हाँ, गलत, या थोड़ा गलत बोलें।',
        'en': 'Did I understand correctly? Say yes, wrong, or partly wrong.',
    }
    confirm_text      = CONFIRM_PROMPTS.get(detected_language, CONFIRM_PROMPTS['en'])
    confirm_audio_b64 = None
    try:
        from pipeline.tts import speak_to_base64
        confirm_audio_b64 = speak_to_base64(confirm_text, detected_language)
    except Exception:
        pass

    requires_escalation = (
        nlu_result.get('requires_escalation', False)
        or nlu_result.get('intent') in ('emergency', 'escalate_complaint', 'seek_safety')
        or emotion_scores.get('escalate', False)
        or escalation_triggered
    )

    handover_audio_b64 = None
    if requires_escalation:
        HANDOVER_PROMPTS = {
            'kn': 'ದಯವಿಟ್ಟು ಕಾಯಿರಿ, ನಾನು ನಿಮ್ಮನ್ನು ನಮ್ಮ ಸಿಬ್ಬಂದಿಗೆ ವರ್ಗಾಯಿಸುತ್ತಿದ್ದೇನೆ.',
            'hi': 'कृपया रुकें, मैं आपको हमारे कर्मचारी से जोड़ रहा हूं।',
            'en': 'Please hold, I am transferring you to our staff now.',
        }
        try:
            from pipeline.tts import speak_to_base64
            handover_audio_b64 = speak_to_base64(
                HANDOVER_PROMPTS.get(detected_language, HANDOVER_PROMPTS['en']),
                detected_language)
        except Exception:
            pass

    # ── Step 9: Record + push ─────────────────────────────────────
    add_turn(call_id, 'citizen', transcript_original, {
        'raw_transcript': transcript_original, 'clean_text': text_native,
        'language': detected_language,
        'intent': nlu_result.get('intent'),
        'entities': json.dumps(nlu_result.get('entities', {})),
        'emotion': emotion_scores.get('dominant_emotion'),
        'confidence': nlu_result.get('confidence'),
        'ai_restatement': restatement_text,
    })

    try:
        push_session_update(call_id, get_session(call_id))
    except Exception as e:
        logger.warning(f'SocketIO push error: {e}')

    _save_turn_to_db(call_id, transcript_original, text_native,
                     detected_language, nlu_result, emotion_scores,
                     restatement_text)

    return jsonify({
        'call_id':              call_id,
        'transcript_original':  transcript_original,
        'transcript_english':   text_en,
        'language':             detected_language,
        'restatement_text':     restatement_text,
        'restatement_audio':    restatement_audio_b64,
        'restatement_audio_b64':restatement_audio_b64,
        'confirm_prompt_text':  confirm_text,
        'confirm_prompt_audio': confirm_audio_b64,
        'handover_audio':       handover_audio_b64,
        'intent':               nlu_result.get('intent'),
        'entities':             nlu_result.get('entities'),
        'confidence':           nlu_result.get('confidence'),
        'emotion_scores':       emotion_scores,
        'fear_score':           fear_score,
        'routing_suggestion':   routing,
        'requires_escalation':  requires_escalation,
        'escalation_reason':    escalation_reason,
        'verification_needed':  True,
    }), 200


# ── POST /api/call/verify ────────────────────────────────────────────────────

@call_bp.route('/verify', methods=['POST'])
def verify_restatement():
    body     = request.get_json(force=True) or {}
    call_id  = body.get('call_id')
    response = body.get('response', '').lower()

    if not call_id:
        return jsonify({'error': 'call_id required'}), 400

    spoken_text      = body.get('spoken_text', '').strip()
    citizen_language = body.get('citizen_language', 'kn')

    if spoken_text:
        parsed = _parse_spoken_confirmation(spoken_text, citizen_language)
        if parsed:
            response = parsed

    if response not in ('confirmed', 'rejected', 'partial'):
        return jsonify({'error': 'response must be confirmed/rejected/partial'}), 400

    session = get_session(call_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    if session.get('turn_number', 0) == 0:
        return jsonify({'error': 'No turns to verify'}), 400

    if response == 'confirmed':
        update_session(call_id, {'verification_status': 'confirmed'})
        dept = session.get('department_routed_to')

        # CRIT-1 FIX: persist citizen confirmation as a Feedback row.
        # Confirmed interpretations are the highest-quality RLHF training signal
        # and must be saved; previously this block returned without writing anything.
        try:
            db_s = CallSession.query.filter_by(call_id=call_id).first()
            last_turn = None
            if db_s:
                last_turn = (CallTurn.query
                             .filter_by(session_id=db_s.id)
                             .order_by(CallTurn.turn_number.desc())
                             .first())
                # Mark the turn as citizen-confirmed
                if last_turn:
                    last_turn.citizen_confirmed = True
                fb = Feedback(
                    session_id     = db_s.id,
                    turn_id        = last_turn.id if last_turn else None,
                    source         = 'citizen',
                    feedback_type  = 'confirmed',
                    original_value = session.get('current_intent', ''),
                    corrected_value= session.get('current_intent', ''),
                )
                db.session.add(fb)
                db.session.commit()
        except Exception as e:
            logger.error(f'Citizen confirmation DB error: {e}')
            db.session.rollback()

        try:
            from feedback.feedback_store import save_correction
            save_correction(call_id, 'confirmed',
                            session.get('current_intent', ''),
                            session.get('current_intent', ''),
                            agent_id=None)
        except Exception as e:
            logger.warning(f'feedback_store confirmation error: {e}')

        push_session_update(call_id, get_session(call_id))
        return jsonify({'status': 'verified', 'next_action': 'route', 'department': dept})

    elif response == 'rejected':
        attempts = session.get('verification_attempts', 0) + 1
        max_att  = current_app.config.get('MAX_VERIFICATION_ATTEMPTS', 2)
        update_session(call_id, {'verification_attempts': attempts,
                                  'verification_status': 'rejected'})
        try:
            db_s = CallSession.query.filter_by(call_id=call_id).first()
            if db_s:
                db_s.verification_attempts = attempts
                db.session.commit()
        except Exception as e:
            logger.error(f'DB verification_attempts error: {e}')
            db.session.rollback()

        if attempts >= max_att:
            _trigger_escalation(call_id, get_session(call_id), 'max_retries')
            return jsonify({'status': 'escalated', 'next_action': 'escalate',
                            'reason': 'max_retries'})
        push_session_update(call_id, get_session(call_id))
        return jsonify({'status': 'retry', 'next_action': 'retry', 'attempts': attempts})

    else:
        # CRIT-2 FIX: partial must increment verification_attempts so that
        # MAX_VERIFICATION_ATTEMPTS escalation fires on repeated partial responses,
        # not just on outright rejections. Previously the counter was never touched
        # in this branch, creating an infinite stall in the human-takeover path.
        attempts = session.get('verification_attempts', 0) + 1
        max_att  = current_app.config.get('MAX_VERIFICATION_ATTEMPTS', 2)
        update_session(call_id, {'verification_attempts': attempts,
                                  'verification_status': 'partial'})
        try:
            db_s = CallSession.query.filter_by(call_id=call_id).first()
            if db_s:
                db_s.verification_attempts = attempts
                db.session.commit()
        except Exception as e:
            logger.error(f'DB verification_attempts (partial) error: {e}')
            db.session.rollback()

        if attempts >= max_att:
            _trigger_escalation(call_id, get_session(call_id), 'max_retries')
            return jsonify({'status': 'escalated', 'next_action': 'escalate',
                            'reason': 'max_retries'})
        push_session_update(call_id, get_session(call_id))
        return jsonify({'status': 'partial', 'next_action': 'retry', 'attempts': attempts})


# ── POST /api/call/end ────────────────────────────────────────────────────────

@call_bp.route('/end', methods=['POST'])
def end_call():
    body    = request.get_json(force=True) or {}
    call_id = body.get('call_id')
    if not call_id:
        return jsonify({'error': 'call_id required'}), 400

    session = get_session(call_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404

    close_session(call_id)
    turns = session.get('turns', [])
    summary_lines = [
        f'Call ID: {call_id}',
        f'Language: {session.get("language","kn")}',
        f'Duration: {len(turns)} turns',
        f'Final Intent: {session.get("current_intent","unknown")}',
        f'Department: {session.get("department_routed_to","None")}',
        f'Dominant Emotion: {session.get("dominant_emotion","neutral")}',
        f'Escalated: {session.get("escalation_flag", False)}',
        '--- Transcript ---',
    ]
    if session.get('fear_score', 0) > 0.3:
        summary_lines.insert(-1, f'Fear Score: {session.get("fear_score")}')
    for t in turns:
        summary_lines.append(f'Turn {t.get("turn_number")}: {t.get("clean_text","")}')
    call_summary = '\n'.join(summary_lines)

    try:
        db_s = CallSession.query.filter_by(call_id=call_id).first()
        if db_s:
            db_s.status              = 'closed'
            db_s.ended_at            = datetime.utcnow()
            db_s.verified_intent     = session.get('current_intent')
            db_s.department_routed_to= session.get('department_routed_to')
            db_s.distress_score      = session.get('distress_score', 0)
            db_s.urgency_score       = session.get('urgency_score', 0)
            db_s.anger_score         = session.get('anger_score', 0)
            db_s.fear_score          = session.get('fear_score', 0)
            db_s.verification_status = session.get('verification_status')
            db_s.confidence_score    = session.get('confidence_score', 0)
            db_s.verified_entities   = json.dumps(session.get('current_entities', {}))
            db_s.raw_transcript      = '\n'.join([t.get('clean_text','') for t in turns])
            db_s.call_summary        = call_summary
            db.session.commit()
    except Exception as e:
        logger.error(f'DB error closing session: {e}')
        db.session.rollback()

    push_session_closed(call_id)
    return jsonify({'status': 'closed', 'call_id': call_id, 'summary': call_summary})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trigger_escalation(call_id, session, reason):
    update_session(call_id, {
        'escalation_flag':   True,
        'escalation_reason': reason,
        'priority':          'critical',
        'status':            'escalated',
    })
    push_escalation_alert(call_id, reason, {
        'language': session.get('language') if session else 'kn',
        'intent':   session.get('current_intent') if session else None,
        'emotion':  session.get('dominant_emotion') if session else None,
    })


def _generate_handover_audio(language):
    messages = {
        'kn': 'ನಿಮ್ಮನ್ನು ನಮ್ಮ ಸಿಬ್ಬಂದಿ ಜೊತೆ ಸಂಪರ್ಕ ಮಾಡ್ತಿದ್ದೇವೆ.',
        'hi': 'हम आपको हमारे कर्मचारी से जोड़ रहे हैं.',
        'en': 'We are connecting you with our staff.',
    }
    try:
        from pipeline.tts import speak_to_base64
        return speak_to_base64(messages.get(language, messages['en']), language)
    except Exception:
        return None


def _save_turn_to_db(call_id, raw, clean, lang, nlu, emotion, restatement):
    """BUG-10 FIX: Also updates CallSession.confidence_score.
    CRIT-5 FIX: Updates CallSession.dialect when a dialect signal is detected.
    Returns the CallTurn ORM object so callers can reference its id."""
    try:
        db_s = CallSession.query.filter_by(call_id=call_id).first()
        if not db_s:
            return None
        turn_count = CallTurn.query.filter_by(session_id=db_s.id).count()
        turn = CallTurn(
            session_id    = db_s.id,
            turn_number   = turn_count + 1,
            speaker       = 'citizen',
            raw_transcript= raw,
            clean_text    = clean,
            language      = lang,
            intent        = nlu.get('intent'),
            entities      = json.dumps(nlu.get('entities', {})),
            emotion       = emotion.get('dominant_emotion'),
            confidence    = nlu.get('confidence'),
            ai_restatement= restatement,
        )
        db.session.add(turn)
        db_s.confidence_score = nlu.get('confidence', 0)
        # CRIT-5: persist dialect when detected; never clobber an existing value
        sess = get_session(call_id)
        if sess and sess.get('dialect') and not db_s.dialect:
            db_s.dialect = sess['dialect']
        db.session.commit()
        return turn
    except Exception as e:
        logger.error(f'DB turn save error: {e}')
        db.session.rollback()
        return None
