/**
 * SpeechInput.js — Browser mic component for VaakSetu demo
 * ─────────────────────────────────────────────────────────────────────────
 * Adapted from SMILE_AGAIN's Speech_Input.js.
 *
 * Changes from original:
 *  - Removed mental health prompts
 *  - Replaced bot handler with POST /api/call/turn
 *  - Added language selector for kn-IN, hi-IN, en-IN
 *  - Added WebSocket listener for TTS audio playback
 *  - Added Confirm/Reject buttons for verification loop
 *  - Pure vanilla JS (no React imports — works with CDN)
 */

const SUPPORTED_LANGUAGES = {
  kn: { name: 'ಕನ್ನಡ',   code: 'kn-IN' },
  hi: { name: 'हिंदी',    code: 'hi-IN' },
  en: { name: 'English',  code: 'en-IN' },
  te: { name: 'తెలుగు',   code: 'te-IN' },
  ta: { name: 'தமிழ்',    code: 'ta-IN' },
};

const API_BASE = window.API_BASE || 'http://localhost:5000';

class VaakSetuSpeechInput {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.callId = null;
    this.language = 'kn';
    this.isListening = false;
    this.recognition = null;
    this.stopTimeout = null;
    this.STOP_DELAY_MS = 8000;
    this._lastFinalTranscript = null;  // BUG-25: track last complete utterance

