// ================================================================
// Chat System
// ================================================================
const chatPanels = {};
let chatPanelCount = 0;

function openChat(agentName) {
    if (chatPanels[agentName]) {
        chatPanels[agentName].panelEl.classList.remove('minimized');
        chatPanels[agentName].inputEl.focus();
        return;
    }

    const role = agentName.replace(/-\d+$/, '');
    const emoji = getRoleEmoji(role);
    const title = getRoleTitle(role);

    const panel = document.createElement('div');
    panel.className = 'chat-panel';
    panel.style.right = (20 + chatPanelCount * 440) + 'px';
    panel.id = 'chat-panel-' + agentName;

    panel.innerHTML =
        '<div class="chat-header" onclick="minimizeChat(\'' + escapeHtml(agentName) + '\')">' +
        '<div class="chat-header-info">' +
        '<span class="chat-header-emoji">' + emoji + '</span>' +
        '<div>' +
        '<div class="chat-agent-name">' + escapeHtml(agentName) + '</div>' +
        '<div class="chat-agent-role">' + escapeHtml(title) + '</div>' +
        '</div>' +
        '</div>' +
        '<div class="chat-header-btns">' +
        '<button onclick="event.stopPropagation(); minimizeChat(\'' + escapeHtml(agentName) + '\')" title="Minimize">&minus;</button>' +
        '<button onclick="event.stopPropagation(); closeChat(\'' + escapeHtml(agentName) + '\')" title="Close">&times;</button>' +
        '</div>' +
        '</div>' +
        '<div class="chat-messages" id="chat-messages-' + agentName + '"></div>' +
        '<div class="chat-input-area">' +
        '<input type="text" id="chat-input-' + agentName + '" placeholder="Type a message..." ' +
        'onkeydown="if(event.key===\'Enter\')sendChatMessage(\'' + escapeHtml(agentName) + '\')" disabled />' +
        '<button id="chat-send-' + agentName + '" onclick="sendChatMessage(\'' + escapeHtml(agentName) + '\')" disabled>' +
        '&#x27A4;</button>' +
        '</div>';

    document.body.appendChild(panel);

    const messagesEl = document.getElementById('chat-messages-' + agentName);
    const inputEl = document.getElementById('chat-input-' + agentName);
    const sendBtn = document.getElementById('chat-send-' + agentName);

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const chatWs = new WebSocket(`${protocol}//${location.host}/ws/chat/${encodeURIComponent(agentName)}`);

    chatPanels[agentName] = {
        ws: chatWs, panelEl: panel, messagesEl: messagesEl,
        inputEl: inputEl, sendBtn: sendBtn, streamingEl: null
    };
    chatPanelCount++;

    appendChatBubble(agentName, 'system', 'Connecting...');

    chatWs.onopen = () => { chatWs.send(JSON.stringify({ type: 'start_session' })); };

    chatWs.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        handleChatMessage(agentName, msg);
    };

    chatWs.onclose = () => {
        appendChatBubble(agentName, 'system', 'Disconnected.');
        inputEl.disabled = true;
        sendBtn.disabled = true;
    };

    chatWs.onerror = () => { appendChatBubble(agentName, 'system', 'Connection error.'); };
}

function handleChatMessage(agentName, msg) {
    const chat = chatPanels[agentName];
    if (!chat) return;

    switch (msg.type) {
        case 'session_started':
            appendChatBubble(agentName, 'system', 'Connected to ' + agentName + '.');
            chat.inputEl.disabled = false;
            chat.sendBtn.disabled = false;
            chat.inputEl.focus();
            break;
        case 'chat_token':
            if (!chat.streamingEl) {
                chat.streamingEl = document.createElement('div');
                chat.streamingEl.className = 'chat-msg assistant streaming';
                chat.streamingEl.setAttribute('data-raw', '');
                chat.messagesEl.appendChild(chat.streamingEl);
            }
            const rawSoFar = chat.streamingEl.getAttribute('data-raw') + msg.content;
            chat.streamingEl.setAttribute('data-raw', rawSoFar);
            chat.streamingEl.innerHTML = DOMPurify.sanitize(marked.parse(rawSoFar));
            chat.messagesEl.scrollTop = chat.messagesEl.scrollHeight;
            break;
        case 'chat_tool_use':
            appendChatBubble(agentName, 'system', 'Using tool: ' + (msg.tool || 'unknown'));
            break;
        case 'chat_response_complete':
            if (chat.streamingEl) {
                chat.streamingEl.classList.remove('streaming');
                const finalRaw = chat.streamingEl.getAttribute('data-raw') || '';
                if (finalRaw) chat.streamingEl.innerHTML = DOMPurify.sanitize(marked.parse(finalRaw));
                chat.streamingEl = null;
            }
            chat.inputEl.disabled = false;
            chat.sendBtn.disabled = false;
            chat.inputEl.focus();
            break;
        case 'chat_error':
            appendChatBubble(agentName, 'system', 'Error: ' + (msg.error || 'Unknown error'));
            chat.inputEl.disabled = false;
            chat.sendBtn.disabled = false;
            break;
        case 'session_stopped':
            appendChatBubble(agentName, 'system', 'Session ended.');
            break;
    }
}

function sendChatMessage(agentName) {
    const chat = chatPanels[agentName];
    if (!chat || !chat.ws) return;
    const text = chat.inputEl.value.trim();
    if (!text) return;

    appendChatBubble(agentName, 'user', text);
    chat.inputEl.value = '';
    chat.inputEl.disabled = true;
    chat.sendBtn.disabled = true;
    chat.ws.send(JSON.stringify({ type: 'chat_message', content: text }));
}

function closeChat(agentName) {
    const chat = chatPanels[agentName];
    if (!chat) return;
    if (chat.ws && chat.ws.readyState === WebSocket.OPEN) {
        chat.ws.send(JSON.stringify({ type: 'stop_session' }));
    }
    chat.ws.close();
    chat.panelEl.remove();
    delete chatPanels[agentName];
    chatPanelCount = Math.max(0, chatPanelCount - 1);
}

function minimizeChat(agentName) {
    const chat = chatPanels[agentName];
    if (!chat) return;
    chat.panelEl.classList.toggle('minimized');
}

function appendChatBubble(agentName, role, text) {
    const chat = chatPanels[agentName];
    if (!chat) return;
    const bubble = document.createElement('div');
    bubble.className = 'chat-msg ' + role;
    if (role === 'assistant') {
        bubble.innerHTML = DOMPurify.sanitize(marked.parse(text));
    } else {
        bubble.textContent = text;
    }
    chat.messagesEl.appendChild(bubble);
    chat.messagesEl.scrollTop = chat.messagesEl.scrollHeight;
}

// ================================================================
// Pause / Resume
// ================================================================
let pausedRoles = new Set();

async function refreshPausedState() {
    try {
        const res = await fetch('/api/agents/paused');
        const data = await res.json();
        pausedRoles = new Set(data.paused_roles || []);
        updatePauseUI();
    } catch (e) { /* ignore */ }
}

function updatePauseUI() {
    const btn = document.getElementById('teamPauseBtn');
    const icon = document.getElementById('teamPauseIcon');
    const label = document.getElementById('teamPauseLabel');
    if (!btn) return;
    if (pausedRoles.size > 0) {
        btn.classList.add('paused');
        icon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"/>';
        label.textContent = 'Resume Team';
    } else {
        btn.classList.remove('paused');
        icon.innerHTML = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';
        label.textContent = 'Pause Team';
    }
    document.querySelectorAll('.role-pause-btn').forEach(el => {
        const role = el.getAttribute('onclick')?.match(/'([^']+)'/)?.[1];
        if (!role) return;
        if (pausedRoles.has(role)) { el.classList.add('paused'); el.innerHTML = '&#9654; Resume'; }
        else { el.classList.remove('paused'); el.innerHTML = '&#10074;&#10074; Pause'; }
    });
}

