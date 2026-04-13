/**
 * WebTerm — Web-based SSH & Telnet Client
 * Main application JavaScript
 */

// ─── State ───

const state = {
    sessions: [],
    tabs: [],          // { id, label, wsId, protocol, host, status, term, ws, fitAddon }
    activeTabId: null,
    sftpVisible: false,
    sftpPath: '~',
    sftpFiles: [],
};

let tabCounter = 0;

// ─── DOM ───

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ─── Sessions ───

async function loadSessions() {
    const resp = await fetch('/api/sessions');
    state.sessions = await resp.json();
    renderSessions();
}

function renderSessions() {
    const list = $('#session-list');
    if (state.sessions.length === 0) {
        list.innerHTML = `
            <div style="padding: 20px; text-align: center; color: var(--text-dim); font-size: 13px;">
                No saved sessions.<br>Click <strong>+ New</strong> to add one.
            </div>`;
        return;
    }

    const groups = {};
    state.sessions.forEach(s => {
        const g = s.group || 'Default';
        if (!groups[g]) groups[g] = [];
        groups[g].push(s);
    });

    let html = '';
    Object.keys(groups).sort().forEach(group => {
        html += `<div class="session-group-label">${esc(group)}</div>`;
        groups[group].forEach(s => {
            html += `
                <div class="session-item" data-id="${s.id}" ondblclick="connectSession('${s.id}')">
                    <div class="dot" style="background: ${s.color || '#58a6ff'}"></div>
                    <div class="info">
                        <div class="name">${esc(s.name || s.host)}</div>
                        <div class="host">${esc(s.username ? s.username + '@' : '')}${esc(s.host)}:${s.port}</div>
                    </div>
                    <span class="protocol-badge ${s.protocol}">${s.protocol}</span>
                    <div class="actions">
                        <button class="btn-icon" title="Connect" onclick="event.stopPropagation(); connectSession('${s.id}')">&#9654;</button>
                        <button class="btn-icon" title="Edit" onclick="event.stopPropagation(); editSession('${s.id}')">&#9998;</button>
                        <button class="btn-icon" title="Delete" onclick="event.stopPropagation(); deleteSession('${s.id}')">&#10005;</button>
                    </div>
                </div>`;
        });
    });
    list.innerHTML = html;
}

// ─── Connection ───

function connectSession(sessionId) {
    const s = state.sessions.find(x => x.id === sessionId);
    if (!s) return;

    // Need password — show quick dialog
    showPasswordPrompt(s);
}

function showPasswordPrompt(session) {
    const modal = $('#password-modal');
    $('#pw-host-label').textContent = `${session.username || 'root'}@${session.host}:${session.port}`;
    $('#pw-input').value = '';
    modal.classList.add('visible');
    setTimeout(() => $('#pw-input').focus(), 100);

    modal._session = session;
}

function submitPassword() {
    const modal = $('#password-modal');
    const session = modal._session;
    const password = $('#pw-input').value;
    modal.classList.remove('visible');

    openTerminal({
        ...session,
        password,
    });
}

function quickConnect() {
    const protocol = $('#qc-protocol').value;
    const host = $('#qc-host').value.trim();
    const port = parseInt($('#qc-port').value) || (protocol === 'ssh' ? 22 : 23);
    const username = $('#qc-username').value.trim();
    const password = $('#qc-password').value;
    const authMethod = $('#qc-auth-method')?.value || 'password';
    const keyPath = $('#qc-key-path')?.value || '';

    if (!host) {
        alert('Host is required');
        return;
    }

    $('#connect-modal').classList.remove('visible');

    openTerminal({
        protocol,
        host,
        port,
        username: username || 'root',
        password,
        auth_method: authMethod,
        key_path: keyPath,
    });
}

