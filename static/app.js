// State
const state = {
    ws: null,
    connected: false,
    running: false,
    mode: 'pipeline', // 'pipeline' | 'chat'
    chatHistory: [],
    apiKey: localStorage.getItem('api_key') || '',
    currentTab: 'log',
    files: {},
    activeFile: null,
    spec: '',
    zipUrl: null,
    sessionId: null,
    reconnectTimer: null,
    reconnectDelay: 1000
};

// Role labels and icons
const ROLE_META = {
    planner: { name: 'Kiến trúc sư', icon: '📐', color: 'var(--role-planner)' },
    tester: { name: 'Kiểm thử viên', icon: '🧪', color: 'var(--role-tester)' },
    coder: { name: 'Lập trình viên', icon: '💻', color: 'var(--role-coder)' },
    reviewer: { name: 'Reviewer', icon: '🔍', color: 'var(--role-reviewer)' },
    system: { name: 'Hệ thống', icon: '⚙️', color: 'var(--role-system)' }
};

// Danh sách cấu hình mặc định gốc
const DEFAULT_PRESETS = [
    {
        id: 'free',
        name: '🎁 Miễn phí (Free)',
        defaultModels: 'meta-llama/llama-3.3-70b-instruct:free, qwen/qwen-2.5-coder-32b-instruct:free',
        plannerModels: 'meta-llama/llama-3.3-70b-instruct:free, deepseek/deepseek-r1:free',
        testerModels: 'qwen/qwen-2.5-coder-32b-instruct:free, meta-llama/llama-3.3-70b-instruct:free',
        coderModels: 'qwen/qwen-2.5-coder-32b-instruct:free, meta-llama/llama-3.3-70b-instruct:free',
        reviewerModels: 'deepseek/deepseek-r1:free, meta-llama/llama-3.3-70b-instruct:free',
        usePlanner: true,
        useReviewer: true,
        maxRounds: 5
    },
    {
        id: 'budget',
        name: '🌟 Ngon - Bổ - Rẻ (DeepSeek)',
        defaultModels: 'deepseek/deepseek-chat, google/gemini-2.0-flash-001',
        plannerModels: 'deepseek/deepseek-r1, deepseek/deepseek-chat',
        testerModels: 'qwen/qwen-2.5-coder-32b-instruct, deepseek/deepseek-chat',
        coderModels: 'deepseek/deepseek-chat, qwen/qwen-2.5-coder-32b-instruct, google/gemini-2.0-flash-001',
        reviewerModels: 'deepseek/deepseek-r1, deepseek/deepseek-chat',
        usePlanner: true,
        useReviewer: true,
        maxRounds: 5
    },
    {
        id: 'pro',
        name: '👑 Cao cấp (Claude / GPT)',
        defaultModels: 'anthropic/claude-3.7-sonnet, openai/gpt-4o',
        plannerModels: 'anthropic/claude-3.7-sonnet, openai/o3-mini',
        testerModels: 'anthropic/claude-3.7-sonnet, openai/gpt-4o-mini',
        coderModels: 'anthropic/claude-3.7-sonnet, deepseek/deepseek-chat, openai/gpt-4o',
        reviewerModels: 'openai/o3-mini, anthropic/claude-3.7-sonnet',
        usePlanner: true,
        useReviewer: true,
        maxRounds: 6
    }
];

function getPresets() {
    const raw = localStorage.getItem('agent_presets');
    if (!raw) {
        savePresets(DEFAULT_PRESETS);
        return DEFAULT_PRESETS;
    }
    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) && parsed.length > 0 ? parsed : DEFAULT_PRESETS;
    } catch (e) {
        return DEFAULT_PRESETS;
    }
}

function savePresets(list) {
    localStorage.setItem('agent_presets', JSON.stringify(list));
}

let activePresetId = localStorage.getItem('active_preset_id') || 'budget';

// DOM references
let el = {};

