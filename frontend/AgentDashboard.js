/**
 * AgentDashboard.js
 * ─────────────────────────────────────────────────────────────────────────
 * Real-time agent dashboard for 1092 helpline operators.
 *
 * BUG-04 FIX: Rewritten in vanilla JS — no React imports, no JSX.
 *
 * Features:
 *  - Live call queue with emotion indicators (including FEAR)
 *  - Real-time intent / entity / confidence display via Socket.IO
 *  - Escalation alerts (auto-pop for high distress/fear calls)
 *  - Agent sentiment validation (emotion override dropdown)
 *  - Fear score display and purple badge
 *  - Editable restatement with "Save Correction" button
 *  - "Take Over Call" button for manual escalation
 *  - Last 3 turns shown as a simple timeline
 *
 * Dependencies: socket.io-client (CDN)
 */

const API_BASE   = window.API_BASE   || 'http://localhost:5000';
const SOCKET_URL = window.SOCKET_URL || window.location.origin;

// ── Emotion badge logic — check FEAR first ──────────────────────────────
function getEmotionBadge(scores) {
    if ((scores.fear || 0) >= 0.65)      return {label:'FEAR',          color:'#6b21a8'};
    if ((scores.distress || 0) >= 0.75)  return {label:'HIGH DISTRESS', color:'#dc2626'};
    if ((scores.urgency || 0) >= 0.60)   return {label:'URGENT',        color:'#ea580c'};
    if ((scores.anger || 0) >= 0.80)     return {label:'ANGRY',         color:'#b45309'};
    if ((scores.confusion || 0) >= 0.90) return {label:'CONFUSED',      color:'#ca8a04'};
    return {label:'CALM', color:'#16a34a'};
}

function escHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Global state ────────────────────────────────────────────────────────
const sessions = {};
let selectedCallId = null;
let socket = null;

function initDashboard() {
    const container = document.getElementById('agent-dashboard');
    if (!container) return;

    socket = io(SOCKET_URL || 'http://localhost:5000', {
        transports: ['websocket', 'polling'],
    });

    // ── Socket events ──────────────────────────────────────────────
    socket.on('connect', () => {
        const el = document.getElementById('ws-status');
        if (el) { el.textContent = '● Live'; el.style.color = '#3fb950'; }
    });
    socket.on('disconnect', () => {
        const el = document.getElementById('ws-status');
        if (el) { el.textContent = '○ Disconnected'; el.style.color = '#f85149'; }
    });

    socket.on('session_update', (data) => {
        updateSessionCard(data);
    });

    socket.on('escalation_alert', (data) => {
        showEscalationAlert(data);
        if (data.call_id) selectCall(data.call_id);
    });

    socket.on('session_closed', ({ call_id }) => {
        delete sessions[call_id];
        renderCallQueue();
        if (selectedCallId === call_id) {
            selectedCallId = null;
            clearDetail();
        }
    });

    // Initial poll
    pollSessions();
    setInterval(pollSessions, 10000);
}

// ── Update a session card ───────────────────────────────────────────────
function updateSessionCard(data) {
    sessions[data.call_id] = { ...(sessions[data.call_id] || {}), ...data };
    renderCallQueue();
    if (selectedCallId === data.call_id) {
        renderDetail(sessions[data.call_id]);
    }
}

// ── Escalation alert ────────────────────────────────────────────────────
function showEscalationAlert(data) {
    const banner = document.getElementById('escalation-banner');
    if (banner) {
        banner.style.display = 'block';
        banner.textContent = `⚠ ESCALATION: ${data.reason} — Call ${data.call_id}`;
        const dashboard = document.getElementById('agent-dashboard');
        if (dashboard) dashboard.classList.add('critical-pulse');
        setTimeout(() => {
            banner.style.display = 'none';
            if (dashboard) dashboard.classList.remove('critical-pulse');
        }, 10000);
    }
    if (socket && data.call_id) {
        socket.emit('join_room', { call_id: data.call_id });
    }
}