async function toggleTeamPause() {
    if (pausedRoles.size > 0) {
        await fetch('/api/agents/resume', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:'all'}) });
    } else {
        await fetch('/api/agents/pause', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:'all'}) });
    }
    await refreshPausedState(); refreshAgents();
}

async function toggleRolePause(role) {
    if (pausedRoles.has(role)) {
        await fetch('/api/agents/resume', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role}) });
    } else {
        await fetch('/api/agents/pause', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role}) });
    }
    await refreshPausedState(); refreshAgents();
}

// ================================================================
// Task Detail Modal
// ================================================================
function openTaskDetail(task) {
    const overlay = document.getElementById('taskDetailOverlay');
    const headerEl = document.getElementById('taskDetailHeader');
    const body = document.getElementById('taskDetailBody');
    if (!overlay || !body) return;

    const s = task.status || 'pending';
    const role = task.assigned_to || '';
    const roleColor = getRoleColor(role);
    const emoji = getRoleEmoji(role);
    const statusIcons = { blocked: '\uD83D\uDD12', pending: '\u23F3', in_progress: '\u26A1', completed: '\u2705', rejected: '\u274C', failed: '\uD83D\uDCA5' };
    const priorityIcons = { critical: '\uD83D\uDD34', high: '\uD83D\uDFE0', medium: '\uD83D\uDFE1', low: '\uD83D\uDFE2' };

    // Header with accent color
    if (headerEl) {
        headerEl.style.borderTop = '3px solid ' + (roleColor.border || 'var(--accent-indigo)');
    }

    // Build header content
    let headerHtml = '<div class="task-detail-header-left">';
    headerHtml += '<div class="task-detail-header-meta">';
    headerHtml += '<span class="task-detail-id">' + escapeHtml(String(task.id || '')) + '</span>';
    if (task.group_id) headerHtml += '<span class="task-detail-id" style="color:var(--text-muted)">' + escapeHtml(task.group_id) + '</span>';
    headerHtml += '</div>';
    headerHtml += '<h2>' + escapeHtml(task.title || '(untitled)') + '</h2>';
    headerHtml += '</div>';
    document.getElementById('taskDetailHeaderLeft').innerHTML = headerHtml;

    // Badges row
    let html = '<div class="task-detail-badges">';
    html += '<span class="task-detail-badge badge-status-' + escapeHtml(s) + '"><span class="badge-icon">' + (statusIcons[s] || '') + '</span>' + escapeHtml(s.replace('_', ' ')) + '</span>';
    const p = task.priority || 'medium';
    const pClass = (p === 'critical' || p === 'high') ? ' badge-priority-' + p : '';
    html += '<span class="task-detail-badge badge-priority' + pClass + '"><span class="badge-icon">' + (priorityIcons[p] || '') + '</span>' + escapeHtml(p) + '</span>';
    if (role && ROLE_COLORS[role]) {
        html += '<span class="task-detail-badge badge-role" style="background:' + roleColor.bg + ';color:' + roleColor.text + ';border-color:' + roleColor.border + '">' + emoji + ' ' + escapeHtml(role) + '</span>';
    }
    if (task.task_type) {
        html += '<span class="task-detail-badge badge-type">' + escapeHtml(task.task_type) + '</span>';
    }
    html += '</div>';

    // Details grid
    html += '<div class="task-detail-section"><div class="task-detail-section-title">Details</div>';
    html += '<div class="task-detail-grid">';
    html += tdField('Assigned To', role ? (emoji + ' ' + escapeHtml(getRoleTitle(role))) : '-');
    html += tdField('Claimed By', task.claimed_by ? escapeHtml(task.claimed_by) : '-', task.claimed_by ? '' : 'muted');
    html += tdField('Created By', task.created_by ? escapeHtml(task.created_by) : '-');
    html += tdField('Parent Task', task.parent_id ? escapeHtml(String(task.parent_id)) : 'None', task.parent_id ? 'mono' : 'muted');
    html += '</div></div>';

    // Timeline grid
    html += '<div class="task-detail-section"><div class="task-detail-section-title">Timeline</div>';
    html += '<div class="task-detail-grid">';
    html += tdField('Created', fmtTs(task.created_at));
    html += tdField('Started', task.started_at ? fmtTs(task.started_at) : 'Not started', task.started_at ? '' : 'muted');
    html += tdField('Completed', task.completed_at ? fmtTs(task.completed_at) : 'In progress', task.completed_at ? '' : 'muted');
    if (task.started_at && task.completed_at) {
        const dur = new Date(task.completed_at) - new Date(task.started_at);
        const mins = Math.round(dur / 60000);
        html += tdField('Duration', mins < 60 ? mins + 'm' : Math.round(mins / 60 * 10) / 10 + 'h');
    } else if (task.started_at) {
        const dur = Date.now() - new Date(task.started_at);
        const mins = Math.round(dur / 60000);
        html += tdField('Elapsed', mins < 60 ? mins + 'm' : Math.round(mins / 60 * 10) / 10 + 'h');
    } else {
        html += tdField('Duration', '-', 'muted');
    }
    html += '</div></div>';

    // Description
    if (task.description) {
        html += '<div class="task-detail-section"><div class="task-detail-section-title">Description</div>';
        html += '<div class="task-detail-description"><div class="value">' + DOMPurify.sanitize(marked.parse(task.description)) + '</div></div></div>';
    }

    // Rejection reason
    if (task.rejection_reason) {
        html += '<div class="task-detail-section"><div class="task-detail-section-title" style="color:var(--accent-rose)">Rejection Reason</div>';
        html += '<div class="task-detail-rejection"><div class="value">' + escapeHtml(task.rejection_reason) + '</div></div></div>';
    }

    body.innerHTML = html;
    overlay.classList.add('open');
    trapFocus(overlay);
}

function tdField(label, value, cls) {
    const valClass = 'value' + (cls ? ' ' + cls : '');
    return '<div class="task-detail-field"><label>' + label + '</label><div class="' + valClass + '">' + value + '</div></div>';
}
function fmtTs(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return escapeHtml(iso); }
}
function closeTaskDetail() { const o = document.getElementById('taskDetailOverlay'); if (o) o.classList.remove('open'); }

// ================================================================
// Settings Modal
// ================================================================
let settingsData = { team: null, roles: {} };
let settingsActiveTab = 'team';

function toggleSettingsModal() {
    const o = document.getElementById('settingsOverlay');
    if (!o) return;
    if (o.classList.contains('open')) { o.classList.remove('open'); }
    else { o.classList.add('open'); loadSettings(); trapFocus(o); }
}
function closeSettings() { const o = document.getElementById('settingsOverlay'); if (o) o.classList.remove('open'); }

async function loadSettings() {
    try {
        const [teamRes, rolesRes] = await Promise.all([fetch('/api/settings/team'), fetch('/api/settings/roles')]);
        settingsData.team = await teamRes.json();
        settingsData.roles = {};
        const arr = await rolesRes.json();
        for (const r of arr) settingsData.roles[r.role] = r;
        renderSettingsTabs(); switchSettingsTab(settingsActiveTab);
    } catch (e) { document.getElementById('settingsContent').innerHTML = '<div style="color:var(--accent-rose)">Failed to load settings</div>'; }
}

function renderSettingsTabs() {
    const el = document.getElementById('settingsTabs');
    let html = '<button class="settings-tab' + (settingsActiveTab==='team'?' active':'') + '" onclick="switchSettingsTab(\'team\')">Team</button>';
    for (const role of Object.keys(settingsData.roles).sort()) {
        html += '<button class="settings-tab' + (settingsActiveTab===role?' active':'') + '" onclick="switchSettingsTab(\'' + escapeHtml(role) + '\')">' + escapeHtml(role) + '</button>';
    }
    el.innerHTML = html;
}

