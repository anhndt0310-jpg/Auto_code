'use strict';

// ───────────────────────── Hằng số & tiện ích ─────────────────────────
const ROLES = ['planner', 'tester', 'coder', 'reviewer'];
const ROLE_META = {
    planner: { name: 'Kiến trúc sư', icon: '📐', color: 'var(--role-planner)' },
    tester: { name: 'Kiểm thử viên', icon: '🧪', color: 'var(--role-tester)' },
    coder: { name: 'Lập trình viên', icon: '💻', color: 'var(--role-coder)' },
    reviewer: { name: 'Reviewer', icon: '🔍', color: 'var(--role-reviewer)' },
    system: { name: 'Hệ thống', icon: '⚙️', color: 'var(--role-system)' }
};
const STATUS_LABEL = {
    running: 'Đang chạy', done: 'Hoàn tất', failed: 'Chưa đạt test', stopped: 'Đã dừng',
    error: 'Lỗi', paused_budget: 'Hết ngân sách', interrupted: 'Bị gián đoạn'
};
const TERMINAL = ['done', 'failed', 'stopped', 'error', 'paused_budget', 'interrupted'];
const RESUMABLE = ['interrupted', 'stopped', 'failed', 'paused_budget', 'error'];
const STAGE_LABEL = { pending: 'Chờ', running: 'Đang chạy', done: 'Xong', error: 'Lỗi' };
const MAX_AUTO_RESUME = 3;

const store = {
    get(k, d = '') { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } }
};

const state = {
    ws: null, connected: false, running: false, mode: 'pipeline',
    runId: null, runStatus: null, runOk: false, lastSeq: 0, fillCommand: false,
    files: {}, activeFile: null, spec: '',
    convId: null, chatPending: false,
    history: { runs: [], conversations: [] }, histTab: 'runs',
    reconnectTimer: null, reconnectDelay: 1000,
    resumeAttempts: 0, resumeTimer: null, historyTimer: null,
    laneFilter: 'all', stage: { planner: 'pending', tester: 'pending', coder: 'pending', reviewer: 'pending' },
    laneStats: {}, laneCounts: {}
};

let el = {};
const lanes = {};

function escapeHtml(t) {
    return String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function timeAgo(sec) {
    const d = Math.max(0, Date.now() / 1000 - sec);
    if (d < 60) return 'vừa xong';
    if (d < 3600) return `${Math.floor(d / 60)} phút trước`;
    if (d < 86400) return `${Math.floor(d / 3600)} giờ trước`;
    return `${Math.floor(d / 86400)} ngày trước`;
}

function inlineMd(s) {
    return '<span class="md-p">' + escapeHtml(s)
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>') + '</span>';
}

// Hiển thị văn bản có khối ```code``` (kể cả khối bị cắt dở).
function renderRich(text) {
    let html = '';
    let rest = text || '';
    const pre = (code) => `<pre class="code-block"><code>${escapeHtml(code)}</code></pre>`;
    while (rest) {
        const a = rest.indexOf('```');
        if (a < 0) { html += inlineMd(rest); break; }
        if (a > 0) html += inlineMd(rest.slice(0, a));
        const afterOpen = rest.slice(a + 3);
        const nl = afterOpen.indexOf('\n');
        const body = nl < 0 ? '' : afterOpen.slice(nl + 1);
        const b = body.indexOf('\n```');
        if (b < 0) { html += pre(body.replace(/\n$/, '')); break; }
        html += pre(body.slice(0, b));
        rest = body.slice(b + 4).replace(/^[^\n]*/, '').replace(/^\n/, '');
    }
    return html;
}

// ───────────────────────── Danh tính & REST ─────────────────────────
function getKey() { return (el.apiKey && el.apiKey.value.trim()) || store.get('api_key'); }
function identity() { return { api_key: getKey(), profile: store.get('profile') }; }
function hasIdentity() { return !!(getKey() || store.get('profile')); }

function authHeaders() {
    const h = {};
    const k = getKey(), p = store.get('profile'), t = store.get('app_token');
    if (k) h['Authorization'] = 'Bearer ' + k;
    if (p) h['X-Profile'] = p;
    if (t) h['X-App-Token'] = t;
    return h;
}

function api(path, opts = {}) {
    return fetch(path, { ...opts, headers: { ...authHeaders(), ...(opts.headers || {}) } });
}

function tokenQuery() {
    const t = store.get('app_token');
    return t ? `?token=${encodeURIComponent(t)}` : '';
}

// ───────────────────────── DOM ─────────────────────────
function initDom() {
    const ids = {
        connStatus: 'conn-status', connText: 'conn-text', connBanner: 'conn-banner',
        apiKey: 'api-key', profile: 'profile-code', appToken: 'app-token',
        command: 'command', btnRun: 'btn-run', btnStop: 'btn-stop', btnResume: 'btn-resume',
        runChip: 'run-chip', stepper: 'stepper',
        logArea: 'log-area', specArea: 'spec-area', fileTabs: 'file-tabs', codeArea: 'code-area',
        downloadStatus: 'download-status', btnDownload: 'btn-download',
        agentsGrid: 'agents-grid', chatList: 'chat-list', chatEmpty: 'chat-empty', chatTitle: 'chat-title',
        pipelinePanel: 'pipeline-panel', chatPanel: 'chat-panel',
        settingsBody: 'settings-body', settingsArrow: 'settings-arrow',
        defaultModels: 'default-models', plannerModels: 'planner-models', testerModels: 'tester-models',
        coderModels: 'coder-models', reviewerModels: 'reviewer-models',
        usePlanner: 'use-planner', useReviewer: 'use-reviewer', maxRounds: 'max-rounds', roundsVal: 'rounds-val',
        tokenBudget: 'token-budget', hardBudget: 'hard-budget', autoResume: 'auto-resume',
        toastContainer: 'toast-container', tokenTracker: 'token-tracker', tokenTotal: 'token-total-count',
        tokenDetail: 'token-detail', tokenBudgetLabel: 'token-budget-label', tokenBar: 'token-bar-fill',
        histRuns: 'hist-runs', histChats: 'hist-chats'
    };
    for (const [k, id] of Object.entries(ids)) el[k] = document.getElementById(id);

    el.apiKey.value = store.get('api_key');
    el.profile.value = store.get('profile');
    el.appToken.value = store.get('app_token');
    el.autoResume.checked = store.get('auto_resume', '1') === '1';

    // Dựng 4 làn AI
    el.agentsGrid.innerHTML = '';
    ROLES.forEach(r => {
        const m = ROLE_META[r];
        const lane = document.createElement('section');
        lane.className = 'lane';
        lane.dataset.role = r;
        lane.innerHTML =
            `<div class="lane-head"><div class="lane-title"><span>${m.icon}</span><span>${m.name}</span>` +
            `<span class="lane-state"><span class="dot"></span><span class="lane-state-txt">Chờ</span></span></div>` +
            `<div class="lane-meta">Chưa có model</div></div>` +
            `<div class="lane-body"><div class="lane-empty">Chưa có phản hồi.</div></div>`;
        el.agentsGrid.appendChild(lane);
        lanes[r] = {
            root: lane, body: lane.querySelector('.lane-body'), meta: lane.querySelector('.lane-meta'),
            stateTxt: lane.querySelector('.lane-state-txt'), dot: lane.querySelector('.dot')
        };
        state.laneCounts[r] = 0;
    });

    el.codeArea.addEventListener('input', () => {
        if (state.activeFile && state.activeFile in state.files) state.files[state.activeFile] = el.codeArea.value;
    });

    const drop = document.getElementById('drop-zone');
    ['dragenter', 'dragover'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.add('drag-over'); }));
    ['dragleave', 'drop'].forEach(n => drop.addEventListener(n, e => { e.preventDefault(); drop.classList.remove('drag-over'); }));
    drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) uploadFilesToServer(e.dataTransfer.files); });

    el.command.addEventListener('keydown', e => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); handleMainAction(); }
    });
}