    this._initUI();
    this._checkSpeechSupport();
  }

  // ── UI Setup ─────────────────────────────────────────────────────

  _initUI() {
    this.container.innerHTML = `
      <div class="speech-panel">
        <h2>🎙️ Citizen Voice Input</h2>

        <div class="speech-controls">
          <div class="lang-select-wrap">
            <label for="lang-select">Language:</label>
            <select id="lang-select">
              ${Object.entries(SUPPORTED_LANGUAGES).map(([k, v]) =>
                `<option value="${k}" ${k === this.language ? 'selected' : ''}>${v.name}</option>`
              ).join('')}
            </select>
          </div>

          <button id="btn-start-call" class="btn btn-green">Start Call</button>
          <button id="btn-mic" class="btn btn-mic" disabled>🎤 Tap to Speak</button>
          <button id="btn-end-call" class="btn btn-red" disabled>End Call</button>
        </div>

        <div id="speech-status" class="speech-status">Ready</div>

        <div id="transcript-area" class="transcript-area">
          <div class="transcript-placeholder">Speak to see transcript here...</div>
        </div>

        <div id="restatement-area" class="restatement-area" style="display:none">
          <h3>AI understood:</h3>
          <p id="restatement-text"></p>
          <div id="confidence-bar" class="confidence-bar">
            <div id="confidence-fill" class="confidence-fill"></div>
            <span id="confidence-label">0%</span>
          </div>
          <div class="verify-buttons">
            <button id="btn-confirm" class="btn btn-green">✓ ಹೌದು (Yes)</button>
            <button id="btn-reject" class="btn btn-red">✗ ಅಲ್ಲ (No)</button>
          </div>
        </div>

        <div id="routing-result" class="routing-result" style="display:none">
          <h3>✅ Routed to:</h3>
          <p id="dept-name"></p>
        </div>

        <div id="text-input-area" class="text-input-area">
          <input type="text" id="text-input" placeholder="Or type here...">
          <button id="btn-send-text" class="btn btn-blue">Send</button>
        </div>
      </div>
    `;

    // Bind events
    this.container.querySelector('#lang-select').addEventListener(
      'change', (e) => { this.language = e.target.value; });
    this.container.querySelector('#btn-start-call').addEventListener(
      'click', () => this.startCall());
    this.container.querySelector('#btn-mic').addEventListener(
      'click', () => this.toggleListening());
    this.container.querySelector('#btn-end-call').addEventListener(
      'click', () => this.endCall());
    this.container.querySelector('#btn-confirm').addEventListener(
      'click', () => this.onConfirm());
    this.container.querySelector('#btn-reject').addEventListener(
      'click', () => this.onReject());
    this.container.querySelector('#btn-send-text').addEventListener(
      'click', () => this.sendText());
    this.container.querySelector('#text-input').addEventListener(
      'keydown', (e) => { if (e.key === 'Enter') this.sendText(); });
  }

  _checkSpeechSupport() {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
      this._setStatus('⚠ Speech not supported in this browser. Use text input.', 'warn');
    }
  }

  _setStatus(msg, level = 'info') {
    const el = this.container.querySelector('#speech-status');
    el.textContent = msg;
    el.className = `speech-status status-${level}`;
  }

  // ── Call Lifecycle ────────────────────────────────────────────────

  async startCall() {
    try {
      const res = await fetch(`${API_BASE}/api/call/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language_hint: this.language }),
      });
      const data = await res.json();
      this.callId = data.call_id;
      this._setStatus(`Call started: ${this.callId}`, 'success');

      this.container.querySelector('#btn-start-call').disabled = true;
      this.container.querySelector('#btn-mic').disabled = false;
      this.container.querySelector('#btn-end-call').disabled = false;
    } catch (err) {
      this._setStatus(`Error starting call: ${err.message}`, 'error');
    }
  }

  async endCall() {
    if (!this.callId) return;
    try {
      await fetch(`${API_BASE}/api/call/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_id: this.callId }),
      });
      this._setStatus('Call ended', 'info');
      this.callId = null;
      this.container.querySelector('#btn-start-call').disabled = false;
      this.container.querySelector('#btn-mic').disabled = true;
      this.container.querySelector('#btn-end-call').disabled = true;
      this.container.querySelector('#restatement-area').style.display = 'none';
    } catch (err) {
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  // ── Speech Recognition ────────────────────────────────────────────

  toggleListening() {
    if (this.isListening) {
      this.stopListening();
    } else {
      this.startListening();
    }
  }

  startListening() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      this._setStatus('Speech not supported', 'error');
      return;
    }

    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.lang = SUPPORTED_LANGUAGES[this.language]?.code || 'kn-IN';

    this.recognition.onstart = () => {
      this.isListening = true;
      this._setStatus('🔴 Listening...', 'listening');
      this.container.querySelector('#btn-mic').textContent = '⏹ Stop';
      this.container.querySelector('#btn-mic').classList.add('listening');
    };

    this.recognition.onresult = (event) => {
      if (this.stopTimeout) clearTimeout(this.stopTimeout);
      const last = event.results[event.results.length - 1];
      const transcript = last[0].transcript;
      this._showTranscript(transcript, !last.isFinal);

      if (last.isFinal) {
        // BUG-25: Store the final transcript so stopListening() can send it
        // if the user clicks Stop before the 8-second timer fires.
        this._lastFinalTranscript = transcript;
        this.stopTimeout = setTimeout(() => {
          this._lastFinalTranscript = null;
          this.stopListening();
          this._sendTurn(transcript);
        }, this.STOP_DELAY_MS);
      }
    };

    this.recognition.onend = () => {
      this.isListening = false;
      this.container.querySelector('#btn-mic').textContent = '🎤 Tap to Speak';
      this.container.querySelector('#btn-mic').classList.remove('listening');
    };

    this.recognition.onerror = (e) => {
      this._setStatus(`Mic error: ${e.error}`, 'error');
      this.isListening = false;
    };

    try {
      this.recognition.start();
    } catch (err) {
      this._setStatus('Error starting mic', 'error');
    }
  }

  stopListening() {
    // BUG-25: If user clicks Stop while final transcript is pending,
    // send it immediately so the utterance is not silently dropped.
    if (this._lastFinalTranscript) {
      const toSend = this._lastFinalTranscript;
      this._lastFinalTranscript = null;
      if (this.stopTimeout) {
        clearTimeout(this.stopTimeout);
        this.stopTimeout = null;
      }
      if (this.recognition) this.recognition.stop();
      this.isListening = false;
      this._setStatus('Processing...', 'info');
      this._sendTurn(toSend);
      return;
    }
    if (this.recognition) {
      this.recognition.stop();
    }
    if (this.stopTimeout) {
      clearTimeout(this.stopTimeout);
    }
    this.isListening = false;
    this._setStatus('Processing...', 'info');
  }

  // ── Send turn to backend ──────────────────────────────────────────

  async sendText() {
    const input = this.container.querySelector('#text-input');
    const text = input.value.trim();
    if (!text || !this.callId) return;
    input.value = '';
    this._showTranscript(text, false);
    await this._sendTurn(text);
  }

  async _sendTurn(text) {
    if (!this.callId) return;
    this._setStatus('🔄 Processing...', 'info');

    try {
      const res = await fetch(`${API_BASE}/api/call/turn`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          call_id: this.callId,
          text: text,
          language_hint: this.language,
        }),
      });
      const data = await res.json();

      if (data.escalate) {
        this._setStatus(`⚠️ Escalated: ${data.reason}`, 'error');
        if (data.restatement_audio_b64) {
          this._playAudio(data.restatement_audio_b64);
        }
        return;
      }

      // Show restatement
      this._showRestatement(data);
      this._setStatus(`Intent: ${data.intent} | Confidence: ${Math.round((data.confidence||0)*100)}%`, 'success');

      // Play TTS
      if (data.restatement_audio_b64) {
        this._playAudio(data.restatement_audio_b64);
      }
    } catch (err) {
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  // ── Verification ──────────────────────────────────────────────────

  async onConfirm() {
    if (!this.callId) return;
    try {
      const res = await fetch(`${API_BASE}/api/call/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_id: this.callId, response: 'confirmed' }),
      });
      const data = await res.json();
      this.container.querySelector('#restatement-area').style.display = 'none';

      if (data.department) {
        this.container.querySelector('#routing-result').style.display = 'block';
        this.container.querySelector('#dept-name').textContent = data.department;
        this._setStatus(`✅ Verified & routed to ${data.department}`, 'success');
      }
    } catch (err) {
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  async onReject() {
    if (!this.callId) return;
    try {
      const res = await fetch(`${API_BASE}/api/call/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_id: this.callId, response: 'rejected' }),
      });
      const data = await res.json();
      this.container.querySelector('#restatement-area').style.display = 'none';

      if (data.next_action === 'escalate') {
        this._setStatus('⚠️ Escalated to human agent', 'error');
      } else {
        this._setStatus('Please try again — speak your complaint', 'info');
      }
    } catch (err) {
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  // ── UI Helpers ────────────────────────────────────────────────────

  _showTranscript(text, isInterim) {
    const area = this.container.querySelector('#transcript-area');
    area.innerHTML = `<div class="transcript ${isInterim ? 'interim' : 'final'}">${text}</div>`;
  }

  _showRestatement(data) {
    const area = this.container.querySelector('#restatement-area');
    area.style.display = 'block';
    this.container.querySelector('#restatement-text').textContent =
      data.restatement_text || 'Processing...';
    const pct = Math.round((data.confidence || 0) * 100);
    this.container.querySelector('#confidence-fill').style.width = `${pct}%`;
    this.container.querySelector('#confidence-label').textContent = `${pct}%`;
    this.container.querySelector('#routing-result').style.display = 'none';
  }

  _playAudio(base64Audio) {
    try {
      const audio = new Audio(`data:audio/mp3;base64,${base64Audio}`);
      audio.play().catch(e => console.warn('Audio play blocked:', e));
    } catch (e) {
      console.warn('Audio playback error:', e);
    }
  }
}

// Export for use
window.VaakSetuSpeechInput = VaakSetuSpeechInput;
