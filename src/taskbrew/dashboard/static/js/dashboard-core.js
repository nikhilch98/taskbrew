// ================================================================
// Configure marked.js
// ================================================================
marked.setOptions({ breaks: true, gfm: true });

// ================================================================
// Constants
// ================================================================
const ROLE_COLORS = {
    pm:         { bg: 'rgba(59,130,246,0.15)',  border: '#3b82f6', text: '#60a5fa' },
    architect:  { bg: 'rgba(139,92,246,0.15)',  border: '#8b5cf6', text: '#a78bfa' },
    coder:      { bg: 'rgba(245,158,11,0.15)',  border: '#f59e0b', text: '#fbbf24' },
    tester:     { bg: 'rgba(16,185,129,0.15)',  border: '#10b981', text: '#34d399' },
    reviewer:   { bg: 'rgba(236,72,153,0.15)',  border: '#ec4899', text: '#f472b6' },
};

const ROLE_EMOJI = {
    pm: '\uD83D\uDCCB', researcher: '\uD83D\uDD0D', architect: '\uD83C\uDFD7\uFE0F',
    coder: '\uD83D\uDCBB', tester: '\uD83E\uDDEA', reviewer: '\uD83D\uDC41\uFE0F'
};

const ROLE_TITLE = {
    pm: 'Project Manager', researcher: 'Researcher', architect: 'Architect',
    coder: 'Coder', tester: 'Tester', reviewer: 'Code Reviewer'
};

const STATUS_ICONS = {
    blocked: '\uD83D\uDD12', pending: '\u23F3', in_progress: '\u26A1',
    completed: '\u2705', rejected: '\u274C', failed: '\uD83D\uDCA5'
};

const MAX_LOG_ENTRIES = 200;

// ================================================================
// State
// ================================================================
let ws = null;
let reconnectTimer = null;
let currentView = 'board';
let currentFilters = {};
let allTasks = [];
let allGroups = [];
let eventCount = 0;
let listSortCol = 'id';
let listSortAsc = true;
let batchMode = false;
let selectedTasks = new Set();
let notifications = [];

// ================================================================
// Toast Notifications
// ================================================================
function showToast(message, type, duration) {
    type = type || 'error';
    duration = duration || 5000;
    var container = document.querySelector('.toast-container');
    if (!container) { container = document.createElement('div'); container.className = 'toast-container'; document.body.appendChild(container); }
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() { toast.remove(); }, duration);
}

// ================================================================
// Pagination Helper
// ================================================================
function createPagination(containerId, items, pageSize, renderFn) {
    let currentPage = 0;
    const totalPages = Math.ceil(items.length / pageSize);

    function render() {
        const start = currentPage * pageSize;
        const pageItems = items.slice(start, start + pageSize);
        const container = document.getElementById(containerId);
        if (!container) return;

        // Render items
        renderFn(pageItems);

        // Render pagination controls
        let paginationEl = container.querySelector('.pagination-controls');
        if (!paginationEl) {
            paginationEl = document.createElement('div');
            paginationEl.className = 'pagination-controls';
            container.appendChild(paginationEl);
        }

        if (totalPages <= 1) {
            paginationEl.innerHTML = '';
            return;
        }

        paginationEl.innerHTML =
            '<button class="pg-prev" ' + (currentPage === 0 ? 'disabled' : '') + '>&laquo; Prev</button>' +
            '<span class="page-info">Page ' + (currentPage + 1) + ' of ' + totalPages + ' (' + items.length + ' items)</span>' +
            '<button class="pg-next" ' + (currentPage >= totalPages - 1 ? 'disabled' : '') + '>Next &raquo;</button>';

        // Attach click handlers via addEventListener (avoids inline onclick reference issues)
        paginationEl.querySelector('.pg-prev').addEventListener('click', function() {
            currentPage = Math.max(0, currentPage - 1);
            render();
        });
        paginationEl.querySelector('.pg-next').addEventListener('click', function() {
            currentPage = Math.min(totalPages - 1, currentPage + 1);
            render();
        });
    }

    render();
    return { goTo: function(p) { currentPage = p; render(); } };
}

// ================================================================
// Prompt Modal (replaces window.prompt)
// ================================================================
function promptModal(title, placeholder) {
    return new Promise(function(resolve) {
        var overlay = document.createElement('div');
        overlay.className = 'prompt-modal-overlay';
        overlay.innerHTML =
            '<div class="prompt-modal-dialog">' +
                '<div class="modal-header"><h3>' + escapeHtml(title || 'Input') + '</h3></div>' +
                '<div class="modal-body">' +
                    '<input type="text" class="prompt-input" placeholder="' + escapeHtml(placeholder || '') + '" />' +
                '</div>' +
                '<div class="modal-actions">' +
                    '<button class="prompt-cancel" type="button">Cancel</button>' +
                    '<button class="prompt-ok btn-modal-primary" type="button">OK</button>' +
                '</div>' +
            '</div>';

        document.body.appendChild(overlay);
        var input = overlay.querySelector('.prompt-input');
        input.focus();

        function cleanup(value) {
            overlay.remove();
            resolve(value);
        }

        overlay.querySelector('.prompt-cancel').addEventListener('click', function() { cleanup(null); });
        overlay.querySelector('.prompt-ok').addEventListener('click', function() { cleanup(input.value); });
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') cleanup(input.value);
            if (e.key === 'Escape') cleanup(null);
        });
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) cleanup(null);
        });
    });
}