// ───────────────────────── WebSocket + tự nối lại ─────────────────────────
function setConn(kind, text) {
    state.connected = kind === 'online';
    const map = { online: ['connected', 'Đã kết nối'], connecting: ['connecting', 'Đang kết nối…'], offline: ['', 'Mất kết nối'] };
    const [cls, label] = map[kind];
    el.connStatus.className = 'conn-dot ' + cls;
    el.connText.textContent = text || label;
}

function showBanner(msg, ok = false) {
    el.connBanner.textContent = msg;
    el.connBanner.className = 'banner' + (ok ? ' ok' : '');
}
function hideBanner() { el.connBanner.className = 'banner hidden'; }

function connectWS() {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
    if (state.ws) {
        const old = state.ws;
        old.onopen = old.onmessage = old.onclose = old.onerror = null;
        try { old.close(); } catch (e) { /* ignore */ }
    }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws${tokenQuery()}`;
    setConn('connecting');

    let ws;
    try { ws = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }
    state.ws = ws;

    ws.onopen = () => {
        if (state.ws !== ws) return;
        state.reconnectDelay = 1000;
        setConn('online');
        onConnected();
    };
    ws.onmessage = (ev) => {
        if (state.ws !== ws) return;
        try { handleServerMessage(JSON.parse(ev.data)); }
        catch (err) { console.error('WS message error:', err, ev.data); }
    };
    ws.onclose = (ev) => {
        if (state.ws !== ws) return;
        setConn('offline');
        if (ev && ev.code === 4401) {
            showToast('Sai hoặc thiếu APP_TOKEN. Nhập lại trong “Đồng bộ nhiều máy & bảo mật”.', 'error');
            state.reconnectDelay = 15000;
        } else if (state.running) {
            showBanner('Mất kết nối. Run vẫn đang chạy trên server — đang nối lại…');
        }
        scheduleReconnect();
    };
    ws.onerror = () => { /* onclose sẽ xử lý */ };
}

function scheduleReconnect() {
    if (state.reconnectTimer) return;
    state.reconnectTimer = setTimeout(() => {
        state.reconnectTimer = null;
        state.reconnectDelay = Math.min(state.reconnectDelay * 1.6, 15000);
        connectWS();
    }, state.reconnectDelay);
}

// Vừa (nối lại) kết nối: nhận diện, lấy lịch sử, gắn lại vào run đang dở.
function onConnected() {
    if (hasIdentity()) sendWS({ type: 'identify', ...identity() }, true);
    const rid = state.runId;
    if (rid && (state.running || !TERMINAL.includes(state.runStatus))) {
        attachRun(rid, state.lastSeq);
        if (state.running) showBanner('Đã nối lại — đang đồng bộ phần bị lỡ…', true);
    } else {
        hideBanner();
    }
    if (state.mode === 'chat' && state.chatPending && state.convId) reloadConversation();
}

function sendWS(payload, silent = false) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        if (!silent) showToast('Chưa kết nối tới server. Đang thử nối lại…', 'error');
        return false;
    }
    state.ws.send(JSON.stringify(payload));
    return true;
}

function attachRun(runId, afterSeq) {
    sendWS({ type: 'attach', run_id: runId, after_seq: afterSeq || 0, ...identity() }, true);
}

// ───────────────────────── Xử lý tin từ server ─────────────────────────
function seen(ev) {
    if (ev.seq === undefined) return false;
    if (ev.seq <= state.lastSeq) return true;
    state.lastSeq = ev.seq;
    return false;
}

function handleServerMessage(data) {
    switch (data.type) {
        case 'session': break;

        case 'history':
            state.history = { runs: data.runs || [], conversations: data.conversations || [] };
            renderHistory();
            break;

        case 'run_started':
            state.runId = data.run_id;
            store.set('last_run', data.run_id);
            if (!data.resumed) state.lastSeq = 0;
            state.runStatus = 'running';
            setRunning(true);
            scheduleHistoryRefresh();
            break;

        case 'run_info':
            if (state.runId !== data.run_id) { state.runId = data.run_id; store.set('last_run', data.run_id); }
            state.runStatus = data.status;
            state.runOk = !!data.ok;
            if (state.fillCommand && data.command) { el.command.value = data.command; state.fillCommand = false; }
            setRunning(data.status === 'running');
            hideBanner();
            updateRunControls();
            renderHistory();
            break;

        case 'run_status':
            if (data.run_id && state.runId && data.run_id !== state.runId) break;
            state.runStatus = data.status;
            if (data.ok !== undefined) state.runOk = !!data.ok;
            setRunning(false);
            hideBanner();
            updateRunControls();
            scheduleHistoryRefresh();
            maybeAutoResume(data.status);
            break;

        case 'log':
            if (seen(data)) break;
            appendLog(data.role, data.message);
            break;

        case 'agent':
            if (seen(data)) break;
            addAgentCard(data);
            break;

        case 'spec':
            state.spec = data.content;
            renderSpec();
            break;

        case 'progress':
            updateProgress(data.stage, data.step, data.total, data.status);
            break;

        case 'tokens':
            updateTokenStats(data.stats);
            break;

        case 'code':
            state.files = data.files || {};
            renderFiles();
            renderAttachedChips();
            updateRunControls();
            break;

        case 'conversation':
            state.convId = data.conversation_id;
            store.set('last_conv', data.conversation_id);
            scheduleHistoryRefresh();
            break;

        case 'chat_reply':
            if (data.conversation_id && state.convId && data.conversation_id !== state.convId) break;
            removeTyping();
            addChatMessage('assistant', data.content, data.model);
            state.chatPending = false;
            setRunning(false);
            break;

        case 'done':
            state.files = data.files || state.files;
            renderFiles();
            if (data.tokens) updateTokenStats(data.tokens);
            if (data.paused) showToast('Đã tạm dừng vì hết ngân sách token. Tăng ngân sách rồi bấm “Tiếp tục”.', 'warning');
            else if (data.ok) showToast('Hoàn thành: toàn bộ test đã qua.', 'success');
            else showToast('Chưa qua hết test — đã giữ phiên bản tốt nhất. Có thể bấm “Tiếp tục”.', 'warning');
            updateRunControls();
            break;

        case 'error':
            if (seen(data)) break;
            if (data.message === 'Không tìm thấy run.') {
                state.runId = null; state.runStatus = null; store.del('last_run'); renderHistory();
            }
            showToast(data.message || 'Có lỗi xảy ra', 'error');
            appendLog('system', `❌ ${data.message}`);
            if (state.mode === 'chat') { removeTyping(); state.chatPending = false; setRunning(false); }
            break;

        default: break;
    }
}

function maybeAutoResume(status) {
    if (status === 'done') { state.resumeAttempts = 0; return; }
    const auto = el.autoResume.checked;
    if (!auto || !['error', 'interrupted'].includes(status)) return;
    if (state.resumeAttempts >= MAX_AUTO_RESUME) {
        showToast('Đã thử tự tiếp tục nhiều lần mà vẫn lỗi. Hãy kiểm tra nhật ký rồi bấm “Tiếp tục”.', 'error');
        return;
    }
    state.resumeAttempts += 1;
    const wait = status === 'interrupted' ? 2500 : 5000 * state.resumeAttempts;
    showBanner(`Run dừng giữa chừng (${STATUS_LABEL[status]}). Tự tiếp tục sau ${Math.round(wait / 1000)} giây (lần ${state.resumeAttempts}/${MAX_AUTO_RESUME})…`);
    clearTimeout(state.resumeTimer);
    state.resumeTimer = setTimeout(() => {
        if (state.runStatus === status) resumeRun(true);
    }, wait);
}

function scheduleHistoryRefresh() {
    clearTimeout(state.historyTimer);
    state.historyTimer = setTimeout(() => {
        if (hasIdentity()) sendWS({ type: 'history', ...identity() }, true);
    }, 400);
}

// ───────────────────────── Tiến độ & 4 làn AI ─────────────────────────
function setStage(role, st, sub) {
    state.stage[role] = st;
    const li = el.stepper.querySelector(`[data-stage="${role}"]`);
    if (li) {
        li.className = 'step ' + st;
        li.querySelector('.step-sub').textContent = sub || STAGE_LABEL[st];
    }
    const lane = lanes[role];
    if (lane) {
        lane.stateTxt.textContent = sub || STAGE_LABEL[st];
        lane.dot.className = 'dot' + (st === 'running' ? ' s-running' : st === 'done' ? ' s-done' : st === 'error' ? ' s-error' : '');
    }
}

function updateProgress(stage, step, total, status) {
    const idx = ROLES.indexOf(stage);
    if (idx < 0) return;
    ROLES.slice(0, idx).forEach(r => {
        if (state.stage[r] === 'pending' || state.stage[r] === 'running') setStage(r, 'done');
    });
    if (status === 'done') setStage(stage, 'done');
    else if (status === 'error') setStage(stage, 'error', 'Chưa qua test');
    else setStage(stage, 'running', stage === 'coder' ? `Vòng ${step}/${total}` : 'Đang chạy');
}

function updateLaneMeta(role) {
    const lane = lanes[role];
    const s = state.laneStats[role];
    if (!lane || !s) return;
    lane.meta.textContent = `${s.model || '—'} · ${s.calls} lượt · ${(s.total || 0).toLocaleString()} tokens`;
}

function addAgentCard(ev) {
    const role = ROLES.includes(ev.role) ? ev.role : 'coder';
    const lane = lanes[role];
    const empty = lane.body.querySelector('.lane-empty');
    if (empty) empty.remove();

    const nearBottom = lane.body.scrollHeight - lane.body.scrollTop - lane.body.clientHeight < 80;
    const status = ['ok', 'fail', 'warn', 'info'].includes(ev.status) ? ev.status : 'ok';
    const badge = { ok: 'Ổn', fail: 'Lỗi', warn: 'Lưu ý', info: 'Ghi chú' }[status];
    const time = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString('vi-VN') : '';
    const content = ev.content || '';

    const card = document.createElement('article');
    card.className = `acard st-${status}`;
    card.innerHTML =
        `<div class="acard-head"><span class="acard-title">${escapeHtml(ev.title || '')}</span>` +
        `<span class="badge ${status}">${badge}</span></div>` +
        `<div class="acard-meta"><span>${escapeHtml(ev.model || '')}</span>` +
        (ev.tokens ? `<span>${Number(ev.tokens).toLocaleString()} tokens</span>` : '') +
        `<span>${time}</span></div>` +
        `<div class="acard-body">${renderRich(content)}</div>`;

    const body = card.querySelector('.acard-body');
    const long = content.length > 700 || content.split('\n').length > 14;
    const actions = document.createElement('div');
    actions.className = 'acard-actions';
    if (long) {
        body.classList.add('collapsed');
        const more = document.createElement('button');
        more.type = 'button'; more.className = 'btn btn-ghost'; more.textContent = 'Xem thêm';
        more.onclick = () => {
            const c = body.classList.toggle('collapsed');
            more.textContent = c ? 'Xem thêm' : 'Thu gọn';
        };
        actions.appendChild(more);
    }
    const copy = document.createElement('button');
    copy.type = 'button'; copy.className = 'btn btn-ghost'; copy.textContent = 'Sao chép';
    copy.onclick = async () => {
        try { await navigator.clipboard.writeText(content); showToast('Đã sao chép.', 'success'); }
        catch (e) { showToast('Trình duyệt chặn sao chép.', 'error'); }
    };
    actions.appendChild(copy);
    card.appendChild(actions);

    lane.body.appendChild(card);
    if (nearBottom) lane.body.scrollTop = lane.body.scrollHeight;
}

window.setLaneFilter = function (f) {
    state.laneFilter = f;
    el.agentsGrid.dataset.filter = f;
    document.querySelectorAll('#agent-filter .fchip').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
};

function resetRunUI() {
    ROLES.forEach(r => {
        lanes[r].body.innerHTML = '<div class="lane-empty">Chưa có phản hồi.</div>';
        lanes[r].meta.textContent = 'Chưa có model';
        setStage(r, 'pending');
    });
    state.laneStats = {};
    el.logArea.innerHTML = '<div class="log-placeholder">Nhấn “Chạy” để bắt đầu…</div>';
    state.spec = '';
    renderSpec();
    el.tokenTracker.classList.add('hidden');
    updateTokenStats({ total: 0, prompt: 0, completion: 0, by_role: {} });
    el.tokenTracker.classList.add('hidden');
}

// ───────────────────────── Nhật ký / đặc tả / code ─────────────────────────
function appendLog(role, message) {
    const ph = el.logArea.querySelector('.log-placeholder');
    if (ph) el.logArea.innerHTML = '';
    const safe = ROLE_META[role] ? role : 'system';
    const meta = ROLE_META[safe];
    const entry = document.createElement('div');
    entry.className = `log-entry role-${safe}`;
    entry.innerHTML = `<span style="color:${meta.color};font-weight:600">${meta.icon} [${meta.name}]</span> ` +
        `<span style="color:var(--text-secondary);font-size:11px">${new Date().toLocaleTimeString('vi-VN')}</span>\n${escapeHtml(message)}`;
    el.logArea.appendChild(entry);
    el.logArea.scrollTop = el.logArea.scrollHeight;
}

function renderSpec() {
    el.specArea.innerHTML = state.spec
        ? renderRich(state.spec)
        : '<div class="log-placeholder">Đặc tả sẽ hiện ở đây sau khi kiến trúc sư hoàn thành.</div>';
}

function renderFiles() {
    const names = Object.keys(state.files);
    el.fileTabs.innerHTML = '';
    if (!names.length) {
        state.activeFile = null;
        el.codeArea.value = '';
        return;
    }
    if (!state.activeFile || !(state.activeFile in state.files)) {
        state.activeFile = names.find(n => !n.startsWith('test_')) || names[0];
    }
    names.sort().forEach(name => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'file-tab-btn' + (name === state.activeFile ? ' active' : '');
        b.textContent = name;
        b.onclick = () => { state.activeFile = name; renderFiles(); };
        el.fileTabs.appendChild(b);
    });
    el.codeArea.value = state.files[state.activeFile] || '';
}

function renderAttachedChips() {
    const list = document.getElementById('attached-chips-list');
    const hint = document.getElementById('attachment-hint');
    const names = Object.keys(state.files);
    list.innerHTML = '';
    hint.textContent = names.length ? `Đã nạp ${names.length} file vào ngữ cảnh:` : 'Hoặc kéo thả file vào khung này';
    names.forEach(name => {
        const chip = document.createElement('div');
        chip.className = 'file-chip';
        chip.innerHTML = `<span>📄 ${escapeHtml(name)}</span>`;
        const del = document.createElement('button');
        del.type = 'button'; del.className = 'file-chip-del'; del.textContent = '✕'; del.title = `Gỡ ${name}`;
        del.onclick = (e) => { e.stopPropagation(); removeAttachedFile(name); };
        chip.appendChild(del);
        list.appendChild(chip);
    });
}

function removeAttachedFile(name) {
    delete state.files[name];
    if (state.activeFile === name) state.activeFile = Object.keys(state.files)[0] || null;
    renderFiles();
    renderAttachedChips();
}

window.triggerFileUpload = () => document.getElementById('file-uploader').click();
window.handleFileUpload = async function (event) {
    if (!event.target.files.length) return;
    await uploadFilesToServer(event.target.files);
    event.target.value = '';
};

async function uploadFilesToServer(fileList) {
    const fd = new FormData();
    for (const f of fileList) fd.append('files', f);
    showToast('Đang đọc file…', 'info');
    try {
        const resp = await api('/api/upload', { method: 'POST', body: fd });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const n = Object.keys(data.files || {}).length;
        if (!n) { showToast('Không đọc được file văn bản hợp lệ nào.', 'warning'); return; }
        Object.assign(state.files, data.files);
        renderFiles();
        renderAttachedChips();
        if (state.mode === 'pipeline') switchTab('code');
        showToast(`Đã nạp ${n} file.` + (data.skipped && data.skipped.length ? ` Bỏ qua: ${data.skipped.join(', ')}` : ''), 'success');
    } catch (e) {
        showToast(`Lỗi upload: ${e.message}`, 'error');
    }
}

// ───────────────────────── Token ─────────────────────────
function updateTokenStats(stats) {
    if (!stats) return;
    el.tokenTracker.classList.remove('hidden');
    const total = stats.total || 0, budget = stats.token_budget || 0;
    el.tokenTotal.textContent = `${total.toLocaleString()} tokens`;
    el.tokenDetail.textContent = `(Prompt: ${(stats.prompt || 0).toLocaleString()} | Output: ${(stats.completion || 0).toLocaleString()})`;
    el.tokenBudgetLabel.textContent = budget > 0 ? `Ngân sách ${budget.toLocaleString()}` : 'Không giới hạn';
    let cls = 'token-badge-total', pct = 0;
    if (budget > 0) {
        pct = Math.min(100, (total / budget) * 100);
        if (total >= budget) cls += ' budget-exceeded';
        else if (total >= budget * 0.8) cls += ' budget-warning';
    }
    el.tokenTotal.className = cls;
    el.tokenBar.style.width = pct + '%';
    el.tokenBar.style.background = total >= budget && budget > 0 ? 'var(--error)' : (pct >= 80 ? 'var(--warning)' : 'var(--accent)');

    Object.entries(stats.by_role || {}).forEach(([role, s]) => {
        if (!lanes[role] || !s.calls) return;
        state.laneStats[role] = s;
        updateLaneMeta(role);
    });
}

// ───────────────────────── Trạng thái chạy / nút ─────────────────────────
function setRunning(running) {
    state.running = running;
    el.btnRun.disabled = running;
    el.btnStop.disabled = !running || state.mode === 'chat';
    updateRunControls();
}

function updateRunControls() {
    const st = state.runStatus;
    const canResume = state.mode === 'pipeline' && state.runId && !state.running && RESUMABLE.includes(st);
    el.btnResume.classList.toggle('hidden', !canResume);

    if (state.mode === 'pipeline' && st) {
        el.runChip.className = 'chip';
        el.runChip.innerHTML = `<span class="dot s-${st}"></span>${escapeHtml(STATUS_LABEL[st] || st)}`;
    } else {
        el.runChip.className = 'chip hidden';
    }

    const hasFiles = Object.keys(state.files).length > 0;
    if (state.runId && hasFiles && !state.running) {
        const warn = st === 'done' ? '' : ' (chưa qua hết test hoặc dừng giữa chừng)';
        el.downloadStatus.innerHTML = st === 'done'
            ? '<span style="color:var(--success);font-weight:600">✓ Dự án sẵn sàng để tải về.</span>'
            : `<span style="color:var(--warning);font-weight:600">⚠ Có thể tải bản hiện tại${warn}.</span>`;
        el.btnDownload.classList.remove('hidden');
    } else if (!state.running) {
        el.downloadStatus.textContent = 'Chưa có file để tải.';
        el.btnDownload.classList.add('hidden');
    } else {
        el.downloadStatus.textContent = 'Đang xử lý — file zip tải được ngay khi có code.';
        el.btnDownload.classList.toggle('hidden', !(state.runId && hasFiles));
    }
}

// ───────────────────────── Hành động của người dùng ─────────────────────────
window.handleMainAction = () => (state.mode === 'chat' ? sendChat() : run());

function readSettings() {
    return {
        default_models: el.defaultModels.value.trim(),
        planner_models: el.plannerModels.value.trim(),
        tester_models: el.testerModels.value.trim(),
        coder_models: el.coderModels.value.trim(),
        reviewer_models: el.reviewerModels.value.trim(),
        use_planner: el.usePlanner.checked,
        use_reviewer: el.useReviewer.checked,
        max_rounds: parseInt(el.maxRounds.value, 10) || 5,
        token_budget: Math.max(0, parseInt(el.tokenBudget.value, 10) || 0),
        hard_budget: el.hardBudget.checked
    };
}

window.run = function () {
    const cmd = el.command.value.trim();
    if (!cmd) { showToast('Vui lòng nhập yêu cầu.', 'error'); el.command.focus(); return; }
    if (!getKey()) { showToast('Vui lòng nhập và lưu OpenRouter API Key.', 'error'); el.apiKey.focus(); return; }

    // Chỉ file .py có tên hợp lệ mới được pipeline nhận làm dự án gốc.
    const pyNames = Object.keys(state.files).filter(n => /^[A-Za-z_][A-Za-z0-9_]*\.py$/.test(n));
    const ignored = Object.keys(state.files).length - pyNames.length;
    if (ignored > 0) showToast(`${ignored} file không phải .py hợp lệ sẽ không đưa vào pipeline.`, 'warning');
    const projectText = pyNames.map(n => `### FILE: ${n}\n\`\`\`python\n${state.files[n].trim()}\n\`\`\``).join('\n\n');

    const payload = { type: 'run', command: cmd, project_text: projectText, ...readSettings(), ...identity() };
    if (!sendWS(payload)) return;

    resetRunUI();
    state.runId = null; state.lastSeq = 0; state.runStatus = 'running'; state.resumeAttempts = 0; state.runOk = false;
    clearTimeout(state.resumeTimer);
    setRunning(true);
    appendLog('system', '🚀 Đã gửi yêu cầu, đang khởi động pipeline…');
    switchTab('agents');
};