function initDom() {
    el = {
        connStatus: document.getElementById('conn-status'),
        connText: document.getElementById('conn-text'),
        apiKey: document.getElementById('api-key'),
        command: document.getElementById('command'),
        btnRun: document.getElementById('btn-run'),
        btnStop: document.getElementById('btn-stop'),
        btnReset: document.getElementById('btn-reset'),
        progressWrap: document.getElementById('progress-wrap'),
        progressFill: document.getElementById('progress-fill'),
        progressLabel: document.getElementById('progress-label'),
        logArea: document.getElementById('log-area'),
        specArea: document.getElementById('spec-area'),
        fileTabs: document.getElementById('file-tabs'),
        codeArea: document.getElementById('code-area'),
        downloadStatus: document.getElementById('download-status'),
        btnDownload: document.getElementById('btn-download'),
        settingsBody: document.getElementById('settings-body'),
        settingsArrow: document.getElementById('settings-arrow'),
        defaultModels: document.getElementById('default-models'),
        plannerModels: document.getElementById('planner-models'),
        testerModels: document.getElementById('tester-models'),
        coderModels: document.getElementById('coder-models'),
        reviewerModels: document.getElementById('reviewer-models'),
        usePlanner: document.getElementById('use-planner'),
        useReviewer: document.getElementById('use-reviewer'),
        maxRounds: document.getElementById('max-rounds'),
        roundsVal: document.getElementById('rounds-val'),
        toastContainer: document.getElementById('toast-container'),
        tokenTracker: document.getElementById('token-tracker'),
        tokenTotalCount: document.getElementById('token-total-count'),
        tokenDetail: document.getElementById('token-detail'),
        tokenValPlanner: document.getElementById('token-val-planner'),
        tokenValTester: document.getElementById('token-val-tester'),
        tokenValCoder: document.getElementById('token-val-coder'),
        tokenValReviewer: document.getElementById('token-val-reviewer')
    };

    if (state.apiKey) {
        el.apiKey.value = state.apiKey;
    }

    // Code area manual edits update active file or project_text
    el.codeArea.addEventListener('input', () => {
        if (state.activeFile && state.files[state.activeFile] !== undefined) {
            state.files[state.activeFile] = el.codeArea.value;
        }
    });

    // Drag & Drop files directly onto interaction card
    const dropZone = document.getElementById('drop-zone');
    if (dropZone) {
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.add('drag-over');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.remove('drag-over');
            }, false);
        });

        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files && files.length > 0) {
                uploadFilesToServer(files);
            }
        });
    }
}

// WebSocket Connection
function connectWS() {
    if (state.ws) {
        try { state.ws.close(); } catch (e) {}
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    updateConnState(false, 'Đang kết nối...');

    try {
        state.ws = new WebSocket(wsUrl);
    } catch (e) {
        onDisconnect();
        return;
    }

    state.ws.onopen = () => {
        updateConnState(true, 'Đã kết nối');
        state.reconnectDelay = 1000;
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
    };

    state.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleServerMessage(data);
        } catch (err) {
            console.error('Parse WS message error:', err, event.data);
        }
    };

    state.ws.onclose = () => {
        onDisconnect();
    };

    state.ws.onerror = () => {
        onDisconnect();
    };
}

function onDisconnect() {
    updateConnState(false, 'Mất kết nối (đang thử lại...)');
    setRunning(false);

    if (!state.reconnectTimer) {
        state.reconnectTimer = setTimeout(() => {
            state.reconnectTimer = null;
            state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 15000);
            connectWS();
        }, state.reconnectDelay);
    }
}

function updateConnState(connected, text) {
    state.connected = connected;
    if (el.connStatus) {
        el.connStatus.className = connected ? 'conn-dot connected' : 'conn-dot disconnected';
        el.connStatus.title = text;
    }
    if (el.connText) {
        el.connText.textContent = text;
    }
}

function sendWS(payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        showToast('Chưa kết nối tới server. Vui lòng thử lại.', 'error');
        return false;
    }
    state.ws.send(JSON.stringify(payload));
    return true;
}

// Message Handlers
function handleServerMessage(data) {
    switch (data.type) {
        case 'session':
            state.sessionId = data.session_id;
            break;

        case 'log':
            appendLog(data.role, data.message);
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
            break;

        case 'chat_reply':
            appendChatBubble('assistant', data.content, data.model);
            state.chatHistory.push({ role: 'assistant', content: data.content });
            if (data.usage) {
                updateTokenStats({
                    total: data.usage.total_tokens || 0,
                    prompt: data.usage.prompt_tokens || 0,
                    completion: data.usage.completion_tokens || 0,
                    by_role: { coder: { total: data.usage.total_tokens || 0 } }
                });
            }
            setRunning(false);
            break;

        case 'done':
            setRunning(false);
            state.files = data.files || state.files;
            renderFiles();
            state.zipUrl = data.zip_url;
            updateDownload(data.ok, data.zip_url);
            if (data.tokens) {
                updateTokenStats(data.tokens);
            }
            if (data.ok) {
                showToast('Hoàn thành xuất sắc toàn bộ quy trình!', 'success');
            } else {
                showToast('Chưa đạt toàn bộ test, đã lưu phiên bản tốt nhất.', 'error');
            }
            break;

        case 'error':
            showToast(data.message || 'Có lỗi xảy ra', 'error');
            appendLog('system', `❌ Lỗi: ${data.message}`);
            setRunning(false);
            break;

        default:
            console.log('Unknown message type:', data);
    }
}