// ================================================================
// Focus Trap Helper
// ================================================================
function trapFocus(modalEl) {
    const focusable = modalEl.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    modalEl.addEventListener('keydown', function(e) {
        if (e.key !== 'Tab') return;
        if (e.shiftKey) { if (document.activeElement === first) { e.preventDefault(); last.focus(); } }
        else { if (document.activeElement === last) { e.preventDefault(); first.focus(); } }
    });
    first.focus();
}

// ================================================================
// Utility Functions
// ================================================================
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function getRoleColor(role) {
    return ROLE_COLORS[role] || { bg: 'rgba(148,163,184,0.15)', border: '#94a3b8', text: '#cbd5e1' };
}

function getRoleEmoji(role) {
    return ROLE_EMOJI[role] || '\uD83E\uDD16';
}

function getRoleTitle(role) {
    return ROLE_TITLE[role] || role || 'Agent';
}

function truncate(str, len) {
    if (!str) return '';
    return str.length > len ? str.substring(0, len) + '...' : str;
}

function timeAgo(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
}

// ================================================================
// Live Clock
// ================================================================
function updateClock() {
    const el = document.getElementById('navClock');
    if (el) el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

// ================================================================
// Sidebar Toggle
// ================================================================
function toggleSidebar() {
    document.body.classList.toggle('sidebar-open');
}

// ================================================================
// Log Panel Toggle
// ================================================================
function toggleLogPanel() {
    const panel = document.getElementById('logPanel');
    const arrow = document.getElementById('logArrow');
    panel.classList.toggle('collapsed');
    arrow.classList.toggle('collapsed');
}

// ================================================================
// View Switching
// ================================================================
function switchView(view) {
    currentView = view;

    // Update toggle buttons
    document.querySelectorAll('.view-toggle-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Show/hide all view containers
    var allViews = ['Board', 'List', 'Graph', 'Memory', 'Quality', 'Skills', 'Knowledge', 'Security', 'Observability', 'Codeintel', 'Planning', 'Autonomous', 'Coordination', 'Learning', 'Testing', 'Monitoring', 'Leaderboard', 'Notifications', 'Pipelines', 'Webhooks', 'Selfimprove', 'Social', 'Codereason', 'Taskintel', 'Verification', 'Process', 'Knowledgemgmt', 'Compliance'];
    var viewMap = {
        board: 'Board', list: 'List', graph: 'Graph',
        memory: 'Memory', quality: 'Quality', skills: 'Skills', knowledge: 'Knowledge',
        security: 'Security', observability: 'Observability', codeintel: 'Codeintel', planning: 'Planning',
        autonomous: 'Autonomous', coordination: 'Coordination', learning: 'Learning', testing: 'Testing',
        monitoring: 'Monitoring', leaderboard: 'Leaderboard', notifications: 'Notifications', pipelines: 'Pipelines', webhooks: 'Webhooks',
        selfimprove: 'Selfimprove', social: 'Social', codereason: 'Codereason', taskintel: 'Taskintel',
        verification: 'Verification', process: 'Process', knowledgemgmt: 'Knowledgemgmt', compliance: 'Compliance'
    };
    allViews.forEach(function(v) {
        var el = document.getElementById('view' + v);
        if (el) el.classList.toggle('active', viewMap[view] === v);
    });

    // Render the active view
    if (view === 'graph') {
        renderGraphView();
    } else if (view === 'memory') {
        loadMemories();
    } else if (view === 'quality') {
        loadQualityScores();
    } else if (view === 'skills') {
        loadSkills();
    } else if (view === 'knowledge') {
        loadKnowledgeGraph();
    } else if (view === 'security') {
        loadSecurityView();
    } else if (view === 'observability') {
        loadObservabilityView();
    } else if (view === 'codeintel') {
        loadCodeIntelView();
    } else if (view === 'planning') {
        loadPlanningView();
    } else if (view === 'autonomous') {
        loadAutonomousView();
    } else if (view === 'coordination') {
        loadCoordinationView();
    } else if (view === 'learning') {
        loadLearningView();
    } else if (view === 'testing') {
        loadTestingView();
    } else if (view === 'monitoring') {
        loadMonitoringView();
    } else if (view === 'leaderboard') {
        loadLeaderboardView();
    } else if (view === 'notifications') {
        loadNotificationsView();
    } else if (view === 'pipelines') {
        loadPipelinesView();
    } else if (view === 'webhooks') {
        loadWebhooksView();
    } else if (view === 'selfimprove') {
        loadSelfImproveView();
    } else if (view === 'social') {
        loadSocialView();
    } else if (view === 'codereason') {
        loadCodeReasonView();
    } else if (view === 'taskintel') {
        loadTaskIntelView();
    } else if (view === 'verification') {
        loadVerificationView();
    } else if (view === 'process') {
        loadProcessView();
    } else if (view === 'knowledgemgmt') {
        loadKnowledgeMgmtView();
    } else if (view === 'compliance') {
        loadComplianceView();
    } else {
        refreshBoard();
    }
}

// ================================================================
// Filter Application
// ================================================================
function applyFilters() {
    const group = document.getElementById('filterGroup').value;
    const assignee = document.getElementById('filterAssignee').value;
    const status = document.getElementById('filterStatus').value;
    const priority = document.getElementById('filterPriority').value;

    currentFilters = {};
    if (group) currentFilters.group_id = group;
    if (assignee) currentFilters.assigned_to = assignee;
    if (status) currentFilters.status = status;
    if (priority) currentFilters.priority = priority;

    if (currentView === 'graph') {
        renderGraphView();
    } else {
        refreshBoard();
    }
}

// ================================================================
// Goal Submission
// ================================================================
async function submitGoal() {
    const input = document.getElementById('goalInput');
    const btn = document.getElementById('goalSubmitBtn');
    const title = input.value.trim();
    if (!title) {
        showToast('Please enter a goal title', 'error', 3000);
        input.focus();
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Submitting...';

    try {
        const resp = await fetch('/api/goals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title, description: title })
        });
        if (resp.ok) {
            const data = await resp.json();
            input.value = '';
            // Refresh board and groups after goal creation
            refreshGroups();
            refreshBoard();
            refreshFilters();
        } else {
            showToast('Goal submission failed: ' + resp.status);
        }
    } catch (err) {
        showToast('Goal submission error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Submit Goal';
    }
}

// ================================================================
// Data Fetching
// ================================================================
async function refreshBoard() {
    try {
        const params = new URLSearchParams(currentFilters);
        const resp = await fetch('/api/board?' + params.toString());
        const data = await resp.json();

        // Flatten all tasks for list view and stats
        allTasks = [];
        const statuses = ['blocked', 'pending', 'in_progress', 'completed', 'failed', 'rejected'];
        for (const status of statuses) {
            const tasks = data[status] || [];
            for (const t of tasks) {
                t._status = status;
                allTasks.push(t);
            }
        }

        // Update stats
        const blockedCount = (data.blocked || []).length;
        const activeCount = (data.pending || []).length + (data.in_progress || []).length;
        document.getElementById('statActive').textContent = activeCount;
        document.getElementById('statBlocked').textContent = blockedCount;

        // Render the appropriate view
        if (currentView === 'board') {
            renderBoardView(data);
        } else if (currentView === 'list') {
            renderListView();
        }
    } catch (err) {
        showToast('Failed to refresh board: ' + err.message);
    }
}

async function refreshGroups() {
    try {
        const resp = await fetch('/api/groups');
        const groups = await resp.json();
        allGroups = groups;
        document.getElementById('statGroups').textContent = groups.length;
    } catch (err) {
        showToast('Failed to refresh groups: ' + err.message);
    }
}

async function refreshAgents() {
    try {
        const resp = await fetch('/api/agents');
        const agents = await resp.json();

        // Update stats
        const onlineCount = agents.filter(a => a.status === 'idle' || a.status === 'working').length;
        document.getElementById('statAgents').textContent = onlineCount;

        // Render sidebar
        renderAgentSidebar(agents);
    } catch (err) {
        showToast('Failed to refresh agents: ' + err.message);
    }
}

async function refreshFilters() {
    try {
        const resp = await fetch('/api/board/filters');
        const filters = await resp.json();

        // Populate group dropdown
        const groupSelect = document.getElementById('filterGroup');
        const currentGroup = groupSelect.value;
        groupSelect.innerHTML = '<option value="">All Groups</option>';
        if (filters.groups) {
            for (const g of filters.groups) {
                const opt = document.createElement('option');
                opt.value = g.id;
                opt.textContent = g.id + (g.title ? ' - ' + truncate(g.title, 30) : '');
                groupSelect.appendChild(opt);
            }
        }
        groupSelect.value = currentGroup;

        // Populate role dropdown
        if (filters.roles && filters.roles.length > 0) {
            const roleSelect = document.getElementById('filterAssignee');
            const currentRole = roleSelect.value;
            roleSelect.innerHTML = '<option value="">All Roles</option>';
            for (const r of filters.roles) {
                const opt = document.createElement('option');
                opt.value = r;
                opt.textContent = r.charAt(0).toUpperCase() + r.slice(1);
                roleSelect.appendChild(opt);
            }
            roleSelect.value = currentRole;
        }

        // Populate priority dropdown
        if (filters.priorities && filters.priorities.length > 0) {
            const priSelect = document.getElementById('filterPriority');
            const currentPri = priSelect.value;
            priSelect.innerHTML = '<option value="">All Priorities</option>';
            for (const p of filters.priorities) {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = p.charAt(0).toUpperCase() + p.slice(1);
                priSelect.appendChild(opt);
            }
            priSelect.value = currentPri;
        }
    } catch (err) {
        showToast('Failed to refresh filters: ' + err.message);
    }
}

// ================================================================
// Board View Rendering
// ================================================================
function renderBoardView(data) {
    const columns = {
        blocked:     { el: 'tasksBlocked',    count: 'countBlocked' },
        pending:     { el: 'tasksPending',    count: 'countPending' },
        in_progress: { el: 'tasksInProgress', count: 'countInProgress' },
        completed:   { el: 'tasksCompleted',  count: 'countCompleted' },
        rejected:    { el: 'tasksRejected',   count: 'countRejected' },
    };

    for (const [status, cfg] of Object.entries(columns)) {
        const tasks = data[status] || [];
        const container = document.getElementById(cfg.el);
        const countEl = document.getElementById(cfg.count);

        countEl.textContent = tasks.length;
        container.innerHTML = '';

        if (tasks.length === 0) {
            container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">' +
                (STATUS_ICONS[status] || '\uD83D\uDCE6') + '</span> No tasks</div>';
            continue;
        }

        for (const task of tasks) {
            container.appendChild(createTaskCard(task, status));
        }
    }
}

function createTaskCard(task, status) {
    const card = document.createElement('div');
    card.className = 'task-card';
    card.setAttribute('data-task-id', String(task.id || ''));
    card.setAttribute('draggable', 'true');
    card.setAttribute('role', 'listitem');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', 'Task ' + String(task.id || '') + ': ' + (task.title || 'untitled'));

    // Drag-and-drop: dragstart
    card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', String(task.id || ''));
        e.dataTransfer.effectAllowed = 'move';
        card.classList.add('dragging');
    });
    card.addEventListener('dragend', () => {
        card.classList.remove('dragging');
    });

    const role = task.assigned_to || task.task_type || '';
    const roleColor = getRoleColor(role);

    // Card border color by role
    if (role && ROLE_COLORS[role]) {
        card.style.borderLeft = '3px solid ' + roleColor.border;
    }

    let html = '';

    // Header: ID + priority + batch checkbox
    html += '<div class="task-card-header">';
    if (batchMode) {
        const checked = selectedTasks.has(String(task.id)) ? ' checked' : '';
        html += '<input type="checkbox" class="task-card-checkbox"' + checked + ' onclick="event.stopPropagation(); toggleTaskSelection(\'' + escapeHtml(String(task.id)) + '\', this)" />';
    }
    html += '<span class="task-card-id">' + escapeHtml(String(task.id || '')) + '</span>';
    if (task.priority === 'critical' || task.priority === 'high') {
        html += '<span class="badge badge-priority-' + escapeHtml(task.priority) + '">' +
            (task.priority === 'critical' ? '\uD83D\uDD34' : '\uD83D\uDFE0') + ' ' +
            escapeHtml(task.priority) + '</span>';
    }
    html += '</div>';

    // Title
    html += '<div class="task-card-title">' + escapeHtml(truncate(task.title || '(untitled)', 80)) + '</div>';

    // Badges row
    html += '<div class="task-card-badges">';

    // Role badge
    if (role && ROLE_COLORS[role]) {
        html += '<span class="badge badge-role" style="background:' + roleColor.bg +
            ';color:' + roleColor.text + ';border:1px solid ' + roleColor.border + '">' +
            escapeHtml(role) + '</span>';
    }

    // Group badge
    if (task.group_id) {
        html += '<span class="badge badge-group">' + escapeHtml(task.group_id) + '</span>';
    }

    html += '</div>';

    // Claimed by
    if (task.claimed_by && status === 'in_progress') {
        html += '<div class="task-card-claimed">\u2192 ' + escapeHtml(task.claimed_by) + '</div>';
    }

    // Blocked indicator
    if (status === 'blocked' && task.blocked_by && task.blocked_by.length > 0) {
        html += '<div class="task-card-blocked">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' +
            ' Blocked by: ' + task.blocked_by.map(id => escapeHtml(String(id))).join(', ') +
            '</div>';
    }

    // Task action buttons (retry, cancel, reassign, artifacts)
    task._status = status;
    html += renderTaskActions(task);

    card.innerHTML = html;
    card.addEventListener('click', () => openTaskDetail(task));
    card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { openTaskDetail(task); }
        if (e.key === ' ') { e.preventDefault(); card.classList.toggle('selected'); }
    });
    return card;
}