// ── Render call queue ───────────────────────────────────────────────────
function renderCallQueue() {
    const callList = document.getElementById('call-list');
    if (!callList) return;

    const allSessions = Object.values(sessions);
    if (allSessions.length === 0) {
        callList.innerHTML = '<div style="color:#8b949e;">No active calls</div>';
        return;
    }

    callList.innerHTML = '';
    allSessions.forEach(s => {
        const emotionScores = s.emotion_scores || {
            distress: s.distress_score || 0,
            urgency: s.urgency_score || 0,
            anger: s.anger_score || 0,
            fear: s.fear_score || 0,
            confusion: s.confusion_score || 0,
        };
        const badge = getEmotionBadge(emotionScores);
        const confidence = Math.round((s.confidence_score || 0) * 100);

        const card = document.createElement('div');
        card.style.cssText = `padding:10px; background:#21262d; border-radius:8px; cursor:pointer; margin-bottom:8px; border-left:3px solid ${badge.color}; transition: all 0.2s;`;
        if (selectedCallId === s.call_id) {
            card.style.borderColor = '#58a6ff';
            card.style.background = '#1a2332';
        }

        const fearDisplay = (s.fear_score || 0) > 0
            ? `<span style="color:#bc8cff; font-size:0.72rem; margin-left:6px;">Fear: ${Math.round((s.fear_score||0)*100)}%</span>`
            : '';

        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                <strong style="color:#e6edf3;">${escHtml(s.call_id)}</strong>
                <span style="font-size:0.75rem; padding:2px 8px; border-radius:10px; background:${badge.color}; color:#fff; font-weight:600;">${badge.label}</span>
            </div>
            <div style="display:flex; align-items:center; gap:8px; font-size:0.78rem; color:#8b949e;">
                <span style="background:#30363d; padding:2px 6px; border-radius:4px; font-weight:600;">${(s.language||'kn').toUpperCase()}</span>
                <span>${escHtml(s.current_intent || s.intent || 'Analyzing…')}</span>
                <span>${confidence}%</span>
                ${fearDisplay}
            </div>
        `;
        card.addEventListener('click', () => selectCall(s.call_id));
        callList.appendChild(card);
    });
}

// ── Select a call ───────────────────────────────────────────────────────
async function selectCall(call_id) {
    selectedCallId = call_id;
    if (socket) socket.emit('join_room', { call_id });
    renderCallQueue();

    try {
        const res = await fetch(`${API_BASE}/api/agent/session/${call_id}`);
        const data = await res.json();
        sessions[call_id] = { ...sessions[call_id], ...data };
        renderDetail(sessions[call_id]);
    } catch (err) {
        console.error('Failed to fetch session detail', err);
    }
}

// ── Render detail panel ─────────────────────────────────────────────────
function renderDetail(s) {
    const liveSession = document.getElementById('live-session');
    if (liveSession) liveSession.style.display = 'block';

    const emotionScores = s.emotion_scores || {
        distress: s.distress_score || 0,
        urgency: s.urgency_score || 0,
        anger: s.anger_score || 0,
        fear: s.fear_score || 0,
        confusion: s.confusion_score || 0,
    };

    const EMOTION_COLORS = {
        distress: '#e74c3c', urgency: '#f39c12',
        anger: '#c0392b', fear: '#6b21a8', confusion: '#8e44ad',
    };

    const badge = getEmotionBadge(emotionScores);

    // Emotion bars
    const emotionEl = document.getElementById('emotion-display');
    if (emotionEl) {
        emotionEl.innerHTML = ['distress','urgency','anger','fear','confusion'].map(e => {
            const val = Math.round((emotionScores[e] || 0) * 100);
            return `<div style="display:flex; align-items:center; gap:8px;">
                <span style="width:70px; font-size:0.78rem; color:#c9d1d9;">${e.charAt(0).toUpperCase()+e.slice(1)}</span>
                <div style="flex:1; height:8px; background:#30363d; border-radius:4px; overflow:hidden;">
                    <div style="width:${val}%; height:100%; background:${EMOTION_COLORS[e] || '#8b949e'}; border-radius:4px; transition:width 0.4s;"></div>
                </div>
                <span style="font-size:0.72rem; color:#8b949e; width:30px; text-align:right;">${val}%</span>
            </div>`;
        }).join('');

        // Add emotion badge + override dropdown
        emotionEl.innerHTML += `
            <div style="margin-top:10px; display:flex; align-items:center; gap:10px;">
                <span style="padding:4px 12px; border-radius:10px; background:${badge.color}; color:#fff; font-weight:600; font-size:0.82rem;">${badge.label}</span>
                <select id="emotion-override" style="background:#21262d; color:#e6edf3; border:1px solid #30363d; border-radius:6px; padding:4px 8px; font-size:0.78rem;">
                    <option value="">Override emotion…</option>
                    <option value="distress">Distress</option>
                    <option value="urgency">Urgency</option>
                    <option value="anger">Anger</option>
                    <option value="fear">Fear</option>
                    <option value="confusion">Confusion</option>
                    <option value="neutral">Neutral</option>
                </select>
            </div>
        `;

        // Bind emotion override
        const overrideSelect = document.getElementById('emotion-override');
        if (overrideSelect) {
            overrideSelect.addEventListener('change', async () => {
                const newEmotion = overrideSelect.value;
                if (!newEmotion || !selectedCallId) return;
                try {
                    await fetch(`${API_BASE}/api/agent/session/${selectedCallId}/correct`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            feedback_type: 'emotion_wrong',
                            corrected_value: newEmotion,
                        }),
                    });
                    overrideSelect.value = '';
                } catch (err) {
                    console.error('Emotion override error', err);
                }
            });
        }
    }

    // NLU display
    const nluEl = document.getElementById('nlu-display');
    if (nluEl) {
        const fearScoreDisplay = (s.fear_score || 0) > 0
            ? `<div style="font-size:0.85rem; margin-bottom:4px;"><b style="color:#bc8cff;">Fear Score:</b> ${Math.round((s.fear_score||0)*100)}%</div>`
            : '';
        nluEl.innerHTML = `
            <div style="font-size:0.85rem; margin-bottom:4px;"><b style="color:#8b949e;">Intent:</b> ${escHtml(s.current_intent || s.intent || '—')}</div>
            <div style="font-size:0.85rem; margin-bottom:4px;"><b style="color:#8b949e;">Confidence:</b> ${Math.round((s.confidence_score||0)*100)}%</div>
            <div style="font-size:0.85rem; margin-bottom:4px;"><b style="color:#8b949e;">Verification:</b> ${escHtml(s.verification_status||'pending')}</div>
            <div style="font-size:0.85rem; margin-bottom:4px;"><b style="color:#8b949e;">Department:</b> ${escHtml(s.department_routed_to||'—')}</div>
            ${fearScoreDisplay}
            <div style="margin-top:8px;">
                <label style="font-size:0.78rem; color:#8b949e;">Edit Restatement:</label>
                <textarea id="restatement-edit" style="width:100%; background:#21262d; color:#e6edf3; border:1px solid #30363d; border-radius:6px; padding:6px; font-size:0.82rem; min-height:50px; margin-top:4px; font-family:inherit; resize:vertical;">${escHtml(s.last_restatement || '')}</textarea>
                <button id="btn-save-correction" style="margin-top:4px; padding:4px 12px; background:#1f6feb; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:0.78rem;">Save Correction</button>
                <button id="btn-takeover" style="margin-top:4px; margin-left:8px; padding:4px 12px; background:#da3633; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:0.78rem; font-weight:600;">Take Over Call</button>
            </div>
        `;

        // Bind save correction
        const saveBtn = document.getElementById('btn-save-correction');
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const textarea = document.getElementById('restatement-edit');
                if (!textarea || !selectedCallId) return;
                try {
                    await fetch(`${API_BASE}/api/agent/session/${selectedCallId}/correct`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            feedback_type: 'restatement_corrected',
                            corrected_value: textarea.value,
                        }),
                    });
                } catch (err) {
                    console.error('Save correction error', err);
                }
            });
        }

        // Bind take over
        const takeoverBtn = document.getElementById('btn-takeover');
        if (takeoverBtn) {
            takeoverBtn.addEventListener('click', async () => {
                if (!selectedCallId) return;
                try {
                    await fetch(`${API_BASE}/api/agent/session/${selectedCallId}/close`, {
                        method: 'POST',
                    });
                    delete sessions[selectedCallId];
                    selectedCallId = null;
                    renderCallQueue();
                    clearDetail();
                } catch (err) {
                    console.error('Take over error', err);
                }
            });
        }
    }

    // Turns (last 3)
    const turnsEl = document.getElementById('turns-display');
    if (turnsEl) {
        const turns = (s.turns || []).slice(-3);
        turnsEl.innerHTML = turns.map(t => `
            <div style="padding:4px 0; border-bottom:1px solid #21262d; font-size:0.82rem;">
                <span style="font-weight:700; color:${t.speaker==='citizen'?'#3fb950':t.speaker==='ai'?'#58a6ff':'#f0883e'};">${t.speaker}:</span>
                <span>${escHtml(t.clean_text || t.text || t.raw_transcript || '')}</span>
                ${t.ai_restatement ? `<div style="color:#8b949e; font-style:italic; font-size:0.78rem;">🔁 ${escHtml(t.ai_restatement)}</div>` : ''}
            </div>
        `).join('');
        turnsEl.scrollTop = turnsEl.scrollHeight;
    }

    // Critical pulse for high-priority
    const dashboard = document.getElementById('agent-dashboard');
    if (dashboard) {
        if (s.priority === 'critical' || (s.fear_score && s.fear_score >= 0.65) || (s.distress_score && s.distress_score > 0.75)) {
            dashboard.classList.add('critical-pulse');
        } else {
            dashboard.classList.remove('critical-pulse');
        }
    }
}

function clearDetail() {
    const liveSession = document.getElementById('live-session');
    if (liveSession) liveSession.style.display = 'none';
}

// ── Polling ─────────────────────────────────────────────────────────────
async function pollSessions() {
    try {
        const res = await fetch(`${API_BASE}/api/agent/sessions?status=active`);
        const data = await res.json();
        if (Array.isArray(data)) {
            data.forEach(s => {
                sessions[s.call_id] = s;
                if (socket) socket.emit('join_room', { call_id: s.call_id });
            });
            renderCallQueue();
        }
    } catch (e) { /* swallow */ }
}

// Auto-initialize
window.AgentDashboardApp = { init: initDashboard };
window.onload = initDashboard;