function switchSettingsTab(tab) {
    settingsActiveTab = tab;
    document.querySelectorAll('.settings-tab').forEach(el => el.classList.toggle('active', el.textContent.trim().toLowerCase() === tab));
    const c = document.getElementById('settingsContent');
    if (tab === 'team') renderTeamSettings(c); else renderRoleSettings(c, tab);
}

function renderTeamSettings(c) {
    const t = settingsData.team || {};
    let html = '<div class="settings-field"><label>Team Name</label><input id="s_team_name" value="' + escapeHtml(t.name||'') + '"></div>';
    html += '<div class="settings-field"><label>Project Directory <span class="restart-badge">Requires restart</span></label><input id="s_project_dir" value="' + escapeHtml(t.project_dir||'') + '" readonly style="opacity:0.6"></div>';
    html += '<div class="settings-field"><label>Default Model</label><input id="s_default_model" value="' + escapeHtml(t.default_model||'') + '"></div>';
    html += '<div style="margin-top:16px"><button class="settings-save-btn" onclick="saveTeamSettings()">Save Team Settings</button><span class="settings-saved-msg" id="teamSavedMsg">Saved!</span></div>';
    c.innerHTML = html;
}

function renderRoleSettings(c, role) {
    const r = settingsData.roles[role] || {};
    let html = '<div class="settings-field"><label>Display Name</label><input id="s_display_name" value="' + escapeHtml(r.display_name||'') + '" readonly style="opacity:0.6"></div>';
    html += '<div class="settings-field"><label>Model</label><select id="s_model" style="width:100%;padding:10px 12px;background:rgba(15,20,38,0.9);border:1px solid var(--border-subtle);border-radius:var(--radius-sm);color:var(--text-primary);font-size:0.9rem">';
    const models = ['claude-opus-4-6','claude-sonnet-4-6','claude-haiku-4-5-20251001'];
    for (const m of models) { html += '<option value="' + m + '"' + (r.model===m?' selected':'') + '>' + m + '</option>'; }
    html += '</select></div>';
    html += '<div class="settings-field"><label>System Prompt</label><textarea id="s_system_prompt" rows="6">' + escapeHtml(r.system_prompt||'') + '</textarea></div>';
    html += '<div class="settings-field"><label>Tools (comma-separated)</label><input id="s_tools" value="' + escapeHtml((r.tools||[]).join(', ')) + '"></div>';
    html += '<div class="settings-field"><label>Max Instances <span class="restart-badge">Requires restart</span></label><input id="s_max_instances" type="number" value="' + (r.max_instances||1) + '" readonly style="opacity:0.6"></div>';
    html += '<div style="margin-top:16px"><button class="settings-save-btn" onclick="saveRoleSettings(\'' + escapeHtml(role) + '\')">Save ' + escapeHtml(role) + ' Settings</button><span class="settings-saved-msg" id="roleSavedMsg">Saved!</span></div>';
    c.innerHTML = html;
}

async function saveTeamSettings() {
    await fetch('/api/settings/team', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ name:document.getElementById('s_team_name').value, default_model:document.getElementById('s_default_model').value }) });
    const m = document.getElementById('teamSavedMsg'); m.classList.add('show'); setTimeout(() => m.classList.remove('show'), 2000);
}

async function saveRoleSettings(role) {
    const tools = document.getElementById('s_tools').value.split(',').map(s=>s.trim()).filter(Boolean);
    const model = document.getElementById('s_model').value;
    await fetch('/api/settings/roles/' + role, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ system_prompt:document.getElementById('s_system_prompt').value, model:model, tools:tools }) });
    const m = document.getElementById('roleSavedMsg'); m.classList.add('show'); setTimeout(() => m.classList.remove('show'), 2000);
    await loadSettings();
}

// ================================================================
// Server Restart
// ================================================================
async function restartServer() {
    showConfirmModal('Restart Server', 'Restart the server? All agents will be stopped and restarted.', doRestartServer);
    return;
}
async function doRestartServer() {
    const overlay = document.getElementById('restartOverlay');
    const status = document.getElementById('restartStatus');
    overlay.classList.add('active');
    status.textContent = 'Sending restart signal...';
    try {
        await fetch('/api/server/restart', { method: 'POST' });
    } catch (e) { /* expected — server dies */ }
    status.textContent = 'Waiting for server to come back...';
    await new Promise(r => setTimeout(r, 2000));
    let attempts = 0;
    const poll = setInterval(async () => {
        attempts++;
        status.textContent = 'Waiting for server to come back... (' + attempts + 's)';
        try {
            const res = await fetch('/api/health', { signal: AbortSignal.timeout(2000) });
            if (res.ok) {
                clearInterval(poll);
                status.textContent = 'Server is back! Reloading...';
                setTimeout(() => location.reload(), 500);
            }
        } catch (e) { /* still down */ }
    }, 1000);
}

// ================================================================
// FAQ Modal
// ================================================================
function toggleFaqModal() {
    const overlay = document.getElementById('faqOverlay');
    overlay.classList.toggle('open');
    if (overlay.classList.contains('open')) trapFocus(overlay);
}

function toggleFaqSection(el) {
    el.closest('.faq-section').classList.toggle('open');
}

window.addEventListener('DOMContentLoaded', function () {
    document.getElementById('faqOverlay').addEventListener('click', function (e) { if (e.target === this) toggleFaqModal(); });
    document.getElementById('taskDetailOverlay').addEventListener('click', function (e) { if (e.target === this) closeTaskDetail(); });
    document.getElementById('settingsOverlay').addEventListener('click', function (e) { if (e.target === this) closeSettings(); });
});

// Note: Escape key handling is done via the keyboard shortcuts system below

// ================================================================
// Initialization
// ================================================================
async function refreshUsage() {
    try {
        const res = await fetch('/api/usage');
        const data = await res.json();
        const daily = data.daily || {};
        const weekly = data.weekly || {};
        document.getElementById('statCostToday').textContent = '$' + (daily.cost_usd || 0).toFixed(2);
        document.getElementById('statCostWeekly').textContent = 'Week: $' + (weekly.cost_usd || 0).toFixed(2) + ' \u00B7 ' + ((weekly.input_tokens || 0) + (weekly.output_tokens || 0)).toLocaleString() + ' tokens';
    } catch (e) { /* ignore */ }
}

function refreshAll() {
    refreshFilters();
    refreshBoard();
    refreshGroups();
    refreshAgents();
    refreshPausedState();
    refreshUsage();
}

// ================================================================
// Project Management
// ================================================================
let projectWizardStep = 1;
let projectWizardData = { name: '', directory: '', with_defaults: true };

async function checkProjectStatus() {
    try {
        const resp = await fetch('/api/projects/status');
        const status = await resp.json();

        if (!status.has_projects) {
            document.getElementById('landingPage').style.display = 'flex';
            document.querySelector('.top-nav').style.display = 'none';
            const mc = document.querySelector('.main-content');
            if (mc) mc.style.display = 'none';
            return false;
        }

        if (!status.active) {
            document.getElementById('landingPage').style.display = 'none';
            document.querySelector('.top-nav').style.display = '';
            document.getElementById('projectSelector').style.display = 'flex';
            await loadProjectList();
            toggleProjectDropdown();
            return false;
        }

        // Normal: active project
        document.getElementById('landingPage').style.display = 'none';
        document.querySelector('.top-nav').style.display = '';
        document.getElementById('projectSelector').style.display = 'flex';
        document.getElementById('activeProjectName').textContent = status.active.name;
        return true;
    } catch (e) {
        console.error('Failed to check project status:', e);
        return true; // fallback to show dashboard
    }
}