window.stop = function () {
    if (!state.running) return;
    clearTimeout(state.resumeTimer);
    state.resumeAttempts = MAX_AUTO_RESUME;      // người dùng chủ động dừng: không tự chạy lại
    sendWS({ type: 'stop', run_id: state.runId, ...identity() });
};

window.resumeRun = function (auto = false) {
    if (!state.runId) return;
    if (!getKey()) { showToast('Cần API key để tiếp tục (key không được lưu trên server).', 'error'); return; }
    const payload = { type: 'resume', run_id: state.runId, ...identity() };
    const cfg = readSettings();
    payload.hard_budget = cfg.hard_budget;
    if (state.runStatus === 'paused_budget') {
        const used = (parseInt(String(el.tokenTotal.textContent).replace(/\D/g, ''), 10) || 0);
        const suggest = Math.ceil((used + 100000) / 10000) * 10000;
        const ans = auto === true ? suggest : prompt('Ngân sách token mới (0 = không giới hạn):', String(suggest));
        if (ans === null) return;
        payload.token_budget = Math.max(0, parseInt(ans, 10) || 0);
    } else if (state.runStatus === 'failed') {
        payload.max_rounds = cfg.max_rounds;
    }
    if (!sendWS(payload, auto === true)) return;
    if (auto !== true) state.resumeAttempts = 0;
    state.runStatus = 'running';
    setRunning(true);
    hideBanner();
    appendLog('system', '⏯ Đang tiếp tục từ checkpoint đã lưu…');
};