// ================================================================
// List View Rendering
// ================================================================
function renderListView() {
    const tbody = document.getElementById('listBody');
    tbody.innerHTML = '';

    // Sort
    const sorted = [...allTasks].sort((a, b) => {
        let va = a[listSortCol] || '';
        let vb = b[listSortCol] || '';
        if (listSortCol === 'status') { va = a._status || ''; vb = b._status || ''; }
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return listSortAsc ? -1 : 1;
        if (va > vb) return listSortAsc ? 1 : -1;
        return 0;
    });

    // Update column sort indicators
    document.querySelectorAll('.list-table thead th').forEach(th => {
        const col = th.dataset.sort;
        th.classList.toggle('sorted', col === listSortCol);
        const arrow = th.querySelector('.sort-arrow');
        if (arrow) {
            arrow.innerHTML = listSortAsc ? '&#9650;' : '&#9660;';
        }
    });

    if (sorted.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="7" style="text-align:center;padding:40px;color:var(--text-muted)">No tasks found</td>';
        tbody.appendChild(tr);
        return;
    }

    for (const task of sorted) {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', () => openTaskDetail(task));
        const status = task._status || task.status || 'pending';
        const role = task.assigned_to || '';
        const roleColor = getRoleColor(role);

        let roleHtml = '';
        if (role && ROLE_COLORS[role]) {
            roleHtml = '<span class="badge badge-role" style="background:' + roleColor.bg +
                ';color:' + roleColor.text + ';border:1px solid ' + roleColor.border + '">' +
                escapeHtml(role) + '</span>';
        } else {
            roleHtml = escapeHtml(role || '-');
        }

        const statusHtml = '<span class="list-status-badge list-status-' + escapeHtml(status) + '">' +
            '<span class="dot"></span>' + escapeHtml(status.replace('_', ' ')) + '</span>';

        const priorityHtml = task.priority
            ? ('<span class="badge ' +
                (task.priority === 'critical' ? 'badge-priority-critical' : '') +
                (task.priority === 'high' ? 'badge-priority-high' : '') + '">' +
                escapeHtml(task.priority) + '</span>')
            : '-';

        tr.innerHTML =
            '<td>' + escapeHtml(String(task.id || '')) + '</td>' +
            '<td>' + escapeHtml(truncate(task.title || '', 60)) + '</td>' +
            '<td>' + roleHtml + '</td>' +
            '<td>' + statusHtml + '</td>' +
            '<td>' + escapeHtml(task.group_id || '-') + '</td>' +
            '<td>' + priorityHtml + '</td>' +
            '<td>' + (task.created_at ? timeAgo(task.created_at) : '-') + '</td>';

        tbody.appendChild(tr);
    }
}