async function loadProjectList() {
    try {
        const resp = await fetch('/api/projects');
        const projects = await resp.json();
        const activeResp = await fetch('/api/projects/active');
        const active = await activeResp.json();
        const activeId = active ? active.id : null;

        const list = document.getElementById('projectList');
        list.innerHTML = '';
        projects.forEach(function(p) {
            const btn = document.createElement('button');
            btn.className = 'dropdown-item project-item' + (p.id === activeId ? ' active' : '');
            btn.dataset.projectId = p.id;
            btn.addEventListener('click', function() { switchProject(p.id); });

            const dot = document.createElement('span');
            dot.className = 'project-dot' + (p.id === activeId ? ' active' : '');
            btn.appendChild(dot);

            const info = document.createElement('div');
            info.className = 'project-info';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'project-name';
            nameSpan.textContent = p.name;
            info.appendChild(nameSpan);

            const dirSpan = document.createElement('span');
            dirSpan.className = 'project-dir';
            dirSpan.textContent = p.directory;
            info.appendChild(dirSpan);

            btn.appendChild(info);
            list.appendChild(btn);
        });
    } catch (e) {
        console.error('Failed to load project list:', e);
    }
}

function toggleProjectDropdown() {
    const dd = document.getElementById('projectDropdown');
    dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
    if (dd.style.display === 'block') {
        loadProjectList();
        setTimeout(function() {
            document.addEventListener('click', closeProjectDropdownOutside);
        }, 0);
    }
}

function closeProjectDropdownOutside(e) {
    const selector = document.getElementById('projectSelector');
    if (!selector.contains(e.target)) {
        document.getElementById('projectDropdown').style.display = 'none';
        document.removeEventListener('click', closeProjectDropdownOutside);
    }
}

async function switchProject(projectId) {
    document.getElementById('projectDropdown').style.display = 'none';
    showToast('Switching project...', 'info');
    try {
        const resp = await fetch('/api/projects/' + projectId + '/activate', { method: 'POST' });
        if (resp.ok) {
            window.location.reload();
        } else {
            const err = await resp.json();
            showToast(err.detail || 'Failed to switch project', 'error');
        }
    } catch (e) {
        showToast('Failed to switch project', 'error');
    }
}

async function browseForDirectory() {
    var btn = document.getElementById('browseDirBtn');
    var originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Opening...';
    try {
        var resp = await fetch('/api/browse-directory', { method: 'POST' });
        if (resp.status === 501) {
            showToast('Folder picker not available on this platform. Please type the path.', 'info');
            return;
        }
        if (resp.status === 408) {
            showToast('Folder picker timed out', 'error');
            return;
        }
        if (!resp.ok) {
            var err = await resp.json();
            showToast(err.detail || 'Failed to open folder picker', 'error');
            return;
        }
        var result = await resp.json();
        if (!result.cancelled && result.path) {
            projectWizardData.directory = result.path;
            var input = document.getElementById('wizardProjectDir');
            if (input) input.value = result.path;
        }
    } catch (e) {
        showToast('Failed to open folder picker', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

function openCreateProjectWizard() {
    projectWizardStep = 1;
    projectWizardData = { name: '', directory: '', with_defaults: true };
    document.getElementById('createProjectOverlay').style.display = 'flex';
    renderWizardStep();
}

function closeCreateProjectWizard() {
    document.getElementById('createProjectOverlay').style.display = 'none';
}

function renderWizardStep() {
    const content = document.getElementById('wizardProjectContent');
    const prevBtn = document.getElementById('wizardPrevBtn');
    const nextBtn = document.getElementById('wizardNextBtn');

    // Update step dots
    document.querySelectorAll('.step-dot').forEach(function(d) {
        d.classList.toggle('active', parseInt(d.dataset.step) <= projectWizardStep);
    });

    if (projectWizardStep === 1) {
        prevBtn.style.display = 'none';
        nextBtn.textContent = 'Next';
        nextBtn.disabled = false;
        content.innerHTML =
            '<div class="wizard-step">' +
            '<h3>Project Identity</h3>' +
            '<p class="wizard-desc">Give your project a name and specify where it lives.</p>' +
            '<div class="wizard-field">' +
            '<label>Project Name</label>' +
            '<input type="text" id="wizardProjectName" placeholder="My Awesome Project" value="' + escapeHtml(projectWizardData.name) + '" oninput="projectWizardData.name = this.value">' +
            '</div>' +
            '<div class="wizard-field">' +
            '<label>Project Directory</label>' +
            '<div style="display:flex;gap:8px;align-items:stretch">' +
            '<input type="text" id="wizardProjectDir" placeholder="/Users/you/projects/my-project" value="' + escapeHtml(projectWizardData.directory) + '" oninput="projectWizardData.directory = this.value" style="flex:1">' +
            '<button type="button" class="btn-secondary" onclick="browseForDirectory()" id="browseDirBtn" style="white-space:nowrap;padding:0 16px">Browse\u2026</button>' +
            '</div>' +
            '<span class="field-hint">Absolute path. Will be created if it doesn\'t exist.</span>' +
            '</div>' +
            '</div>';
    } else {
        prevBtn.style.display = 'inline-flex';
        nextBtn.textContent = 'Create Project';
        nextBtn.disabled = false;
        content.innerHTML =
            '<div class="wizard-step">' +
            '<h3>Agent Setup</h3>' +
            '<p class="wizard-desc">Choose how to set up your project\'s agents.</p>' +
            '<div class="wizard-options">' +
            '<label class="wizard-option ' + (projectWizardData.with_defaults ? 'selected' : '') + '" onclick="projectWizardData.with_defaults = true; renderWizardStep();">' +
            '<div class="option-radio ' + (projectWizardData.with_defaults ? 'checked' : '') + '"></div>' +
            '<div class="option-content">' +
            '<strong>Start with default agents</strong>' +
            '<p>Scaffolds PM, Architect, Coder, Tester, and Reviewer with a standard pipeline. Ready to use immediately.</p>' +
            '</div></label>' +
            '<label class="wizard-option ' + (!projectWizardData.with_defaults ? 'selected' : '') + '" onclick="projectWizardData.with_defaults = false; renderWizardStep();">' +
            '<div class="option-radio ' + (!projectWizardData.with_defaults ? 'checked' : '') + '"></div>' +
            '<div class="option-content">' +
            '<strong>Start empty</strong>' +
            '<p>Just creates the config directory. Add agents manually in Settings.</p>' +
            '</div></label>' +
            '</div></div>';
    }
}

function wizardPrevStep() {
    if (projectWizardStep > 1) {
        projectWizardStep--;
        renderWizardStep();
    }
}

async function wizardNextStep() {
    if (projectWizardStep === 1) {
        // Validate
        if (!projectWizardData.name.trim()) {
            showToast('Please enter a project name', 'error');
            return;
        }
        if (!projectWizardData.directory.trim()) {
            showToast('Please enter a project directory', 'error');
            return;
        }
        if (!projectWizardData.directory.startsWith('/')) {
            showToast('Directory must be an absolute path (starting with /)', 'error');
            return;
        }
        projectWizardStep = 2;
        renderWizardStep();
    } else {
        // Create project
        var nextBtn = document.getElementById('wizardNextBtn');
        nextBtn.disabled = true;
        nextBtn.textContent = 'Creating...';

        try {
            var resp = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(projectWizardData),
            });
            if (!resp.ok) {
                var err = await resp.json();
                showToast(err.detail || 'Failed to create project', 'error');
                nextBtn.disabled = false;
                nextBtn.textContent = 'Create Project';
                return;
            }
            var project = await resp.json();

            // Activate it
            var actResp = await fetch('/api/projects/' + project.id + '/activate', { method: 'POST' });
            if (!actResp.ok) {
                showToast('Project created but failed to activate', 'error');
            }

            closeCreateProjectWizard();

            if (!projectWizardData.with_defaults) {
                window.location.href = '/settings';
            } else {
                window.location.reload();
            }
        } catch (e) {
            showToast('Failed to create project', 'error');
            nextBtn.disabled = false;
            nextBtn.textContent = 'Create Project';
        }
    }
}

// ================================================================
// Initialization
// ================================================================
(async function init() {
    const hasProject = await checkProjectStatus();
    if (hasProject) {
        connectWebSocket();
        refreshAll();
    }
})();

// ================================================================
// Drag-and-Drop: Column handlers
// ================================================================
(function initDragDrop() {
    const COL_STATUS_MAP = {
        'col-blocked': 'blocked',
        'col-pending': 'pending',
        'col-in_progress': 'in_progress',
        'col-completed': 'completed',
        'col-rejected': 'rejected',
    };

    document.querySelectorAll('.kanban-column').forEach(col => {
        col.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        });

        col.addEventListener('dragenter', (e) => {
            e.preventDefault();
            col.classList.add('drag-over');
        });

        col.addEventListener('dragleave', (e) => {
            // Only remove if we actually left the column (not entered a child)
            if (!col.contains(e.relatedTarget)) {
                col.classList.remove('drag-over');
            }
        });

        col.addEventListener('drop', async (e) => {
            e.preventDefault();
            col.classList.remove('drag-over');

            const taskId = e.dataTransfer.getData('text/plain');
            if (!taskId) return;

            const newStatus = COL_STATUS_MAP[col.id];
            if (!newStatus) return;

            // Find the card being dragged
            const card = document.querySelector('[data-task-id="' + CSS.escape(taskId) + '"]');
            if (!card) return;

            // Check if card is already in this column
            const currentCol = card.closest('.kanban-column');
            if (currentCol && currentCol.id === col.id) return;

            // Optimistic DOM move: move card to new column immediately
            const tasksContainer = col.querySelector('.column-tasks');
            if (!tasksContainer) return;

            // Remove empty-state placeholder if present in target
            const emptyState = tasksContainer.querySelector('.empty-state');
            if (emptyState) emptyState.remove();

            // Remove card from source column
            const srcContainer = currentCol ? currentCol.querySelector('.column-tasks') : null;
            card.remove();

            // Add empty-state to source if now empty
            if (srcContainer && srcContainer.querySelectorAll('.task-card').length === 0) {
                const srcStatus = currentCol.id.replace('col-', '');
                srcContainer.innerHTML = '<div class="empty-state"><span class="empty-state-icon">' +
                    (STATUS_ICONS[srcStatus] || '\uD83D\uDCE6') + '</span> No tasks</div>';
            }

            // Append card to target column
            tasksContainer.appendChild(card);
            updateColumnCounts();

            // Call PATCH API to persist the status change
            try {
                const resp = await fetch('/api/tasks/' + encodeURIComponent(taskId), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus }),
                });
                if (!resp.ok) {
                    const errData = await resp.json().catch(() => ({}));
                    showToast('Failed to move task: ' + (errData.detail || resp.statusText));
                    // Revert: refresh the board
                    refreshBoard();
                } else {
                    showToast('Task ' + taskId + ' moved to ' + newStatus.replace('_', ' '), 'success', 3000);
                }
            } catch (err) {
                showToast('Failed to move task: ' + err.message);
                refreshBoard();
            }
        });
    });
})();

