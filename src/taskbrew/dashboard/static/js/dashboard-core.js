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
    system:     { bg: 'rgba(6,182,212,0.15)',   border: '#06b6d4', text: '#67e8f9' },
};

const ROLE_EMOJI = {
    pm: '\uD83D\uDCCB', researcher: '\uD83D\uDD0D', architect: '\uD83C\uDFD7\uFE0F',
    coder: '\uD83D\uDCBB', tester: '\uD83E\uDDEA', reviewer: '\uD83D\uDC41\uFE0F',
    system: '\u2699\uFE0F'
};

const ROLE_TITLE = {
    pm: 'Project Manager', researcher: 'Researcher', architect: 'Architect',
    coder: 'Coder', tester: 'Tester', reviewer: 'Code Reviewer', system: 'System AI'
};

const STATUS_ICONS = {
    backlog: 'B', blocked: '\uD83D\uDD12', pending: '\u23F3', in_progress: '\u26A1',
    review: 'R', integrating: '\u21C4', completed: '\u2705',
    rejected: '\u274C', failed: '\uD83D\uDCA5'
};

const BOARD_STATUSES = [
    'backlog', 'pending', 'in_progress', 'review', 'integrating',
    'blocked', 'completed', 'rejected', 'failed'
];

const BOARD_COLUMNS = {
    backlog:     { el: 'tasksBacklog',    count: 'countBacklog' },
    pending:     { el: 'tasksPending',    count: 'countPending' },
    in_progress: { el: 'tasksInProgress', count: 'countInProgress' },
    review:      { el: 'tasksReview',     count: 'countReview' },
    integrating: { el: 'tasksIntegrating', count: 'countIntegrating' },
    blocked:     { el: 'tasksBlocked',    count: 'countBlocked' },
    completed:   { el: 'tasksCompleted',  count: 'countCompleted' },
    rejected:    { el: 'tasksRejected',   count: 'countRejected' },
    failed:      { el: 'tasksFailed',     count: 'countFailed' },
};