function appendLog(role, message) {
    const isFirst = el.logArea.querySelector('.log-placeholder');
    if (isFirst) {
        el.logArea.innerHTML = '';
    }

    const entry = document.createElement('div');
    const safeRole = role || 'system';
    entry.className = `log-entry role-${safeRole}`;

    const meta = ROLE_META[safeRole] || ROLE_META.system;
    const timeStr = new Date().toLocaleTimeString('vi-VN');

    entry.innerHTML = `<span style="color: ${meta.color}; font-weight: 600;">${meta.icon} [${meta.name}]</span> <span style="color: var(--text-secondary); font-size: 11px;">${timeStr}</span>\n${escapeHtml(message)}`;

    el.logArea.appendChild(entry);
    el.logArea.scrollTop = el.logArea.scrollHeight;
}

function appendChatBubble(role, content, model = '') {
    const isPlaceholder = el.logArea.querySelector('.log-placeholder');
    if (isPlaceholder) {
        el.logArea.innerHTML = '';
    }

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    const authorDiv = document.createElement('div');
    authorDiv.className = 'chat-bubble-author';
    const timeStr = new Date().toLocaleTimeString('vi-VN');

    if (role === 'user') {
        authorDiv.innerHTML = `<span>👤 Bạn</span> <span style="color: var(--text-secondary); font-size: 11px;">${timeStr}</span>`;
    } else {
        authorDiv.innerHTML = `<span>🤖 AI (${model || 'Trợ lý'})</span> <span style="color: var(--text-secondary); font-size: 11px;">${timeStr}</span>`;
    }

    const contentDiv = document.createElement('div');
    contentDiv.textContent = content;

    bubble.appendChild(authorDiv);
    bubble.appendChild(contentDiv);
    el.logArea.appendChild(bubble);
    el.logArea.scrollTop = el.logArea.scrollHeight;
}

function renderSpec() {
    if (!state.spec) {
        el.specArea.innerHTML = '<div class="log-placeholder">Chưa có đặc tả.</div>';
        return;
    }
    el.specArea.textContent = state.spec;
}

function renderFiles() {
    const fileNames = Object.keys(state.files);
    el.fileTabs.innerHTML = '';

    if (fileNames.length === 0) {
        state.activeFile = null;
        if (!el.codeArea.value.trim()) {
            el.codeArea.placeholder = 'Chưa có file nào được tạo.';
        }
        return;
    }

    if (!state.activeFile || !state.files[state.activeFile]) {
        // Ưu tiên file không phải test, nếu không thì lấy file đầu tiên
        const mainFile = fileNames.find(n => !n.startsWith('test_')) || fileNames[0];
        state.activeFile = mainFile;
    }

    fileNames.sort().forEach(name => {
        const btn = document.createElement('button');
        btn.className = `file-tab-btn ${name === state.activeFile ? 'active' : ''}`;
        btn.textContent = name;
        btn.onclick = () => {
            state.activeFile = name;
            renderFiles();
        };
        el.fileTabs.appendChild(btn);
    });

    el.codeArea.value = state.files[state.activeFile] || '';
}

function renderAttachedChips() {
    const list = document.getElementById('attached-chips-list');
    const hint = document.getElementById('attachment-hint');
    if (!list) return;

    const fileNames = Object.keys(state.files);
    list.innerHTML = '';

    if (fileNames.length === 0) {
        if (hint) hint.textContent = 'Bạn có thể đính kèm file code hoặc tài liệu để AI đọc';
        return;
    }

    if (hint) hint.textContent = `Đã nạp ${fileNames.length} file vào ngữ cảnh:`;

    fileNames.forEach(name => {
        const chip = document.createElement('div');
        chip.className = 'file-chip';
        chip.innerHTML = `<span>📄 ${escapeHtml(name)}</span>`;

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'file-chip-del';
        del.textContent = '✕';
        del.title = `Gỡ file ${name}`;
        del.onclick = (e) => {
            e.stopPropagation();
            removeAttachedFile(name);
        };

        chip.appendChild(del);
        list.appendChild(chip);
    });
}

window.removeAttachedFile = function(name) {
    if (state.files[name]) {
        delete state.files[name];
        if (state.activeFile === name) {
            state.activeFile = Object.keys(state.files)[0] || null;
        }
        renderFiles();
        renderAttachedChips();
        showToast(`Đã gỡ file ${name}`, 'info');
    }
};

window.triggerFileUpload = function() {
    const uploader = document.getElementById('file-uploader');
    if (uploader) uploader.click();
};

window.handleFileUpload = async function(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    await uploadFilesToServer(files);
    event.target.value = '';
};

async function uploadFilesToServer(fileList) {
    const formData = new FormData();
    for (let i = 0; i < fileList.length; i++) {
        formData.append('files', fileList[i]);
    }

    showToast('Đang tải và giải nén/đọc file...', 'info');

    try {
        const resp = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        const data = await resp.json();
        if (data.ok && data.files) {
            const addedCount = Object.keys(data.files).length;
            if (addedCount === 0) {
                showToast('Không đọc được file văn bản nào hợp lệ.', 'warning');
                return;
            }
            Object.assign(state.files, data.files);
            renderFiles();
            renderAttachedChips();
            switchTab('code');
            showToast(`Đã nạp thành công ${addedCount} file để AI đọc!`, 'success');
        } else {
            showToast('Lỗi khi tải file lên.', 'error');
        }
    } catch (e) {
        showToast(`Lỗi upload: ${e.message}`, 'error');
    }
}