window.newSession = function () {
    if (state.running && !confirm('Run đang chạy. Dừng nó và bắt đầu phiên mới? (Tiến độ vẫn được lưu trong Lịch sử.)')) return;
    if (state.running) sendWS({ type: 'stop', run_id: state.runId, ...identity() }, true);
    sendWS({ type: 'reset' }, true);
    clearTimeout(state.resumeTimer);
    state.runId = null; state.runStatus = null; state.lastSeq = 0; state.files = {}; state.activeFile = null;
    state.convId = null; state.chatPending = false; state.resumeAttempts = 0;
    store.del('last_run'); store.del('last_conv');
    resetRunUI();
    renderFiles(); renderAttachedChips(); clearChat();
    el.command.value = '';
    setRunning(false);
    hideBanner();
    renderHistory();
};

window.switchTab = function (name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${name}`));
};

window.toggleSettings = function () {
    const hidden = el.settingsBody.classList.toggle('hidden');
    el.settingsArrow.textContent = hidden ? '▸' : '▾';
};

window.downloadZip = function () {
    if (!state.runId) { showToast('Chưa có run để tải.', 'error'); return; }
    window.location.href = `/api/download/${state.runId}${tokenQuery()}`;
};

// ───────────────────────── Chế độ & chat ─────────────────────────
window.switchMode = function (mode) {
    state.mode = mode;
    document.getElementById('btn-mode-pipeline').classList.toggle('active', mode === 'pipeline');
    document.getElementById('btn-mode-chat').classList.toggle('active', mode === 'chat');
    el.pipelinePanel.classList.toggle('hidden', mode === 'chat');
    el.chatPanel.classList.toggle('hidden', mode !== 'chat');
    const label = document.getElementById('input-label');
    if (mode === 'pipeline') {
        label.textContent = '📝 Yêu cầu lập trình của bạn';
        el.command.placeholder = 'Ví dụ: Tạo logic tính cashback 5% cho đơn hàng, tối đa 50.000đ';
        el.btnRun.textContent = '▶ Chạy (4 AI)';
    } else {
        label.textContent = '💬 Tin nhắn';
        el.command.placeholder = 'Hỏi hoặc nhờ AI giải thích file bạn đã đính kèm… (Ctrl+Enter để gửi)';
        el.btnRun.textContent = '💬 Gửi';
    }
    setRunning(state.mode === 'chat' ? state.chatPending : state.runStatus === 'running');
};

function clearChat() {
    el.chatList.innerHTML = '';
    el.chatList.appendChild(el.chatEmpty);
    el.chatEmpty.classList.remove('hidden');
    el.chatTitle.textContent = 'Cuộc trò chuyện mới';
}

function addChatMessage(role, content, model, ts) {
    el.chatEmpty.classList.add('hidden');
    const d = document.createElement('div');
    d.className = 'msg ' + (role === 'user' ? 'msg-user' : 'msg-ai');
    const time = (ts ? new Date(ts * 1000) : new Date()).toLocaleTimeString('vi-VN');
    d.innerHTML = `<div class="msg-meta">${role === 'user' ? '👤 Bạn' : '🤖 ' + escapeHtml(model || 'AI')} · ${time}</div>${renderRich(content)}`;
    el.chatList.appendChild(d);
    el.chatList.scrollTop = el.chatList.scrollHeight;
}

function addTyping() {
    removeTyping();
    const d = document.createElement('div');
    d.id = 'chat-typing'; d.className = 'msg msg-ai msg-typing';
    d.textContent = 'AI đang trả lời…';
    el.chatList.appendChild(d);
    el.chatList.scrollTop = el.chatList.scrollHeight;
}
function removeTyping() { const t = document.getElementById('chat-typing'); if (t) t.remove(); }

window.newChat = function () {
    state.convId = null; state.chatPending = false;
    store.del('last_conv');
    clearChat();
    renderHistory();
};

window.sendChat = function () {
    const msg = el.command.value.trim();
    if (!msg) { showToast('Vui lòng nhập tin nhắn.', 'error'); el.command.focus(); return; }
    if (!getKey()) { showToast('Vui lòng nhập và lưu OpenRouter API Key.', 'error'); el.apiKey.focus(); return; }
    const ok = sendWS({
        type: 'chat', message: msg, conversation_id: state.convId,
        files: state.files, default_models: el.defaultModels.value.trim(), ...identity()
    });
    if (!ok) return;
    addChatMessage('user', msg);
    addTyping();
    el.command.value = '';
    state.chatPending = true;
    setRunning(true);
};

async function loadConversation(convId) {
    try {
        const resp = await api(`/api/conversations/${convId}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        showToast(`Không tải được cuộc trò chuyện: ${e.message}`, 'error');
        return null;
    }
}