function sortList(col) {
    if (listSortCol === col) {
        listSortAsc = !listSortAsc;
    } else {
        listSortCol = col;
        listSortAsc = true;
    }
    renderListView();
}

// ================================================================
// Graph View Rendering
// ================================================================
async function renderGraphView() {
    const container = document.getElementById('graphContainer');
    const groupId = document.getElementById('filterGroup').value;

    if (!groupId) {
        container.innerHTML = '<div class="graph-message" id="graphMessage">' +
            'Select a group from the filter to view the task dependency graph.</div>';
        return;
    }

    container.innerHTML = '<div class="graph-message">Loading graph...</div>';

    try {
        const resp = await fetch('/api/groups/' + encodeURIComponent(groupId) + '/graph');
        const graph = await resp.json();
        const nodes = graph.nodes || [];
        const edges = graph.edges || [];

        if (nodes.length === 0) {
            container.innerHTML = '<div class="graph-message">No tasks in this group.</div>';
            return;
        }

        // Calculate progress
        const completedCount = nodes.filter(n => n.status === 'completed').length;
        const totalCount = nodes.length;
        const pct = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

        // Find group info
        const groupInfo = allGroups.find(g => g.id === groupId);
        const groupTitle = groupInfo ? groupInfo.title : groupId;

        // Build layout: assign depth to each node using topological sort
        const nodeMap = {};
        const children = {};
        const parents = {};

        for (const n of nodes) {
            nodeMap[n.id] = n;
            children[n.id] = [];
            parents[n.id] = [];
        }

        for (const e of edges) {
            if (children[e.from] && parents[e.to]) {
                children[e.from].push(e.to);
                parents[e.to].push(e.from);
            }
        }

        // Compute depth (longest path from root)
        const depth = {};
        const visited = new Set();
        const queue = [];

        // Find roots (no parents)
        for (const n of nodes) {
            if (parents[n.id].length === 0) {
                depth[n.id] = 0;
                queue.push(n.id);
            }
        }

        // BFS to assign depths
        while (queue.length > 0) {
            const id = queue.shift();
            if (visited.has(id)) continue;
            visited.add(id);

            for (const childId of (children[id] || [])) {
                const newDepth = (depth[id] || 0) + 1;
                if (!depth[childId] || newDepth > depth[childId]) {
                    depth[childId] = newDepth;
                }
                queue.push(childId);
            }
        }

        // Assign depth 0 to any unvisited nodes
        for (const n of nodes) {
            if (depth[n.id] === undefined) depth[n.id] = 0;
        }

        // Group nodes by depth level
        const levels = {};
        let maxDepth = 0;
        for (const n of nodes) {
            const d = depth[n.id];
            if (!levels[d]) levels[d] = [];
            levels[d].push(n);
            if (d > maxDepth) maxDepth = d;
        }

        // Layout parameters
        const nodeW = 180;
        const nodeH = 60;
        const hGap = 40;
        const vGap = 80;
        const padding = 40;

        // Compute positions
        let maxWidth = 0;
        const positions = {};

        for (let d = 0; d <= maxDepth; d++) {
            const lvl = levels[d] || [];
            const totalW = lvl.length * nodeW + (lvl.length - 1) * hGap;
            if (totalW > maxWidth) maxWidth = totalW;
        }

        for (let d = 0; d <= maxDepth; d++) {
            const lvl = levels[d] || [];
            const totalW = lvl.length * nodeW + (lvl.length - 1) * hGap;
            const startX = padding + (maxWidth - totalW) / 2;

            for (let i = 0; i < lvl.length; i++) {
                positions[lvl[i].id] = {
                    x: startX + i * (nodeW + hGap),
                    y: padding + d * (nodeH + vGap)
                };
            }
        }

        const svgW = maxWidth + padding * 2;
        const svgH = (maxDepth + 1) * (nodeH + vGap) - vGap + padding * 2;

        // Build HTML
        let html = '';

        // Progress bar
        html += '<div class="graph-progress-bar">';
        html += '<div class="graph-progress-title">' + escapeHtml(groupId) + ': ' +
            escapeHtml(groupTitle) + ' &mdash; ' + completedCount + '/' + totalCount + ' tasks complete</div>';
        html += '<div class="graph-progress-track">';
        html += '<div class="graph-progress-fill" style="width:' + pct + '%"></div>';
        html += '</div>';
        html += '<div class="graph-progress-label">' + pct + '% complete</div>';
        html += '</div>';

        // SVG
        html += '<div class="graph-svg-container">';
        html += '<svg width="' + svgW + '" height="' + svgH + '" xmlns="http://www.w3.org/2000/svg">';

        // Draw edges first (behind nodes)
        for (const e of edges) {
            const from = positions[e.from];
            const to = positions[e.to];
            if (!from || !to) continue;

            const x1 = from.x + nodeW / 2;
            const y1 = from.y + nodeH;
            const x2 = to.x + nodeW / 2;
            const y2 = to.y;

            // Curved path
            const midY = (y1 + y2) / 2;
            html += '<path d="M' + x1 + ',' + y1 + ' C' + x1 + ',' + midY + ' ' + x2 + ',' + midY + ' ' + x2 + ',' + y2 + '" ' +
                'fill="none" stroke="rgba(99,102,241,0.25)" stroke-width="2" ' +
                'marker-end="url(#arrowhead)"/>';
        }

        // Arrowhead marker
        html += '<defs><marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
            '<polygon points="0 0, 8 3, 0 6" fill="rgba(99,102,241,0.4)"/></marker></defs>';

        // Draw nodes
        for (const n of nodes) {
            const pos = positions[n.id];
            if (!pos) continue;

            const role = n.assigned_to || n.task_type || '';
            const color = getRoleColor(role);
            const statusIcon = STATUS_ICONS[n.status] || '\u2753';

            html += '<g class="graph-node" transform="translate(' + pos.x + ',' + pos.y + ')">';
            html += '<rect width="' + nodeW + '" height="' + nodeH + '" rx="8" ry="8" ' +
                'fill="' + color.bg + '" stroke="' + color.border + '" stroke-width="1.5"/>';
            html += '<text x="10" y="20" font-family="Inter, sans-serif" font-size="11" font-weight="700" fill="' + color.text + '">' +
                statusIcon + ' ' + escapeHtml(String(n.id || '')) + '</text>';
            html += '<text x="10" y="40" font-family="Inter, sans-serif" font-size="10" fill="' + (n.status === 'completed' ? '#34d399' : '#8b93a7') + '">' +
                escapeHtml(truncate(n.title || '', 22)) + '</text>';
            html += '</g>';
        }

        html += '</svg>';
        html += '</div>';

        container.innerHTML = html;
    } catch (err) {
        showToast('Failed to render graph: ' + err.message);
        container.innerHTML = '<div class="graph-message">Failed to load graph. Make sure a group is selected.</div>';
    }
}