function updateProgress(stage, step, total, status) {
    el.progressWrap.classList.remove('hidden');
    const meta = ROLE_META[stage] || ROLE_META.system;
    const pct = total > 0 ? Math.min(100, Math.round((step / total) * 100)) : 0;

    el.progressFill.style.width = `${pct}%`;
    el.progressFill.style.backgroundColor = meta.color;

    let statusText = status === 'done' ? '✓ Hoàn thành' : (status === 'error' ? '✗ Lỗi' : 'Đang xử lý...');
    el.progressLabel.textContent = `${meta.icon} ${meta.name} (Bước ${step}/${total}) — ${statusText}`;
}

function updateDownload(ok, zipUrl) {
    if (zipUrl) {
        el.downloadStatus.innerHTML = ok
            ? '<span style="color: var(--success); font-weight: 600;">✓ Dự án đã tạo thành công và sẵn sàng để tải về!</span>'
            : '<span style="color: var(--warning); font-weight: 600;">⚠ Dự án đã đóng gói phiên bản hiện tại (một số test chưa pass).</span>';
        el.btnDownload.classList.remove('hidden');
    } else {
        el.downloadStatus.textContent = 'Chưa có file nén để tải.';
        el.btnDownload.classList.add('hidden');
    }
}

function updateTokenStats(stats) {
    if (!stats || !el.tokenTracker) return;
    el.tokenTracker.classList.remove('hidden');

    const total = stats.total || 0;
    const prompt = stats.prompt || 0;
    const completion = stats.completion || 0;

    if (el.tokenTotalCount) {
        el.tokenTotalCount.textContent = `${total.toLocaleString()} tokens`;
        if (state.tokenBudget && state.tokenBudget > 0) {
            if (total >= state.tokenBudget) {
                el.tokenTotalCount.className = 'token-badge-total budget-exceeded';
                el.tokenTotalCount.title = `Vượt hạn mức ${state.tokenBudget.toLocaleString()} tokens!`;
            } else if (total >= state.tokenBudget * 0.8) {
                el.tokenTotalCount.className = 'token-badge-total budget-warning';
                el.tokenTotalCount.title = `Sắp chạm hạn mức ${state.tokenBudget.toLocaleString()} tokens`;
            } else {
                el.tokenTotalCount.className = 'token-badge-total';
                el.tokenTotalCount.title = '';
            }
        }
    }
    if (el.tokenDetail) el.tokenDetail.textContent = `(Prompt: ${prompt.toLocaleString()} | Output: ${completion.toLocaleString()})`;

    const byRole = stats.by_role || {};
    if (el.tokenValPlanner && byRole.planner) {
        el.tokenValPlanner.textContent = `${(byRole.planner.total || 0).toLocaleString()}`;
    }
    if (el.tokenValTester && byRole.tester) {
        el.tokenValTester.textContent = `${(byRole.tester.total || 0).toLocaleString()}`;
    }
    if (el.tokenValCoder && byRole.coder) {
        el.tokenValCoder.textContent = `${(byRole.coder.total || 0).toLocaleString()}`;
    }
    if (el.tokenValReviewer && byRole.reviewer) {
        el.tokenValReviewer.textContent = `${(byRole.reviewer.total || 0).toLocaleString()}`;
    }
}

function setRunning(running) {
    state.running = running;
    el.btnRun.disabled = running;
    el.btnStop.disabled = !running;
    if (!running) {
        setTimeout(() => {
            // Keep progress bar visible for inspection
        }, 1000);
    }
}

// Kiểm tra số dư / Credits OpenRouter
window.fetchCredits = async function(showToastMsg = false) {
    const key = (el.apiKey ? el.apiKey.value.trim() : '') || state.apiKey;
    const amountEl = document.getElementById('credits-amount');
    if (!amountEl) return;

    if (!key) {
        amountEl.textContent = '💳 Số dư: (Chưa có key)';
        if (showToastMsg) showToast('Hãy nhập API Key để kiểm tra số dư!', 'info');
        return;
    }

    amountEl.textContent = '💳 Số dư: Đang tính...';

    try {
        const resp = await fetch(`/api/credits?api_key=${encodeURIComponent(key)}`);
        const data = await resp.json();
        if (data.ok) {
            const rem = data.remaining;
            amountEl.textContent = `💳 Số dư: $${rem < 0.01 && rem > 0 ? rem.toFixed(4) : rem.toFixed(2)}`;
            amountEl.title = `Tổng nạp: $${data.total_credits.toFixed(2)} | Đã dùng: $${data.total_usage.toFixed(2)} | Còn lại: $${rem.toFixed(4)}`;
            if (showToastMsg) {
                showToast(`Số dư OpenRouter còn lại: $${rem.toFixed(4)}`, 'success');
            }
        } else {
            amountEl.textContent = '💳 Số dư: Lỗi key';
            if (showToastMsg) showToast(`Không lấy được số dư: ${data.error || 'Lỗi'}`, 'error');
        }
    } catch (err) {
        amountEl.textContent = '💳 Số dư: --';
    }
};