const PACKAGE_STATUS_COLUMN = {
    waiting_revision: 'review',
    cancelled: 'rejected',
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
let currentBoardMode = 'packages';
let lastTaskBoardData = null;
let lastPackageBoardData = null;
let lastOperationsSummary = null;
let openPackageId = null;
let currentPackageDrawerTab = 'tasks';

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
// audit 13 F#2: the previous implementation used textContent + innerHTML
// round-tripping, which encodes &, <, >, " but NOT the single quote '.
// We use the "quoted attribute" pattern `onclick="fn('" + escapeHtml(x) + "')"`
// in dozens of places; an id containing ' breaks out of the JS string
// literal and runs arbitrary JS in the dashboard origin. Switch to a
// manual replacement that also escapes ' and ` so the helper is safe in
// both attribute-context and JS-string-literal-in-attribute contexts.
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/`/g, '&#96;');
}

// audit 13 F#4: every render site that wanted markdown previously
// inlined ``DOMPurify.sanitize(marked.parse(text))`` with no
// feature-detect, no locked ALLOWED_TAGS config, and no fallback
// for the (rare) case where one of the two CDN libraries fails to
// load. A future edit that tweaks ALLOWED_TAGS in one place and
// misses another silently reopens an XSS vector for LLM output.
// Centralise here.
window.SAFE_MARKDOWN_CONFIG = {
    ALLOWED_TAGS: [
        'p', 'br', 'hr', 'strong', 'em', 'b', 'i', 'u', 's', 'code', 'pre',
        'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'span', 'div',
    ],
    ALLOWED_ATTR: ['href', 'title', 'class', 'target', 'rel'],
    FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick'],
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button'],
    ALLOW_DATA_ATTR: false,
};

function safeMarkdown(text) {
    if (text === null || text === undefined) return '';
    const raw = String(text);
    // Fail closed: if either library is missing, render as escaped
    // plain text rather than silently concatenating user/LLM HTML.
    if (typeof window.marked === 'undefined' || typeof window.DOMPurify === 'undefined') {
        return escapeHtml(raw).replace(/\n/g, '<br>');
    }
    try {
        return window.DOMPurify.sanitize(
            window.marked.parse(raw),
            window.SAFE_MARKDOWN_CONFIG
        );
    } catch (e) {
        return escapeHtml(raw).replace(/\n/g, '<br>');
    }
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
    if ((assignee || priority) && currentBoardMode === 'packages') {
        currentBoardMode = 'tasks';
    }

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
        const packageBoard = await loadPackageBoard();
        const operationsSummary = await loadOperationsSummary();
        lastTaskBoardData = data;
        lastPackageBoardData = packageBoard;
        lastOperationsSummary = operationsSummary;

        // Flatten all tasks for list view and stats
        allTasks = [];
        for (const status of BOARD_STATUSES) {
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
        renderPackageCommandCenter(operationsSummary);

        // Render the appropriate view
        if (currentView === 'board') {
            if (shouldRenderPackageBoard(packageBoard)) {
                renderPackageBoard(packageBoard);
            } else {
                renderBoardView(data);
            }
        } else if (currentView === 'list') {
            renderListView();
        }
        refreshOpenPackageDrawer();
        handlePackageHashRoute();
    } catch (err) {
        showToast('Failed to refresh board: ' + err.message);
    }
}

async function loadPackageBoard() {
    if (currentBoardMode !== 'packages') {
        return null;
    }
    if (batchMode || currentFilters.assigned_to || currentFilters.priority) {
        return null;
    }
    try {
        const params = new URLSearchParams();
        if (currentFilters.group_id) {
            params.set('group_id', currentFilters.group_id);
        }
        const query = params.toString();
        const resp = await fetch('/api/work-packages/board' + (query ? '?' + query : ''));
        if (!resp.ok) {
            return null;
        }
        return await resp.json();
    } catch (e) {
        return null;
    }
}

async function loadOperationsSummary() {
    try {
        const params = new URLSearchParams();
        if (currentFilters.group_id) {
            params.set('group_id', currentFilters.group_id);
        }
        const query = params.toString();
        const resp = await fetch('/api/operations/summary' + (query ? '?' + query : ''));
        if (!resp.ok) {
            return null;
        }
        return await resp.json();
    } catch (e) {
        return null;
    }
}

async function loadPackageDetail(packageId) {
    const resp = await fetch('/api/work-packages/' + encodeURIComponent(packageId));
    if (!resp.ok) {
        throw new Error('HTTP ' + resp.status);
    }
    return await resp.json();
}

function shouldRenderPackageBoard(packageBoard) {
    if (batchMode) {
        return false;
    }
    return Boolean(
        currentBoardMode === 'packages' &&
        packageBoard &&
        Array.isArray(packageBoard.packages)
    );
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
    currentBoardMode = 'tasks';
    updateBoardModeControls();
    for (const [status, cfg] of Object.entries(BOARD_COLUMNS)) {
        const selectedStatus = currentFilters.status || '';
        setBoardColumnVisible(status, !selectedStatus || selectedStatus === status);
        const tasks = selectedStatus && selectedStatus !== status ? [] : (data[status] || []);
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

function renderPackageBoard(data) {
    currentBoardMode = 'packages';
    updateBoardModeControls();
    for (const [status, cfg] of Object.entries(BOARD_COLUMNS)) {
        const selectedStatus = currentFilters.status || '';
        setBoardColumnVisible(status, !selectedStatus || selectedStatus === status);
        const packages = packagesForColumn(data, status);
        const container = document.getElementById(cfg.el);
        const countEl = document.getElementById(cfg.count);
        if (!container || !countEl) continue;

        countEl.textContent = packages.length;
        container.innerHTML = '';

        if (packages.length === 0) {
            container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">' +
                (STATUS_ICONS[status] || '\uD83D\uDCE6') + '</span> No work packages</div>';
            continue;
        }

        for (const pkg of packages) {
            container.appendChild(createPackageCard(pkg));
        }
    }
}

function packagesForColumn(data, status) {
    const selectedStatus = currentFilters.status || '';
    if (selectedStatus && selectedStatus !== status) {
        return [];
    }
    const columns = data.columns || {};
    const packages = [...(columns[status] || [])];
    for (const [rawStatus, mappedStatus] of Object.entries(PACKAGE_STATUS_COLUMN)) {
        if (mappedStatus === status) {
            packages.push(...(columns[rawStatus] || []));
        }
    }
    return packages;
}

function createPackageCard(pkg) {
    const card = document.createElement('div');
    card.className = 'task-card package-card';
    card.setAttribute('data-package-id', String(pkg.id || ''));
    card.setAttribute('role', 'listitem');
    card.setAttribute('tabindex', '0');
    card.setAttribute(
        'aria-label',
        'Work package ' + String(pkg.id || '') + ': ' + (pkg.title || 'untitled')
    );

    const counts = pkg.task_counts || {};
    const completed = counts.completed || 0;
    const total = counts.total || 0;
    const riskLevel = pkg.risk_level || 'medium';
    const riskClass = classToken(riskLevel);
    const reviewStatus = pkg.review_status || 'not_started';
    const gate = pkg.review_gate || null;
    const gateStatus = gate ? (gate.status || 'pending') : reviewStatus;
    const latestIntegration = pkg.latest_integration || null;
    const attentionCount = Array.isArray(pkg.attention_reasons) ? pkg.attention_reasons.length : 0;

    let html = '<div class="task-card-header">';
    html += '<span class="task-card-id">' + escapeHtml(String(pkg.id || '')) + '</span>';
    html += '<span class="badge badge-package-role">Work Package</span>';
    if (pkg.needs_attention) {
        html += '<span class="badge badge-package-attention">' + attentionCount + ' attention</span>';
    }
    html += '</div>';
    html += '<div class="task-card-title">' + escapeHtml(truncate(pkg.title || '(untitled)', 80)) + '</div>';
    html += '<div class="package-progress">' + completed + '/' + total + ' tasks complete</div>';
    html += '<div class="task-card-badges">';
    html += '<span class="badge badge-system-gate gate-state-' + escapeHtml(classToken(gateStatus)) + '">' +
        escapeHtml(formatPackageLabel(gateStatus)) + '</span>';
    html += '<span class="badge badge-package-risk risk-' + escapeHtml(riskClass) + '">' +
        escapeHtml(formatPackageLabel(riskLevel)) + '</span>';
    if (pkg.group_id) {
        html += '<span class="badge badge-group">' + escapeHtml(pkg.group_id) + '</span>';
    }
    if (latestIntegration) {
        html += '<span class="badge badge-system-gate gate-state-' +
            escapeHtml(classToken(latestIntegration.status || 'queued')) + '">' +
            'Merge ' + escapeHtml(formatPackageLabel(latestIntegration.status || 'queued')) +
            '</span>';
    }
    html += '</div>';
    if (latestIntegration) {
        html += '<div class="package-attention-line">' +
            escapeHtml((latestIntegration.source_branch || '?') + ' -> ' +
                (latestIntegration.target_branch || 'main')) + '</div>';
    }
    if (gate && gate.status === 'running') {
        html += '<div class="task-card-system-gate">' +
            '<span class="system-gate-dot"></span> System agent reviewing</div>';
    } else if (pkg.needs_attention && pkg.attention_reasons && pkg.attention_reasons[0]) {
        html += '<div class="package-attention-line">' +
            escapeHtml(pkg.attention_reasons[0].message || 'Needs attention') + '</div>';
    }
    if (pkg.waiting_age_seconds !== null && pkg.waiting_age_seconds !== undefined) {
        html += '<div class="package-age">Waiting ' +
            escapeHtml(formatPackageAge(pkg.waiting_age_seconds)) + '</div>';
    }

    card.innerHTML = html;
    card.addEventListener('click', () => openPackageDrawer(pkg.id));
    card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { openPackageDrawer(pkg.id); }
    });
    return card;
}

function formatPackageLabel(value) {
    return String(value || '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase());
}

function formatPackageAge(seconds) {
    const total = Number(seconds || 0);
    if (total < 60) return total + 's';
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return minutes + 'm';
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return hours + 'h';
    return Math.floor(hours / 24) + 'd';
}

function renderPackageCommandCenter(summary) {
    const shell = document.getElementById('packageCommandCenter');
    if (!shell) return;
    shell.hidden = currentView !== 'board';
    renderPackageCommandSummary(summary);
    renderPackageActionRail(summary);
    updateBoardModeControls();
}

function renderPackageCommandSummary(summary) {
    const el = document.getElementById('packageCommandSummary');
    if (!el) return;
    const counts = summary && summary.counts ? summary.counts : {};
    const systemAgent = summary && summary.system_agent ? summary.system_agent : {};
    const items = [
        ['Packages', counts.packages_total || 0],
        ['Active', counts.packages_active || 0],
        ['Review', counts.packages_review || 0],
        ['Integrating', counts.packages_integrating || 0],
        ['Blocked', counts.packages_blocked || 0],
        ['Revision', counts.packages_waiting_revision || 0],
        ['Attention', counts.packages_attention || 0],
    ];
    let html = '<div class="package-summary-grid">';
    items.forEach(function(item) {
        html += '<div class="package-summary-item"><span>' +
            escapeHtml(item[0]) + '</span><strong>' + escapeHtml(String(item[1])) +
            '</strong></div>';
    });
    html += '<div class="package-summary-agent"><span>System Agent</span><strong>' +
        escapeHtml(formatPackageLabel(systemAgent.status || 'idle')) + '</strong><small>' +
        escapeHtml(systemAgent.label || systemAgent.status_detail || 'Idle') + '</small></div>';
    html += '</div>';
    el.innerHTML = html;
}

function renderPackageActionRail(summary) {
    const el = document.getElementById('packageActionRail');
    if (!el) return;
    if (!summary) {
        el.innerHTML = '<div class="rail-empty">Operations summary unavailable</div>';
        return;
    }
    const queues = summary.queues || {};
    let html = '<div class="rail-header"><div><span class="rail-kicker">System</span>' +
        '<h3>Needs Attention</h3></div><span class="rail-count">' +
        escapeHtml(String((queues.attention || []).length)) + '</span></div>';
    html += renderPackageRailSection('Review Gates', queues.review || []);
    html += renderPackageRailSection('Blocked', queues.blocked || []);
    html += renderPackageRailSection('Revision', queues.revision || []);
    html += renderPackageRailSection('Stale', queues.stale || []);
    const decisions = summary.recent_decisions || [];
    if (decisions.length) {
        html += '<div class="rail-section"><h4>Recent Decisions</h4>';
        decisions.slice(0, 4).forEach(function(decision) {
            html += '<button type="button" class="rail-item" onclick="openPackageDrawer(\'' +
                escapeHtml(String(decision.package_id || '')) + '\')">' +
                '<strong>' + escapeHtml(decision.package_id || '') + '</strong>' +
                '<span>' + escapeHtml(formatPackageLabel(decision.outcome || 'updated')) + '</span>' +
                '</button>';
        });
        html += '</div>';
    }
    el.innerHTML = html;
}

function renderPackageRailSection(title, items) {
    let html = '<div class="rail-section"><h4>' + escapeHtml(title) + '</h4>';
    if (!items.length) {
        html += '<div class="rail-empty">None</div></div>';
        return html;
    }
    items.slice(0, 5).forEach(function(item) {
        const reason = item.attention_reasons && item.attention_reasons[0]
            ? item.attention_reasons[0].message
            : formatPackageLabel(item.status || '');
        html += '<button type="button" class="rail-item" onclick="openPackageDrawer(\'' +
            escapeHtml(String(item.id || '')) + '\')">' +
            '<strong>' + escapeHtml(item.id || '') + '</strong>' +
            '<span>' + escapeHtml(truncate(item.title || reason || '', 54)) + '</span>' +
            '</button>';
    });
    html += '</div>';
    return html;
}

function updateBoardModeControls() {
    const packagesBtn = document.getElementById('modePackagesBtn');
    const tasksBtn = document.getElementById('modeTasksBtn');
    if (packagesBtn) packagesBtn.classList.toggle('active', currentBoardMode === 'packages');
    if (tasksBtn) tasksBtn.classList.toggle('active', currentBoardMode === 'tasks');
    const assignee = document.getElementById('filterAssignee');
    const priority = document.getElementById('filterPriority');
    const batchBtn = document.getElementById('batchSelectModeBtn');
    const packageMode = currentBoardMode === 'packages';
    if (assignee) assignee.disabled = packageMode;
    if (priority) priority.disabled = packageMode;
    if (batchBtn) batchBtn.disabled = packageMode;
}

function setBoardMode(mode) {
    currentBoardMode = mode === 'tasks' ? 'tasks' : 'packages';
    if (currentBoardMode === 'packages') {
        delete currentFilters.assigned_to;
        delete currentFilters.priority;
        const assignee = document.getElementById('filterAssignee');
        const priority = document.getElementById('filterPriority');
        if (assignee) assignee.value = '';
        if (priority) priority.value = '';
        if (batchMode) toggleBatchMode();
    }
    updateBoardModeControls();
    if (lastTaskBoardData && currentBoardMode === 'tasks') {
        renderBoardView(lastTaskBoardData);
    } else {
        refreshBoard();
    }
}

async function openPackageDrawer(packageId) {
    if (!packageId) return;
    openPackageId = String(packageId);
    const overlay = document.getElementById('packageDetailOverlay');
    const body = document.getElementById('packageDetailBody');
    if (!overlay || !body) return;
    overlay.classList.add('open');
    body.innerHTML = '<div class="package-detail-loading">Loading package...</div>';
    try {
        const detail = await loadPackageDetail(openPackageId);
        renderPackageDrawer(detail);
    } catch (err) {
        body.innerHTML = '<div class="package-detail-error">Failed to load package detail</div>';
    }
}

function closePackageDrawer() {
    const overlay = document.getElementById('packageDetailOverlay');
    if (overlay) overlay.classList.remove('open');
    openPackageId = null;
}

async function refreshOpenPackageDrawer() {
    const overlay = document.getElementById('packageDetailOverlay');
    if (!openPackageId || !overlay || !overlay.classList.contains('open')) return;
    try {
        renderPackageDrawer(await loadPackageDetail(openPackageId));
    } catch (e) {
        // Keep the existing drawer content visible if a live refresh fails.
    }
}

function renderPackageDrawer(detail) {
    const header = document.getElementById('packageDetailHeaderLeft');
    const body = document.getElementById('packageDetailBody');
    if (!header || !body) return;
    header.innerHTML = '<div class="task-detail-header-meta"><span class="task-detail-id">' +
        escapeHtml(detail.id || '') + '</span><span class="task-detail-id">' +
        escapeHtml(formatPackageLabel(detail.status || '')) + '</span></div><h2>' +
        escapeHtml(detail.title || '(untitled)') + '</h2>';
    const reasons = detail.attention_reasons || [];
    let html = '<div class="package-drawer-actions"><button type="button" onclick="openPackageFullPage(\'' +
        escapeHtml(String(detail.id || '')) + '\')">Open full page</button></div>';
    if (reasons.length) {
        html += '<div class="package-attention-panel"><strong>Needs Attention</strong>';
        reasons.forEach(function(reason) {
            html += '<div>' + escapeHtml(reason.message || reason.type || '') + '</div>';
        });
        html += '</div>';
    } else {
        html += '<div class="package-attention-panel quiet">No active issues</div>';
    }
    html += renderPackageDrawerTabs(detail);
    body.innerHTML = html;
}

function renderPackageDrawerTabs(detail) {
    const tabs = ['tasks', 'review', 'integration', 'artifacts', 'history'];
    let html = '<div class="package-tabs">';
    tabs.forEach(function(tab) {
        html += '<button type="button" class="' + (currentPackageDrawerTab === tab ? 'active' : '') +
            '" onclick="setPackageDrawerTab(\'' + tab + '\', \'' + escapeHtml(String(detail.id || '')) + '\')">' +
            escapeHtml(formatPackageLabel(tab)) + '</button>';
    });
    html += '</div><div class="package-tab-body">';
    if (currentPackageDrawerTab === 'review') {
        html += renderPackageReviewTab(detail);
    } else if (currentPackageDrawerTab === 'integration') {
        html += renderPackageIntegrationTab(detail);
    } else if (currentPackageDrawerTab === 'artifacts') {
        html += renderPackageArtifactsTab(detail);
    } else if (currentPackageDrawerTab === 'history') {
        html += renderPackageHistoryTab(detail);
    } else {
        html += renderPackageTasksTab(detail);
    }
    html += '</div>';
    return html;
}

function renderPackageIntegrationTab(detail) {
    const rows = detail.integration_queue || [];
    if (!rows.length) return '<div class="rail-empty">No integration queue items</div>';
    let html = '';
    rows.forEach(function(row) {
        html += '<div class="package-detail-row"><strong>' +
            escapeHtml(row.id || '') + '</strong><span>' +
            escapeHtml((row.source_branch || '?') + ' -> ' + (row.target_branch || 'main')) +
            '</span><em>' + escapeHtml(formatPackageLabel(row.status || 'queued')) + '</em></div>';
        if (row.last_error) {
            html += '<div class="package-attention-line">' +
                escapeHtml(truncate(row.last_error, 180)) + '</div>';
        }
    });
    return html;
}

async function setPackageDrawerTab(tab, packageId) {
    currentPackageDrawerTab = tab;
    if (packageId) {
        renderPackageDrawer(await loadPackageDetail(packageId));
    }
}

function renderPackageTasksTab(detail) {
    const tasks = detail.tasks || [];
    if (!tasks.length) return '<div class="rail-empty">No child tasks</div>';
    let html = '';
    tasks.forEach(function(task) {
        html += '<div class="package-detail-row"><strong>' + escapeHtml(task.id || '') +
            '</strong><span>' + escapeHtml(truncate(task.title || '', 70)) +
            '</span><em>' + escapeHtml(formatPackageLabel(task.status || '')) + '</em></div>';
    });
    return html;
}

function renderPackageReviewTab(detail) {
    const gate = detail.review_gate || {};
    let html = '<div class="package-detail-grid">';
    html += tdField('Gate State', escapeHtml(formatPackageLabel(gate.status || detail.review_status || 'none')));
    html += tdField('Review Round', escapeHtml(String(gate.review_round || detail.review_round || 0)));
    html += tdField('Latest Reason', escapeHtml(detail.latest_review_reason_summary || gate.reason || '-'));
    html += '</div>';
    const revisions = detail.revision_tasks || [];
    html += '<h4 class="package-subheading">Revision Tasks</h4>';
    html += revisions.length ? renderPackageTasksTab({tasks: revisions}) : '<div class="rail-empty">No revision tasks</div>';
    return html;
}

function renderPackageArtifactsTab(detail) {
    const artifacts = detail.artifacts || [];
    if (!artifacts.length) return '<div class="rail-empty">No artifacts yet</div>';
    let html = '';
    artifacts.forEach(function(artifact) {
        html += '<div class="package-detail-row"><strong>' + escapeHtml(artifact.task_id || '') +
            '</strong><span>' + (artifact.has_output ? 'Output captured' : 'No output yet') +
            '</span><em>' + escapeHtml(artifact.merge_status || artifact.branch_name || '-') + '</em></div>';
    });
    return html;
}

function renderPackageHistoryTab(detail) {
    const timeline = detail.timeline || [];
    if (!timeline.length) return '<div class="rail-empty">No history yet</div>';
    let html = '<div class="package-timeline">';
    timeline.slice().reverse().forEach(function(item) {
        html += '<div class="package-timeline-item"><strong>' +
            escapeHtml(item.label || item.type || '') + '</strong><span>' +
            escapeHtml(fmtTs(item.at)) + '</span></div>';
    });
    html += '</div>';
    return html;
}

async function openPackageFullPage(packageId) {
    if (!packageId) return;
    location.hash = 'package=' + encodeURIComponent(packageId);
    const detail = await loadPackageDetail(packageId);
    renderPackageFullPage(detail);
}

function closePackageFullPage() {
    const page = document.getElementById('packageFullPage');
    if (page) page.hidden = true;
    if (location.hash.startsWith('#package=')) {
        history.replaceState(null, '', location.pathname + location.search);
    }
}

async function handlePackageHashRoute() {
    if (!location.hash.startsWith('#package=')) return;
    const packageId = decodeURIComponent(location.hash.replace('#package=', ''));
    if (!packageId) return;
    try {
        renderPackageFullPage(await loadPackageDetail(packageId));
    } catch (e) {
        showToast('Failed to load package page: ' + e.message, 'error');
    }
}

function renderPackageFullPage(detail) {
    const page = document.getElementById('packageFullPage');
    const title = document.getElementById('packageFullPageTitle');
    const body = document.getElementById('packageFullPageBody');
    if (!page || !body) return;
    page.hidden = false;
    if (title) title.textContent = (detail.id || '') + ' · ' + (detail.title || '');
    body.innerHTML =
        '<section><h3>Package Spec</h3><p>' + escapeHtml(detail.description || 'No description') + '</p></section>' +
        '<section><h3>Child Tasks</h3>' + renderPackageTasksTab(detail) + '</section>' +
        '<section><h3>Review</h3>' + renderPackageReviewTab(detail) + '</section>' +
        '<section><h3>Artifacts</h3>' + renderPackageArtifactsTab(detail) + '</section>' +
        '<section><h3>History</h3>' + renderPackageHistoryTab(detail) + '</section>';
}

window.addEventListener('hashchange', handlePackageHashRoute);

function setBoardColumnVisible(status, visible) {
    const col = document.getElementById(STATUS_TO_COL_ID[status] || ('col-' + status));
    if (col) {
        col.hidden = !visible;
    }
}

function classToken(value) {
    return String(value || '').replace(/[^\w-]/g, '-');
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
    if (task.needs_review === true || task.needs_review === 1) {
        html += '<span class="badge badge-needs-review">Needs Review</span>';
    }
    const gateBadge = renderSystemGateBadge(task, status);
    if (gateBadge) {
        html += gateBadge;
    }

    html += '</div>';

    // Claimed by
    if (task.claimed_by && status === 'in_progress') {
        html += '<div class="task-card-claimed">\u2192 ' + escapeHtml(task.claimed_by) + '</div>';
    }
    const gateLine = renderSystemGateLine(task, status);
    if (gateLine) {
        html += gateLine;
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

function systemGateState(task, status) {
    if (status === 'backlog') {
        const state = task.backlog_intake_status || 'pending';
        const labels = {
            pending: 'Queued for system check',
            running: 'System agent checking',
            completed: 'System check done',
            failed: 'System check failed'
        };
        return { gate: 'backlog', state, label: labels[state] || 'System check pending' };
    }
    if (status === 'review') {
        const state = task.review_status || 'pending';
        const labels = {
            pending: 'Queued for system review',
            running: 'System agent reviewing',
            waiting_revision: 'Waiting on revision',
            failed: 'Review attempt failed',
            approved: 'Review approved',
            rejected: 'Review rejected'
        };
        return { gate: 'review', state, label: labels[state] || 'System review pending' };
    }
    return null;
}

function asArray(value) {
    if (Array.isArray(value)) return value;
    if (!value) return [];
    try {
        const parsed = JSON.parse(value);
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        return [];
    }
}

function latestSystemGateRun(task, gateName) {
    const runs = asArray(task.system_gate_runs);
    for (let i = runs.length - 1; i >= 0; i--) {
        if (!gateName || runs[i].gate === gateName) return runs[i];
    }
    return null;
}

function renderSystemGateBadge(task, status) {
    const gate = systemGateState(task, status);
    if (!gate) return '';
    const cls = 'badge-system-gate gate-' + classToken(gate.gate) +
        ' gate-state-' + classToken(gate.state);
    return '<span class="badge ' + cls + '">' + escapeHtml(gate.label) + '</span>';
}

function renderSystemGateLine(task, status) {
    const gate = systemGateState(task, status);
    if (!gate || !['running', 'failed', 'waiting_revision'].includes(gate.state)) {
        return '';
    }
    const cls = 'task-card-system-gate gate-state-' + classToken(gate.state);
    return '<div class="' + cls + '"><span class="system-gate-dot"></span>' +
        escapeHtml(gate.label) + '</div>';
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
        const statusLabel = agent.status_detail || agent.status || 'idle';

        const card = document.createElement('div');
        card.className = 'sidebar-agent-card';
        if (role === 'system') card.classList.add('sidebar-agent-system');

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
        if (agent.current_gate) {
            html += '<div class="sidebar-agent-meta">Gate: ' + escapeHtml(String(agent.current_gate)) + '</div>';
        }
        if (agent.last_heartbeat) {
            html += '<div class="sidebar-agent-meta">Last seen: ' + timeAgo(agent.last_heartbeat) + '</div>';
        }

        // Activity log
        const activityClass = agent.status === 'working' ? 'agent-activity active' : 'agent-activity';
        html += '<div class="' + activityClass + '" id="activity-' + escapeHtml(agent.instance_id || '') + '"></div>';

        // Actions
        if (role !== 'system') {
            html += '<div class="sidebar-agent-actions">';
            html += '<button class="btn btn-chat" onclick="openChat(\'' + escapeHtml(agent.instance_id || '') + '\')">Chat</button>';
            html += '</div>';
        }

        card.innerHTML = html;
        container.appendChild(card);
    }

    // Per-role pause controls
    const roles = [...new Set(agents.map(a => a.role).filter(r => r && r !== 'system'))];
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
    for (const [, cfg] of Object.entries(BOARD_COLUMNS)) {
        const container = document.getElementById(cfg.el);
        const countEl = document.getElementById(cfg.count);
        if (container && countEl) {
            const cards = container.querySelectorAll('.task-card');
            countEl.textContent = cards.length;
        }
    }
}

const STATUS_TO_COL_ID = {
    backlog: 'col-backlog',
    pending: 'col-pending',
    in_progress: 'col-in_progress',
    review: 'col-review',
    integrating: 'col-integrating',
    blocked: 'col-blocked',
    completed: 'col-completed',
    rejected: 'col-rejected',
    cancelled: 'col-rejected',
    failed: 'col-failed',
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
        const emptyLabel = currentBoardMode === 'packages' ? 'No work packages' : 'No tasks';
        container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">' +
            (STATUS_ICONS[status] || '\uD83D\uDCE6') + '</span> ' + emptyLabel + '</div>';
    }
}

async function updateSingleTask(taskId) {
    if (currentBoardMode === 'packages') {
        refreshBoard();
        return;
    }
    try {
        const scrollPositions = _saveColumnScrollPositions();

        const resp = await fetch('/api/tasks/' + encodeURIComponent(taskId));
        if (!resp.ok) { refreshBoard(); return; }
        const task = await resp.json();
        const status = task.status || 'pending';
        task._status = status;
        const selectedStatus = currentFilters.status || '';
        if (selectedStatus && selectedStatus !== status) {
            const existingCard = document.querySelector(
                '[data-task-id="' + CSS.escape(String(taskId)) + '"]'
            );
            if (existingCard) {
                const currentCol = existingCard.closest('.kanban-column');
                existingCard.remove();
                _addEmptyStateIfNeeded(currentCol);
                updateColumnCounts();
            }
            _restoreColumnScrollPositions(scrollPositions);
            return;
        }

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
    if (currentBoardMode === 'packages') {
        refreshBoard();
        return;
    }
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
            } else if (taskId && currentBoardMode !== 'packages') {
                updateSingleTask(taskId);
            } else {
                refreshBoard();
            }
            if (type.startsWith('task.system_gate_')) {
                refreshAgents();
            }
        } else if (type.startsWith('work_package.')) {
            refreshBoard();
            refreshAgents();
        } else if (type.startsWith('review_gate.')) {
            refreshBoard();
            refreshAgents();
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