function renderConversation(conv) {
    el.chatList.innerHTML = '';
    el.chatList.appendChild(el.chatEmpty);
    el.chatEmpty.classList.toggle('hidden', (conv.messages || []).length > 0);
    el.chatTitle.textContent = conv.title || 'Cuộc trò chuyện';
    (conv.messages || []).forEach(m => addChatMessage(m.role, m.content, m.model, m.ts));
}

// Nối lại sau khi mất mạng: câu trả lời có thể đã được server lưu.
async function reloadConversation() {
    const conv = await loadConversation(state.convId);
    if (!conv) return;
    renderConversation(conv);
    const last = (conv.messages || []).slice(-1)[0];
    if (last && last.role === 'assistant') { state.chatPending = false; setRunning(false); }
    else if (state.chatPending) addTyping();
}

window.openConversation = async function (convId) {
    const conv = await loadConversation(convId);
    if (!conv) return;
    state.convId = convId; state.chatPending = false;
    store.set('last_conv', convId);
    if (Object.keys(conv.files || {}).length) { state.files = conv.files; renderFiles(); renderAttachedChips(); }
    switchMode('chat');
    renderConversation(conv);
    toggleSidebar(false);
    renderHistory();
};

window.openRun = function (runId) {
    if (state.running && state.runId !== runId && !confirm('Đang có run chạy. Chuyển sang run khác? (Run vẫn chạy nền và có thể xem lại trong Lịch sử.)')) return;
    switchMode('pipeline');
    resetRunUI();
    state.files = {}; state.activeFile = null;
    renderFiles(); renderAttachedChips();
    state.runId = runId; state.runStatus = null; state.lastSeq = 0; state.resumeAttempts = 0;
    state.fillCommand = true;
    store.set('last_run', runId);
    attachRun(runId, 0);
    toggleSidebar(false);
    renderHistory();
};