// User Actions
window.saveApiKey = function() {
    const key = el.apiKey.value.trim();
    if (!key) {
        showToast('Vui lòng nhập OpenRouter API Key!', 'error');
        return;
    }
    localStorage.setItem('api_key', key);
    state.apiKey = key;
    showToast('Đã lưu API Key thành công!', 'success');
    fetchCredits(true);
};

window.clearApiKey = function() {
    if (!localStorage.getItem('api_key') && !el.apiKey.value) return;
    if (confirm('Bạn có chắc muốn xóa API Key đã lưu khỏi trình duyệt?')) {
        localStorage.removeItem('api_key');
        state.apiKey = '';
        el.apiKey.value = '';
        const amountEl = document.getElementById('credits-amount');
        if (amountEl) amountEl.textContent = '💳 Số dư: $--';
        showToast('Đã xóa API Key thành công.', 'info');
    }
};

window.toggleKeyVisibility = function() {
    el.apiKey.type = el.apiKey.type === 'password' ? 'text' : 'password';
};

window.switchMode = function(mode) {
    state.mode = mode;
    const btnPipe = document.getElementById('btn-mode-pipeline');
    const btnChat = document.getElementById('btn-mode-chat');
    const inputLabel = document.getElementById('input-label');
    const cmdArea = el.command;
    const btnRun = el.btnRun;

    if (btnPipe) btnPipe.classList.toggle('active', mode === 'pipeline');
    if (btnChat) btnChat.classList.toggle('active', mode === 'chat');

    if (mode === 'pipeline') {
        if (inputLabel) inputLabel.textContent = '📝 Yêu cầu lập trình của bạn';
        if (cmdArea) cmdArea.placeholder = 'Ví dụ: Tạo logic tính cashback 5% cho đơn hàng, tối đa 50.000đ';
        if (btnRun) btnRun.textContent = '▶ Chạy (4 AI)';
        showToast('Chế độ: Lập trình tự động (Kiến trúc sư → Tester → Coder → Reviewer)', 'info');
    } else {
        if (inputLabel) inputLabel.textContent = '💬 Nhập câu hỏi hoặc trò chuyện với AI';
        if (cmdArea) cmdArea.placeholder = 'Nhập tin nhắn hoặc câu hỏi bình thường (ví dụ: "Giải thích file code tôi vừa tải lên", "Xin chào!")...';
        if (btnRun) btnRun.textContent = '💬 Gửi tin nhắn';
        showToast('Chế độ: Trò chuyện & Hỏi đáp (AI trả lời bình thường, không chạy test/code tự động)', 'info');
    }
};

window.handleMainAction = function() {
    if (state.mode === 'chat') {
        sendChat();
    } else {
        run();
    }
};

window.sendChat = function() {
    const msg = el.command.value.trim();
    if (!msg) {
        showToast('Vui lòng nhập tin nhắn của bạn!', 'error');
        el.command.focus();
        return;
    }

    const key = el.apiKey.value.trim() || state.apiKey;
    if (!key) {
        showToast('Vui lòng nhập và lưu OpenRouter API Key!', 'error');
        el.apiKey.focus();
        return;
    }

    appendChatBubble('user', msg);
    state.chatHistory.push({ role: 'user', content: msg });
    el.command.value = '';

    sendWS({
        type: 'chat',
        message: msg,
        history: state.chatHistory.slice(-8),
        files: state.files,
        default_models: el.defaultModels.value.trim(),
        api_key: key
    });

    setRunning(true);
    switchTab('log');
};