// ================================================================
// Agent Sidebar Rendering
// ================================================================
function renderAgentSidebar(agents) {
    const container = document.getElementById('sidebarAgents');

    if (!agents || agents.length === 0) {
        container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">\uD83D\uDCAD</span> No agents online.</div>';
        return;
    }

    container.innerHTML = '';

    for (const agent of agents) {
        const role = agent.role || '';
        const roleColor = getRoleColor(role);
        const emoji = getRoleEmoji(role);
        const statusClass = agent.status === 'paused' ? 'sidebar-status-paused' : (agent.status === 'working' ? 'sidebar-status-working' : 'sidebar-status-idle');
        const statusLabel = agent.status || 'idle';

        const card = document.createElement('div');
        card.className = 'sidebar-agent-card';

        let html = '<div class="sidebar-agent-top">';
        html += '<div class="sidebar-agent-identity">';
        html += '<div class="sidebar-agent-avatar" style="background:' + roleColor.bg + ';border:1px solid ' + roleColor.border + '">' + emoji + '</div>';
        html += '<div>';
        html += '<div class="sidebar-agent-name">' + escapeHtml(agent.instance_id || 'unknown') + '</div>';
        html += '<div class="sidebar-agent-role">' + escapeHtml(getRoleTitle(role)) + '</div>';
        html += '</div>';
        html += '</div>';
        html += '<span class="sidebar-agent-status ' + statusClass + '">';
        html += '<span class="status-dot"></span>' + escapeHtml(statusLabel);
        html += '</span>';
        html += '</div>';

        // Meta
        if (agent.current_task) {
            html += '<div class="sidebar-agent-meta">Task: <span class="task-link">' + escapeHtml(String(agent.current_task)) + '</span></div>';
        }
        if (agent.last_heartbeat) {
            html += '<div class="sidebar-agent-meta">Last seen: ' + timeAgo(agent.last_heartbeat) + '</div>';
        }

        // Activity log
        const activityClass = agent.status === 'working' ? 'agent-activity active' : 'agent-activity';
        html += '<div class="' + activityClass + '" id="activity-' + escapeHtml(agent.instance_id || '') + '"></div>';

        // Actions
        html += '<div class="sidebar-agent-actions">';
        html += '<button class="btn btn-chat" onclick="openChat(\'' + escapeHtml(agent.instance_id || '') + '\')">Chat</button>';
        html += '</div>';

        card.innerHTML = html;
        container.appendChild(card);
    }

    // Per-role pause controls
    const roles = [...new Set(agents.map(a => a.role).filter(Boolean))];
    if (roles.length > 0) {
        const divider = document.createElement('div');
        divider.style.cssText = 'height:1px;background:var(--border-subtle);margin:12px 0;';
        container.appendChild(divider);
        const controlsTitle = document.createElement('div');
        controlsTitle.style.cssText = 'font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;';
        controlsTitle.textContent = 'Role Controls';
        container.appendChild(controlsTitle);
        for (const role of roles) {
            const row = document.createElement('div');
            row.className = 'role-controls';
            const isPaused = pausedRoles.has(role);
            row.innerHTML = '<span class="role-label">' + escapeHtml(role) + '</span>' +
                '<button class="role-pause-btn' + (isPaused ? ' paused' : '') + '" onclick="toggleRolePause(\'' + escapeHtml(role) + '\')">' +
                (isPaused ? '&#9654; Resume' : '&#10074;&#10074; Pause') + '</button>';
            container.appendChild(row);
        }
    }
}

