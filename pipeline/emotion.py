"""
pipeline/emotion.py
───────────────────────────────────────────────────────────────────────────────
Emotion scoring engine for VaakSetu — adapted from SMILE_AGAIN.

Scores text across 5 dimensions: distress, urgency, anger, fear, confusion.
Uses keyword matching with co-occurrence boosting and length normalisation.
Zero API calls — pure local computation, real-time safe.

Data dependency: data/emotion_keywords.json

B3 FIX: Polite-suffix filtering — words like 'help', 'fast', 'urgent', 'sir',
        'madam' are now treated as genuine signals ONLY when they co-occur with
        other real distress/urgency content.  A query containing ONLY these
        polite suffixes no longer triggers escalation.
        Implementation: after scoring, if the only non-zero hits are from the
        'polite_suffixes' exclusion list and there are no other hits, reduce
        the raw score by 70% before applying thresholds.

B6 FIX: Recalibrated per-emotion thresholds:
        - distress: 0.75 → 0.70  (catch "house on fire help")
        - anger:    0.80 → 0.60  (catch "4 times nobody listening" frustration)
        - fear:     0.65 → 0.60  (catch moderate fear signals)
        emotion_keywords.json already had 'help' removed from distress (B3),
        and frustration phrases added to anger (B6) — see that file.
        Escalation now also fires on anger so chronic complainants are handled.
"""

import json
import re
import os
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# B3 FIX: Words that are polite suffixes / fillers and should NOT boost emotion
# scores on their own.  If these are the ONLY hits for distress/urgency, we
# dampen the score rather than escalating.
_POLITE_SUFFIXES = frozenset({
    'help', 'please', 'sir', 'madam', 'fast', 'quick', 'quickly',
    'soon', 'kindly', 'request', 'pls', 'plz', 'asap',
})