window.run = function() {
    const cmd = el.command.value.trim();
    if (!cmd) {
        showToast('Vui lòng nhập yêu cầu của bạn!', 'error');
        el.command.focus();
        return;
    }

    const key = el.apiKey.value.trim() || state.apiKey;
    if (!key) {
        showToast('Vui lòng nhập và lưu OpenRouter API Key!', 'error');
        el.apiKey.focus();
        return;
    }

    // Format current code if any
    let projectText = '';
    const fileNames = Object.keys(state.files);
    if (fileNames.length > 0) {
        projectText = fileNames.map(name => `### FILE: ${name}\n\`\`\`python\n${state.files[name].trim()}\n\`\`\``).join('\n\n');
    } else if (el.codeArea.value.trim()) {
        projectText = el.codeArea.value.trim();
    }

    const payload = {
        type: 'run',
        command: cmd,
        project_text: projectText,
        default_models: el.defaultModels.value.trim(),
        planner_models: el.plannerModels.value.trim(),
        tester_models: el.testerModels.value.trim(),
        coder_models: el.coderModels.value.trim(),
        reviewer_models: el.reviewerModels.value.trim(),
        use_planner: el.usePlanner.checked,
        use_reviewer: el.useReviewer.checked,
        max_rounds: parseInt(el.maxRounds.value, 10) || 5,
        api_key: key
    };

    if (!sendWS(payload)) return;

    setRunning(true);
    if (el.tokenTracker) el.tokenTracker.classList.remove('hidden');
    if (el.tokenTotalCount) el.tokenTotalCount.textContent = '0 tokens';
    if (el.tokenDetail) el.tokenDetail.textContent = '(Prompt: 0 | Output: 0)';
    if (el.tokenValPlanner) el.tokenValPlanner.textContent = '0';
    if (el.tokenValTester) el.tokenValTester.textContent = '0';
    if (el.tokenValCoder) el.tokenValCoder.textContent = '0';
    if (el.tokenValReviewer) el.tokenValReviewer.textContent = '0';

    el.logArea.innerHTML = '';
    appendLog('system', '🚀 Đã gửi yêu cầu, đang khởi động pipeline...');
    switchTab('log');
    el.downloadStatus.textContent = 'Đang xử lý, file zip sẽ được tạo sau khi hoàn tất...';
    el.btnDownload.classList.add('hidden');
};

window.stop = function() {
    if (!state.running) return;
    sendWS({ type: 'stop' });
    appendLog('system', '⏹️ Đang gửi lệnh dừng...');
};

window.reset = function() {
    if (state.running) {
        sendWS({ type: 'stop' });
    }
    sendWS({ type: 'reset' });

    state.files = {};
    state.activeFile = null;
    state.spec = '';
    state.zipUrl = null;
    state.chatHistory = [];
    renderAttachedChips();

    el.logArea.innerHTML = '<div class="log-placeholder">Nhấn "Chạy" để bắt đầu...</div>';
    el.specArea.innerHTML = '<div class="log-placeholder">Đặc tả sẽ hiển thị ở đây sau khi kiến trúc sư hoàn thành.</div>';
    el.codeArea.value = '';
    el.fileTabs.innerHTML = '';
    el.progressWrap.classList.add('hidden');
    if (el.tokenTracker) el.tokenTracker.classList.add('hidden');
    if (el.tokenTotalCount) el.tokenTotalCount.textContent = '0 tokens';
    if (el.tokenDetail) el.tokenDetail.textContent = '(Prompt: 0 | Output: 0)';
    el.downloadStatus.textContent = 'Chưa có file để tải.';
    el.btnDownload.classList.add('hidden');

    setRunning(false);
    showToast('Đã làm mới phiên làm việc.', 'success');
};

window.switchTab = function(tabName) {
    state.currentTab = tabName;
    document.querySelectorAll('.tab').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-tab') === tabName);
    });
    document.querySelectorAll('.tab-content').forEach(c => {
        c.classList.toggle('active', c.id === `tab-${tabName}`);
    });
};

window.toggleSettings = function() {
    const isHidden = el.settingsBody.classList.contains('hidden');
    el.settingsBody.classList.toggle('hidden', !isHidden);
    el.settingsArrow.textContent = isHidden ? '▾' : '▸';
};

window.downloadZip = function() {
    if (!state.zipUrl) {
        showToast('Chưa có file zip sẵn sàng!', 'error');
        return;
    }
    window.location.href = state.zipUrl;
};

// Quản lý Preset động (Xem, Sửa, Xóa, Tạo mới)
function renderPresetButtons() {
    const container = document.getElementById('preset-buttons-container');
    if (!container) return;
    const presets = getPresets();

    let current = presets.find(p => p.id === activePresetId);
    if (!current) {
        current = presets[0];
        activePresetId = current.id;
        localStorage.setItem('active_preset_id', activePresetId);
    }

    container.innerHTML = '';
    presets.forEach(p => {
        const wrap = document.createElement('div');
        wrap.className = `preset-item-wrap ${p.id === activePresetId ? 'active' : ''}`;

        const nameBtn = document.createElement('button');
        nameBtn.type = 'button';
        nameBtn.className = 'preset-btn-name';
        nameBtn.textContent = p.name;
        nameBtn.onclick = () => selectPreset(p.id);

        wrap.appendChild(nameBtn);

        // Cho phép xóa nếu có nhiều hơn 1 preset
        if (presets.length > 1) {
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'preset-btn-del';
            delBtn.textContent = '✕';
            delBtn.title = `Xóa chế độ "${p.name}"`;
            delBtn.onclick = (e) => {
                e.stopPropagation();
                deletePresetById(p.id);
            };
            wrap.appendChild(delBtn);
        }

        container.appendChild(wrap);
    });

    const ind = document.getElementById('current-preset-name');
    if (ind && current) {
        ind.textContent = current.name;
    }
}