// ================================================================
// Agent Activity Streaming
// ================================================================
function appendAgentActivity(agentName, kind, text) {
    const el = document.getElementById('activity-' + agentName);
    if (!el) return;
    el.classList.add('active');

    const div = document.createElement('div');
    if (kind === 'text') {
        div.className = 'activity-text';
        div.textContent = text;
    } else if (kind === 'tool') {
        div.className = 'activity-tool';
        div.innerHTML = '<span class="tool-name">' + escapeHtml(text) + '</span>';
    } else if (kind === 'result') {
        div.className = 'activity-result';
        div.textContent = text;
        // Clear activity after a short delay when agent completes
        setTimeout(() => { el.classList.remove('active'); }, 5000);
    } else if (kind === 'error') {
        div.className = 'activity-error';
        div.textContent = text;
    }
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;

    // Keep activity log from growing too large
    while (el.children.length > 50) {
        el.removeChild(el.firstChild);
    }
}

// ================================================================
// Differential Board Updates
// ================================================================
function updateColumnCounts() {
    const columns = {
        blocked:     { el: 'tasksBlocked',    count: 'countBlocked' },
        pending:     { el: 'tasksPending',    count: 'countPending' },
        in_progress: { el: 'tasksInProgress', count: 'countInProgress' },
        completed:   { el: 'tasksCompleted',  count: 'countCompleted' },
        rejected:    { el: 'tasksRejected',   count: 'countRejected' },
    };
    for (const [status, cfg] of Object.entries(columns)) {
        const container = document.getElementById(cfg.el);
        const countEl = document.getElementById(cfg.count);
        if (container && countEl) {
            const cards = container.querySelectorAll('.task-card');
            countEl.textContent = cards.length;
        }
    }
}

