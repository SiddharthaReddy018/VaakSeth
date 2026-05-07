"""
feedback/feedback_store.py
───────────────────────────────────────────────────────────────────────────────
Captures agent corrections and stores them as labeled training data
for future model improvement (RLHF-style learning loop).

Every correction is a labeled data point. 200 corrections = a small
but real fine-tuning dataset.
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Flat-file log for easy export (in addition to DB)
FEEDBACK_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'data', 'feedback_log.jsonl'
)


def save_correction(call_id: str, feedback_type: str,
                    original_value: str, corrected_value: str,
                    agent_id: str = None) -> bool:
    """
    Save an agent correction for the learning loop.

    Args:
        call_id: Call session ID
        feedback_type: 'intent_wrong', 'entity_wrong',
                       'routing_wrong', 'emotion_wrong'
        original_value: What the AI predicted
        corrected_value: What the agent says is correct
        agent_id: Optional agent identifier

    Returns:
        True if saved successfully
    """
    record = {
        'call_id': call_id,
        'feedback_type': feedback_type,
        'original_value': original_value,
        'corrected_value': corrected_value,
        'agent_id': agent_id,
        'timestamp': datetime.utcnow().isoformat(),
    }

    # Append to JSONL file
    try:
        os.makedirs(os.path.dirname(FEEDBACK_LOG_PATH), exist_ok=True)
        with open(FEEDBACK_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        logger.info(f'Feedback saved: {feedback_type} for {call_id}')
        return True
    except Exception as e:
        logger.error(f'Error saving feedback: {e}')
        return False


def get_corrections_summary(since_date: str = None) -> dict:
    """
    Get aggregated stats on agent corrections.

    Args:
        since_date: ISO date string to filter from (optional)

    Returns:
        {
            'total': int,
            'by_type': { feedback_type: count },
            'most_common_wrong_intents': [...],
            'most_common_wrong_emotions': [...],
        }
    """
    try:
        if not os.path.exists(FEEDBACK_LOG_PATH):
            return {'total': 0, 'by_type': {}}

        records = []
        with open(FEEDBACK_LOG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Skipping invalid JSONL line: {line[:50]}")
                    continue

                if since_date and rec.get('timestamp', '') < since_date:
                    continue

                records.append(rec)
                
        by_type = {}
        wrong_intents = {}
        wrong_emotions = {}

        for rec in records:
            ft = rec.get('feedback_type', 'unknown')
            by_type[ft] = by_type.get(ft, 0) + 1

            if ft == 'intent_wrong':
                orig = rec.get('original_value', '')
                wrong_intents[orig] = wrong_intents.get(orig, 0) + 1
            elif ft == 'emotion_wrong':
                orig = rec.get('original_value', '')
                wrong_emotions[orig] = wrong_emotions.get(orig, 0) + 1

        return {
            'total': len(records),
            'by_type': by_type,
            'most_common_wrong_intents': sorted(
                wrong_intents.items(), key=lambda x: -x[1])[:5],
            'most_common_wrong_emotions': sorted(
                wrong_emotions.items(), key=lambda x: -x[1])[:5],
        }
    except Exception as e:
        logger.error(f'Error reading feedback summary: {e}')
        return {'total': 0, 'by_type': {}, 'error': str(e)}