// ───────────────────────── Lịch sử (sidebar) ─────────────────────────
window.toggleSidebar = (open) => document.body.classList.toggle('sidebar-open', !!open);

window.switchHistTab = function (tab) {
    state.histTab = tab;
    document.querySelectorAll('.hist-tab').forEach(b => b.classList.toggle('active', b.dataset.htab === tab));
    el.histRuns.classList.toggle('hidden', tab !== 'runs');
    el.histChats.classList.toggle('hidden', tab !== 'chats');
};

function histItem(id, title, metaHtml, active, onOpen, onDelete) {
    const d = document.createElement('div');
    d.className = 'hist-item' + (active ? ' active' : '');
    d.tabIndex = 0;
    d.innerHTML = `<span class="hist-title">${escapeHtml(title || '(không tiêu đề)')}</span>` +
        `<span class="hist-meta">${metaHtml}</span>`;
    const del = document.createElement('button');
    del.type = 'button'; del.className = 'hist-del'; del.textContent = '✕'; del.title = 'Xóa';
    del.onclick = (e) => { e.stopPropagation(); onDelete(); };
    d.appendChild(del);
    d.onclick = onOpen;
    d.onkeydown = (e) => { if (e.key === 'Enter') onOpen(); };
    return d;
}

function renderHistory() {
    if (!el.histRuns) return;
    el.histRuns.innerHTML = '';
    el.histChats.innerHTML = '';
    const { runs, conversations } = state.history;
    if (!hasIdentity()) {
        el.histRuns.innerHTML = '<div class="hist-empty">Nhập API key (hoặc mã đồng bộ) để xem lịch sử.</div>';
    } else if (!runs.length) {
        el.histRuns.innerHTML = '<div class="hist-empty">Chưa có lần chạy nào.</div>';
    }
    runs.forEach(r => {
        const st = r.live ? 'running' : r.status;
        el.histRuns.appendChild(histItem(r.run_id, r.title,
            `<span class="dot s-${st}"></span>${escapeHtml(STATUS_LABEL[st] || st)} · ${timeAgo(r.updated)}`,
            r.run_id === state.runId, () => openRun(r.run_id), () => deleteRun(r.run_id)));
    });
    if (hasIdentity() && !conversations.length) el.histChats.innerHTML = '<div class="hist-empty">Chưa có cuộc trò chuyện nào.</div>';
    conversations.forEach(c => {
        el.histChats.appendChild(histItem(c.conv_id, c.title, timeAgo(c.updated),
            c.conv_id === state.convId, () => openConversation(c.conv_id), () => deleteConversation(c.conv_id)));
    });
}

async function deleteRun(id) {
    if (!confirm('Xóa lần chạy này khỏi lịch sử?')) return;
    const resp = await api(`/api/runs/${id}`, { method: 'DELETE' });
    if (resp.status === 409) { showToast('Run đang chạy — hãy dừng trước khi xóa.', 'warning'); return; }
    if (!resp.ok) { showToast('Không xóa được.', 'error'); return; }
    if (state.runId === id) newSession();
    scheduleHistoryRefresh();
}

async function deleteConversation(id) {
    if (!confirm('Xóa cuộc trò chuyện này?')) return;
    const resp = await api(`/api/conversations/${id}`, { method: 'DELETE' });
    if (!resp.ok) { showToast('Không xóa được.', 'error'); return; }
    if (state.convId === id) newChat();
    scheduleHistoryRefresh();
}