const STATUS_TO_COL_ID = {
    blocked: 'col-blocked',
    pending: 'col-pending',
    in_progress: 'col-in_progress',
    completed: 'col-completed',
    rejected: 'col-rejected',
    cancelled: 'col-rejected',
    failed: 'col-rejected',
};

function _saveColumnScrollPositions() {
    const positions = {};
    document.querySelectorAll('.column-tasks').forEach(ct => {
        if (ct.id) positions[ct.id] = ct.scrollTop;
    });
    return positions;
}

function _restoreColumnScrollPositions(positions) {
    for (const [id, top] of Object.entries(positions)) {
        const el = document.getElementById(id);
        if (el) el.scrollTop = top;
    }
}

function _addEmptyStateIfNeeded(col) {
    if (!col) return;
    const container = col.querySelector('.column-tasks');
    if (container && container.querySelectorAll('.task-card').length === 0) {
        const status = col.id.replace('col-', '');
        container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">' +
            (STATUS_ICONS[status] || '\uD83D\uDCE6') + '</span> No tasks</div>';
    }
}

async function updateSingleTask(taskId) {
    try {
        const scrollPositions = _saveColumnScrollPositions();

        const resp = await fetch('/api/tasks/' + encodeURIComponent(taskId));
        if (!resp.ok) { refreshBoard(); return; }
        const task = await resp.json();
        const status = task.status || 'pending';
        task._status = status;

        // Find existing card and update or move it
        const existingCard = document.querySelector('[data-task-id="' + CSS.escape(String(taskId)) + '"]');
        if (existingCard) {
            const newCard = createTaskCard(task, status);
            const currentCol = existingCard.closest('.kanban-column');
            const targetColId = STATUS_TO_COL_ID[status] || 'col-pending';
            const targetCol = document.getElementById(targetColId);
            if (currentCol && targetCol && currentCol.id !== targetColId) {
                // Status changed: move card to different column
                existingCard.remove();
                const emptyState = targetCol.querySelector('.column-tasks .empty-state');
                if (emptyState) emptyState.remove();
                const tasksContainer = targetCol.querySelector('.column-tasks');
                if (tasksContainer) tasksContainer.appendChild(newCard);
                _addEmptyStateIfNeeded(currentCol);
            } else {
                // Same column: update card content in-place
                existingCard.replaceWith(newCard);
            }
            updateColumnCounts();
        } else {
            // New task: append card to correct column (no full re-render)
            const targetColId = STATUS_TO_COL_ID[status] || 'col-pending';
            const targetCol = document.getElementById(targetColId);
            if (targetCol) {
                const tasksContainer = targetCol.querySelector('.column-tasks');
                if (tasksContainer) {
                    const emptyState = tasksContainer.querySelector('.empty-state');
                    if (emptyState) emptyState.remove();
                    tasksContainer.appendChild(createTaskCard(task, status));
                    updateColumnCounts();
                } else {
                    refreshBoard();
                }
            } else {
                refreshBoard();
            }
        }

        _restoreColumnScrollPositions(scrollPositions);
    } catch (e) {
        refreshBoard(); // fallback on error
    }
}