// ================================================================
// Feature: Task Search Bar with Debounce
// ================================================================
let searchTimeout;
document.getElementById('taskSearchInput').addEventListener('input', function() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => applySearchFilters(), 300);
});

async function applySearchFilters() {
    const query = document.getElementById('taskSearchInput').value;
    const status = document.getElementById('statusFilter').value;
    const role = document.getElementById('roleFilter').value;
    const priority = document.getElementById('priorityFilter').value;

    let params = new URLSearchParams();
    if (query) params.set('q', query);
    if (status) params.set('status', status);
    if (role) params.set('assigned_to', role);
    if (priority) params.set('priority', priority);

    // Update URL without reload
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState(null, '', newUrl);

    if (query) {
        // Use search endpoint
        try {
            const resp = await fetch('/api/tasks/search?' + params.toString());
            const data = await resp.json();
            renderSearchResults(data);
        } catch(e) {
            // Fallback to client-side filtering
            filterTasksClientSide(query, status, role, priority);
        }
    } else {
        // Sync with existing filter bar
        if (role) {
            document.getElementById('filterAssignee').value = role;
            currentFilters.assigned_to = role;
        }
        if (priority) {
            document.getElementById('filterPriority').value = priority;
            currentFilters.priority = priority;
        }
        if (status) {
            document.getElementById('filterStatus').value = status;
            currentFilters.status = status;
        }
        refreshBoard();
    }
}

function filterTasksClientSide(query, status, role, priority) {
    const q = (query || '').toLowerCase();
    const filtered = allTasks.filter(t => {
        if (q && !(t.title || '').toLowerCase().includes(q) && !(t.description || '').toLowerCase().includes(q) && !(t.id || '').toLowerCase().includes(q)) return false;
        if (status && t._status !== status) return false;
        if (role && t.assigned_to !== role) return false;
        if (priority && t.priority !== priority) return false;
        return true;
    });

    // Render filtered results in the board view
    const grouped = { blocked: [], pending: [], in_progress: [], completed: [], rejected: [] };
    for (const t of filtered) {
        const s = t._status || 'pending';
        if (grouped[s]) grouped[s].push(t);
    }
    renderBoardView(grouped);
}

function renderSearchResults(data) {
    // data may be an array of tasks or an object with results
    const tasks = Array.isArray(data) ? data : (data.results || data.tasks || []);
    const grouped = { blocked: [], pending: [], in_progress: [], completed: [], rejected: [] };
    for (const t of tasks) {
        const s = t.status || t._status || 'pending';
        if (grouped[s]) grouped[s].push(t);
        else grouped.pending.push(t);
    }
    renderBoardView(grouped);
}

// ================================================================
// Feature: URL-Based Filter State
// ================================================================
function restoreFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('q')) document.getElementById('taskSearchInput').value = params.get('q');
    if (params.get('status')) document.getElementById('statusFilter').value = params.get('status');
    if (params.get('assigned_to')) document.getElementById('roleFilter').value = params.get('assigned_to');
    if (params.get('priority')) document.getElementById('priorityFilter').value = params.get('priority');

    if (params.toString()) {
        applySearchFilters();
    }
}
// Called after DOM is ready
restoreFiltersFromUrl();

// ================================================================
// Feature: Light/Dark Theme Toggle
// ================================================================
function toggleTheme() {
    document.body.classList.toggle('light-theme');
    const isLight = document.body.classList.contains('light-theme');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    document.getElementById('themeIcon').innerHTML = isLight ? '&#9728;' : '&#9790;';
}

// Restore theme on load
if (localStorage.getItem('theme') === 'light') {
    document.body.classList.add('light-theme');
    document.getElementById('themeIcon').innerHTML = '&#9728;';
}

// ================================================================
// Feature: Skeleton Loading Screens
// ================================================================
function showBoardSkeletons() {
    const columns = document.querySelectorAll('.column-tasks');
    columns.forEach(col => {
        col.innerHTML = '<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>';
    });
}

function showStatSkeletons() {
    document.querySelectorAll('.stat-value').forEach(el => {
        el.innerHTML = '<div class="skeleton" style="width: 40px; height: 26px; display: inline-block;"></div>';
    });
}

// ================================================================
// Feature: Notification Center
// ================================================================
async function loadNotifications() {
    try {
        const resp = await fetch('/api/notifications?limit=20');
        notifications = await resp.json();
        if (!Array.isArray(notifications)) notifications = [];
        updateNotifBadge();
        renderNotifications();
    } catch(e) {
        // API may not exist yet, silently ignore
        notifications = [];
        updateNotifBadge();
        renderNotifications();
    }
}

function updateNotifBadge() {
    const badge = document.getElementById('notifBadge');
    const count = notifications.length;
    badge.textContent = count > 9 ? '9+' : count;
    badge.classList.toggle('visible', count > 0);
}