async function saveAndConnect() {
    const protocol = $('#qc-protocol').value;
    const host = $('#qc-host').value.trim();
    const port = parseInt($('#qc-port').value) || (protocol === 'ssh' ? 22 : 23);
    const username = $('#qc-username').value.trim();
    const password = $('#qc-password').value;
    const name = $('#qc-name').value.trim() || host;
    const group = $('#qc-group').value.trim() || 'Default';

    if (!host) return;

    const resp = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, protocol, host, port, username, group }),
    });

    await loadSessions();
    $('#connect-modal').classList.remove('visible');

    openTerminal({ protocol, host, port, username: username || 'root', password });
}

// ─── Terminal ───

function openTerminal(config) {
    const wsId = `t_${Date.now()}_${++tabCounter}`;
    const label = `${config.username || ''}@${config.host}`;

    // Create tab
    const tab = {
        id: wsId,
        label,
        protocol: config.protocol || 'ssh',
        host: config.host,
        status: 'connecting',
        term: null,
        ws: null,
        fitAddon: null,
    };
    state.tabs.push(tab);
    renderTabs();
    activateTab(wsId);

    // Create xterm
    const pane = document.createElement('div');
    pane.className = 'terminal-pane';
    pane.id = `pane-${wsId}`;
    pane.innerHTML = `<div class="xterm-wrapper" id="xterm-${wsId}"></div>`;
    $('#terminal-container').appendChild(pane);

    const term = new Terminal({
        cursorBlink: true,
        cursorStyle: 'bar',
        fontSize: 14,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', Consolas, monospace",
        theme: {
            background: '#0d1117',
            foreground: '#e6edf3',
            cursor: '#58a6ff',
            selectionBackground: '#264f78',
            black: '#484f58',
            red: '#ff7b72',
            green: '#3fb950',
            yellow: '#d29922',
            blue: '#58a6ff',
            magenta: '#bc8cff',
            cyan: '#39c5cf',
            white: '#b1bac4',
            brightBlack: '#6e7681',
            brightRed: '#ffa198',
            brightGreen: '#56d364',
            brightYellow: '#e3b341',
            brightBlue: '#79c0ff',
            brightMagenta: '#d2a8ff',
            brightCyan: '#56d4dd',
            brightWhite: '#f0f6fc',
        },
        allowProposedApi: true,
        scrollback: 10000,
    });

    const fitAddon = new FitAddon.FitAddon();
    const webLinksAddon = new WebLinksAddon.WebLinksAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);

    term.open(document.getElementById(`xterm-${wsId}`));
    fitAddon.fit();

    tab.term = term;
    tab.fitAddon = fitAddon;

    // WebSocket connection
    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/terminal/${wsId}`);
    tab.ws = ws;

    ws.onopen = () => {
        ws.send(JSON.stringify(config));

        // Send resize
        const dims = fitAddon.proposeDimensions();
        if (dims) {
            ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
        }
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'output') {
            term.write(msg.data);
        } else if (msg.type === 'connected') {
            tab.status = 'connected';
            renderTabs();
            updateStatusBar();
        } else if (msg.type === 'status') {
            term.write(`\r\n\x1b[33m${msg.message}\x1b[0m\r\n`);
        } else if (msg.type === 'error') {
            tab.status = 'disconnected';
            term.write(`\r\n\x1b[31mError: ${msg.message}\x1b[0m\r\n`);
            renderTabs();
            updateStatusBar();
        }
    };

    ws.onclose = () => {
        tab.status = 'disconnected';
        term.write('\r\n\x1b[31m--- Connection closed ---\x1b[0m\r\n');
        renderTabs();
        updateStatusBar();
    };

    // Terminal input -> WebSocket
    term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'input', data }));
        }
    });

    // Handle resize
    const resizeObserver = new ResizeObserver(() => {
        fitAddon.fit();
        const dims = fitAddon.proposeDimensions();
        if (dims && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
        }
    });
    resizeObserver.observe(document.getElementById(`xterm-${wsId}`));

    // Hide welcome
    $('#welcome-screen').style.display = 'none';

    term.focus();
}

// ─── Tabs ───

function renderTabs() {
    const bar = $('#tab-list');
    bar.innerHTML = state.tabs.map(t => `
        <div class="tab ${t.id === state.activeTabId ? 'active' : ''}"
             onclick="activateTab('${t.id}')"
             data-id="${t.id}">
            <div class="status-dot ${t.status}"></div>
            <span class="tab-label">${esc(t.label)}</span>
            <span class="close-btn" onclick="event.stopPropagation(); closeTab('${t.id}')">&times;</span>
        </div>
    `).join('');
}

function activateTab(tabId) {
    state.activeTabId = tabId;
    renderTabs();

    $$('.terminal-pane').forEach(p => p.classList.remove('active'));
    const pane = $(`#pane-${tabId}`);
    if (pane) {
        pane.classList.add('active');
        const tab = state.tabs.find(t => t.id === tabId);
        if (tab?.term) {
            setTimeout(() => {
                tab.fitAddon.fit();
                tab.term.focus();
            }, 10);
        }
    }
    updateStatusBar();
}

function closeTab(tabId) {
    const idx = state.tabs.findIndex(t => t.id === tabId);
    if (idx === -1) return;

    const tab = state.tabs[idx];
    if (tab.ws) tab.ws.close();
    if (tab.term) tab.term.dispose();

    const pane = $(`#pane-${tabId}`);
    if (pane) pane.remove();

    state.tabs.splice(idx, 1);

    if (state.activeTabId === tabId) {
        if (state.tabs.length > 0) {
            const next = state.tabs[Math.min(idx, state.tabs.length - 1)];
            activateTab(next.id);
        } else {
            state.activeTabId = null;
            $('#welcome-screen').style.display = '';
        }
    }
    renderTabs();
    updateStatusBar();
}

// ─── SFTP ───

async function toggleSftp() {
    state.sftpVisible = !state.sftpVisible;
    const panel = $('#sftp-panel');

    if (state.sftpVisible) {
        panel.classList.add('visible');
        await loadSftpDir('~');
    } else {
        panel.classList.remove('visible');
    }

    // Re-fit terminal
    const tab = state.tabs.find(t => t.id === state.activeTabId);
    if (tab?.fitAddon) {
        setTimeout(() => tab.fitAddon.fit(), 50);
    }
}

async function loadSftpDir(path) {
    const tab = state.tabs.find(t => t.id === state.activeTabId);
    if (!tab) return;

    state.sftpPath = path;
    $('#sftp-path-input').value = path;

    try {
        const resp = await fetch(`/api/sftp/${tab.id}/ls?path=${encodeURIComponent(path)}`);
        const files = await resp.json();
        renderSftpFiles(files);
    } catch {
        renderSftpFiles([{ error: 'Failed to load directory' }]);
    }
}

function renderSftpFiles(files) {
    const container = $('#sftp-files');

    if (files.length === 1 && files[0].error) {
        container.innerHTML = `<div style="padding: 16px; color: var(--red); font-size: 12px;">${esc(files[0].error)}</div>`;
        return;
    }

    // Add parent directory
    let html = `
        <div class="sftp-item" onclick="loadSftpDir('${esc(state.sftpPath)}/..')">
            <span class="icon">&#128193;</span>
            <span class="name" style="color: var(--text-dim)">..</span>
        </div>`;

    files.forEach(f => {
        if (f.error) return;
        const icon = f.is_dir ? '&#128193;' : '&#128196;';
        const fullPath = state.sftpPath === '~' ? `~/${f.name}` : `${state.sftpPath}/${f.name}`;
        const size = f.is_dir ? '' : formatSize(f.size);
        const click = f.is_dir
            ? `loadSftpDir('${esc(fullPath)}')`
            : `downloadFile('${esc(fullPath)}')`;

        html += `
            <div class="sftp-item" onclick="${click}">
                <span class="icon">${icon}</span>
                <span class="name">${esc(f.name)}</span>
                <span class="size">${size}</span>
            </div>`;
    });

    container.innerHTML = html;
}

function downloadFile(path) {
    const tab = state.tabs.find(t => t.id === state.activeTabId);
    if (!tab) return;
    window.open(`/api/sftp/${tab.id}/download?path=${encodeURIComponent(path)}`);
}

// ─── Session Modal ───

function showConnectModal() {
    const modal = $('#connect-modal');
    $('#qc-host').value = '';
    $('#qc-port').value = '22';
    $('#qc-username').value = '';
    $('#qc-password').value = '';
    $('#qc-name').value = '';
    $('#qc-group').value = 'Default';
    setProtocol('ssh');
    modal.classList.add('visible');
    setTimeout(() => $('#qc-host').focus(), 100);
}

function setProtocol(proto) {
    $('#qc-protocol').value = proto;
    $$('.proto-btn').forEach(b => b.classList.toggle('active', b.dataset.proto === proto));
    $('#qc-port').value = proto === 'ssh' ? '22' : '23';

    // Show/hide SSH-specific fields
    const sshFields = $('#ssh-fields');
    if (sshFields) {
        sshFields.style.display = proto === 'ssh' ? 'block' : 'none';
    }
}

async function editSession(sessionId) {
    const s = state.sessions.find(x => x.id === sessionId);
    if (!s) return;
    showConnectModal();
    $('#qc-host').value = s.host;
    $('#qc-port').value = s.port;
    $('#qc-username').value = s.username;
    $('#qc-name').value = s.name;
    $('#qc-group').value = s.group;
    setProtocol(s.protocol);
}

async function deleteSession(sessionId) {
    if (!confirm('Delete this session?')) return;
    await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    await loadSessions();
}

// ─── Status Bar ───

function updateStatusBar() {
    const tab = state.tabs.find(t => t.id === state.activeTabId);
    const connInfo = $('#status-conn');
    const tabCount = $('#status-tabs');

    if (tab) {
        const proto = tab.protocol.toUpperCase();
        const status = tab.status === 'connected' ? '&#x25CF; Connected' : tab.status === 'connecting' ? '&#x25CB; Connecting...' : '&#x25CB; Disconnected';
        connInfo.innerHTML = `${proto} | ${esc(tab.host)} | ${status}`;
    } else {
        connInfo.innerHTML = 'No connection';
    }

    const active = state.tabs.filter(t => t.status === 'connected').length;
    tabCount.textContent = `${active} active / ${state.tabs.length} tabs`;
}

// ─── Toggle Sidebar ───

function toggleSidebar() {
    $('#sidebar').classList.toggle('collapsed');
    // Re-fit terminals
    setTimeout(() => {
        state.tabs.forEach(t => {
            if (t.fitAddon) t.fitAddon.fit();
        });
    }, 250);
}

// ─── Utilities ───

function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(1) + ' GB';
}

// ─── Keyboard Shortcuts ───

document.addEventListener('keydown', (e) => {
    // Ctrl+Shift+N — New connection
    if (e.ctrlKey && e.shiftKey && e.key === 'N') {
        e.preventDefault();
        showConnectModal();
    }
    // Ctrl+W — Close tab
    if (e.ctrlKey && e.key === 'w') {
        e.preventDefault();
        if (state.activeTabId) closeTab(state.activeTabId);
    }
    // Ctrl+Tab — Next tab
    if (e.ctrlKey && e.key === 'Tab') {
        e.preventDefault();
        const idx = state.tabs.findIndex(t => t.id === state.activeTabId);
        if (state.tabs.length > 1) {
            const next = (idx + 1) % state.tabs.length;
            activateTab(state.tabs[next].id);
        }
    }
    // Ctrl+B — Toggle sidebar
    if (e.ctrlKey && e.key === 'b') {
        e.preventDefault();
        toggleSidebar();
    }
    // Escape — Close modals
    if (e.key === 'Escape') {
        $$('.modal-overlay').forEach(m => m.classList.remove('visible'));
    }
    // Enter in password modal
    if (e.key === 'Enter' && $('#password-modal').classList.contains('visible')) {
        submitPassword();
    }
});

// ─── Init ───

document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    updateStatusBar();
});