function removeSingleTask(taskId) {
    const card = document.querySelector('[data-task-id="' + CSS.escape(String(taskId)) + '"]');
    if (!card) return;
    const col = card.closest('.kanban-column');
    card.remove();
    _addEmptyStateIfNeeded(col);
    updateColumnCounts();
}

// ================================================================
// WebSocket
// ================================================================
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('wsIndicator').className = 'ws-status connected';
        document.getElementById('wsIndicator').title = 'WebSocket connected';
        document.getElementById('wsLabel').textContent = 'Connected';
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        refreshAll();
    };

    ws.onmessage = (e) => {
        const event = JSON.parse(e.data);
        appendLog(event);

        const type = event.type || '';
        if (type.startsWith('task.')) {
            // Selective update: update only the affected task card
            const taskId = event.task_id;
            if (type === 'task.deleted' && taskId) {
                removeSingleTask(taskId);
            } else if (taskId) {
                updateSingleTask(taskId);
            } else {
                refreshBoard();
            }
        } else if (type.startsWith('pipeline.')) {
            refreshBoard();
        }
        if (type.startsWith('group.')) {
            refreshGroups();
            // Only refresh board for group creation/deletion, not updates
            if (type === 'group.created' || type === 'group.deleted') {
                refreshBoard();
            }
        }
        if (type === 'agent.text') {
            appendAgentActivity(event.agent_name, 'text', event.text || '');
        } else if (type === 'agent.result') {
            appendAgentActivity(event.agent_name, 'result', event.result || '');
        } else if (type === 'tool.pre_use') {
            appendAgentActivity(event.agent_name, 'tool', event.tool_name + (event.tool_input ? ': ' + truncate(JSON.stringify(event.tool_input), 120) : ''));
        } else if (type === 'team.paused' || type === 'team.resumed' || type === 'role.paused' || type === 'role.resumed') {
            refreshPausedState();
            refreshAgents();
        } else if (type.startsWith('agent.')) {
            refreshAgents();
        }
    };

    ws.onclose = () => {
        document.getElementById('wsIndicator').className = 'ws-status disconnected';
        document.getElementById('wsIndicator').title = 'WebSocket disconnected - reconnecting...';
        document.getElementById('wsLabel').textContent = 'Disconnected';
        reconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => { ws.close(); };
}

// ================================================================
// Event Log
// ================================================================
function formatModelBadge(model) {
    if (!model) return '';
    const short = model.replace('claude-', '').replace('-20251001', '');
    const colors = { 'opus-4-6': '#8b5cf6', 'sonnet-4-6': '#3b82f6', 'haiku-4-5': '#10b981' };
    const c = colors[short] || 'var(--text-muted)';
    return '<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;background:' + c + '22;color:' + c + ';border:1px solid ' + c + '44;margin-left:6px">' + escapeHtml(short) + '</span>';
}

function appendLog(event) {
    const logPanel = document.getElementById('logPanel');
    const emptyState = logPanel.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const time = new Date().toLocaleTimeString();
    const type = event.type || 'unknown';
    const model = event.model || '';
    const filtered = Object.assign({}, event);
    delete filtered.type;
    delete filtered.model;
    const body = JSON.stringify(filtered, null, 0);

    entry.innerHTML =
        '<span class="log-time">' + escapeHtml(time) + '</span>' +
        '<span class="log-type">' + escapeHtml(type) + '</span>' +
        formatModelBadge(model) +
        '<span class="log-body">' + escapeHtml(body) + '</span>';

    logPanel.prepend(entry);

    eventCount++;
    const statEvents = document.getElementById('statEvents');
    if (statEvents) statEvents.textContent = eventCount;

    const entries = logPanel.querySelectorAll('.log-entry');
    if (entries.length > MAX_LOG_ENTRIES) {
        for (let i = MAX_LOG_ENTRIES; i < entries.length; i++) {
            entries[i].remove();
        }
    }
}