function renderNotifications() {
    const list = document.getElementById('notifList');
    if (notifications.length === 0) {
        list.innerHTML = '<div style="padding: 24px; text-align: center; color: var(--text-muted);">No new notifications</div>';
        return;
    }
    list.innerHTML = notifications.map(n => {
        const severityIcon = n.severity === 'critical' ? '&#128308;' : n.severity === 'warning' ? '&#128992;' : n.severity === 'error' ? '&#128308;' : '&#128309;';
        return '<div class="notif-item" onclick="markNotifRead(' + (n.id || 0) + ')">' +
            '<div style="display: flex; gap: 8px; align-items: start;">' +
            '<span style="font-size: 16px;">' + severityIcon + '</span>' +
            '<div>' +
            '<div style="font-weight: 500; font-size: 13px;">' + escapeHtml(n.title || '') + '</div>' +
            '<div style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">' + escapeHtml(n.message || '') + '</div>' +
            '<div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">' + (n.created_at ? new Date(n.created_at).toLocaleString() : '') + '</div>' +
            '</div></div></div>';
    }).join('');
}

function toggleNotifications() {
    const dd = document.getElementById('notifDropdown');
    dd.classList.toggle('open');
}

async function markNotifRead(id) {
    try {
        await fetch('/api/notifications/' + id + '/read', { method: 'POST' });
    } catch(e) {}
    loadNotifications();
}

async function markAllRead() {
    try {
        await fetch('/api/notifications/read-all', { method: 'POST' });
    } catch(e) {}
    loadNotifications();
}

// Close notification dropdown when clicking outside
document.addEventListener('click', function(e) {
    const nc = document.querySelector('.notification-center');
    if (nc && !nc.contains(e.target)) {
        document.getElementById('notifDropdown').classList.remove('open');
    }
});

// Poll notifications every 30s
setInterval(loadNotifications, 30000);
loadNotifications();

// ================================================================
// Feature: Task Card Context Menu (Cancel, Retry, Reassign)
// ================================================================
function renderTaskActions(task) {
    const status = task._status || task.status || '';
    let actions = '';

    if (status === 'failed' || status === 'rejected' || status === 'cancelled') {
        actions += '<button class="task-action-retry" onclick="event.stopPropagation(); retryTask(\'' + escapeHtml(String(task.id)) + '\')">Retry</button>';
    }
    if (status === 'pending' || status === 'blocked' || status === 'in_progress') {
        actions += '<button class="task-action-cancel" onclick="event.stopPropagation(); cancelTask(\'' + escapeHtml(String(task.id)) + '\')">Cancel</button>';
    }
    if (status === 'pending' || status === 'blocked') {
        actions += '<button class="task-action-reassign" onclick="event.stopPropagation(); showReassignModal(\'' + escapeHtml(String(task.id)) + '\', \'' + escapeHtml(task.assigned_to || '') + '\')">Reassign</button>';
    }
    if (task.group_id) {
        actions += '<button class="task-action-artifacts" onclick="event.stopPropagation(); showArtifactViewer(\'' + escapeHtml(task.group_id || '') + '\', \'' + escapeHtml(String(task.id)) + '\')">Artifacts</button>';
    }

    return actions ? '<div class="task-card-actions">' + actions + '</div>' : '';
}

async function retryTask(taskId) {
    try {
        await fetch('/api/tasks/' + taskId + '/retry', { method: 'POST' });
        showToast('Task ' + taskId + ' retried', 'success');
        refreshBoard();
    } catch(e) {
        showToast('Failed to retry task: ' + e.message);
    }
}