function selectPreset(id, isInitial = false) {
    const presets = getPresets();
    const p = presets.find(item => item.id === id);
    if (!p) return;

    activePresetId = id;
    localStorage.setItem('active_preset_id', id);

    if (p.defaultModels !== undefined) el.defaultModels.value = p.defaultModels;
    if (p.plannerModels !== undefined) el.plannerModels.value = p.plannerModels;
    if (p.testerModels !== undefined) el.testerModels.value = p.testerModels;
    if (p.coderModels !== undefined) el.coderModels.value = p.coderModels;
    if (p.reviewerModels !== undefined) el.reviewerModels.value = p.reviewerModels;
    if (p.usePlanner !== undefined) el.usePlanner.checked = p.usePlanner;
    if (p.useReviewer !== undefined) el.useReviewer.checked = p.useReviewer;
    if (p.maxRounds !== undefined) {
        el.maxRounds.value = p.maxRounds;
        el.roundsVal.textContent = p.maxRounds;
    }

    renderPresetButtons();
    if (!isInitial) {
        showToast(`Đã chuyển sang: ${p.name}`, 'success');
    }
}

window.saveCurrentPresetChanges = function() {
    const presets = getPresets();
    const idx = presets.findIndex(p => p.id === activePresetId);
    if (idx === -1) return;

    presets[idx].defaultModels = el.defaultModels.value.trim();
    presets[idx].plannerModels = el.plannerModels.value.trim();
    presets[idx].testerModels = el.testerModels.value.trim();
    presets[idx].coderModels = el.coderModels.value.trim();
    presets[idx].reviewerModels = el.reviewerModels.value.trim();
    presets[idx].usePlanner = el.usePlanner.checked;
    presets[idx].useReviewer = el.useReviewer.checked;
    presets[idx].maxRounds = parseInt(el.maxRounds.value, 10) || 5;

    savePresets(presets);
    showToast(`Đã lưu thay đổi vào chế độ "${presets[idx].name}"!`, 'success');
};

window.addNewPreset = function() {
    const name = prompt('Nhập tên cho chế độ mới:', 'Chế độ mới');
    if (!name || !name.trim()) return;

    const presets = getPresets();
    const newId = 'preset_' + Date.now();
    const newPreset = {
        id: newId,
        name: name.trim(),
        defaultModels: el.defaultModels.value.trim(),
        plannerModels: el.plannerModels.value.trim(),
        testerModels: el.testerModels.value.trim(),
        coderModels: el.coderModels.value.trim(),
        reviewerModels: el.reviewerModels.value.trim(),
        usePlanner: el.usePlanner.checked,
        useReviewer: el.useReviewer.checked,
        maxRounds: parseInt(el.maxRounds.value, 10) || 5
    };

    presets.push(newPreset);
    savePresets(presets);
    selectPreset(newId);
    showToast(`Đã tạo chế độ mới: "${newPreset.name}"!`, 'success');
};

window.renameCurrentPreset = function() {
    const presets = getPresets();
    const p = presets.find(item => item.id === activePresetId);
    if (!p) return;

    const newName = prompt('Đổi tên cho chế độ này:', p.name);
    if (!newName || !newName.trim() || newName.trim() === p.name) return;

    p.name = newName.trim();
    savePresets(presets);
    renderPresetButtons();
    showToast(`Đã đổi tên thành "${p.name}"!`, 'success');
};

window.deleteCurrentPreset = function() {
    deletePresetById(activePresetId);
};

function deletePresetById(id) {
    const presets = getPresets();
    if (presets.length <= 1) {
        showToast('Bạn phải giữ lại ít nhất 1 chế độ cấu hình.', 'warning');
        return;
    }

    const target = presets.find(p => p.id === id);
    if (!target) return;

    if (!confirm(`Bạn có chắc muốn xóa chế độ "${target.name}"?`)) {
        return;
    }

    const filtered = presets.filter(p => p.id !== id);
    savePresets(filtered);

    if (activePresetId === id) {
        activePresetId = filtered[0].id;
    }
    selectPreset(activePresetId);
    showToast(`Đã xóa chế độ "${target.name}".`, 'info');
}

window.resetToDefaultPresets = function() {
    if (!confirm('Khôi phục lại danh sách 3 chế độ mặc định ban đầu? (Các chỉnh sửa của bạn sẽ được thiết lập lại)')) {
        return;
    }
    savePresets(DEFAULT_PRESETS);
    activePresetId = 'budget';
    selectPreset('budget');
    showToast('Đã khôi phục các chế độ gốc!', 'success');
};