class EmotionEngine:
    """Keyword-based emotion scorer for citizen complaints."""

    def __init__(self, keywords_path: str = None):
        if keywords_path is None:
            keywords_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                'data', 'emotion_keywords.json'
            )
        self.keywords = {}
        # B6 FIX: Recalibrated thresholds
        self.thresholds = {
            'distress':  0.70,   # was 0.75 — catches "house on fire help"
            'urgency':   0.60,
            'anger':     0.60,   # was 0.80 — catches repeat-complaint frustration
            'fear':      0.60,   # was 0.65
            'confusion': 0.90,
        }
        self._load_keywords(keywords_path)
        self._build_regex()

    def _load_keywords(self, path: str):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            self.keywords = {k: v for k, v in raw.items()
                             if not k.startswith('_')}
        except FileNotFoundError:
            logger.error(f'Emotion keywords not found: {path}')
            self.keywords = {}
        except Exception as e:
            logger.error(f'Error loading emotion keywords: {e}')
            self.keywords = {}

    def _build_regex(self):
        """Pre-compile regex patterns per emotion per language."""
        self.patterns = {}
        for emotion, lang_dict in self.keywords.items():
            self.patterns[emotion] = {}
            for lang, words in lang_dict.items():
                if words:
                    escaped = [re.escape(w) for w in words]
                    pattern = re.compile(
                        '(' + '|'.join(escaped) + ')',
                        re.IGNORECASE | re.UNICODE
                    )
                    self.patterns[emotion][lang] = pattern

    def _count_hits(self, text: str, emotion: str, language: str) -> Tuple[int, int]:
        """
        Return (total_hits, polite_only_hits) for an emotion in the given text.

        polite_only_hits counts how many of the English hits are ONLY from
        polite suffix words — used by the B3 dampening logic.
        """
        hits = 0
        polite_hits = 0

        # Target language
        pattern = self.patterns.get(emotion, {}).get(language)
        if pattern:
            hits += len(pattern.findall(text))

        # English (code-mixing)
        if language != 'en':
            en_pattern = self.patterns.get(emotion, {}).get('en')
            if en_pattern:
                en_matches = en_pattern.findall(text)
                hits += len(en_matches)
                # Count how many en matches are polite-suffix-only
                polite_hits += sum(
                    1 for m in en_matches
                    if m.lower().strip() in _POLITE_SUFFIXES
                )

        # Manglish
        manglish_pattern = self.patterns.get(emotion, {}).get('manglish')
        if manglish_pattern:
            hits += len(manglish_pattern.findall(text))

        return hits, polite_hits

    def score(self, text: str, language: str = 'kn') -> dict:
        """
        Score text across all emotion dimensions.

        Returns:
            {
                'distress': 0.0–1.0,
                'urgency': 0.0–1.0,
                'anger': 0.0–1.0,
                'fear': 0.0–1.0,
                'confusion': 0.0–1.0,
                'dominant_emotion': str,
                'escalate': bool,
            }
        """
        if not text or not text.strip():
            return self._neutral_scores()

        scores = {}
        word_count = max(len(text.split()), 1)

        for emotion in ['distress', 'urgency', 'anger', 'fear', 'confusion']:
            hits, polite_hits = self._count_hits(text, emotion, language)

            # Normalize by text length, cap at 1.0
            raw_score = min(hits / max(word_count * 0.3, 1), 1.0)

            # B3 FIX: Dampen score when ALL hits are from polite suffix words.
            # e.g. "where is the hospital pls" → hits=1 ('pls'), polite_hits=1
            # → this is not genuine distress, reduce score by 70%.
            if hits > 0 and polite_hits == hits:
                raw_score *= 0.30

            # Co-occurrence boost for multiple genuine hits
            genuine_hits = hits - polite_hits
            if genuine_hits >= 3:
                raw_score = min(raw_score * 1.3, 1.0)
            elif genuine_hits >= 2:
                raw_score = min(raw_score * 1.15, 1.0)

            scores[emotion] = round(raw_score, 3)

        # Find dominant
        dominant = max(scores, key=scores.get)
        if scores[dominant] < 0.05:
            dominant = 'neutral'

        # Escalation check — B6 FIX: anger now included; CRIT-6 FIX: confusion included
        escalate = (
            scores['distress'] >= self.thresholds['distress'] or
            scores['urgency']  >= self.thresholds['urgency']  or
            scores['fear']     >= self.thresholds['fear']     or
            scores['anger']    >= self.thresholds['anger']    or
            scores['confusion'] >= self.thresholds['confusion']
        )

        return {
            **scores,
            'dominant_emotion': dominant,
            'escalate': escalate,
        }

    def should_escalate(self, scores: dict) -> Tuple[bool, str | None]:
        """
        Determine if scores warrant escalation.
        Returns (True, reason) or (False, None).
        """
        if scores.get('distress', 0) >= self.thresholds['distress']:
            return True, 'distress'
        if scores.get('urgency', 0) >= self.thresholds['urgency']:
            return True, 'urgency'
        if scores.get('fear', 0) >= self.thresholds['fear']:
            return True, 'fear'
        if scores.get('anger', 0) >= self.thresholds['anger']:
            return True, 'anger'
        # CRIT-6 FIX: confusion is a valid escalation trigger
        if scores.get('confusion', 0) >= self.thresholds['confusion']:
            return True, 'confusion'
        return False, None

    def _neutral_scores(self) -> dict:
        return {
            'distress': 0.0, 'urgency': 0.0,
            'anger': 0.0,    'fear': 0.0,
            'confusion': 0.0,
            'dominant_emotion': 'neutral',
            'escalate': False,
        }


# ── Module-level singleton ────────────────────────────────────────
_engine = None

def get_engine() -> EmotionEngine:
    """Get or create the singleton EmotionEngine instance."""
    global _engine
    if _engine is None:
        _engine = EmotionEngine()
    return _engine

def score_text(text: str, language: str = 'kn') -> dict:
    """Convenience function — scores text using singleton engine."""
    return get_engine().score(text, language)