window.exportHistory = async function () {
    try {
        const resp = await api('/api/export');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'agent_history.json';
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { showToast(`Không sao lưu được: ${e.message}`, 'error'); }
};

window.importHistory = async function (event) {
    const f = event.target.files[0];
    event.target.value = '';
    if (!f) return;
    try {
        const resp = await api('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: await f.text() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const d = await resp.json();
        showToast(`Đã khôi phục ${d.runs} run và ${d.conversations} cuộc trò chuyện.`, 'success');
        scheduleHistoryRefresh();
    } catch (e) { showToast(`Không khôi phục được: ${e.message}`, 'error'); }
};

// ───────────────────────── API key, kết nối, credits ─────────────────────────
window.fetchCredits = async function (toast = false) {
    const amount = document.getElementById('credits-amount');
    const key = getKey();
    if (!key) { amount.textContent = '💳 (chưa có key)'; if (toast) showToast('Hãy nhập API key.', 'info'); return; }
    amount.textContent = '💳 …';
    try {
        const resp = await api('/api/credits');
        const d = await resp.json();
        if (d.ok) {
            const r = d.remaining;
            amount.textContent = `💳 $${r > 0 && r < 0.01 ? r.toFixed(4) : r.toFixed(2)}`;
            amount.parentElement.title = `Đã nạp $${d.total_credits.toFixed(2)} · đã dùng $${d.total_usage.toFixed(2)}`;
            if (toast) showToast(`Số dư còn lại: $${r.toFixed(4)}`, 'success');
        } else {
            amount.textContent = '💳 Lỗi key';
            if (toast) showToast(`Không lấy được số dư: ${d.error || 'lỗi'}`, 'error');
        }
    } catch (e) { amount.textContent = '💳 --'; }
};

window.saveApiKey = function () {
    const key = el.apiKey.value.trim();
    if (!key) { showToast('Vui lòng nhập OpenRouter API Key.', 'error'); return; }
    store.set('api_key', key);
    showToast('Đã lưu API key (chỉ nằm trong trình duyệt này).', 'success');
    fetchCredits(true);
    scheduleHistoryRefresh();
};

window.clearApiKey = function () {
    if (!getKey()) return;
    if (!confirm('Xóa API key đã lưu khỏi trình duyệt?')) return;
    store.del('api_key');
    el.apiKey.value = '';
    document.getElementById('credits-amount').textContent = '💳 $--';
    state.history = { runs: [], conversations: [] };
    renderHistory();
};

window.toggleKeyVisibility = () => { el.apiKey.type = el.apiKey.type === 'password' ? 'text' : 'password'; };

window.saveConnection = function () {
    store.set('profile', el.profile.value.trim());
    store.set('app_token', el.appToken.value.trim());
    showToast('Đã lưu. Đang kết nối lại…', 'success');
    connectWS();
};

// ───────────────────────── Preset model ─────────────────────────
const DEFAULT_PRESETS = [
    {
        id: 'free', name: '🎁 Miễn phí (Free)',
        defaultModels: 'meta-llama/llama-3.3-70b-instruct:free, qwen/qwen-2.5-coder-32b-instruct:free',
        plannerModels: 'meta-llama/llama-3.3-70b-instruct:free, deepseek/deepseek-r1:free',
        testerModels: 'qwen/qwen-2.5-coder-32b-instruct:free, meta-llama/llama-3.3-70b-instruct:free',
        coderModels: 'qwen/qwen-2.5-coder-32b-instruct:free, meta-llama/llama-3.3-70b-instruct:free',
        reviewerModels: 'deepseek/deepseek-r1:free, meta-llama/llama-3.3-70b-instruct:free',
        usePlanner: true, useReviewer: true, maxRounds: 5, tokenBudget: 200000, hardBudget: true
    },
    {
        id: 'budget', name: '🌟 Ngon - Bổ - Rẻ (DeepSeek)',
        defaultModels: 'deepseek/deepseek-chat, google/gemini-2.0-flash-001',
        plannerModels: 'deepseek/deepseek-r1, deepseek/deepseek-chat',
        testerModels: 'qwen/qwen-2.5-coder-32b-instruct, deepseek/deepseek-chat',
        coderModels: 'deepseek/deepseek-chat, qwen/qwen-2.5-coder-32b-instruct, google/gemini-2.0-flash-001',
        reviewerModels: 'deepseek/deepseek-r1, deepseek/deepseek-chat',
        usePlanner: true, useReviewer: true, maxRounds: 5, tokenBudget: 200000, hardBudget: true
    },
    {
        id: 'pro', name: '👑 Cao cấp (Claude / GPT)',
        defaultModels: 'anthropic/claude-3.7-sonnet, openai/gpt-4o',
        plannerModels: 'anthropic/claude-3.7-sonnet, openai/o3-mini',
        testerModels: 'anthropic/claude-3.7-sonnet, openai/gpt-4o-mini',
        coderModels: 'anthropic/claude-3.7-sonnet, deepseek/deepseek-chat, openai/gpt-4o',
        reviewerModels: 'openai/o3-mini, anthropic/claude-3.7-sonnet',
        usePlanner: true, useReviewer: true, maxRounds: 6, tokenBudget: 400000, hardBudget: true
    }
];

let activePresetId = store.get('active_preset_id', 'budget');

function getPresets() {
    const raw = store.get('agent_presets');
    if (!raw) { savePresets(DEFAULT_PRESETS); return DEFAULT_PRESETS; }
    try {
        const p = JSON.parse(raw);
        return Array.isArray(p) && p.length ? p : DEFAULT_PRESETS;
    } catch (e) { return DEFAULT_PRESETS; }
}
function savePresets(list) { store.set('agent_presets', JSON.stringify(list)); }

function presetFromForm(base) {
    const s = readSettings();
    return {
        ...base,
        defaultModels: s.default_models, plannerModels: s.planner_models, testerModels: s.tester_models,
        coderModels: s.coder_models, reviewerModels: s.reviewer_models, usePlanner: s.use_planner,
        useReviewer: s.use_reviewer, maxRounds: s.max_rounds, tokenBudget: s.token_budget, hardBudget: s.hard_budget
    };
}

function renderPresetButtons() {
    const box = document.getElementById('preset-buttons-container');
    const presets = getPresets();
    let cur = presets.find(p => p.id === activePresetId);
    if (!cur) { cur = presets[0]; activePresetId = cur.id; store.set('active_preset_id', cur.id); }
    box.innerHTML = '';
    presets.forEach(p => {
        const wrap = document.createElement('div');
        wrap.className = 'preset-item-wrap' + (p.id === activePresetId ? ' active' : '');
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'preset-btn-name'; b.textContent = p.name;
        b.onclick = () => selectPreset(p.id);
        wrap.appendChild(b);
        if (presets.length > 1) {
            const d = document.createElement('button');
            d.type = 'button'; d.className = 'preset-btn-del'; d.textContent = '✕'; d.title = `Xóa "${p.name}"`;
            d.onclick = (e) => { e.stopPropagation(); deletePresetById(p.id); };
            wrap.appendChild(d);
        }
        box.appendChild(wrap);
    });
    document.getElementById('current-preset-name').textContent = cur.name;
}

function selectPreset(id, initial = false) {
    const p = getPresets().find(x => x.id === id);
    if (!p) return;
    activePresetId = id;
    store.set('active_preset_id', id);
    if (p.defaultModels !== undefined) el.defaultModels.value = p.defaultModels;
    if (p.plannerModels !== undefined) el.plannerModels.value = p.plannerModels;
    if (p.testerModels !== undefined) el.testerModels.value = p.testerModels;
    if (p.coderModels !== undefined) el.coderModels.value = p.coderModels;
    if (p.reviewerModels !== undefined) el.reviewerModels.value = p.reviewerModels;
    if (p.usePlanner !== undefined) el.usePlanner.checked = p.usePlanner;
    if (p.useReviewer !== undefined) el.useReviewer.checked = p.useReviewer;
    if (p.maxRounds !== undefined) { el.maxRounds.value = p.maxRounds; el.roundsVal.textContent = p.maxRounds; }
    el.tokenBudget.value = p.tokenBudget !== undefined ? p.tokenBudget : 200000;
    el.hardBudget.checked = p.hardBudget !== undefined ? p.hardBudget : true;
    renderPresetButtons();
    if (!initial) showToast(`Đã chuyển sang: ${p.name}`, 'success');
}

window.saveCurrentPresetChanges = function () {
    const presets = getPresets();
    const i = presets.findIndex(p => p.id === activePresetId);
    if (i < 0) return;
    presets[i] = presetFromForm(presets[i]);
    savePresets(presets);
    showToast(`Đã lưu vào "${presets[i].name}".`, 'success');
};

window.addNewPreset = function () {
    const name = prompt('Tên cho cấu hình mới:', 'Cấu hình mới');
    if (!name || !name.trim()) return;
    const presets = getPresets();
    const np = presetFromForm({ id: 'preset_' + Date.now(), name: name.trim() });
    presets.push(np);
    savePresets(presets);
    selectPreset(np.id);
};

window.renameCurrentPreset = function () {
    const presets = getPresets();
    const p = presets.find(x => x.id === activePresetId);
    if (!p) return;
    const n = prompt('Đổi tên cấu hình:', p.name);
    if (!n || !n.trim() || n.trim() === p.name) return;
    p.name = n.trim();
    savePresets(presets);
    renderPresetButtons();
};

window.deleteCurrentPreset = () => deletePresetById(activePresetId);

function deletePresetById(id) {
    const presets = getPresets();
    if (presets.length <= 1) { showToast('Cần giữ lại ít nhất 1 cấu hình.', 'warning'); return; }
    const t = presets.find(p => p.id === id);
    if (!t || !confirm(`Xóa cấu hình "${t.name}"?`)) return;
    const rest = presets.filter(p => p.id !== id);
    savePresets(rest);
    if (activePresetId === id) activePresetId = rest[0].id;
    selectPreset(activePresetId);
}

window.resetToDefaultPresets = function () {
    if (!confirm('Khôi phục 3 cấu hình mặc định? (Chỉnh sửa của bạn sẽ mất)')) return;
    savePresets(DEFAULT_PRESETS);
    activePresetId = 'budget';
    selectPreset('budget');
};

// ───────────────────────── Toast & theme ─────────────────────────
function showToast(message, type = 'info') {
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.textContent = message;
    el.toastContainer.appendChild(t);
    setTimeout(() => {
        t.style.transition = 'opacity .3s';
        t.style.opacity = '0';
        setTimeout(() => t.remove(), 300);
    }, 4000);
}

const DEFAULT_THEME = { title: 'AI Code Agent', logo: '🤖', accent: '#58a6ff', bg: 'github' };
const THEME_BG = {
    github: { bg: '#0d1117', surface: '#161b22', border: '#30363d' },
    oled: { bg: '#000000', surface: '#111111', border: '#222222' },
    midnight: { bg: '#060913', surface: '#0f172a', border: '#1e293b' },
    slate: { bg: '#111318', surface: '#1b1e24', border: '#282c34' }
};

function getTheme() {
    try { return { ...DEFAULT_THEME, ...JSON.parse(store.get('app_theme', '{}')) }; }
    catch (e) { return { ...DEFAULT_THEME }; }
}

function paintBg(key) {
    const s = THEME_BG[key] || THEME_BG.github, r = document.documentElement.style;
    r.setProperty('--bg', s.bg); r.setProperty('--surface', s.surface); r.setProperty('--border', s.border);
}

function applyTheme(t) {
    document.getElementById('app-title').textContent = t.title || DEFAULT_THEME.title;
    document.getElementById('app-logo').textContent = t.logo || DEFAULT_THEME.logo;
    document.title = `${t.logo || '🤖'} ${t.title || DEFAULT_THEME.title}`;
    document.documentElement.style.setProperty('--accent', t.accent || DEFAULT_THEME.accent);
    paintBg(t.bg);
}

window.openThemeModal = function () {
    const t = getTheme();
    document.getElementById('custom-app-title').value = t.title || '';
    document.getElementById('custom-app-logo').value = t.logo || '';
    document.getElementById('custom-accent-color').value = t.accent || '#58a6ff';
    const r = document.querySelector(`input[name="theme-bg"][value="${t.bg}"]`);
    if (r) r.checked = true;
    document.getElementById('theme-modal').classList.remove('hidden');
};
window.closeThemeModal = () => document.getElementById('theme-modal').classList.add('hidden');
window.setLogoEmoji = (e) => { document.getElementById('custom-app-logo').value = e; };
window.setThemeAccent = (c) => {
    document.getElementById('custom-accent-color').value = c;
    document.documentElement.style.setProperty('--accent', c);
};
window.setThemeBg = paintBg;
window.saveThemeSettings = function () {
    const r = document.querySelector('input[name="theme-bg"]:checked');
    const t = {
        title: document.getElementById('custom-app-title').value.trim() || DEFAULT_THEME.title,
        logo: document.getElementById('custom-app-logo').value.trim() || DEFAULT_THEME.logo,
        accent: document.getElementById('custom-accent-color').value || DEFAULT_THEME.accent,
        bg: r ? r.value : 'github'
    };
    store.set('app_theme', JSON.stringify(t));
    applyTheme(t);
    closeThemeModal();
    showToast('Đã áp dụng giao diện.', 'success');
};
window.resetThemeDefaults = function () {
    if (!confirm('Khôi phục giao diện mặc định?')) return;
    store.del('app_theme');
    applyTheme(DEFAULT_THEME);
    closeThemeModal();
};

// ───────────────────────── Khởi động ─────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initDom();
    applyTheme(getTheme());
    renderPresetButtons();
    selectPreset(activePresetId, true);
    renderHistory();

    // Khôi phục phiên làm việc cũ (tải lại trang / mở lại tab / đổi máy với cùng mã đồng bộ)
    const lastRun = store.get('last_run');
    const lastConv = store.get('last_conv');
    if (lastRun) { state.runId = lastRun; state.fillCommand = true; }
    if (lastConv && !lastRun) {
        state.convId = lastConv;
        switchMode('chat');
        if (hasIdentity()) reloadConversation();
    }

    connectWS();           // onConnected() sẽ identify + attach lại run đang dở
    fetchCredits(false);
});