function showInputModal(title, placeholder, callback) {
    const overlay = document.createElement('div');
    overlay.className = 'task-detail-overlay';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
        <div class="modal" style="padding: 24px; max-width: 400px; width: 90%;">
            <h3 style="margin: 0 0 16px 0;">${title}</h3>
            <input type="text" class="modal-input" placeholder="${placeholder}"
                   style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-primary); color: var(--text-primary); font-size: 14px; margin-bottom: 16px; box-sizing: border-box;" />
            <div style="display: flex; gap: 8px; justify-content: flex-end;">
                <button class="btn-secondary" style="padding: 8px 16px; border-radius: 6px; cursor: pointer;">Cancel</button>
                <button class="btn-primary" style="padding: 8px 16px; border-radius: 6px; cursor: pointer; background: var(--accent); color: white; border: none;">Confirm</button>
            </div>
        </div>
    `;

    const input = overlay.querySelector('.modal-input');
    const cancelBtn = overlay.querySelector('.btn-secondary');
    const confirmBtn = overlay.querySelector('.btn-primary');

    cancelBtn.onclick = () => { document.body.removeChild(overlay); };
    confirmBtn.onclick = () => {
        const value = input.value;
        document.body.removeChild(overlay);
        callback(value);
    };
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { confirmBtn.click(); }
        if (e.key === 'Escape') { cancelBtn.click(); }
    });
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) { cancelBtn.click(); }
    });

    document.body.appendChild(overlay);
    input.focus();
}

async function cancelTask(taskId) {
    showInputModal('Cancel Task', 'Reason (optional)...', async (reason) => {
        const cancelReason = reason || 'Cancelled by user';
        try {
            await fetch('/api/tasks/' + taskId + '/cancel', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({reason: cancelReason})
            });
            showToast('Task ' + taskId + ' cancelled', 'info');
            refreshBoard();
        } catch(e) {
            showToast('Failed to cancel task: ' + e.message);
        }
    });
}

function showReassignModal(taskId, currentRole) {
    const roles = ['pm', 'architect', 'coder', 'tester', 'reviewer'];
    showInputModal('Reassign Task ' + taskId, 'Role (' + roles.join(', ') + ')...', (newRole) => {
        if (newRole && roles.includes(newRole)) {
            fetch('/api/tasks/' + taskId + '/reassign', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({assigned_to: newRole})
            }).then(() => {
                showToast('Task ' + taskId + ' reassigned to ' + newRole, 'success');
                refreshBoard();
            }).catch(e => {
                showToast('Failed to reassign: ' + e.message);
            });
        } else if (newRole) {
            showToast('Invalid role: ' + newRole + '. Must be one of: ' + roles.join(', '), 'warning');
        }
    });
}

// ================================================================
// Feature: Artifact Viewer Panel
// ================================================================
async function showArtifactViewer(groupId, taskId) {
    // Remove any existing viewer
    const existing = document.getElementById('artifactViewer');
    if (existing) existing.remove();

    const panel = document.createElement('div');
    panel.id = 'artifactViewer';
    panel.style.cssText = 'position: fixed; right: 0; top: 0; bottom: 0; width: 500px; background: var(--bg-secondary); border-left: 1px solid var(--border-subtle); z-index: 300; overflow-y: auto; padding: 24px; transform: translateX(100%); transition: transform 0.3s ease;';

    let html = '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">' +
        '<h3 style="font-size: 16px; font-weight: 700;">Artifacts: ' + escapeHtml(String(taskId)) + '</h3>' +
        '<button onclick="this.parentElement.parentElement.remove()" style="background: none; border: none; color: var(--text-secondary); font-size: 20px; cursor: pointer;">&#10005;</button>' +
        '</div>';

    try {
        const resp = await fetch('/api/artifacts/' + encodeURIComponent(groupId) + '/' + encodeURIComponent(taskId));
        const data = await resp.json();

        if (data.files && data.files.length > 0) {
            for (const file of data.files) {
                html += '<div style="margin-bottom: 12px; cursor: pointer;" onclick="loadArtifactContent(\'' + escapeHtml(groupId) + '\', \'' + escapeHtml(String(taskId)) + '\', \'' + escapeHtml(file) + '\')">' +
                    '<div style="padding: 12px; background: var(--bg-card); border-radius: var(--radius-sm); border: 1px solid var(--border-subtle); transition: border-color 0.2s;" onmouseover="this.style.borderColor=\'var(--border-hover)\'" onmouseout="this.style.borderColor=\'var(--border-subtle)\'">' +
                    '<span style="font-size: 14px;">&#128196; ' + escapeHtml(file) + '</span>' +
                    '</div></div>';
            }
        } else {
            html += '<div style="color: var(--text-muted); text-align: center; padding: 40px;">No artifacts yet</div>';
        }
    } catch(e) {
        html += '<div style="color: var(--text-muted); text-align: center; padding: 40px;">Could not load artifacts</div>';
    }

    html += '<div id="artifactContent" style="margin-top: 16px;"></div>';

    panel.innerHTML = html;
    document.body.appendChild(panel);
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });
}

async function loadArtifactContent(groupId, taskId, filename) {
    try {
        const resp = await fetch('/api/artifacts/' + encodeURIComponent(groupId) + '/' + encodeURIComponent(taskId) + '/' + encodeURIComponent(filename));
        const data = await resp.json();
        const container = document.getElementById('artifactContent');
        if (container) {
            container.innerHTML = '<div style="background: var(--bg-card); border-radius: var(--radius-sm); padding: 16px; border: 1px solid var(--border-subtle);">' +
                '<div style="font-weight: 600; margin-bottom: 8px;">' + escapeHtml(filename) + '</div>' +
                '<pre style="white-space: pre-wrap; font-size: 12px; color: var(--text-secondary); max-height: 500px; overflow-y: auto; font-family: \'SF Mono\', \'Fira Code\', monospace;">' + escapeHtml(data.content || '') + '</pre>' +
                '</div>';
        }
    } catch(e) {
        const container = document.getElementById('artifactContent');
        if (container) container.innerHTML = '<div style="color: var(--accent-rose); padding: 16px;">Failed to load file content</div>';
    }
}

// ================================================================
// Feature: Batch Operations
// ================================================================
function toggleBatchMode() {
    batchMode = !batchMode;
    selectedTasks.clear();
    document.getElementById('batchBar').classList.toggle('active', batchMode);
    updateBatchCount();
    refreshBoard();
}

function toggleTaskSelection(taskId, checkbox) {
    if (checkbox.checked) {
        selectedTasks.add(taskId);
    } else {
        selectedTasks.delete(taskId);
    }
    updateBatchCount();
}

function updateBatchCount() {
    document.getElementById('batchCount').textContent = selectedTasks.size + ' selected';
}

async function batchCancel() {
    if (selectedTasks.size === 0) { showToast('No tasks selected', 'info'); return; }
    showInputModal('Cancel ' + selectedTasks.size + ' Tasks', 'Reason (optional)...', async (reason) => {
        const cancelReason = reason || 'Batch cancelled by user';
        for (const taskId of selectedTasks) {
            try {
                await fetch('/api/tasks/' + taskId + '/cancel', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({reason: cancelReason})
                });
            } catch(e) {}
        }
        showToast(selectedTasks.size + ' tasks cancelled', 'success');
        selectedTasks.clear();
        updateBatchCount();
        refreshBoard();
    });
}

async function batchRetry() {
    if (selectedTasks.size === 0) { showToast('No tasks selected', 'info'); return; }
    for (const taskId of selectedTasks) {
        try {
            await fetch('/api/tasks/' + taskId + '/retry', { method: 'POST' });
        } catch(e) {}
    }
    showToast(selectedTasks.size + ' tasks retried', 'success');
    selectedTasks.clear();
    updateBatchCount();
    refreshBoard();
}


// ================================================================
// Feature: Task Create Modal
// ================================================================
function openTaskModal() {
    document.getElementById('task-create-modal').classList.add('open');
    document.getElementById('task-title').focus();
}

function closeTaskModal() {
    document.getElementById('task-create-modal').classList.remove('open');
    document.getElementById('task-create-form').reset();
}

async function handleTaskCreate(e) {
    e.preventDefault();
    const title = document.getElementById('task-title').value.trim();
    if (!title) {
        showToast('Please enter a task title', 'error', 3000);
        document.getElementById('task-title').focus();
        return false;
    }

    const description = document.getElementById('task-description').value.trim();
    const taskType = document.getElementById('task-type').value;
    const priority = document.getElementById('task-priority').value;
    const assignee = document.getElementById('task-assignee').value;

    // Determine assignee: use selected or auto-assign based on task type
    const typeToRole = {
        implementation: 'coder',
        bug_fix: 'coder',
        code_review: 'reviewer',
        tech_design: 'architect',
        qa_verification: 'tester'
    };
    const assignTo = assignee || typeToRole[taskType] || 'coder';

    // We need a group_id. Try to find an active group or create via goal.
    // For direct task creation, use the goal endpoint to get a group, then create the task.
    try {
        // First create a group via the goals endpoint
        const goalResp = await fetch('/api/goals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title, description: description || title })
        });

        if (!goalResp.ok) {
            const errData = await goalResp.json().catch(() => ({}));
            showToast('Failed to create task: ' + (errData.detail || goalResp.status), 'error');
            return false;
        }

        const goalData = await goalResp.json();
        const groupId = goalData.group_id;

        // Now create the actual task in that group
        const taskResp = await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                group_id: groupId,
                title: title,
                description: description || '',
                task_type: taskType,
                assigned_to: assignTo,
                assigned_by: 'human',
                priority: priority
            })
        });

        if (taskResp.ok) {
            showToast('Task created successfully', 'success');
            closeTaskModal();
            refreshBoard();
            refreshGroups();
        } else {
            const errData = await taskResp.json().catch(() => ({}));
            showToast('Failed to create task: ' + (errData.detail || taskResp.status), 'error');
        }
    } catch (err) {
        showToast('Error creating task: ' + err.message, 'error');
    }
    return false;
}

// ================================================================
// Feature: Confirmation Modal
// ================================================================
let _confirmCallback = null;

function showConfirmModal(title, message, callback) {
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-message').textContent = message;
    _confirmCallback = callback;
    document.getElementById('confirm-modal').classList.add('open');
}

function closeConfirmModal() {
    document.getElementById('confirm-modal').classList.remove('open');
    _confirmCallback = null;
}

function executeConfirmAction() {
    const cb = _confirmCallback;
    closeConfirmModal();
    if (cb) cb();
}

// ================================================================
// Feature: Client-Side Task Card Filtering
// ================================================================
let filterDebounceTimer;
function filterTasks() {
    clearTimeout(filterDebounceTimer);
    // If date range filters are active, use the advanced filter path which re-renders from allTasks
    var dateFrom = document.getElementById('filterDateFrom') ? document.getElementById('filterDateFrom').value : '';
    var dateTo = document.getElementById('filterDateTo') ? document.getElementById('filterDateTo').value : '';
    if (dateFrom || dateTo) {
        filterDebounceTimer = setTimeout(applyAdvancedFilters, 150);
    } else {
        filterDebounceTimer = setTimeout(doFilterTasks, 150);
    }
}

function doFilterTasks() {
    const query = (document.getElementById('task-search').value || '').toLowerCase();
    const statusFilter = document.getElementById('filter-status').value;
    const priorityFilter = document.getElementById('filter-priority').value;
    const assigneeFilter = document.getElementById('filter-assignee').value;

    const cards = document.querySelectorAll('.task-card');
    cards.forEach(function(card) {
        const taskId = card.getAttribute('data-task-id') || '';
        const titleEl = card.querySelector('.task-card-title');
        const title = titleEl ? titleEl.textContent.toLowerCase() : '';
        const fullText = card.textContent.toLowerCase();

        // Text search: match on title, id, or full card text
        let textMatch = true;
        if (query) {
            textMatch = title.includes(query) || taskId.toLowerCase().includes(query) || fullText.includes(query);
        }

        // Status filter: check which column the card is in
        let statusMatch = true;
        if (statusFilter) {
            const column = card.closest('.kanban-column');
            const colId = column ? column.id : '';
            // col-pending, col-in_progress, col-completed, col-blocked, col-rejected
            const colStatus = colId.replace('col-', '');
            statusMatch = colStatus === statusFilter;
        }

        // Priority filter: check badge text
        let priorityMatch = true;
        if (priorityFilter) {
            const badges = card.querySelectorAll('.task-card-badges .badge');
            let found = false;
            badges.forEach(function(b) {
                if (b.textContent.toLowerCase().trim() === priorityFilter) found = true;
            });
            priorityMatch = found;
        }

        // Assignee filter: check badge or claimed_by text
        let assigneeMatch = true;
        if (assigneeFilter) {
            const assigneeText = card.textContent.toLowerCase();
            assigneeMatch = assigneeText.includes(assigneeFilter);
        }

        card.style.display = (textMatch && statusMatch && priorityMatch && assigneeMatch) ? '' : 'none';
    });
}

// ================================================================
// Feature: Keyboard Shortcuts
// ================================================================
const shortcuts = {
    '/': () => { document.getElementById('taskSearchInput').focus(); },
    'n': () => { if (!document.activeElement.matches('input,textarea,select')) { document.getElementById('goalInput').focus(); } },
    'r': () => { if (!document.activeElement.matches('input,textarea,select')) refreshBoard(); },
    'b': () => { if (!document.activeElement.matches('input,textarea,select')) toggleBatchMode(); },
    '?': () => { if (!document.activeElement.matches('input,textarea,select')) showShortcutsHelp(); },
};

// Comprehensive keyboard handler (replaces the old Escape-only handler)
document.addEventListener('keydown', function(e) {
    // Don't intercept ctrl/cmd shortcuts
    if (e.ctrlKey || e.metaKey) return;

    // Escape closes modals
    if (e.key === 'Escape') {
        // Close task create modal
        const tcm = document.getElementById('task-create-modal');
        if (tcm && tcm.classList.contains('open')) { closeTaskModal(); return; }
        // Close confirm modal
        const cfm = document.getElementById('confirm-modal');
        if (cfm && cfm.classList.contains('open')) { closeConfirmModal(); return; }
        // Close create project wizard
        const cpw = document.getElementById('createProjectOverlay');
        if (cpw && cpw.style.display !== 'none') { closeCreateProjectWizard(); return; }
        // Close project dropdown
        const pdd = document.getElementById('projectDropdown');
        if (pdd && pdd.style.display !== 'none') { pdd.style.display = 'none'; document.removeEventListener('click', closeProjectDropdownOutside); return; }
        // Close artifact viewer
        const av = document.getElementById('artifactViewer');
        if (av) { av.remove(); return; }
        // Close onboarding
        const ob = document.getElementById('onboardingOverlay');
        if (ob) { ob.remove(); localStorage.setItem('onboarding_done', '1'); return; }
        // Close notification dropdown
        const nd = document.getElementById('notifDropdown');
        if (nd && nd.classList.contains('open')) { nd.classList.remove('open'); return; }
        // Close task detail
        const td = document.getElementById('taskDetailOverlay');
        if (td && td.classList.contains('open')) { closeTaskDetail(); return; }
        // Close settings
        const st = document.getElementById('settingsOverlay');
        if (st && st.classList.contains('open')) { closeSettings(); return; }
        // Close FAQ
        const faq = document.getElementById('faqOverlay');
        if (faq && faq.classList.contains('open')) { toggleFaqModal(); return; }
        // Exit batch mode
        if (batchMode) { toggleBatchMode(); return; }
        return;
    }

    const handler = shortcuts[e.key];
    if (handler) {
        if (e.key === '/') e.preventDefault();
        handler();
    }
});

function showShortcutsHelp() {
    const helpHtml = [
        { key: '/', desc: 'Focus search' },
        { key: 'n', desc: 'New goal' },
        { key: 'r', desc: 'Refresh board' },
        { key: 'b', desc: 'Toggle batch mode' },
        { key: '?', desc: 'Show shortcuts' },
        { key: 'Esc', desc: 'Close modals' },
    ].map(s =>
        '<div style="display:flex;justify-content:space-between;gap:16px;padding:4px 0;">' +
        '<kbd style="background:var(--bg-card);padding:2px 8px;border-radius:4px;font-size:12px;font-family:monospace;border:1px solid var(--border-subtle);">' + s.key + '</kbd>' +
        '<span style="color:var(--text-secondary);font-size:13px;">' + s.desc + '</span></div>'
    ).join('');

    // Show in a temporary modal
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
    overlay.innerHTML = '<div style="background:var(--bg-secondary);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:28px 32px;min-width:280px;">' +
        '<h3 style="margin-bottom:16px;font-size:16px;font-weight:700;">Keyboard Shortcuts</h3>' +
        '<div style="display:flex;flex-direction:column;gap:4px;">' + helpHtml + '</div>' +
        '<div style="margin-top:16px;text-align:right;"><button onclick="this.closest(\'div[style*=fixed]\').remove()" style="padding:6px 16px;background:var(--accent-indigo);border:none;border-radius:8px;color:white;cursor:pointer;font-family:Inter,sans-serif;font-weight:600;">Got it</button></div>' +
        '</div>';
    document.body.appendChild(overlay);
}

// ================================================================
// Feature: Onboarding Flow
// ================================================================
function checkOnboarding() {
    if (!localStorage.getItem('onboarding_done')) {
        showOnboarding();
    }
}

function showOnboarding() {
    const overlay = document.createElement('div');
    overlay.id = 'onboardingOverlay';
    overlay.style.cssText = 'position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 1000; display: flex; align-items: center; justify-content: center;';

    const steps = [
        { title: 'Welcome to TaskBrew Dashboard', desc: 'This is your command center for managing AI agents that build software autonomously.', icon: '&#127919;' },
        { title: 'Submit Goals', desc: 'Use the Goal Bar to submit work for your AI team. The PM agent will decompose it into tasks.', icon: '&#127775;' },
        { title: 'Task Board', desc: 'Watch tasks flow through the pipeline: Pending, In Progress, Completed. Each column shows tasks by status.', icon: '&#128203;' },
        { title: 'Agent Status', desc: 'Monitor your agents in real-time. See which agent is working on what task. Click Agents in the nav to open the sidebar.', icon: '&#129302;' },
        { title: 'Chat', desc: 'Open the chat panel to talk directly with any agent from the agent sidebar.', icon: '&#128172;' },
    ];

    let currentStep = 0;

    function renderStep() {
        overlay.innerHTML =
            '<div style="background: var(--bg-secondary); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); padding: 40px; max-width: 480px; text-align: center;">' +
            '<div style="font-size: 32px; margin-bottom: 16px;">' + steps[currentStep].icon + '</div>' +
            '<h2 style="margin-bottom: 12px; font-size: 20px; font-weight: 700;">' + steps[currentStep].title + '</h2>' +
            '<p style="color: var(--text-secondary); margin-bottom: 24px; line-height: 1.6;">' + steps[currentStep].desc + '</p>' +
            '<div style="display: flex; justify-content: space-between; align-items: center;">' +
            '<span style="color: var(--text-muted); font-size: 13px;">' + (currentStep + 1) + ' / ' + steps.length + '</span>' +
            '<div style="display: flex; gap: 8px;">' +
            '<button id="onboardSkipBtn" style="padding: 8px 16px; background: none; border: 1px solid var(--border-subtle); border-radius: 8px; color: var(--text-secondary); cursor: pointer; font-family: Inter, sans-serif;">Skip</button>' +
            '<button id="onboardNextBtn" style="padding: 8px 20px; background: var(--accent-indigo); border: none; border-radius: 8px; color: white; cursor: pointer; font-weight: 500; font-family: Inter, sans-serif;">' +
            (currentStep === steps.length - 1 ? 'Get Started' : 'Next') + '</button>' +
            '</div></div></div>';

        document.getElementById('onboardSkipBtn').addEventListener('click', () => {
            overlay.remove();
            localStorage.setItem('onboarding_done', '1');
        });
        document.getElementById('onboardNextBtn').addEventListener('click', () => {
            if (currentStep < steps.length - 1) {
                currentStep++;
                renderStep();
            } else {
                overlay.remove();
                localStorage.setItem('onboarding_done', '1');
            }
        });
    }

    renderStep();
    document.body.appendChild(overlay);
}