// Toast notification
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    el.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

function escapeHtml(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Quản lý Giao diện & Tên ứng dụng
const DEFAULT_THEME = {
    title: 'AI Code Agent',
    logo: '🤖',
    accent: '#58a6ff',
    bg: 'github',
    budget: 0
};

const THEME_BG_STYLES = {
    github: { bg: '#0d1117', surface: '#161b22', border: '#30363d' },
    oled: { bg: '#000000', surface: '#111111', border: '#222222' },
    midnight: { bg: '#060913', surface: '#0f172a', border: '#1e293b' },
    slate: { bg: '#111318', surface: '#1b1e24', border: '#282c34' }
};

function getThemeSettings() {
    try {
        const saved = localStorage.getItem('app_theme');
        return saved ? { ...DEFAULT_THEME, ...JSON.parse(saved) } : { ...DEFAULT_THEME };
    } catch (e) {
        return { ...DEFAULT_THEME };
    }
}

function applyThemeSettings(theme) {
    const root = document.documentElement;

    const titleEl = document.getElementById('app-title');
    const logoEl = document.getElementById('app-logo');
    if (titleEl) titleEl.textContent = theme.title || DEFAULT_THEME.title;
    if (logoEl) logoEl.textContent = theme.logo || DEFAULT_THEME.logo;
    document.title = `${theme.logo || '🤖'} ${theme.title || 'AI Code Agent'}`;

    if (theme.accent) {
        root.style.setProperty('--accent', theme.accent);
    }

    const bgStyle = THEME_BG_STYLES[theme.bg] || THEME_BG_STYLES.github;
    root.style.setProperty('--bg', bgStyle.bg);
    root.style.setProperty('--surface', bgStyle.surface);
    root.style.setProperty('--border', bgStyle.border);

    state.tokenBudget = parseInt(theme.budget, 10) || 0;
}

window.openThemeModal = function() {
    const theme = getThemeSettings();
    const modal = document.getElementById('theme-modal');
    if (!modal) return;

    document.getElementById('custom-app-title').value = theme.title || '';
    document.getElementById('custom-app-logo').value = theme.logo || '';
    document.getElementById('custom-accent-color').value = theme.accent || '#58a6ff';
    document.getElementById('custom-token-budget').value = theme.budget ? theme.budget : '';

    const bgRadio = document.querySelector(`input[name="theme-bg"][value="${theme.bg}"]`);
    if (bgRadio) bgRadio.checked = true;

    modal.classList.remove('hidden');
};

window.closeThemeModal = function() {
    const modal = document.getElementById('theme-modal');
    if (modal) modal.classList.add('hidden');
};

window.setLogoEmoji = function(emoji) {
    const input = document.getElementById('custom-app-logo');
    if (input) input.value = emoji;
};

window.setThemeAccent = function(color) {
    const input = document.getElementById('custom-accent-color');
    if (input) input.value = color;
    document.documentElement.style.setProperty('--accent', color);
};

window.setThemeBg = function(bgKey) {
    const bgStyle = THEME_BG_STYLES[bgKey] || THEME_BG_STYLES.github;
    const root = document.documentElement;
    root.style.setProperty('--bg', bgStyle.bg);
    root.style.setProperty('--surface', bgStyle.surface);
    root.style.setProperty('--border', bgStyle.border);
};

window.saveThemeSettings = function() {
    const title = document.getElementById('custom-app-title').value.trim() || DEFAULT_THEME.title;
    const logo = document.getElementById('custom-app-logo').value.trim() || DEFAULT_THEME.logo;
    const accent = document.getElementById('custom-accent-color').value || DEFAULT_THEME.accent;
    const bgRadio = document.querySelector('input[name="theme-bg"]:checked');
    const bg = bgRadio ? bgRadio.value : 'github';
    const budget = parseInt(document.getElementById('custom-token-budget').value, 10) || 0;

    const newTheme = { title, logo, accent, bg, budget };
    localStorage.setItem('app_theme', JSON.stringify(newTheme));
    applyThemeSettings(newTheme);
    closeThemeModal();
    showToast('Đã lưu và áp dụng giao diện mới!', 'success');
};

window.resetThemeDefaults = function() {
    if (!confirm('Khôi phục lại giao diện và tên ứng dụng mặc định?')) return;
    localStorage.removeItem('app_theme');
    applyThemeSettings(DEFAULT_THEME);
    closeThemeModal();
    showToast('Đã khôi phục giao diện mặc định!', 'info');
};

// Init on load
document.addEventListener('DOMContentLoaded', () => {
    initDom();
    connectWS();
    applyThemeSettings(getThemeSettings());
    renderPresetButtons();
    selectPreset(activePresetId, true);
    fetchCredits(false);
});
